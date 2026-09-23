#!/usr/bin/env python3
"""Trade Signal — 관심종목이 지정한 차트를 만들면 텔레그램(@Tradesignal_GD_bot)으로 알림.

차트 조건 (일봉)
  20일 신고가 : 오늘 종가 > 직전 20거래일 최고 종가   (config new_high_basis="high" 이면 고가 기준)
                단 60일선 대비 +30% 넘게 떠 있으면 제외 (max_breakout_extension)
  20일 눌림   : 오늘 종가가 20일 이동평균 대비 -1% ~ +5%
  60일 눌림   : 오늘 종가가 60일 이동평균 대비 -1% ~ +5%
  눌림 꼬리   : 윗꼬리가 달리면 제외, 아랫꼬리가 달리면 상한을 +7% 까지 연다
  눌림 공통   : 오늘 종가 < 1~3일 전 종가 평균 이면 '내려오는 차트'로 보고 제외
  정배열 필수 : 20일 눌림은 5 > 20 > 60, 60일 눌림은 20 > 60 > 120 (config pullback_align_ma)
  20일선 회복 : 오늘 종가가 20일선 대비 -3% ~ 0% (선 바로 아래), 이틀 연속 상승 마감,
                20 > 60 > 120 정배열. '내려오는 차트' 제외는 눌림과 같다. 20일 눌림이 잡히면 붙이지 않는다.
  (신고가 과열 컷은 tradesignal 규칙을 가져옴)

데이터: 네이버의 비공식 read-only 엔드포인트 (공식 API 아님 — 구조가 바뀌면 실패할 수 있음)
        종목명 해석은 stock.naver.com, 일봉은 api.stock.naver.com.
        2026-09-21 에 일봉 경로가 404 로 사라져 옮겼다. check_endpoints.py 로 확인할 수 있다.

Apps Script 가 GitHub Actions(.github/workflows/chart-tracking.yml)를 매시 부릅니다
(industry-report-telegram-bot/appsscript/dispatch.gs 의 dispatchChartTracking).
평일 08~21시, 주말은 명령만 처리하려고 6시간 간격으로 셋만 엽니다.
  장중(intraday_start~intraday_end) : 시간당 1번 현재가로 스캔, 신호 있을 때만 발송
  scan_after 이후                   : 하루 1번 종가 알림 (신호 없어도 발송)
  같은 종목은 하루 한 번만 알림 (/scan 강제 실행은 예외)

사용법
  python trade_signal.py                  텔레그램 명령 처리 + (시간대에 맞으면) 스캔·발송
  python trade_signal.py --dry-run        발송 없이 콘솔 출력 (텔레그램 명령도 처리 안 함)
  python trade_signal.py --commands-only  텔레그램 명령(/add /del /list /scan)만 처리
  python trade_signal.py --force          휴장일·오늘 이미 발송함 체크 무시
"""
import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

BASE = Path(__file__).resolve().parent
CONFIG_PATH = BASE / "config.json"
LOCAL_CONFIG_PATH = BASE / "config.local.json"  # 봇 토큰 (git 에 안 올림). GitHub 에서는 환경변수로 받음
STATE_PATH = BASE / "state.json"
WATCHLIST_PATH = BASE / "watchlist.txt"
LOG_PATH = BASE / "trade_signal.log"
LOCK_PATH = BASE / ".lock"

KST = timezone(timedelta(hours=9))
NAVER = "https://stock.naver.com"          # 종목명 해석(autocomplete)
CHART = "https://api.stock.naver.com"      # 일봉. 아래 fetch_daily 주석 참고
WEEKDAYS = "월화수목금토일"
WATCHLIST_HEADER = "# 관심종목 — 한 줄에 하나씩 종목명(또는 6자리 코드). '#' 뒤는 무시됩니다.\n"

DEFAULTS = {
    "bot_token": "",
    "chat_id": "",
    "intraday_start": "10:00",
    # KRX 정규장 마감. 15:30 이었다가 거래시간이 늘면서 20:00 이 됐다.
    # 이 값이 곧 '아직 장중인가'의 기준선이고, scan_after 는 그 뒤에 온다.
    "intraday_end": "20:00",
    "scan_after": "20:10",
    "new_high_days": 20,
    "new_high_basis": "close",
    "pullback_ma_days": [20, 60],
    "pullback_band_pct": [-1.0, 5.0],
    # 눌림 꼬리. 윗꼬리는 위에서 판 물량이 있었다는 뜻이라 눌림에서 뺀다. 아랫꼬리는
    # 아래에서 받친 매수가 있었다는 뜻이라 상한을 pullback_lower_wick_max 까지 연다.
    # 꼬리로 치는 기준: 몸통 × wick_body_ratio 이상이고 종가의 wick_min_pct% 이상.
    # 둘 다 달렸으면 긴 쪽으로 본다.
    "pullback_lower_wick_max": 7.0,
    "wick_body_ratio": 1.0,
    "wick_min_pct": 2.0,
    "falling_filter_days": 3,
    "pullback_align_ma": {"20": [5, 20, 60], "60": [20, 60, 120]},
    # 아래 셋은 tradesignal(volume-surge-telegram-bot/tradesignal/signals.py)에서 가져왔다.
    # 신고가여도 60일선 위로 이만큼 넘게 떠 있으면 알리지 않는다. 손절선이 한참 아래라서다.
    "max_breakout_extension": 30.0,
    # 20일선 회복: 20일선 바로 아래(reclaim_band_pct)에서 reclaim_up_days 일 연속 상승 마감하며
    # 선으로 다가오는 자리. 20일 눌림은 5>20>60 을 요구해서, 선 아래로 눌려 5일선이
    # 20일선 밑으로 내려간 종목은 못 잡는다(메가터치 8/31~9/08). 그 자리를 여기서 본다.
    # 대신 중장기 추세는 20>60>120 정배열로 확인한다.
    "reclaim_ma_days": 20,
    "reclaim_band_pct": [-3.0, 0.0],
    "reclaim_up_days": 2,
    "reclaim_align_ma": [20, 60, 120],
    "notify_when_empty": True,
}

HELP = (
    "Trade Signal 사용법\n"
    "• 종목명 보내기 (또는 /add 종목명) → 관심종목 추가, 쉼표로 여러 개\n"
    "• /del 종목명 (또는 -종목명) → 삭제\n"
    "• /list → 관심종목 보기\n"
    "• /scan → 지금 바로 스캔\n"
    "명령은 평일 08~19시 매시 15분(보통 10~20분 늦음), 주말 6시간마다 처리됩니다."
)


def log(msg):
    line = f"[{datetime.now(KST):%Y-%m-%d %H:%M:%S}] {msg}"
    print(line)
    with LOG_PATH.open("a", encoding="utf-8") as f:
        f.write(line + "\n")


def load_json(path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return default


def save_json(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


class FileLock:
    """작업 스케줄러의 스캔/명령 작업이 겹쳐 state.json 을 동시에 쓰지 않도록."""

    def __enter__(self):
        deadline = time.time() + 120
        while True:
            try:
                os.close(os.open(LOCK_PATH, os.O_CREAT | os.O_EXCL | os.O_WRONLY))
                return self
            except FileExistsError:
                try:
                    if time.time() - LOCK_PATH.stat().st_mtime > 600:
                        LOCK_PATH.unlink(missing_ok=True)
                        continue
                except FileNotFoundError:
                    continue
                if time.time() > deadline:
                    raise RuntimeError("이전 실행이 아직 끝나지 않았습니다 (.lock)")
                time.sleep(2)

    def __exit__(self, *exc):
        LOCK_PATH.unlink(missing_ok=True)


# ---------------------------------------------------------------- HTTP

def http_json(url, payload=None, timeout=20):
    headers = {"User-Agent": "Mozilla/5.0 trade-signal/1.0", "Accept": "application/json, text/plain, */*"}
    if url.startswith(NAVER) or url.startswith(CHART):
        headers["Referer"] = NAVER + "/"
    data = None
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.load(resp)
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:300]
        safe_url = re.sub(r"bot\d+:[\w-]+", "bot***", url.split("?")[0])
        raise RuntimeError(f"HTTP {e.code} {safe_url} {detail}") from None


def tg(cfg, method, **params):
    return http_json(f"https://api.telegram.org/bot{cfg['bot_token']}/{method}", params)


def send(cfg, text, dry_run):
    if dry_run:
        print("----- [dry-run] 텔레그램 메시지 -----\n" + text + "\n------------------------------------")
        return
    chunks, buf = [], ""
    for line in text.splitlines(keepends=True):
        if len(buf) + len(line) > 3900:
            chunks.append(buf)
            buf = ""
        buf += line
    if buf:
        chunks.append(buf)
    for chunk in chunks:
        tg(cfg, "sendMessage", chat_id=cfg["chat_id"], text=chunk, disable_web_page_preview=True)


# ---------------------------------------------------------------- 관심종목

ZERO_WIDTH = re.compile(r"[\u200b-\u200f\u2060\ufeff]")  # 복사·붙여넣기로 딸려오는 보이지 않는 문자


def norm(s):
    return "".join(s.split()).upper()


def read_watchlist():
    if not WATCHLIST_PATH.exists():
        return []
    items = []
    for line in WATCHLIST_PATH.read_text(encoding="utf-8").splitlines():
        kw = ZERO_WIDTH.sub("", line.split("#", 1)[0]).strip()
        if kw and kw not in items:
            items.append(kw)
    return items


def add_to_watchlist(keyword):
    text = WATCHLIST_PATH.read_text(encoding="utf-8") if WATCHLIST_PATH.exists() else WATCHLIST_HEADER
    if text and not text.endswith("\n"):
        text += "\n"
    WATCHLIST_PATH.write_text(text + keyword + "\n", encoding="utf-8")


def remove_from_watchlist(keywords):
    kept = [
        line for line in WATCHLIST_PATH.read_text(encoding="utf-8").splitlines()
        if line.split("#", 1)[0].strip() not in keywords
    ]
    WATCHLIST_PATH.write_text("\n".join(kept) + "\n", encoding="utf-8")


def resolve(keyword, cache):
    """키워드 → {"code", "name"}. 하나로 못 정하면 {"candidates": [...]}."""
    if keyword in cache:
        return cache[keyword]
    q = urllib.parse.quote(keyword)
    data = http_json(f"{NAVER}/api/autocomplete/search/autoComplete?query={q}&target=stock")
    items = [i for i in (data.get("result") or {}).get("items") or [] if i.get("nationCode") == "KOR"]
    exact = [i for i in items if i.get("code") == keyword or norm(i.get("name", "")) == norm(keyword)]
    pick = exact[0] if exact else (items[0] if len(items) == 1 else None)
    if not pick:
        return {"candidates": [f"{i['name']}({i['code']})" for i in items[:6]]}
    cache[keyword] = {"code": pick["code"], "name": pick["name"]}
    return cache[keyword]


# ---------------------------------------------------------------- 차트 조건

def fetch_daily(code, days):
    """일봉 days 거래일치. 오래된 것부터.

    2026-09-21 에 경로를 옮겼다. 그 전까지 쓰던
    stock.naver.com/api/domestic/detail/{code}/siseDay 가 404 를 준다 —
    "Route GET:/api/domestic/detail/.../siseDay not found". 네이버가 라우트를
    지운 것이라 재시도로는 안 살아난다. 종목명 해석(autocomplete)은 그대로
    200 이어서, 스캔이 '못 찾음' 없이 18종목 전부 '조회 실패'로 죽었다.

    새 경로는 날짜 범위를 받는다. 거래일 수를 달력 날짜로 바꿔야 하는데,
    주말만 쳐도 7/5 배이고 연휴가 겹치면 더 빈다. 넉넉히 잡아 두고 뒤에서
    필요한 만큼만 잘라 쓴다 — 모자라게 받으면 120일선이 조용히 짧아져서
    정배열 판정이 틀린다.
    """
    span = int(days) * 2 + 30
    start = (datetime.now(KST) - timedelta(days=span)).strftime("%Y%m%d0000")
    end = datetime.now(KST).strftime("%Y%m%d0000")
    rows = http_json(f"{CHART}/chart/domestic/item/{code}/day"
                     f"?startDateTime={start}&endDateTime={end}")
    if not isinstance(rows, list):
        raise RuntimeError(f"일봉 응답이 목록이 아님: {str(rows)[:200]}")

    def num(v):
        return float(str(v).replace(",", "") or 0)

    candles = []
    for r in rows:
        close = num(r.get("closePrice"))
        volume = num(r.get("accumulatedTradingVolume"))
        date = r.get("localDate")
        # 거래량 0 인 봉은 버린다. 휴장 전후에 값이 빈 '오늘 봉'이 섞여 오는데,
        # 그걸 오늘로 삼으면 종가가 0 이 되어 전 종목이 신호를 낸 것처럼 보인다.
        if not date or close <= 0 or volume <= 0:
            continue
        candles.append({"date": str(date), "open": num(r.get("openPrice")) or close,
                        "high": num(r.get("highPrice")) or close,
                        "low": num(r.get("lowPrice")) or close,
                        "close": close, "rate": 0.0})
    if not candles:
        raise RuntimeError(f"쓸 수 있는 일봉이 없음 (받은 행 {len(rows)})")
    candles.sort(key=lambda c: c["date"])

    # 새 경로는 전일비를 안 준다. 직접 계산한다 — 알림 본문에만 쓰이고
    # 신호 판정에는 안 쓰이므로 첫 봉은 0 으로 둔다.
    for i in range(1, len(candles)):
        prev = candles[i - 1]["close"]
        if prev:
            candles[i]["rate"] = (candles[i]["close"] / prev - 1) * 100
    return candles[-int(days):]


def detect(candles, cfg):
    closes = [c["close"] for c in candles]
    today = candles[-1]
    close = today["close"]
    hits = []

    def ma_of(d):
        return sum(closes[-d:]) / d if len(closes) >= d else None

    n = cfg["new_high_days"]
    if len(candles) > n:
        if cfg["new_high_basis"] == "high":
            ref, value = max(c["high"] for c in candles[-n - 1:-1]), today["high"]
        else:
            ref, value = max(closes[-n - 1:-1]), close
        if value > ref:
            # 60일선에서 너무 멀어진 신고가는 알리지 않는다. 60봉이 안 되면 막지 않는다.
            ma60 = ma_of(60)
            ext = (close / ma60 - 1) * 100 if ma60 else None
            if ext is None or ext <= cfg["max_breakout_extension"]:
                hits.append((f"{n}일 신고가", f"직전 {n}일 최고 {ref:,.0f}"))

    k = cfg["falling_filter_days"]
    falling = len(closes) <= k or close < sum(closes[-k - 1:-1]) / k
    if not falling:  # 내려오는 차트면 눌림 제외
        hits.extend(pullbacks(closes, close, cfg, wick(today, cfg)))

        if not any(label == f"{cfg['reclaim_ma_days']}일 눌림" for label, _ in hits):
            hits.extend(reclaim(closes, close, cfg))
    return hits


def reclaim(closes, close, cfg):
    """20일선 회복. 선 바로 아래에서 이틀 연속 올라 선으로 다가오는 자리."""
    m, k = cfg["reclaim_ma_days"], cfg["reclaim_up_days"]
    align = cfg["reclaim_align_ma"]
    if len(closes) < max(m, k + 1, *align):
        return []
    mas = [sum(closes[-a:]) / a for a in align]
    if any(x <= y for x, y in zip(mas, mas[1:])):
        return []  # 정배열 아님
    if any(closes[-i] <= closes[-i - 1] for i in range(1, k + 1)):
        return []  # 연속 상승 마감이 아님
    ma = sum(closes[-m:]) / m
    gap = (close / ma - 1) * 100
    lo, hi = cfg["reclaim_band_pct"]
    if not lo <= gap <= hi:
        return []
    return [(f"{m}일선 회복", f"{m}일선 {ma:,.0f} 대비 {gap:+.1f}% · {k}일 연속 상승")]


def wick(candle, cfg):
    """오늘 봉의 꼬리. "upper" / "lower" / None.

    몸통만으로 재면 도지(시가≈종가)는 1틱 꼬리도 꼬리가 되므로 종가 대비 최소 길이를
    같이 본다. 장중에는 아직 봉이 다 안 그려졌으므로 그 시각 모양으로 판정한다.
    """
    o, h, l, c = candle.get("open"), candle.get("high"), candle.get("low"), candle["close"]
    if not o or not h or not l:
        return None
    body = abs(c - o)
    upper = h - max(o, c)
    lower = min(o, c) - l
    need = max(body * cfg["wick_body_ratio"], c * cfg["wick_min_pct"] / 100)
    if upper >= need and upper >= lower:
        return "upper"
    if lower >= need:
        return "lower"
    return None


def pullbacks(closes, close, cfg, tail=None):
    hits = []
    if tail == "upper":
        return hits  # 윗꼬리 — 위에서 누르는 물량이 있었다
    lo, hi = cfg["pullback_band_pct"]
    if tail == "lower":
        hi = max(hi, cfg["pullback_lower_wick_max"])
    for m in cfg["pullback_ma_days"]:
        if len(closes) < m:
            continue
        align = cfg["pullback_align_ma"].get(str(m), [])
        if align:
            if len(closes) < max(align):
                continue  # 이평선 계산할 만큼 일봉이 없음 → 정배열 확인 불가
            mas = [sum(closes[-a:]) / a for a in align]
            if any(x <= y for x, y in zip(mas, mas[1:])):
                continue  # 정배열 아님
        ma = sum(closes[-m:]) / m
        gap = (close / ma - 1) * 100
        if lo <= gap <= hi:
            note = " · 아랫꼬리" if tail == "lower" else ""
            hits.append((f"{m}일 눌림", f"{m}일선 {ma:,.0f} 대비 {gap:+.1f}%{note}"))
    return hits


def scan(cfg, state, dry_run, force):
    now = datetime.now(KST)
    today = now.strftime("%Y%m%d")
    hhmm, slot = now.strftime("%H:%M"), now.strftime("%Y%m%d%H")
    if not force and now.weekday() >= 5:
        log("주말 — 명령만 처리")
        return
    if force:
        mode = "force"
    elif hhmm >= cfg["scan_after"]:
        if state.get("last_sent_date") == today:
            log("오늘 종가 알림은 이미 발송함 — 건너뜀 (--force 로 재실행)")
            return
        mode = "close"
    elif cfg["intraday_start"] <= hhmm < cfg["intraday_end"]:
        if state.get("last_intraday_slot") == slot:
            log(f"{now:%H}시 장중 스캔은 이미 함 — 건너뜀")
            return
        mode = "intraday"
    else:
        log("스캔 시간 아님 — 명령만 처리")
        return

    keywords = read_watchlist()
    if not keywords:
        log("watchlist.txt 가 비어 있음")
        if force:
            send(cfg, "관심종목이 비어 있습니다. 종목명을 보내서 추가하세요.", dry_run)
        return

    cache = state.setdefault("resolved", {})
    align_days = [a for mas in cfg["pullback_align_ma"].values() for a in mas]
    days = max(cfg["new_high_days"] + 1, *cfg["pullback_ma_days"], *align_days,
               *cfg["reclaim_align_ma"], cfg["reclaim_up_days"] + 1,
               cfg["falling_filter_days"] + 1) + 5
    groups, unresolved, failed, seen = {}, [], [], set()
    scanned, latest, found_codes, already = 0, "", set(), []
    # 같은 종목은 하루 한 번만 알림 (/scan 강제 실행은 예외)
    alerted = state.get("alerted_today") or {}
    if alerted.get("date") != today:
        alerted = {"date": today, "codes": []}

    for kw in keywords:
        try:
            info = resolve(kw, cache)
        except Exception as e:
            log(f"검색 실패 {kw}: {e}")
            failed.append(kw)
            continue
        if "code" not in info:
            unresolved.append(kw)
            continue
        if info["code"] in seen:
            continue
        seen.add(info["code"])
        try:
            candles = fetch_daily(info["code"], days)
        except Exception as e:
            log(f"일봉 조회 실패 {info['name']}: {e}")
            failed.append(info["name"])
            continue
        finally:
            time.sleep(0.2)
        if not candles or candles[-1]["close"] <= 0:
            failed.append(info["name"])
            continue
        scanned += 1
        latest = max(latest, candles[-1]["date"])
        t = candles[-1]
        hits = detect(candles, cfg)
        if hits and mode != "force" and info["code"] in alerted["codes"]:
            already.append(info["name"])
            continue
        for label, detail in hits:
            found_codes.add(info["code"])
            groups.setdefault(label, []).append(
                f"• {info['name']} {t['close']:,.0f}원 ({t['rate']:+.2f}%) — {detail}")

    if not scanned:
        log(f"스캔된 종목 없음 (못 찾음 {unresolved}, 실패 {failed})")
        if force or unresolved or failed:
            send(cfg, f"⚠️ Trade Signal 스캔 실패\n못 찾음: {', '.join(unresolved) or '-'}\n조회 실패: {', '.join(failed) or '-'}", dry_run)
        return
    if mode != "force" and latest != today:
        log(f"최신 일봉 {latest} ≠ 오늘 {today} — 휴장일로 판단, 발송 안 함")
        return

    def mark_done():
        if dry_run or mode == "force":
            return
        if mode == "close":
            state["last_sent_date"] = today
        else:
            state["last_intraday_slot"] = slot
        state.pop("today_signals", None)  # 예전 🆕 표시용 키
        state["alerted_today"] = {"date": today, "codes": sorted(set(alerted["codes"]) | found_codes)}

    d = datetime.strptime(latest, "%Y%m%d")
    # 장중인지는 intraday_end 하나로 정한다. 예전에는 여기에 (15, 30) 이
    # 따로 박혀 있었는데, 거래시간이 20:00 으로 늘어난 뒤 이 줄만 남아
    # 18:57 실행이 "종가 기준"이라고 적었다 -- 아직 두 시간 남은 시각이다.
    # 같은 값을 두 군데 두면 한쪽만 고치고 끝난다.
    live = latest == today and f"{now:%H:%M}" < cfg["intraday_end"]
    basis = f"{now:%H:%M} 장중 현재가" if live else "종가"
    parts = [f"📡 Trade Signal · {d:%m/%d}({WEEKDAYS[d.weekday()]}) {basis} 기준"]
    order = [f"{cfg['new_high_days']}일 신고가"] + [f"{m}일 눌림" for m in cfg["pullback_ma_days"]] + [f"{cfg['reclaim_ma_days']}일선 회복"]
    for label in order:
        if groups.get(label):
            parts.append(f"\n[{label}] {len(groups[label])}종목")
            parts.extend(groups[label])
    if not groups:
        if mode == "intraday" or (mode == "close" and not cfg["notify_when_empty"]):
            log(f"새로 알릴 신호 없음 ({scanned}종목, 오늘 이미 알림 {len(already)}) — 발송 안 함")
            mark_done()
            return
        parts.append("\n새로 알릴 종목이 없습니다.")
    parts.append(f"\n관심 {scanned}종목 스캔" + (f" · 오늘 이미 알린 {len(already)}종목 제외" if already else ""))
    if unresolved:
        parts.append(f"⚠️ 종목을 못 찾음: {', '.join(unresolved)}")
    if failed:
        parts.append(f"⚠️ 조회 실패: {', '.join(failed)}")

    send(cfg, "\n".join(parts), dry_run)
    log(f"발송{'(dry-run)' if dry_run else ''} [{mode}]: {scanned}종목, 신호 {sum(len(v) for v in groups.values())}건")
    mark_done()


# ---------------------------------------------------------------- 텔레그램 명령

def split_keywords(arg):
    return [p.strip() for p in re.split(r"[,\n]", arg) if p.strip()]


def handle_command(text, state):
    """반환: (답장, 지금 스캔할지)"""
    cache = state.setdefault("resolved", {})
    m = re.match(r"^/(\w+)(?:@\w+)?\s*(.*)$", text, re.S)
    if m:
        cmd, arg = m.group(1).lower(), m.group(2).strip()
    elif text.startswith("-"):
        cmd, arg = "del", text[1:].strip()
    else:
        cmd, arg = "add", text.lstrip("+").strip()

    if cmd == "scan":
        return None, True

    if cmd == "list":
        items = read_watchlist()
        if not items:
            return "관심종목이 비어 있습니다.", False
        lines = []
        for kw in items:
            info = cache.get(kw) or {}
            lines.append(f"• {info.get('name', kw)}" + (f" ({info['code']})" if info.get("code") else ""))
        return f"관심종목 {len(items)}개\n" + "\n".join(lines), False

    if cmd == "add" and arg:
        current = read_watchlist()
        codes = {cache[k]["code"] for k in current if "code" in cache.get(k, {})}
        out, queue = [], split_keywords(arg)
        while queue:
            kw = queue.pop(0)
            info = resolve(kw, cache)
            if "code" not in info:
                if " " in kw:  # "삼성전자 SK하이닉스" 처럼 띄어쓰기로 여러 개 보낸 경우
                    queue = kw.split() + queue
                    continue
                hint = "후보: " + ", ".join(info["candidates"]) if info["candidates"] else "검색 결과 없음"
                out.append(f"❓ '{kw}' — {hint}")
                continue
            if info["code"] in codes:
                out.append(f"이미 있음: {info['name']}")
                continue
            add_to_watchlist(info["name"])
            cache[info["name"]] = {"code": info["code"], "name": info["name"]}
            codes.add(info["code"])
            out.append(f"✅ 추가: {info['name']} ({info['code']})")
        return "\n".join(out), False

    if cmd == "del" and arg:
        targets = split_keywords(arg)
        remove = {
            kw for kw in read_watchlist() for t in targets
            if norm(kw) == norm(t)
            or cache.get(kw, {}).get("code") == t
            or norm(cache.get(kw, {}).get("name", "")) == norm(t)
        }
        if not remove:
            return f"관심종목에서 못 찾음: {', '.join(targets)}", False
        remove_from_watchlist(remove)
        return "🗑 삭제: " + ", ".join(cache.get(k, {}).get("name", k) for k in remove), False

    return HELP, False


def process_commands(cfg, state):
    want_scan = False
    resp = tg(cfg, "getUpdates", offset=state.get("update_offset", 0), timeout=0, allowed_updates=["message"])
    for upd in resp.get("result", []):
        state["update_offset"] = upd["update_id"] + 1
        msg = upd.get("message") or {}
        text = ZERO_WIDTH.sub("", msg.get("text") or "").strip()
        if str((msg.get("chat") or {}).get("id")) != str(cfg["chat_id"]) or not text:
            continue
        log(f"명령 수신: {text[:60]}")
        try:
            reply, scan_now = handle_command(text, state)
        except Exception as e:
            reply, scan_now = f"처리 중 오류: {e}", False
        want_scan |= scan_now
        if reply:
            send(cfg, reply, dry_run=False)
        save_json(STATE_PATH, state)
    return want_scan


def main():
    for stream in (sys.stdout, sys.stderr):
        if stream is not None and hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="replace")

    ap = argparse.ArgumentParser(description="관심종목 차트 신호 → 텔레그램")
    ap.add_argument("--dry-run", action="store_true", help="발송 없이 콘솔 출력 (명령 처리 안 함)")
    ap.add_argument("--commands-only", action="store_true", help="텔레그램 명령만 처리")
    ap.add_argument("--force", action="store_true", help="휴장일·중복 발송 체크 무시")
    args = ap.parse_args()

    cfg = {**DEFAULTS, **load_json(CONFIG_PATH, {}), **load_json(LOCAL_CONFIG_PATH, {})}
    cfg["bot_token"] = os.environ.get("TELEGRAM_BOT_TOKEN") or cfg["bot_token"]
    cfg["chat_id"] = os.environ.get("TELEGRAM_CHAT_ID") or cfg["chat_id"]
    if not args.dry_run and not (cfg["bot_token"] and cfg["chat_id"]):
        sys.exit("봇 토큰이 없습니다 — 환경변수 TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID 또는 config.local.json")

    with FileLock():
        state = load_json(STATE_PATH, {})
        try:
            want_scan = False if args.dry_run else process_commands(cfg, state)
            if args.commands_only and not want_scan:
                return
            scan(cfg, state, args.dry_run, force=args.force or args.commands_only)
        except Exception as e:
            log(f"오류: {e}")
            raise
        finally:
            save_json(STATE_PATH, state)


if __name__ == "__main__":
    main()
