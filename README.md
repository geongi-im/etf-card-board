# ETF 카드 보드 생성기

섹터/테마 ETF 28종의 당일 등락을 보드 한 장으로 만듭니다. 게시판 본문과 텔레그램
이미지가 같은 레이아웃입니다.

- 머리글: 왼쪽 위에 계정 이름(`render.BOARD_HANDLE`, `@mq_way.finance`)과 그 아래 제목, 오른쪽에
  기준일, 상승/하락/보합 개수
- 상승 상위 4종 / 하락 상위 4종: 카드로 크게 싣습니다. 당일 등락률, 6개월 종가
  미니차트(점선 = 6개월 전 종가, 선 색 = 6개월 방향), 비중 상위 4종목과 각 등락률
- 나머지 ETF: 등락률 순 표. 작은 미니차트, 당일 등락률, 6개월 수익률

이미지(폭 1080px)는 나머지 표를 2열로, 게시판(본문 폭 800px)은 1열로 둡니다.
게시판에서는 상위 카드의 구성종목 이름에 뉴스 링크가 걸립니다.

데이터는 모두 KRX 정보데이터시스템(pykrx)에서 가져옵니다.

결과물은 두 갈래로 나갑니다 (`--send`).

- **HTML 조각** (`render_fragment()`) -> MQWAY 게시판(`board-research`, 카테고리 `섹터분석`)
- **PNG** (`render()` 문서를 찍음) -> 텔레그램 운영 방 (게시물 링크를 캡션에 붙임)

---

## 설치

```bash
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
```

macOS/Linux는 `.venv\Scripts\python`을 `.venv/bin/python`으로 바꿔 읽으세요.

### KRX 계정 설정 (필수)

pykrx 1.2.x는 KRX 정보데이터시스템 로그인을 요구합니다. ETF 시세와 구성종목(PDF)
모두 로그인 없이는 빈 응답이나 `KeyError`로 실패합니다.

```bash
copy .env.example .env
```

```
KRX_LOGIN_ID=아이디
KRX_LOGIN_PASSWORD=비밀번호
```

pykrx는 `KRX_ID` / `KRX_PW`라는 이름만 읽으므로 `main.py`가 위 값을 그 이름으로
옮겨 준 뒤 pykrx를 호출합니다.

### wkhtmltoimage 설치 (필수)

PNG는 [wkhtmltopdf](https://wkhtmltopdf.org/downloads.html)에 함께 들어 있는
`wkhtmltoimage`로 찍습니다. 설치한 뒤 실행 파일 경로를 `.env`에 적습니다.
형제 프로젝트(krx-topsector-report 등)와 같은 이름입니다.

```
WKHTMLTOIMAGE_PATH=C:\Program Files\wkhtmltopdf\bin\wkhtmltoimage.exe
```

### 전송 설정 (`--send` 시 필수)

```
BASE_URL=https://사이트주소          # 끝의 /api 는 코드가 붙인다
TELEGRAM_BOT_TOKEN=봇토큰
TELEGRAM_CHAT_ID=운영방              # 보드 PNG 가 가는 방
TELEGRAM_CHAT_TEST_ID=실패알림방      # 비우면 운영 방으로 간다
```

형제 프로젝트 krx-daily-brief / liquidity-feed와 같은 이름이라 값을 그대로
옮겨도 됩니다.

### 설치 확인

```bash
.venv\Scripts\python main.py --check
```

`.env` 로드, KRX 로그인, ETF 시세/구성종목 조회, wkhtmltoimage 경로를 순서대로
점검합니다. MQWAY 주소와 텔레그램 설정은 `--send`에만 필요하므로 없으면 경고만
냅니다. 로그인에 실패하면 KRX가 돌려준 오류 코드(CD006, CD007 등)와 조치 방법을
보여줍니다.

---

## 사용법

```bash
.venv\Scripts\python main.py                    # 오늘 기준, HTML/PNG 만 만들고 전송 안 함
.venv\Scripts\python main.py --send             # 오늘 기준, MQWAY 게시 + 텔레그램 전송까지
.venv\Scripts\python main.py --date 2026-10-28  # 기준일 지정
.venv\Scripts\python main.py --demo             # 샘플 데이터로 렌더링만 확인 (KRX 접속 불필요)
```

| 옵션 | 설명 |
|---|---|
| `--date YYYY-MM-DD` | 기준 거래일. 생략하면 오늘. `output\etf_card_board_YYYYMMDD.html` / `.png` 생성 (둘 다 텔레그램 이미지 레이아웃) |
| `--send` | HTML 은 MQWAY 게시판에, PNG 는 텔레그램에 전송. **기본은 전송하지 않음** |
| `--out 경로` | HTML 출력 경로 직접 지정. PNG 는 같은 이름에 확장자만 `.png` |
| `--demo` | 내장 샘플로 렌더링만 확인. `output\_demo.html` / `.png`에 저장. `--send`와 함께 못 씀 |
| `--check` | 환경 자가진단 |

실행 경과는 콘솔과 `logs\YYYY-MM-DD_log.log`에 함께 기록됩니다.

수집에 2분 남짓 걸립니다 (2026-10-28 실측 2분 15초). 대부분 ETF별 6개월 시세
조회 시간입니다.

---

## 전송 (`--send`)

```
수집 -> HTML 저장 -> PNG 생성 -> MQWAY 게시 -> 텔레그램 사진 전송
```

| 대상 | 보내는 것 | 비고 |
|---|---|---|
| MQWAY `POST {BASE_URL}/api/board-research` | `render_fragment()` 조각 | 카테고리 `섹터분석`, 작성자 `admin` 고정 (`main.py`) |
| 텔레그램 운영 방 `sendPhoto` | 보드 PNG | 캡션: 제목, 상승/하락/보합 개수, 상승/하락 상위 3종(이름에 네이버 증권 링크, `render.NAVER_ITEM_URL`), 게시물 링크 |
| 텔레그램 실패 방 | 실패 사유 | MQWAY 게시 실패, PNG 생성 실패 |

동작상 알아둘 점:

- **HTML/PNG 파일은 전송 전에 먼저 저장됩니다.** 전송이 실패해도 파일은
  `output\`에 남습니다.
- **게시와 사진 전송은 서로 독립입니다.** MQWAY 게시가 실패해도 PNG 는 링크 없이
  운영 방으로 가고, 실패 사유는 실패 방으로 따로 갑니다.
- **전송이 실패해도 종료 코드는 0**입니다. 보드 생성 자체는 성공했기 때문입니다.
- 게시판 목록 썸네일로 보드 PNG 를 함께 올립니다 (800px 로 줄여서). PNG 가
  없으면 썸네일 없이 올립니다.

### 게시판 본문 제약

본문은 완결 문서가 아니라 **조각**입니다. 게시판 페이지에 그대로 삽입되므로
`<!DOCTYPE>`/`<head>`를 빼고, CSS 는 모든 선택자를 `.eci` 아래로 한정했습니다.
`body` 같은 페이지 전체 선택자는 CSS 에 넣지 않습니다.

- **64KB 상한** (`mq_content` 가 MySQL `text`). 2026-10-30 실데이터 기준
  33,575B 로 상한의 약 51% 입니다. 넘으면 `ApiUtil.check_payload()`가 보내기 전에
  막습니다. 용량을 아끼려고 미니차트는 상대 좌표 path 로 적고, CSS 설명은 CSS 주석이
  아니라 파이썬 주석으로 둡니다.
- 상위 카드의 구성종목 이름에는 네이버 증권 종목 뉴스 링크를 겁니다
  (`render.NAVER_NEWS_URL`). 국내 시세표에 있는 종목에만 걸고, PNG 에는 걸지 않습니다.
- 게시판 목록은 본문을 `strip_tags()` 해서 미리보기를 만듭니다. 본문이 제목, 날짜,
  상승/하락 개수, 상위 ETF 순으로 시작하므로 미리보기도 그 순서로 나옵니다.
  `<style>`이 앞에 있으면 CSS 텍스트가 미리보기에 나오므로 `<style>`은 맨 뒤에 둡니다.
- 게시판 본문 폭은 800px 라 나머지 표는 **1열**입니다. 좁은 화면(600px 이하)에서는
  상승 상위 / 하락 상위 두 열을 위아래로 쌓습니다.
- 게시판은 Tailwind 를 CDN 으로 쓰므로, 페이지에 나온 클래스 이름을 보고 유틸리티
  CSS 를 만들어 붙입니다. 보드 마크업에 `grid`, `flex`, `table`, `hidden` 처럼
  Tailwind 유틸리티와 같은 클래스 이름을 쓰면 레이아웃이 깨집니다.

### PNG 렌더링 제약

wkhtmltoimage 는 오래된 QtWebKit 이라 **CSS grid, flex gap, CSS 변수(`var()`)를
지원하지 않습니다.** 그래서 레이아웃은 `inline-block`과 `table`로만 짭니다. 색은
`render.PALETTE` 한 곳에 모아 두고, CSS 에는 `$up` 처럼 적어 파이썬이 값으로 바꿔
넣습니다 (`render.css()`). 게시판과 이미지가 같은 `render.CSS` 를 씁니다. CSS 를
고칠 때 이 제약을 지켜야 PNG 가 깨지지 않습니다.

카드로 크게 싣는 상위 ETF 수와 카드의 구성종목 수는 `render.FOCUS_N` /
`render.FOCUS_HOLDINGS` 입니다. 그날 상승(또는 하락)한 ETF 가 없으면 그 열에
"상승한 ETF 없음" 칸이 나옵니다.

wkhtmltoimage 는 PNG 를 거의 압축하지 않습니다 (예전 4열 보드 1400x1571 실측 8.8MB).
텔레그램 사진 상한이 10MB 라, 찍은 뒤 Pillow 로 무손실 재압축합니다.

---

## 데이터 기준

| 항목 | 출처 (pykrx) | 기준 |
|---|---|---|
| ETF 당일 등락률 | `get_etf_ohlcv_by_date` | 기준일 종가 / 전 거래일 종가 |
| 미니차트, 6개월 수익률 | `get_etf_ohlcv_by_date` | 기준일 6개월 전 날짜(휴장이면 그 직전 거래일) 종가부터 |
| 비중 상위 종목 | `get_etf_portfolio_deposit_file` | **기준일의 전 거래일 PDF** |
| 구성종목 당일 등락률 | `get_market_ohlcv_by_ticker(date, "ALL")` | 전 종목 시세 1회 조회 후 코드로 찾음 |

- 구성종목은 전 거래일 PDF를 씁니다. 장중에도 확정된 값이 있고, 당일 등락에 따라
  비중 순위가 흔들리지 않기 때문입니다.
- PDF의 현금/예금/선물 행(예: `010010 원화현금`)은 상위 종목에서 뺍니다
  (`collect.py`의 `NON_STOCK_WORDS`).
- 국내 시세표에 없는 종목(해외 주식 등)은 등락률을 `-`로 비워 둡니다.
- 시세는 KRX 정규장 기준입니다. 네이버 증권은 넥스트레이드(NXT) 체결까지 반영한
  통합 시세를 보여주므로 종가와 등락률이 다를 수 있습니다.

### 장중 기준 (미확인)

KRX 정보데이터시스템이 장중에 당일 시세를 얼마나 늦게 갱신하는지는 아직 확인하지
않았습니다. 장중 특정 시각(예: 10:05) 기준 보드가 필요하면 먼저 장중에 실행해
당일 값이 채워지는지 확인해야 합니다.

---

## ETF 목록 바꾸기

`collect.py`의 `ETF_TICKERS`에 `"표시명": "종목코드"`로 적습니다. 카드 순서는
적은 순서를 따르고, 표시명은 카드 제목에 그대로 쓰입니다. 종목코드에는 `0090B0`
처럼 영문이 섞인 신규 코드도 있으니 KRX 목록에서 확인해 그대로 적으세요.

---

## 폴더 구성

| 파일 | 용도 |
|---|---|
| `main.py` | 진입점. CLI, `.env` 로드, KRX 로그인 진단, 자가진단(`--check`), PNG 생성 호출, 전송(`--send`) |
| `collect.py` | 수집. 설정 상수(`ETF_TICKERS` 등), `Bundle`/`EtfCard`/`Holding`, KRX 수집, 데모 데이터 |
| `render.py` | 렌더링. 색(`PALETTE`), 등락률 포맷, SVG 미니차트, 게시판 조각/텔레그램 이미지 문서/캡션, PNG(wkhtmltoimage), CSS |
| `utils/api_util.py` | MQWAY 게시판 API (liquidity-feed 에서 가져옴) |
| `utils/telegram_util.py` | 텔레그램 메시지/사진 전송 (liquidity-feed 에서 가져와 `send_photo` 추가) |
| `utils/logger_util.py` | 로거. `logs/YYYY-MM-DD_log.log` |
| `requirements.txt` | 의존성 (pykrx, pandas, requests, python-dotenv, httpx, imgkit, Pillow) |
| `.env` / `.env.example` | KRX 계정, wkhtmltoimage 경로, MQWAY 주소, 텔레그램 (`.env`는 공유 금지) |
| `output/*.html`, `output/*.png` | 생성된 보드 |
| `logs/*.log` | 실행 로그 (커밋 제외) |

의존 방향은 `main.py -> render.py -> collect.py` 한 방향입니다.

---

## 주의사항

- 정보 제공 목적이며, 투자 권유가 아닙니다.
