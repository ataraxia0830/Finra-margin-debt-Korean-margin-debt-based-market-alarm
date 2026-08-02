# 미국·한국 주식·코인 자동 알람

이 프로젝트는 자동주문 없이 공개 시장자료와 한국투자증권 조회 API만 사용합니다.
FINRA, 금융투자협회 FreeSIS, 한국투자증권 KIS, Binance 자료를 SQLite에 누적하고,
상태가 바뀔 때만 Telegram으로 알립니다. 매월 1일에는 전체 수치를 다시 보냅니다.

## 1. 구현된 규칙

### 미국주식

- 최근 48개월 FINRA margin debt YoY `+50% 이상` 이력이 없으면 **평시/횡보장**으로
  판정합니다.
  - 현재 YoY `-10% 이하`: **약한 매수 알람**
  - 현재 YoY `-15% 이하`: **강한 매수 알람**
- 최근 48개월 안에 YoY `+50% 이상` 이력이 있으면 **선행 과열 후 디레버리징**
  국면으로 판정하며 기존 기준을 적용합니다.
  - 현재 YoY `-25% 이하`: **약한 매수 알람**
  - 현재 YoY `-30% 이하`: **강한 매수 알람**
- 두 국면은 서로 배타적입니다. +50% 이력이 있으면 평시/횡보장 `-10%/-15%`
  알람은 발생하지 않습니다.
- 위 두 YoY 기준은 핵심 독립조건입니다. 절대잔고와 3개월 하락 둔화는
  AND로 묶지 않고 보조정보로만 표시합니다.
- 절대잔고 고점 대비 `-20%/-25%/-30%`를 넘을 때 각각 추가 경고문을 표시합니다.
- 하락 둔화는 최근 두 월간 변화율, 둔화폭(%p), 3개월 누적변화와 범위를 함께 표시합니다.
- 종목별 200일선 매수 보조조건: Alphabet·Amazon `-25%`, 나머지 지정 반도체주
  `-30%`
- 종목별 120일선 매도 보조조건: Alphabet·Amazon `+25%/+30%`, 나머지 지정
  반도체주 `+35%/+40%`를 약한/강한 과열 기준으로 표시
- YoY 정점 대비 상대하락 `-10%`: **약한 매도 알람**
- 상대하락 `-15%`: **강한 매도 알람** 및 매도시점 계산 활성화
- 활성화 당시 YoY가 `+50% 이하`: 정점월 +3개월
- 활성화 당시 여전히 `+50% 초과`: 최초 `+50% 하회월` +3개월
- 강한 매도 확인 시 즉시 알리고, 목표월 예약 안내와 실제 목표월 강한 매도 알람을
  각각 상태가 전환될 때 1회
  보냅니다. 그 사이 3개월 동안 매달 반복하지 않습니다.

### 한국주식

- 전체 신용잔고 YoY `+60% 이상`: 선행 과열
- 현재 YoY `-25%/-30%`: 약한 매수/강한 매수
- 절대잔고 고점 대비 `-30%` 및 종목 가격·신용조건을 함께 확인
- 삼성전자: 200일선 `-25%`, 개별 신용잔고 `-30%` 또는 잔고율 하위 20%
- SK하이닉스: 200일선 `-30%`, 개별 신용잔고 `-35%` 또는 잔고율 하위 20%
- 매도: YoY 정점 대비 `-15%`, 전체 절대잔고 최초 감소, 최근 18개월 `-45%`
  붕괴 없음
- 종목별 매도 보조조건은 `120일선 과열 OR 개별 신용잔고율 최근 5년 상위 10%`입니다.
  120일선 과열 기준은 삼성 `+25%`(강한 과열 `+30%`), 하이닉스 `+35%`
  (강한 과열 `+40%`)입니다.

### BTC·SOL

- 약한 매수 알람: BTC는 고점 후 12~14개월이면서 고점 대비 `-60%`, SOL은 고점 후
  10~14개월이면서 고점 대비 `-80%`
- 강매수 알람: 기존 규칙대로 BTC `-70%`, SOL `-85%`와 파생조건 2개 이상 충족
- 약한 매수 알람에는 시간·기회 가격조건만 사용하고, 강매수 알람에는 시간·강매수
  가격조건·파생조건 2개 이상을 모두 사용합니다.
- OI, 펀딩비, OI/가격, SOL/BTC, 롱·숏 비율, 베이시스를 숫자로 표시
- Binance 공개 REST에는 과거 전체시장 강제청산 내역이 없으므로
  `7일 가격 -10% 및 OI -20%`를 대규모 롱 청산의 보수적 프록시로 명시해 사용
- OI 공개 과거 조회는 최근 구간만 제공되므로 매 4시간 수집한 SQLite 이력이 시간이
  갈수록 장기 기준선이 됨

#### BTC 매도

- 직전 확정 대형고점 42개월: 예비 관찰, 45개월: 정식 감시, 45~51개월: 핵심
  고점창, 51개월 초과: 지연 상태로 계속 감시
- 가격 필수조건: 현재 120일선 상방 이격률이 최근 3년 상위 10%
- 파생 과열: 사용자가 명시한 `OI·펀딩비·분기선물 베이시스` 3개 중 2개 이상
- 약한 매도: 시간·가격·파생 과열 조건까지 충족
- 강한 매도: 위 조건에 더해 OI `-10%`, 7일 펀딩비 `-30%`, 베이시스
  `-25%`, 가격 `-12% + OI/펀딩 감소` 중 하나 이상 확인
- OI·펀딩·베이시스 꺾임은 가격이 신고가 대비 10% 이내일 때만 인정

#### SOL 매도

- BTC와 SOL 자체의 직전 확정 대형고점에서 모두 42개월이 지난 뒤 감시하고,
  42~51개월을 핵심 고점창으로 사용하며 이후에도 계속 감시
- 가격 필수조건: 저점 대비 10배 도달 또는 120일선 이격률 최근 3년 상위 10%
- 파생 과열: OI 상위 10%, OI 180일 증가율 `+100%`, 7일·30일 펀딩비 상위
  10%, 선물 베이시스 상위 10% 중 2개 이상
- 약한 매도: 시간·가격·파생 과열 조건까지 충족
- 강한 매도: 위 조건에 더해 OI `-15%`, 7일 펀딩비 `-30%`, SOL/BTC
  `-10%`, SOL 가격 `-15%` 중 하나 이상 확인

BTC 분기선물은 Binance COIN-M의 실제 거래 중인 최근 분기물을 사용해 만기 잔여일로
연율 베이시스를 계산합니다. COIN-M REST가 막히면 무기한선물 프리미엄을 대체값으로
저장합니다. 최초 실행에는 공식 공개 데이터 아카이브에서 190일 이력을 백필하므로
OI·펀딩·베이시스 분위수의 최소 180일 조건을 바로 계산할 수 있습니다.

`sell_cycle_high_date`는 자동으로 신고가 날짜로 바꾸지 않습니다. 새 대형고점이
확정된 뒤에만 BTC와 SOL의 설정 날짜를 수동 변경해야 다음 사이클 경과개월이
올바르게 계산됩니다.

임계값은 모두 `config.yml`에서 바꿀 수 있습니다. `config.example.yml`을 복사해
수정하면 됩니다.

## 2. 사용자가 준비할 정보

실제 비밀값은 네 개뿐입니다. 채팅이나 코드에 직접 붙이지 말고 GitHub Secrets에
입력하십시오.

| 이름 | 어디서 얻나 | 용도 |
|---|---|---|
| `KIS_APP_KEY` | 한국투자증권 KIS Developers의 API 신청/앱 키 화면 | 국내·해외 시세 |
| `KIS_APP_SECRET` | 같은 화면의 App Secret | OAuth 토큰 자동 발급 |
| `TELEGRAM_BOT_TOKEN` | Telegram `@BotFather`의 `/newbot` | 알림 발송 |
| `TELEGRAM_CHAT_ID` | 봇에게 메시지 후 `getUpdates`의 `message.chat.id` | 받을 대화 지정 |

계좌번호, 계좌 비밀번호, 공동인증서, HTS 비밀번호, Binance API Key는 필요 없습니다.
코드는 주문 API를 호출하지 않습니다.

## 3. 한국투자증권 준비 절차

1. 한국투자증권 계좌를 개설합니다.
2. [KIS Developers](https://apiportal.koreainvestment.com/)에 로그인합니다.
3. `API 신청`에서 본인 계좌로 Open API 서비스를 신청합니다.
4. 발급 화면에서 App Key와 App Secret을 각각 복사해 안전한 암호관리자에 보관합니다.
5. GitHub 저장소 `Settings → Secrets and variables → Actions → Secrets`에서
   `KIS_APP_KEY`, `KIS_APP_SECRET`을 생성합니다.
6. 접근토큰은 코드가 `/oauth2/tokenP`로 자동 발급합니다. 별도로 전달하지 않습니다.
   `all` 실행에서도 토큰은 한 번만 발급하여 국내·해외 조회가 함께 사용합니다.

사용 API:

- 국내 기간별 시세: `inquire-daily-itemchartprice` / `FHKST03010100`
- 국내 종목 신용잔고: `daily-credit-balance` / `FHPST04760000`
- 해외 기간별 시세: `dailyprice` / `HHDFS76240000`

## 4. Telegram 준비 절차

1. Telegram에서 `@BotFather`를 열고 `/newbot`을 실행합니다.
2. 봇 이름과 `...bot`으로 끝나는 사용자명을 정하고 Token을 받습니다.
3. 새 봇과의 개인대화를 열어 `/start` 또는 아무 메시지를 보냅니다.
4. 브라우저에서 아래 주소를 엽니다. 실제 Token은 주소의 자리만 바꿉니다.

   `https://api.telegram.org/bot<TELEGRAM_BOT_TOKEN>/getUpdates`

5. 결과의 `result → message → chat → id` 값을 확인합니다. 개인대화는 보통 숫자,
   그룹은 음수일 수 있습니다.
6. GitHub Secrets에 `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`를 생성합니다.

## 5. FreeSIS 최초 준비

FreeSIS 로그인이나 별도 API 키는 필요 없습니다. 프로그램은 FreeSIS 화면 자체가 쓰는
금융투자협회 공식 JSON 요청으로 2010년 이후 월별 `신용거래융자-전체`를 먼저 받습니다.
이 경로가 성공하면 Chromium과 Excel 버튼 탐색은 실행하지 않습니다. 따라서 최초
실행부터 13개월 이상의 자료가 들어와 YoY를 계산할 수 있습니다.

공식 JSON 경로가 바뀌는 예외 상황에는 다음 보조 경로를 사용합니다.

1. 저장소의 `bootstrap/freesis_credit.csv` 또는 `.xlsx`
2. `FREESIS_DOWNLOAD_URL`로 지정한 내부 다운로드 요청
3. HTML 표·Excel/CSV 링크
4. 사용자가 명시적으로 `FREESIS_USE_PLAYWRIGHT=true`로 설정한 브라우저 수집

내부 요청을 고정하고 싶다면 Chrome 개발자도구 `Network`에서 Excel 버튼을 누른 뒤
생긴 요청의 URL, Method, Form Data를 확인합니다.

- URL → GitHub Secret `FREESIS_DOWNLOAD_URL`
- Method → GitHub Variable `FREESIS_DOWNLOAD_METHOD` (`GET` 또는 `POST`)
- Form Data를 JSON 객체로 변환 → Secret `FREESIS_DOWNLOAD_PAYLOAD_JSON`

FreeSIS 하나가 실패해도 `all` 작업은 미국·코인 수집을 계속합니다. 기존 한국 자료가
있으면 마지막 정상자료로 판정하고, 최초 실행이라 한국 자료가 없으면 한국 전체 신호만
`판정 보류`로 표시합니다. 공공데이터포털의
[금융투자협회 종합통계정보 API](https://www.data.go.kr/data/15094809/openapi.do)는
무료이고 신용공여 조회를 제공하므로, FreeSIS가 장기적으로 차단될 경우의 공식 대체
경로입니다. 이 버전은 사용자가 요청한 FreeSIS 우선순위를 유지하므로 해당 API 키를
요구하지 않습니다.

## 6. 비공개 GitHub 저장소 설치

1. GitHub에서 `New repository`를 누르고 반드시 `Private`으로 만듭니다.
2. v1.8 ZIP은 압축을 풀면 `pyproject.toml`, `src`, `.github`가 바로 나오는
   **저장소 최상단용 구조**입니다. ZIP 전체를 다시 `market-alarm` 하위 폴더에 넣지 말고,
   압축을 푼 내용물 자체를 저장소 최상단에 올립니다.
3. GitHub 저장소 첫 화면에서 `pyproject.toml`, `src`, `.github`가 서로 같은 깊이에
   보이는지 확인합니다. `market-alarm/pyproject.toml`처럼 한 단계 안쪽에 보이면 잘못
   올린 것입니다.
4. 위 네 개 Secret을 등록합니다.
5. 내부 다운로드 주소를 직접 지정한 경우에만 Variable
   `FREESIS_DOWNLOAD_METHOD`를 추가합니다(기본 `GET`). 공식 JSON 수집에는 Chromium이
   필요하지 않습니다.
6. 저장소 `Actions → Market Alarm → Run workflow`에서 `all`을 수동 실행합니다.
   v1.8의 수동 실행은 `force_alert=true`가 기본값입니다.
7. 시작 직후 Telegram에서 `[시장 알람 시스템 점검] Telegram 연결: 정상`을 먼저
   확인합니다. 이 메시지도 오지 않으면 시장 데이터 문제가 아니라 Telegram Secret
   설정 문제이며, Actions의 `Check configuration and Secrets` 단계에 누락 이름이
   표시됩니다.
8. 이어서 시장별 시험 알림과 `data/market_alarm.sqlite3` 커밋을 확인합니다.

v1.8은 `actions/checkout@v5`, `actions/setup-python@v6`을 사용하므로 Node.js 20 폐기
경고가 나오지 않습니다. Actions 실행 화면 맨 아래의 `Summary`에도 실행 버전, 작업,
강제 알림 여부와 최종 JSON 결과를 남깁니다.

`force_alert=true`의 시험 출력은 실제 Telegram 알람과 같은 형식입니다. 다만 실제
예약 실행은 상태가 바뀔 때만 발송하므로 매번 전체 메시지가 반복되지는 않습니다.
알림의 `[신호]` 명칭은 시장과 자산에 관계없이 `평시 매수`, `약한 매수`, `강한 매수`,
`약한 매도`, `강한 매도`로 통일되며 `[상태]`, `[신뢰단계]`, 임계값까지의 거리값은
출력하지 않습니다.

초기 버전의 `config.yml`에 FINRA 과열 기준 `60`이 남아 있으면 최종 규칙인 `50`으로
자동 이전합니다. 최근 48개월은 비결측 관측치 개수가 아니라 달력상 월 경계로 계산하며,
판정 메시지에 해당 기간의 최고 YoY와 발생월을 함께 표시합니다.

### KIS 토큰 403

v1.2의 `all` 작업은 해외주식과 국내주식 수집기가 각각 접근토큰을 발급해 짧은 시간에
`/oauth2/tokenP`를 두 번 호출할 수 있었습니다. 첫 발급 안내가 온 뒤 두 번째 호출에서
403이 발생하는 원인이었습니다. 이 버전은 같은 실행의 토큰을 공유합니다. 기존
`config.yml`의 `-15/-20` 미국 매도 설정도 `-10/-15`로 자동 이전합니다.

### Binance 451

Binance OI·펀딩비·롱숏비율은 인증이 필요 없는 공개 시장자료입니다. GitHub-hosted runner의
IP에서 `fapi.binance.com`이 HTTP 451을 반환하면, 같은 지역 제한을 받을 수 있는 웹
게이트웨이를 재호출하지 않고 Binance 공식 `data.binance.vision` ZIP 아카이브로
전환합니다.

- OI·롱숏비율: 공식 일별 `metrics` ZIP
- 펀딩비: 공식 월별 `fundingRate` ZIP. 당월 확정 파일이 아직 없으면 공식 Premium
  Index 아카이브와 Binance 8시간 공식 산식으로 누락 구간만 추정하고, 다음 달 확정값이
  공개되면 자동 대체
- 마크가격·지수가격·프리미엄: 월별 ZIP과 당월 일별 ZIP
- 최초 실행: 190일 백필, 이후: 최근 누락분만 갱신
- 아카이브 기준시각: 통상 전일 UTC 마감(T-1)

따라서 4시간마다 실행하더라도 REST가 451인 동안에는 가장 최근의 공식 아카이브 값으로
판정합니다. 이 차단은 API 키가 아니라 접속 IP 기준이므로 Binance 계정이나 API 키를
추가해도 해결되지 않습니다.

한국주식 시험·실제 알림에는 종목별로 `[매수 보조조건]`과 `[매도 보조조건]`을 모두
표시합니다. 삼성전자는 200일선 `-25%`, 120일선 `+25%/+30%`, SK하이닉스는 200일선
`-30%`, 120일선 `+35%/+40%`의 달성 여부를 각각 표시합니다.

미국주식도 각 종목별로 현재 `120/200 이격률`, `[매수 보조조건]`,
`[매도 보조조건]`을 표시합니다. Alphabet·Amazon은 200일선 `-25%`와 120일선
`+25%/+30%`, 나머지 지정 반도체주는 200일선 `-30%`와 120일선 `+35%/+40%`의
달성 여부를 각각 표시합니다.

저장소 설정에서 Actions의 쓰기 권한이 막혀 있으면
`Settings → Actions → General → Workflow permissions → Read and write permissions`를
선택합니다. 브랜치 보호가 봇의 커밋을 막는 경우에는 `data/` 전용 브랜치를 쓰도록
워크플로를 조정해야 합니다.

## 7. 로컬 시험

Python 3.11 이상:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[test]"
cp .env.example .env
cp config.example.yml config.yml
pytest
market-alarm doctor
DRY_RUN=true market-alarm run finra --force-alert
```

Windows PowerShell에서는 활성화 명령만 다음과 같습니다.

```powershell
.\.venv\Scripts\Activate.ps1
```

FreeSIS Playwright까지 시험할 때:

```bash
pip install -e ".[browser]"
python -m playwright install chromium
FREESIS_USE_PLAYWRIGHT=true DRY_RUN=true market-alarm run korea --force-alert
```

## 8. 실행 시각

| 작업 | KST | GitHub UTC cron |
|---|---:|---:|
| 한국주식 | 평일 18:30 | `30 9 * * 1-5` |
| 미국주식 | 매일 08:00 | `0 23 * * *` |
| BTC·SOL | 00:05부터 4시간 간격 | `5 3,7,11,15,19,23 * * *` |
| FINRA | 매월 15~25일 15:00 | `0 6 15-25 * *` |
| 월말 확정 | 28~31일 23:50 중 실제 월말만 | `50 14 28-31 * *` |
| 전체 보고 | 매월 1일 09:00 | `0 0 1 * *` |

GitHub 예약 실행은 몇 분 지연될 수 있습니다. 시장 판정은 실시간 주문이 아니므로 결과에
영향을 주지 않습니다.

## 9. 데이터와 보안

- Secret은 SQLite에 저장하지 않습니다.
- KIS 접근토큰도 저장소에 커밋하지 않습니다.
- SQLite에는 공개 시장자료, 계산상태, 실행결과만 들어갑니다.
- 상태가 같으면 반복 알림을 보내지 않습니다.
- 수집 오류는 데이터 0으로 대체하지 않고 작업을 실패시키며 Telegram 오류 알림을
  보냅니다.
- `data/market_alarm.sqlite3`를 지우면 이력과 중복방지 상태도 초기화됩니다.
- 이것은 투자판단 보조도구이며 자동주문·수익보장 시스템이 아닙니다.

## 10. 공식 자료

- [FINRA Margin Statistics](https://www.finra.org/rules-guidance/key-topics/margin-accounts/margin-statistics)
- [한국투자증권 KIS Developers](https://apiportal.koreainvestment.com/)
- [금융투자협회 FreeSIS](https://freesis.kofia.or.kr/)
- [Binance Spot REST](https://developers.binance.com/en/docs/products/spot/rest-api)
- [Binance USDⓈ-M Market Data](https://developers.binance.com/en/docs/catalog/core-trading-derivatives-trading-usd-s-m-futures/api/rest-api/market-data)
- [Telegram Bot API](https://core.telegram.org/bots/api)
