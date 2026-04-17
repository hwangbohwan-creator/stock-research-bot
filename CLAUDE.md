# 주식리서치 봇 — 프로젝트 가이드

## 프로젝트 개요
미국주식 리서치 자동화 봇. 유튜브·웹·SEC공시 등 다양한 소스에서 콘텐츠를 수집하고,
Gemini AI로 분석 후 Google 스프레드시트에 저장한다.
텔레그램 봇 인터페이스로 링크를 보내면 자동 분석·저장된다.

---

## 파일 구조

```
주식리서치/
├── research.py          # 메인 봇 코드 (수집·분석·저장·텔레그램 핸들러 전부)
├── bot.sh               # 봇 시작/정지/재시작/상태/로그 관리 스크립트
├── .env                 # API 키 저장 (git 제외)
├── credentials.json     # Google 서비스 계정 키 (git 제외)
├── bot.pid              # 실행 중인 봇 PID (자동 생성)
├── bot.log              # 봇 로그 (자동 생성)
├── seen_urls.json       # 중복 수집 방지용 URL 기록 (자동 생성)
├── chat_id.txt          # 브리핑 대상 텔레그램 chat_id (자동 생성)
└── .gitignore           # credentials.json, .env, bot.pid 등 제외
```

---

## API 키 위치

| 키 | 위치 | 비고 |
|---|---|---|
| Gemini API Key | `.env` → `GEMINI_API_KEY` | Google AI Studio에서 발급. 유출 시 즉시 재발급 필요 |
| Telegram Bot Token | `.env` → `TELEGRAM_TOKEN` | BotFather에서 발급 |
| Google Sheets | `credentials.json` | Google Cloud 서비스 계정 키 (JSON) |

> `.env` 파일은 git에 커밋되지 않는다. 새 환경 세팅 시 직접 작성해야 한다.

---

## 주요 설정 (research.py 상단)

```python
SHEET_NAME   = "미국주식 리서치"   # Google 스프레드시트 이름
TAB_YOUTUBE  = "유튜브"            # 유튜브 분석 탭
TAB_TEXT     = "텍스트"            # 웹페이지 분석 탭
TAB_FILING   = "기업공시"          # SEC EDGAR 공시 탭

WATCHLIST    = ["CRCL", "RKLB", "IREN", "TSLA", "INTC", "BTC-USD", "ETH-USD"]
AUTO_TICKERS = ["TSLA", "IREN", "RKLB", "CRCL"]   # 자동 수집 대상
EDGAR_FORMS  = ["8-K", "10-Q", "10-K"]
```

---

## 주요 명령어

### 봇 관리 (bot.sh)
```bash
bash bot.sh start    # 봇 백그라운드 시작
bash bot.sh stop     # 봇 종료
bash bot.sh restart  # 재시작
bash bot.sh status   # 실행 상태 + 최근 로그 20줄
bash bot.sh logs     # 실시간 로그 스트리밍 (Ctrl+C로 종료)
```

### 수동 실행
```bash
# 봇 모드 (텔레그램 대기)
python3 research.py --bot

# 특정 기능 테스트 (함수 직접 호출 방식으로 확인)
python3 research.py
```

---

## 주요 기능 (research.py 함수 구조)

| 기능 | 설명 |
|---|---|
| `get_web_content(url)` | 웹페이지 본문 추출 (네이버 블로그 모바일 우회 포함) |
| `get_youtube_transcript(url)` | 유튜브 자막 추출 |
| `fetch_edgar_filings()` | SEC EDGAR에서 8-K/10-Q/10-K 자동 수집 |
| `analyze_with_gemini(text, mode)` | Gemini AI 분석 (유튜브/텍스트/공시 모드) |
| `connect_tab(tab_name)` | Google 스프레드시트 탭 연결 (없으면 자동 생성) |
| `ensure_all_tabs()` | 3개 탭 일괄 초기화 확인 |

---

## 문제 해결

### Gemini API 403 PERMISSION_DENIED (키 유출)
1. Google AI Studio에서 기존 키 삭제
2. 새 키 발급
3. `.env`의 `GEMINI_API_KEY` 값 교체
4. `bash bot.sh restart`

### 봇이 응답 없음
```bash
bash bot.sh status   # 실행 여부 확인
bash bot.sh logs     # 에러 로그 확인
```

### Google 스프레드시트 권한 오류
- `credentials.json` 서비스 계정 이메일에 스프레드시트 편집 권한 부여 필요

---

## 의존성
```
gspread, google-auth, google-genai
python-telegram-bot
beautifulsoup4, requests, feedparser
youtube-transcript-api
yfinance
python-dotenv, pytz
```

---

## 파일 구조 (추가)

```
주식리서치/
├── setup.sh             # ngrok + code-server 초기 설치 (최초 1회)
├── ngrok.sh             # code-server + ngrok 시작/종료/상태 관리
├── ngrok.pid            # ngrok PID (자동 생성)
├── ngrok.log            # ngrok 로그 (자동 생성)
├── code-server.pid      # code-server PID (자동 생성)
└── code-server.log      # code-server 로그 (자동 생성)
```

## VS Code 원격 접속 (code-server + ngrok)

- **방식**: code-server(웹 VS Code) + ngrok HTTP 터널
- **접속**: 브라우저에서 ngrok URL 클릭 → VS Code 바로 열림 (모바일 포함)
- **포트**: 9000 (code-server 내부 포트)
- **비밀번호**: `~/.config/code-server/config.yaml`에 저장

### 명령어
```bash
# WSL Ubuntu 안에서 실행
bash ~/주식리서치/setup.sh          # 최초 1회 설치
bash ~/주식리서치/ngrok.sh start    # 시작 + 텔레그램 링크 전송
bash ~/주식리서치/ngrok.sh stop     # 종료
bash ~/주식리서치/ngrok.sh status   # 현재 URL 확인
bash ~/주식리서치/ngrok.sh resend   # 텔레그램 재전송
```

### 주의사항
- 무료 플랜: 재시작마다 URL 변경됨
- `chat_id.txt`가 있어야 텔레그램 전송 가능 (봇에 `/start` 먼저 전송)
- WSL 환경에서 실행해야 함 (`wsl.exe -d Ubuntu -- bash ~/주식리서치/ngrok.sh start`)

---

## 행동 지침

- 실행 여부 묻지 말고 바로 실행
- 코드 수정 후 자동으로 테스트까지
- 패키지 설치 자동으로
- 파일 생성/수정 자동으로
- 단, 파일 삭제나 GitHub push는 반드시 먼저 물어볼 것
- 오류 나면 스스로 3번까지 시도 후 보고
