#!/usr/bin/env python3
"""Trade Signal — 관심종목이 지정한 차트를 만들면 텔레그램(@Tradesignal_GD_bot)으로 알림.

차트 조건 (일봉)
  20일 신고가 : 오늘 종가 > 직전 20거래일 최고 종가   (config new_high_basis="high" 이면 고가 기준)
  20일 눌림   : 오늘 종가가 20일 이동평균 대비 -1% ~ +3%
  60일 눌림   : 오늘 종가가 60일 이동평균 대비 -1% ~ +3%
  눌림 공통   : 오늘 종가 < 1~3일 전 종가 평균 이면 '내려오는 차트'로 보고 제외

데이터: stock.naver.com 의 비공식 read-only 엔드포인트 (공식 API 아님 — 구조가 바뀌면 실패할 수 있음)

GitHub Actions(.github/workflows/chart-tracking.yml)가 평일 30분마다 실행합니다.

사용법
  python trade_signal.py                  텔레그램 명령 처리 + (scan_after 시각 이후면) 스캔·발송
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
NAVER = "https://stock.naver.com"
WEEKDAYS = "월화수목금토일"
WATCHLIST_HEADER = "# 관심종목 — 한 줄에 하나씩 종목명(또는 6자리 코드). '#' 뒤는 무시됩니다.\n"

DEFAULTS = {
    "bot_token": "",
    "chat_id": "",
    "scan_after": "15:40",
    "new_high_days": 20,
    "new_high_basis": "close",
    "pullback_ma_days": [20, 60],
    "pullback_band_pct": [-1.0, 3.0],
    "falling_filter_days": 3,
    "notify_when_empty": True,
}

HELP = (
    "Trade Signal 사용법\n"
    "• 종목명 보내기 (또는 /add 종목명) → 관심종목 추가, 쉼표로 여러 개\n"
    "• /del 종목명 (또는 -종목명) → 삭제\n"
    "• /list → 관심종목 보기\n"
    "• /scan → 지금 바로 스캔\n"
    "명령은 약 10분 간격으로 처리됩니다."
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
    if url.startswith(NAVER):
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

def norm(s):
    return "".join(s.split()).upper()


def read_watchlist():
    if not WATCHLIST_PATH.exists():
        return []
    items = []
    for line in WATCHLIST_PATH.read_text(encoding="utf-8").splitlines():
        kw = line.split("#", 1)[0].strip()
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
    rows = http_json(f"{NAVER}/api/domestic/detail/{code}/siseDay?pageSize={days}")

    def num(v):
        return float(str(v).replace(",", "") or 0)

    candles = [
        {"date": r["bizdate"], "high": num(r["highPrice"]), "close": num(r["closePrice"]),
         "rate": num(r.get("prevChangeRate") or 0)}
        for r in rows
    ]
    candles.sort(key=lambda c: c["date"])
    return candles


def detect(candles, cfg):
    closes = [c["close"] for c in candles]
    today = candles[-1]
    close = today["close"]
    hits = []

    n = cfg["new_high_days"]
    if len(candles) > n:
        if cfg["new_high_basis"] == "high":
            ref, value = max(c["high"] for c in candles[-n - 1:-1]), today["high"]
        else:
            ref, value = max(closes[-n - 1:-1]), close
        if value > ref:
            hits.append((f"{n}일 신고가", f"직전 {n}일 최고 {ref:,.0f}"))

    k = cfg["falling_filter_days"]
    if len(closes) <= k or close < sum(closes[-k - 1:-1]) / k:
        return hits  # 내려오는 차트 → 눌림 제외

    lo, hi = cfg["pullback_band_pct"]
    for m in cfg["pullback_ma_days"]:
        if len(closes) < m:
            continue
        ma = sum(closes[-m:]) / m
        gap = (close / ma - 1) * 100
        if lo <= gap <= hi:
            hits.append((f"{m}일 눌림", f"{m}일선 {ma:,.0f} 대비 {gap:+.1f}%"))
    return hits


def scan(cfg, state, dry_run, force):
    now = datetime.now(KST)
    today = now.strftime("%Y%m%d")
    if not force and now.strftime("%H:%M") < cfg["scan_after"]:
        log(f"스캔 시각({cfg['scan_after']}) 전 — 명령만 처리")
        return
    if not force and state.get("last_sent_date") == today:
        log("오늘은 이미 발송함 — 건너뜀 (--force 로 재실행)")
        return

    keywords = read_watchlist()
    if not keywords:
        log("watchlist.txt 가 비어 있음")
        if force:
            send(cfg, "관심종목이 비어 있습니다. 종목명을 보내서 추가하세요.", dry_run)
        return

    cache = state.setdefault("resolved", {})
    days = max(cfg["new_high_days"] + 1, *cfg["pullback_ma_days"], cfg["falling_filter_days"] + 1) + 5
    groups, unresolved, failed, seen = {}, [], [], set()
    scanned, latest = 0, ""

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
        for label, detail in detect(candles, cfg):
            groups.setdefault(label, []).append(
                f"• {info['name']} {t['close']:,.0f}원 ({t['rate']:+.2f}%) — {detail}")

    if not scanned:
        log(f"스캔된 종목 없음 (못 찾음 {unresolved}, 실패 {failed})")
        if force or unresolved or failed:
            send(cfg, f"⚠️ Trade Signal 스캔 실패\n못 찾음: {', '.join(unresolved) or '-'}\n조회 실패: {', '.join(failed) or '-'}", dry_run)
        return
    if not force and latest != today:
        log(f"최신 일봉 {latest} ≠ 오늘 {today} — 휴장일로 판단, 발송 안 함")
        return

    d = datetime.strptime(latest, "%Y%m%d")
    intraday = latest == today and (now.hour, now.minute) < (15, 30)
    parts = [f"📡 Trade Signal · {d:%m/%d}({WEEKDAYS[d.weekday()]}) {'장중 현재가' if intraday else '종가'} 기준"]
    order = [f"{cfg['new_high_days']}일 신고가"] + [f"{m}일 눌림" for m in cfg["pullback_ma_days"]]
    for label in order:
        if groups.get(label):
            parts.append(f"\n[{label}] {len(groups[label])}종목")
            parts.extend(groups[label])
    if not groups:
        if not cfg["notify_when_empty"] and not force:
            log(f"조건 충족 없음 ({scanned}종목) — 발송 안 함")
            state["last_sent_date"] = today
            return
        parts.append("\n조건을 만족한 종목이 없습니다.")
    parts.append(f"\n관심 {scanned}종목 스캔")
    if unresolved:
        parts.append(f"⚠️ 종목을 못 찾음: {', '.join(unresolved)}")
    if failed:
        parts.append(f"⚠️ 조회 실패: {', '.join(failed)}")

    send(cfg, "\n".join(parts), dry_run)
    log(f"발송{'(dry-run)' if dry_run else ''}: {scanned}종목, 신호 {sum(len(v) for v in groups.values())}건")
    if not dry_run and not force:
        state["last_sent_date"] = today


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
        text = (msg.get("text") or "").strip()
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
