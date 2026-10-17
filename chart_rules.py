"""차트 규칙 — 차트시그널(chart-tracking)과 tradesignal 이 같이 쓰는 한 벌.

!! 이 파일은 두 저장소에 **글자 하나 다르지 않게** 똑같이 있다 !!
    wkdtlgurdk-boop/chart-tracking            chart_rules.py
    wkdtlgurdk-boop/volume-surge-telegram-bot tradesignal/chart_rules.py
한쪽을 고치면 다른 쪽에도 그대로 복사한다. 두 봇이 같은 차트를 다르게 부르면
안 된다 -- 사용자 결정(2026-09-23). 확인: 두 파일의 sha256sum 이 같아야 한다.
숫자도 여기서만 바꾼다. 각 봇의 config 에 같은 키를 다시 두면 그 봇만 달라진다.

표준 라이브러리만 쓴다. 입력은 일봉 목록(오래된 것부터)이고 한 봉은
{"date", "open", "high", "low", "close"} 이다. 마지막 봉이 기준일이다.
장중에 부르면 마지막 봉은 그 시각까지의 봉이다.

차트 유형 (우선순위 순)
  20일 신고가  : 종가 > 직전 20거래일 최고 종가. 60일선 +30% 넘게 떠 있으면 제외(과열).
  20·60일 눌림 : 종가가 기준선 대비 -1% ~ +5%.
                 윗꼬리면 제외, 아랫꼬리면 상한 +7% 까지.
                 20일 눌림은 5>20>60, 60일 눌림은 20>60>120 정배열.
  20일선 회복  : 종가가 20일선 대비 -3% ~ 0%, 오늘 상승 마감 + 전날 상승 또는 보합(±1%),
                 20>60>120 정배열. 20일 눌림이 잡힌 날엔 붙이지 않는다.
  공통         : 종가 < 직전 2일 종가 평균 이면 '내려오는 차트'로 보고 눌림·회복 제외.
"""
from __future__ import annotations

RULES = {
    # 신고가
    "new_high_days": 20,
    "new_high_basis": "close",      # "close" 종가 기준 / "high" 고가 기준
    # 신고가여도 60일선(pullback_ma_days 중 가장 긴 선) 위로 이만큼 넘게 떠 있으면
    # 알리지 않는다. 손절선이 한참 아래라 물리면 크게 물린다.
    "max_breakout_extension": 30.0,

    # 눌림
    "pullback_ma_days": [20, 60],
    "pullback_band_pct": [-1.0, 5.0],
    # 윗꼬리는 위에서 판 물량이 있었다는 뜻이라 눌림에서 뺀다. 아랫꼬리는 아래에서
    # 받친 매수가 있었다는 뜻이라 상한을 여기까지 연다.
    "pullback_lower_wick_max": 7.0,
    # 꼬리로 치는 기준: 몸통 × wick_body_ratio 이상 **그리고** 종가의 wick_min_pct% 이상.
    # OR 로 하면 하루 5~10% 움직이는 종목은 80% 가 꼬리가 되어 가르는 힘이 없다.
    "wick_body_ratio": 1.0,
    "wick_min_pct": 2.0,
    "pullback_align_ma": {"20": [5, 20, 60], "60": [20, 60, 120]},

    # 눌림·회복 공통: 종가가 직전 N일 종가 평균보다 낮고 **전일 종가보다도** 낮으면
    # '내려오는 차트'. 둘 중 하나만 넘으면 통과한다.
    # 3 이었다가 2 로 줄였다 (2026-09-23 사용자 결정). 3일이면 사흘 전 고점이 평균에
    # 섞여 막 돌아선 날을 막았다 -- 메가터치 8/24, 9/04.
    # 전일 종가 조건은 2026-10-17 사용자 결정. 급락 다음 날 반등한 첫날이 2일 평균에
    # 못 미쳐 빠졌다 -- 피노 10/16 (전일 8,720 → 9,100, 2일 평균 9,185).
    "falling_filter_days": 2,

    # 20일선 회복. 20일 눌림은 5>20>60 을 요구해서, 선 아래로 눌려 5일선이 20일선
    # 밑으로 내려간 종목은 선 바로 아래서 반등해도 못 잡았다 (메가터치 8/31~9/08).
    # 그 자리를 본다. 중장기 추세는 20>60>120 정배열로 확인한다.
    "reclaim_ma_days": 20,
    "reclaim_band_pct": [-3.0, 0.0],
    "reclaim_up_days": 2,
    # 앞선 날들은 보합(±이 값 %)이어도 된다. 오늘은 반드시 상승 마감.
    # 메가터치 9/07(-0.3%) → 9/08(+3.1%) 같은 '보합 후 상승'을 잡으려고 둔다.
    "reclaim_flat_pct": 1.0,
    "reclaim_align_ma": [20, 60, 120],
}

BREAKOUT, PULLBACK, RECLAIM = "breakout", "pullback", "reclaim"


def merged(cfg: dict | None = None) -> dict:
    """RULES 위에 cfg 를 얹는다. 차트 키를 cfg 에 두지 않는 것이 원칙이다(맨 위 참고)."""
    return {**RULES, **(cfg or {})}


def bars_needed(cfg: dict) -> int:
    """판정에 필요한 일봉 수. 모자라게 받으면 120일선이 조용히 짧아져 정배열이 틀린다."""
    align = [a for mas in cfg["pullback_align_ma"].values() for a in mas]
    return max(cfg["new_high_days"] + 1, *cfg["pullback_ma_days"], *align,
               *cfg["reclaim_align_ma"], cfg["reclaim_up_days"] + 1,
               cfg["falling_filter_days"] + 1)


def _ma(closes: list[float], days: int) -> float | None:
    return sum(closes[-days:]) / days if len(closes) >= days else None


def _aligned(closes: list[float], days: list[int]) -> bool:
    """days 순서대로 짧은 선 > 긴 선 인가. 선을 낼 만큼 봉이 없으면 False."""
    if not days:
        return True
    if len(closes) < max(days):
        return False
    mas = [_ma(closes, d) for d in days]
    return all(x > y for x, y in zip(mas, mas[1:]))


def wick(candle: dict, cfg: dict) -> str | None:
    """기준일 봉의 꼬리. "upper" / "lower" / None. 둘 다 달렸으면 긴 쪽."""
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


def is_falling(closes: list[float], cfg: dict) -> bool:
    """직전 k일 평균 아래 **이고** 전일 종가 아래면 내려오는 차트. 하나라도 넘으면 아니다."""
    k = cfg["falling_filter_days"]
    if len(closes) <= k:
        return True
    return closes[-1] < sum(closes[-k - 1:-1]) / k and closes[-1] < closes[-2]


def breakout(candles: list[dict], cfg: dict) -> dict | None:
    """신고가면 {"prior_high", "extension"} 아니면 None. 과열 여부는 부르는 쪽이 본다."""
    n = cfg["new_high_days"]
    if len(candles) <= n:
        return None
    closes = [c["close"] for c in candles]
    if cfg["new_high_basis"] == "high":
        ref, value = max(c["high"] for c in candles[-n - 1:-1]), candles[-1]["high"]
    else:
        ref, value = max(closes[-n - 1:-1]), closes[-1]
    if value <= ref:
        return None
    long_ma = _ma(closes, max(cfg["pullback_ma_days"]))
    # 60봉이 안 되면 이격도를 모른다. 모르는 것을 과열로 치면 신규 상장주가
    # 영영 못 든다 -- 막지 않는다.
    ext = (closes[-1] / long_ma - 1) * 100 if long_ma else None
    return {"prior_high": ref, "extension": ext}


def overextension(candles: list[dict], cfg: dict) -> float | None:
    """신고가인데 과열로 컷된 이격도(%). 신고가가 아니거나 과열이 아니면 None."""
    b = breakout(candles, cfg)
    if not b or b["extension"] is None:
        return None
    return b["extension"] if b["extension"] > cfg["max_breakout_extension"] else None


def pullbacks(candles: list[dict], cfg: dict) -> list[dict]:
    closes = [c["close"] for c in candles]
    tail = wick(candles[-1], cfg)
    if tail == "upper":
        return []
    lo, hi = cfg["pullback_band_pct"]
    if tail == "lower":
        hi = max(hi, cfg["pullback_lower_wick_max"])
    hits = []
    for m in cfg["pullback_ma_days"]:
        if len(closes) < m:
            continue
        if not _aligned(closes, cfg["pullback_align_ma"].get(str(m), [])):
            continue
        ma = _ma(closes, m)
        gap = (closes[-1] / ma - 1) * 100
        if lo <= gap <= hi:
            note = " · 아랫꼬리" if tail == "lower" else ""
            hits.append({"kind": PULLBACK, "label": f"{m}일 눌림", "ma_days": m, "gap": gap,
                         "detail": f"{m}일선 {ma:,.0f} 대비 {gap:+.1f}%{note}"})
    return hits


def reclaim(candles: list[dict], cfg: dict) -> dict | None:
    closes = [c["close"] for c in candles]
    m, k = cfg["reclaim_ma_days"], cfg["reclaim_up_days"]
    if len(closes) < max(m, k + 1, *cfg["reclaim_align_ma"]):
        return None
    if not _aligned(closes, cfg["reclaim_align_ma"]):
        return None
    if closes[-1] <= closes[-2]:
        return None                       # 오늘 상승 마감이 아님
    floor = 1 - cfg["reclaim_flat_pct"] / 100
    if any(closes[-i] < closes[-i - 1] * floor for i in range(2, k + 1)):
        return None                       # 앞선 날이 보합 밖으로 밀렸다
    ma = _ma(closes, m)
    gap = (closes[-1] / ma - 1) * 100
    lo, hi = cfg["reclaim_band_pct"]
    if not lo <= gap <= hi:
        return None
    return {"kind": RECLAIM, "label": f"{m}일선 회복", "ma_days": m, "gap": gap,
            "detail": f"{m}일선 {ma:,.0f} 대비 {gap:+.1f}%"}


def detect(candles: list[dict], cfg: dict) -> list[dict]:
    """맞는 차트 전부. 우선순위 순(신고가 → 눌림 → 회복). 없으면 빈 목록.

    한 항목은 {"kind", "label", "detail"} 에 눌림·회복이면 "ma_days", "gap" 이 붙는다.
    """
    if not candles:
        return []
    hits = []
    b = breakout(candles, cfg)
    if b and (b["extension"] is None or b["extension"] <= cfg["max_breakout_extension"]):
        n = cfg["new_high_days"]
        hits.append({"kind": BREAKOUT, "label": f"{n}일 신고가",
                     "detail": f"직전 {n}일 최고 {b['prior_high']:,.0f}"})
    closes = [c["close"] for c in candles]
    if not is_falling(closes, cfg):
        pb = pullbacks(candles, cfg)
        hits.extend(pb)
        if not any(h["ma_days"] == cfg["reclaim_ma_days"] for h in pb):
            r = reclaim(candles, cfg)
            if r:
                hits.append(r)
    return hits


def label_order(cfg: dict) -> list[str]:
    """알림에 묶어 찍는 순서."""
    return ([f"{cfg['new_high_days']}일 신고가"]
            + [f"{m}일 눌림" for m in cfg["pullback_ma_days"]]
            + [f"{cfg['reclaim_ma_days']}일선 회복"])


def explain(candles: list[dict], cfg: dict) -> list[str]:
    """어느 조건에서 떨어졌나. 규칙을 다듬을 때 보는 것이라 맞은 것도 함께 적는다."""
    closes = [c["close"] for c in candles]
    out = []
    b = breakout(candles, cfg)
    if b:
        ext = b["extension"]
        cut = cfg["max_breakout_extension"]
        if ext is not None and ext > cut:
            out.append(f"신고가지만 과열 — 60일선 {ext:+.1f}% (컷 {cut}%)")
    else:
        out.append(f"신고가 아님 — 직전 {cfg['new_high_days']}일 최고 종가 넘지 못함")
    if is_falling(closes, cfg):
        k = cfg["falling_filter_days"]
        out.append(f"내려오는 차트 — 직전 {k}일 평균 {sum(closes[-k - 1:-1]) / k:,.0f}원"
                   f"·전일 {closes[-2]:,.0f}원 둘 다 아래")
    tail = wick(candles[-1], cfg)
    if tail == "upper":
        out.append("윗꼬리 — 눌림 제외")
    elif tail == "lower":
        out.append(f"아랫꼬리 — 눌림 상한 +{cfg['pullback_lower_wick_max']}%")
    lo, hi = cfg["pullback_band_pct"]
    if tail == "lower":
        hi = max(hi, cfg["pullback_lower_wick_max"])
    for m in cfg["pullback_ma_days"]:
        ma = _ma(closes, m)
        if not ma:
            out.append(f"{m}일선 없음 — 봉이 모자라다")
            continue
        g = (closes[-1] / ma - 1) * 100
        align = cfg["pullback_align_ma"].get(str(m), [])
        if not lo <= g <= hi:
            out.append(f"{m}일 눌림 띠 밖 — {g:+.1f}% ({lo}~{hi}%)")
        elif not _aligned(closes, align):
            out.append(f"{m}일 눌림 정배열 아님 ({'>'.join(map(str, align))})")
    m = cfg["reclaim_ma_days"]
    ma = _ma(closes, m)
    if ma:
        g = (closes[-1] / ma - 1) * 100
        lo, hi = cfg["reclaim_band_pct"]
        if lo <= g <= hi and reclaim(candles, cfg) is None:
            if not _aligned(closes, cfg["reclaim_align_ma"]):
                out.append(f"{m}일선 회복 정배열 아님 "
                           f"({'>'.join(map(str, cfg['reclaim_align_ma']))})")
            elif len(closes) > 2 and closes[-1] <= closes[-2]:
                out.append(f"{m}일선 회복 — 오늘 상승 마감 아님")
            elif len(closes) > cfg["reclaim_up_days"]:
                out.append(f"{m}일선 회복 — 전날이 보합(±{cfg['reclaim_flat_pct']}%) 밖으로 밀림")
    return out
