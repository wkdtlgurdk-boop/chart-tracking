# chart-tracking — Claude 작업 규칙

## 답변
- 사용자에게는 한글로만 답한다 (코드·파일명·명령어는 예외).

## 차트 규칙은 두 저장소가 한 벌을 같이 쓴다 (2026-09-23 사용자 결정)

차트 판정(신고가·눌림·20일선 회복·꼬리·정배열·과열 컷)은 **`chart_rules.py`** 한 파일에 있고,
아래 두 곳에 **글자 하나 다르지 않게** 똑같이 있어야 한다. 시험 파일도 같다.

| 저장소 | 규칙 | 시험 |
|---|---|---|
| wkdtlgurdk-boop/chart-tracking (차트시그널) | `chart_rules.py` | `test_chart_rules.py` |
| wkdtlgurdk-boop/volume-surge-telegram-bot (tradesignal) | `tradesignal/chart_rules.py` | `tradesignal/test_chart_rules.py` |

차트 규칙을 하나라도 바꾸라는 요청을 받으면:
1. 이 저장소의 `chart_rules.py`(필요하면 `test_chart_rules.py`)를 고친다.
2. **다른 저장소도 세션에 붙여서(add_repo) 같은 파일을 그대로 복사한다.** 한쪽만 바꾸고 끝내지 않는다.
3. 두 파일의 `sha256sum` 이 같은지 확인한다.
4. 두 저장소 모두에서 `python3 test_chart_rules.py` 를 돌리고, tradesignal 쪽은 `test_signals.py` 도 돌린다.
5. 두 저장소 모두 커밋·푸시한다. 사용자에게 두 곳 다 반영했다고 알린다.

- 차트 숫자는 `chart_rules.py` 의 `RULES` 에서만 바꾼다. `config.json` 에 같은 키를 두면 이 봇만 달라지므로 두지 않는다.
- `trade_signal.py` 에 판정 로직을 다시 만들지 않는다. `chart_rules.detect()` 만 부른다.
- 봉 수는 `chart_rules.bars_needed()` 로 정한다 (120일선 정배열 때문에 120봉 이상 필요).
