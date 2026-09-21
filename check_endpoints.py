"""네이버 엔드포인트가 살아 있는지 한 번에 찍어 본다.

이 봇은 PC 작업 스케줄러에서 돌기 때문에, 조회가 전부 실패해도 로그가 그 PC
안에만 남는다. 그러면 '네이버가 바뀐 것'과 '내 PC 에서만 막힌 것'을 가를 수가
없다. 같은 호출을 GitHub 러너에서도 해 보면 그 둘이 갈린다 — 양쪽 다 실패하면
엔드포인트가 바뀐 것이고, 러너만 되면 PC 쪽(IP 차단·프록시·백신)이다.

실행: python check_endpoints.py [종목코드]
"""
import json
import sys
import urllib.error
import urllib.parse
import urllib.request

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

CODE = (sys.argv[1] if len(sys.argv) > 1 else "005930").zfill(6)

CANDIDATES = [
    # trade_signal.py 가 지금 쓰는 것
    ("siseDay (현재 사용)",
     f"https://stock.naver.com/api/domestic/detail/{CODE}/siseDay?pageSize=30"),
    # holdings_brief.py(volume-surge 리포)가 쓰는 것 — 9/21 기준 정상 확인됨
    ("chart/day (holdings-brief)",
     f"https://api.stock.naver.com/chart/domestic/item/{CODE}/day"
     "?startDateTime=202606010000&endDateTime=202612310000"),
    # 같은 화면이 쓰는 다른 경로들
    ("api.stock integration",
     f"https://api.stock.naver.com/stock/{CODE}/integration"),
    ("m.stock price",
     f"https://m.stock.naver.com/api/stock/{CODE}/price?pageSize=30&page=1"),
    ("autocomplete (종목명 해석)",
     "https://stock.naver.com/api/autocomplete/search/autoComplete"
     "?query=" + urllib.parse.quote("삼성전자") + "&target=stock"),
]

HEADERS = {
    "User-Agent": "Mozilla/5.0 trade-signal/1.0",
    "Accept": "application/json, text/plain, */*",
    "Referer": "https://stock.naver.com/",
}


def probe(label, url):
    req = urllib.request.Request(url, headers=HEADERS)
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            raw = resp.read()
            status = resp.status
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "replace")[:200].replace("\n", " ")
        print(f"  [{label}] HTTP {exc.code} — {body}")
        return
    except Exception as exc:
        print(f"  [{label}] 실패: {exc!r}")
        return
    try:
        data = json.loads(raw.decode("utf-8"))
    except Exception:
        head = raw[:120].decode("utf-8", "replace").replace("\n", " ")
        print(f"  [{label}] HTTP {status} 이지만 JSON 이 아님 — {head}")
        return
    if isinstance(data, list):
        shape = f"목록 {len(data)}행, 첫 행 키 {sorted(data[0]) if data else '없음'}"
    elif isinstance(data, dict):
        shape = f"객체, 키 {sorted(data)[:8]}"
    else:
        shape = type(data).__name__
    print(f"  [{label}] HTTP {status} OK — {shape}")


def main():
    print(f"종목 {CODE} 로 확인")
    for label, url in CANDIDATES:
        print(f"{url}")
        probe(label, url)
        print()


if __name__ == "__main__":
    main()
