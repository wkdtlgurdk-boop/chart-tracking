# chart-tracking (Trade Signal)

관심종목이 지정한 차트를 만들면 텔레그램 **@Tradesignal_GD_bot** 으로 알림을 보냅니다.
GitHub Actions 에서 돌아가므로 PC가 꺼져 있어도 됩니다. 파이썬 표준 라이브러리만 씁니다.

## 차트 조건 (일봉)

| 신호 | 조건 |
| --- | --- |
| 20일 신고가 | 오늘 종가 > 직전 20거래일 최고 종가 |
| 20일 눌림 | 오늘 종가가 20일선 대비 -1% ~ +5% |
| 60일 눌림 | 오늘 종가가 60일선 대비 -1% ~ +5% |
| 눌림 꼬리 | 윗꼬리가 달리면 제외, 아랫꼬리가 달리면 상한 +7% |
| 눌림 제외 | 오늘 종가 < 1~3일 전 종가 평균 (내려오는 차트) |
| 20일 눌림 필수 | 5일선 > 20일선 > 60일선 정배열 |
| 60일 눌림 필수 | 20일선 > 60일선 > 120일선 정배열 |
| 20일선 회복 | 오늘 종가가 20일선 대비 -3% ~ 0%, 이틀 연속 상승 마감, 20 > 60 > 120 정배열, 내려오는 차트 제외 |

정배열 조건은 `config.json` 의 `pullback_align_ma` (`{"20": [5, 20, 60], "60": [20, 60, 120]}`) 에서 바꿉니다.

장중에는 "오늘 종가" 자리에 그 시각 현재가를 씁니다.

## 알림 시간 (평일)

| 시각 (KST) | 내용 |
| --- | --- |
| 10:00 ~ 20:00 매시 | 장중 현재가로 스캔 — **신호가 있을 때만** 발송 |
| 20:10 이후 첫 실행 | 종가 알림 — 신호가 없어도 "없음" 발송 |

- 같은 종목은 **하루 한 번만** 알립니다 (신호 종류가 바뀌어도). `/scan` 은 예외로 전부 보여줍니다.
- 휴장일은 자동으로 건너뜁니다.
- 시계는 Apps Script 트리거입니다. 매시 한 번 부르고, 그 시각이 어느 구간인지는
  봇이 `config.json` 의 `intraday_start` / `intraday_end` / `scan_after` 로 판단합니다.

숫자는 `config.json` 에서 바꿀 수 있습니다.
- `intraday_start` / `intraday_end`: 장중 스캔 시간대 `"10:00"` ~ `"20:00"` (9시부터 받으려면 `"09:00"`).
  `intraday_end` 는 KRX 정규장 마감이고, 알림 머리말의 "장중/종가"도 이 값으로 갈린다
- `scan_after`: 이 시각 이후 첫 실행에서 종가 알림 `"20:10"`
- `new_high_basis`: `"close"`(종가 기준) / `"high"`(고가 기준)
- `pullback_band_pct`: 눌림 범위 `[-1.0, 5.0]`
- `pullback_lower_wick_max`: 아랫꼬리가 달린 날의 눌림 상한 `7.0`
- `wick_body_ratio` / `wick_min_pct`: 꼬리로 치는 기준 — 몸통의 `1.0`배 이상이고 종가의 `2.0`% 이상
- `falling_filter_days`: 내려오는 차트 판정에 쓰는 일수 `3`
- `notify_when_empty`: 종가 알림에서 신호가 없어도 "없음" 메시지를 보낼지

## 관심종목 넣기 — 텔레그램 봇에게 보내기

- `에코프로` 또는 `/add 에코프로, 삼성전자` → 추가 (한 줄에 하나씩 붙여넣어도 됨)
- `/del 에코프로` 또는 `-에코프로` → 삭제
- `/list` → 목록
- `/scan` → 지금 바로 스캔

명령은 평일 08~21시 **매시**, 주말은 6시간마다 처리되고 답장이 옵니다.
시계는 Apps Script 에 있습니다 — GitHub cron 은 9/22~23 에 하루 12번 중 네 번만,
그것도 몇 시간씩 늦게 떠서 걷어냈습니다.
급하면 GitHub Actions 탭에서 Run workflow 를 누르면 바로 처리됩니다.
이름이 여러 종목에 걸리면(예: `삼성`) 후보를 답장하고 추가하지 않습니다.
`watchlist.txt` 를 리포에서 직접 편집해도 됩니다 (한 줄에 종목명 하나).

## GitHub Actions

`.github/workflows/chart-tracking.yml`
- 매 실행: 텔레그램 명령 처리 → 시간대에 맞으면 스캔·발송
- 바뀐 `watchlist.txt` / `state.json` 은 리포에 다시 커밋
- Secrets: `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`
- Actions 탭 → chart-tracking → Run workflow (`force` 체크) 로 즉시 스캔

## 로컬 실행 (선택)

봇 토큰을 `config.local.json` 에 `{"bot_token": "...", "chat_id": "..."}` 로 넣고:

```
python trade_signal.py --dry-run --force   # 발송 없이 결과만 콘솔에
python trade_signal.py --force             # 지금 바로 스캔해서 발송
```

GitHub 과 로컬 작업 스케줄러(`register_task.ps1`)를 동시에 켜면 알림이 두 번 옵니다 — 하나만 쓰세요.

데이터는 stock.naver.com 의 비공식 엔드포인트라 네이버가 구조를 바꾸면 실패할 수 있습니다.
