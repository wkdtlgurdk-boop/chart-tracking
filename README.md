# chart-tracking (Trade Signal)

관심종목이 지정한 차트를 만들면 텔레그램 **@Tradesignal_GD_bot** 으로 알림을 보냅니다.
GitHub Actions 에서 돌아가므로 PC가 꺼져 있어도 됩니다. 파이썬 표준 라이브러리만 씁니다.

## 차트 조건 (일봉)

| 신호 | 조건 |
| --- | --- |
| 20일 신고가 | 오늘 종가 > 직전 20거래일 최고 종가 |
| 20일 눌림 | 오늘 종가가 20일선 대비 -1% ~ +3% |
| 60일 눌림 | 오늘 종가가 60일선 대비 -1% ~ +3% |
| 눌림 제외 | 오늘 종가 < 1~3일 전 종가 평균 (내려오는 차트) |

장중에는 "오늘 종가" 자리에 그 시각 현재가를 씁니다.

## 알림 시간 (평일)

| 시각 (KST) | 내용 |
| --- | --- |
| 10:05 ~ 15:05 매시 | 장중 현재가로 스캔 — **신호가 있을 때만** 발송 |
| 15:42 | 종가 알림 — 신호가 없어도 "없음" 발송 (늦거나 빠지면 16:05 가 대신) |

- 그날 두 번째 알림부터 새로 생긴 신호에 🆕 가 붙습니다.
- 휴장일은 자동으로 건너뜁니다.
- GitHub 스케줄은 몇 분~수십 분 늦을 수 있습니다.

숫자는 `config.json` 에서 바꿀 수 있습니다.
- `intraday_start` / `intraday_end`: 장중 스캔 시간대 `"10:00"` ~ `"15:30"` (9시부터 받으려면 `"09:00"`)
- `scan_after`: 이 시각 이후 첫 실행에서 종가 알림 `"15:40"`
- `new_high_basis`: `"close"`(종가 기준) / `"high"`(고가 기준)
- `pullback_band_pct`: 눌림 범위 `[-1.0, 3.0]`
- `falling_filter_days`: 내려오는 차트 판정에 쓰는 일수 `3`
- `notify_when_empty`: 종가 알림에서 신호가 없어도 "없음" 메시지를 보낼지

## 관심종목 넣기 — 텔레그램 봇에게 보내기

- `에코프로` 또는 `/add 에코프로, 삼성전자` → 추가 (한 줄에 하나씩 붙여넣어도 됨)
- `/del 에코프로` 또는 `-에코프로` → 삭제
- `/list` → 목록
- `/scan` → 지금 바로 스캔

명령은 평일 08:05 ~ 19:05 **매시 5분**에 처리되고 답장이 옵니다.
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
