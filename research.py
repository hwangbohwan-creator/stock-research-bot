import gspread
from google.oauth2.service_account import Credentials
from google import genai
from google.genai import types
import requests
from bs4 import BeautifulSoup
from youtube_transcript_api import YouTubeTranscriptApi
from telegram import Update, Bot
from telegram.ext import ApplicationBuilder, MessageHandler, filters, ContextTypes
import feedparser
import pytz
import yfinance as yf
from datetime import datetime, timedelta, time as dtime
import tempfile
import os
import re
import sys
import json

# ---- 설정 ----
SHEET_NAME   = "미국주식 리서치"
CREDS_FILE   = "credentials.json"
GEMINI_API_KEY  = "AIzaSyAhTicJqr1UWlqPAGbKyLg_Xj0KUpdQJnA"
TELEGRAM_TOKEN  = "8513472599:AAFT6cGolTaEfqlY5_y5FrF_Dn_-DWQamcM"

TAB_YOUTUBE = "유튜브"
TAB_TEXT    = "텍스트"
TAB_FILING  = "기업공시"

# 브리핑 설정
BRIEFING_CHAT_ID = None
CHAT_ID_FILE  = os.path.join(os.path.dirname(os.path.abspath(__file__)), "chat_id.txt")
SEEN_FILE     = os.path.join(os.path.dirname(os.path.abspath(__file__)), "seen_urls.json")
WATCHLIST     = ["CRCL", "RKLB", "IREN", "TSLA", "INTC", "BTC-USD", "ETH-USD"]
AUTO_TICKERS  = ["TSLA", "IREN", "RKLB", "CRCL"]   # 자동 수집 관심종목
EDGAR_FORMS   = ["8-K", "10-Q", "10-K"]
EDGAR_UA      = "stock-research-bot contact@research.com"
KST           = pytz.timezone("Asia/Seoul")

HEADERS_DEFAULT = ["날짜", "회사명", "티커", "소스타입", "발표자", "링크", "핵심요약", "투자포인트", "리스크"]
HEADERS_FILING  = ["날짜", "기업명", "티커", "공시유형", "핵심요약", "주요내용", "투자영향", "링크/파일명"]

client = genai.Client(api_key=GEMINI_API_KEY)

# ------------------------------------------------------------------ #
#  구글 시트                                                           #
# ------------------------------------------------------------------ #
def _open_spreadsheet():
    scopes = ["https://spreadsheets.google.com/feeds",
               "https://www.googleapis.com/auth/drive"]
    creds = Credentials.from_service_account_file(CREDS_FILE, scopes=scopes)
    return gspread.authorize(creds).open(SHEET_NAME)

def connect_tab(tab_name: str):
    spreadsheet = _open_spreadsheet()
    try:
        return spreadsheet.worksheet(tab_name)
    except gspread.exceptions.WorksheetNotFound:
        headers = HEADERS_FILING if tab_name == TAB_FILING else HEADERS_DEFAULT
        ws = spreadsheet.add_worksheet(title=tab_name, rows=1000, cols=20)
        ws.append_row(headers)
        print(f"📋 탭 생성: {tab_name}")
        return ws

def ensure_all_tabs():
    spreadsheet = _open_spreadsheet()
    existing = [ws.title for ws in spreadsheet.worksheets()]
    for tab_name in [TAB_YOUTUBE, TAB_TEXT, TAB_FILING]:
        if tab_name not in existing:
            headers = HEADERS_FILING if tab_name == TAB_FILING else HEADERS_DEFAULT
            ws = spreadsheet.add_worksheet(title=tab_name, rows=1000, cols=20)
            ws.append_row(headers)
            print(f"📋 탭 생성: {tab_name}")
        else:
            print(f"✅ 탭 확인: {tab_name}")

# ------------------------------------------------------------------ #
#  콘텐츠 수집                                                         #
# ------------------------------------------------------------------ #
BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
}

MOBILE_HEADERS = {
    **BROWSER_HEADERS,
    "User-Agent": (
        "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
        "AppleWebKit/605.1.15 (KHTML, like Gecko) "
        "Version/17.0 Mobile/15E148 Safari/604.1"
    ),
}

def get_web_content(url: str) -> str | None:
    try:
        naver_match = re.search(r"blog\.naver\.com/([^/?#]+)/(\d+)", url)
        if naver_match:
            bid, lno = naver_match.group(1), naver_match.group(2)
            url = f"https://m.blog.naver.com/{bid}/{lno}"
            hdrs = MOBILE_HEADERS
        else:
            hdrs = BROWSER_HEADERS

        res = requests.get(url, headers=hdrs, timeout=15)
        if res.status_code != 200:
            print(f"⚠️ HTTP {res.status_code}: {url}")
            return None

        soup = BeautifulSoup(res.text, "html.parser")
        for tag in soup(["script", "style", "nav", "footer", "header", "aside"]):
            tag.decompose()
        text = soup.get_text(separator=" ", strip=True)

        # 본문이 너무 짧으면 차단된 것으로 간주
        if len(text) < 200:
            print(f"⚠️ 본문 너무 짧음({len(text)}자) — 차단 의심: {url}")
            return None

        return text[:10000]
    except Exception as e:
        print(f"⚠️ 웹 요청 실패: {e}")
        return None

def get_youtube_transcript(url: str) -> str | None:
    vid_match = re.search(r"(?:v=|youtu\.be/)([^&\n?#]+)", url)
    if not vid_match:
        return None
    vid = vid_match.group(1)
    try:
        api = YouTubeTranscriptApi()
        transcript = api.fetch(vid, languages=["ko", "en"])
        return " ".join(t.text for t in transcript)[:12000]
    except Exception as e:
        print(f"⚠️ 자막 추출 실패: {e}")
        return None

# ------------------------------------------------------------------ #
#  통합 Gemini 프롬프트 — 내용 분류 + 필드 추출 한 번에               #
# ------------------------------------------------------------------ #
UNIFIED_PROMPT = """
다음은 미국 주식·금융 관련 콘텐츠입니다.
내용을 읽고 아래 JSON을 반드시 JSON 형식으로만 답하세요 (JSON 외 텍스트 없이).

분류 기준:
- "기업공시": SEC 공시(10-K/10-Q/8-K), 실적발표, IR자료, 합병·자사주·배당 공시
- "유튜브": 유튜브 영상 스크립트·자막
- "텍스트": 뉴스, 블로그, SNS 글, 일반 기사

공시유형은 다음 중 하나: 10-K, 10-Q, 8-K, 합병, 자사주매입, 실적발표, 배당, 기타
투자영향은 "긍정 - 이유", "부정 - 이유", "중립 - 이유" 형식

{
  "content_type": "기업공시 | 유튜브 | 텍스트",
  "ticker": "종목 티커 (없으면 UNKNOWN)",
  "company": "회사명",
  "presenter": "발표자·채널명 (유튜브·텍스트용, 없으면 UNKNOWN)",
  "filing_type": "공시유형 (기업공시용, 해당 없으면 빈 문자열)",
  "summary": "핵심 내용 3줄 요약",
  "investment_points": "투자 포인트 2-3가지, 쉼표 구분 (유튜브·텍스트용)",
  "risks": "주요 리스크 1-2가지, 쉼표 구분 (유튜브·텍스트용)",
  "details": "주요 수치·내용 3-5가지, 줄바꿈 구분 (기업공시용)",
  "impact": "투자영향 (기업공시용)"
}
"""

def parse_json(text: str) -> dict | None:
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group())
        except json.JSONDecodeError:
            return None
    return None

def gemini_analyze(text: str) -> dict | None:
    response = client.models.generate_content(
        model="gemini-2.5-flash",
        contents=f"{UNIFIED_PROMPT}\n\n내용:\n{text}",
    )
    return parse_json(response.text)

def gemini_analyze_pdf(pdf_path: str) -> dict | None:
    print("📤 PDF Gemini 업로드 중...")
    uploaded = client.files.upload(file=pdf_path, config={"mime_type": "application/pdf"})
    print("🤖 Gemini PDF 분석 중...")
    response = client.models.generate_content(
        model="gemini-2.5-flash",
        contents=[
            types.Part(file_data=types.FileData(file_uri=uploaded.uri, mime_type="application/pdf")),
            types.Part(text=UNIFIED_PROMPT),
        ],
    )
    client.files.delete(name=uploaded.name)
    return parse_json(response.text)

# ------------------------------------------------------------------ #
#  분석 진입점 — 입력 종류별                                           #
# ------------------------------------------------------------------ #
def analyze_from_url(url: str) -> tuple[dict | None, str]:
    """URL을 받아 (analysis, ref) 반환. content_type은 analysis 안에 있음."""
    is_youtube = "youtube.com" in url or "youtu.be" in url

    if is_youtube:
        transcript = get_youtube_transcript(url)
        if transcript:
            print("📝 자막 추출 성공 → 텍스트로 분석")
            analysis = gemini_analyze(transcript)
        else:
            print("📹 자막 없음 → 영상 직접 분석")
            response = client.models.generate_content(
                model="gemini-2.5-flash",
                contents=[
                    types.Part(file_data=types.FileData(file_uri=url)),
                    types.Part(text=UNIFIED_PROMPT),
                ],
            )
            analysis = parse_json(response.text)
        # 유튜브 URL은 content_type을 강제로 유튜브로
        if analysis:
            analysis["content_type"] = "유튜브"
    else:
        content = get_web_content(url)
        if not content:
            return None, url
        analysis = gemini_analyze(content)

    return analysis, url

def analyze_from_text(text: str) -> tuple[dict | None, str]:
    analysis = gemini_analyze(text)
    return analysis, "(텍스트 직접 입력)"

def analyze_from_pdf(pdf_path: str, filename: str) -> tuple[dict | None, str]:
    analysis = gemini_analyze_pdf(pdf_path)
    return analysis, filename

# ------------------------------------------------------------------ #
#  시트 저장 — content_type에 따라 자동 분기                           #
# ------------------------------------------------------------------ #
def route_and_save(analysis: dict, ref: str) -> tuple[str, str]:
    """저장하고 (tab_name, source_label) 반환."""
    today = datetime.now().strftime("%Y-%m-%d")
    ctype = analysis.get("content_type", "텍스트")

    if ctype == "기업공시":
        ws = connect_tab(TAB_FILING)
        ws.append_row([
            today,
            analysis.get("company", ""),
            analysis.get("ticker", ""),
            analysis.get("filing_type", ""),
            analysis.get("summary", ""),
            analysis.get("details", ""),
            analysis.get("impact", ""),
            ref,
        ])
        print(f"✅ [{analysis.get('ticker')}] → [기업공시] 저장")
        return TAB_FILING, "기업공시"

    elif ctype == "유튜브":
        ws = connect_tab(TAB_YOUTUBE)
        ws.append_row([
            today,
            analysis.get("company", ""),
            analysis.get("ticker", ""),
            "유튜브",
            analysis.get("presenter", ""),
            ref,
            analysis.get("summary", ""),
            analysis.get("investment_points", ""),
            analysis.get("risks", ""),
        ])
        print(f"✅ [{analysis.get('ticker')}] → [유튜브] 저장")
        return TAB_YOUTUBE, "유튜브"

    else:  # 텍스트
        ws = connect_tab(TAB_TEXT)
        ws.append_row([
            today,
            analysis.get("company", ""),
            analysis.get("ticker", ""),
            "텍스트",
            analysis.get("presenter", ""),
            ref,
            analysis.get("summary", ""),
            analysis.get("investment_points", ""),
            analysis.get("risks", ""),
        ])
        print(f"✅ [{analysis.get('ticker')}] → [텍스트] 저장")
        return TAB_TEXT, "텍스트"

# ------------------------------------------------------------------ #
#  텔레그램 회신 포맷                                                   #
# ------------------------------------------------------------------ #
def format_reply(analysis: dict, tab_name: str) -> str:
    ctype = analysis.get("content_type", "텍스트")
    header = (
        f"✅ 분석 완료 — [{tab_name}] 탭 저장됨\n\n"
        f"📌 *{analysis.get('company', '')}* `{analysis.get('ticker', '')}`\n"
    )
    if ctype == "기업공시":
        return (
            header +
            f"📋 공시유형: {analysis.get('filing_type', '')}\n\n"
            f"📝 *핵심 요약*\n{analysis.get('summary', '')}\n\n"
            f"🔍 *주요 내용*\n{analysis.get('details', '')}\n\n"
            f"📊 *투자 영향*\n{analysis.get('impact', '')}"
        )
    elif ctype == "유튜브":
        return (
            header +
            f"🎙 {analysis.get('presenter', '')}\n\n"
            f"📝 *핵심 요약*\n{analysis.get('summary', '')}\n\n"
            f"💡 *투자 포인트*\n{analysis.get('investment_points', '')}\n\n"
            f"⚠️ *리스크*\n{analysis.get('risks', '')}"
        )
    else:
        return (
            header +
            f"🎙 {analysis.get('presenter', '')}\n\n"
            f"📝 *핵심 요약*\n{analysis.get('summary', '')}\n\n"
            f"💡 *투자 포인트*\n{analysis.get('investment_points', '')}\n\n"
            f"⚠️ *리스크*\n{analysis.get('risks', '')}"
        )

# ------------------------------------------------------------------ #
#  텔레그램 핸들러                                                      #
# ------------------------------------------------------------------ #
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """텍스트 메시지 — URL 또는 일반 텍스트"""
    msg = update.message.text.strip()
    url_match = re.search(r"https?://\S+", msg)

    await update.message.reply_text("🤖 Gemini 분석 중...")
    try:
        if url_match:
            analysis, ref = analyze_from_url(url_match.group())
            if not analysis:
                await update.message.reply_text(
                    "🚫 링크 접근 불가\n\n텍스트로 내용을 붙여넣어 주세요."
                )
                return
        else:
            if len(msg) < 20:
                await update.message.reply_text("📋 내용이 너무 짧습니다. 본문을 붙여넣어 주세요.")
                return
            analysis, ref = analyze_from_text(msg)
            if not analysis:
                await update.message.reply_text("❌ 분석하지 못했습니다.")
                return

        tab_name, _ = route_and_save(analysis, ref)
        await update.message.reply_text(format_reply(analysis, tab_name), parse_mode="Markdown")
    except Exception as e:
        await update.message.reply_text(f"❌ 오류: {e}")

async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """PDF 파일 수신"""
    doc = update.message.document
    if doc.mime_type != "application/pdf":
        await update.message.reply_text("📄 PDF 파일만 분석할 수 있습니다.")
        return

    await update.message.reply_text("📄 PDF 업로드 및 Gemini 분석 중...")
    try:
        tg_file = await context.bot.get_file(doc.file_id)
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
            tmp_path = tmp.name
        await tg_file.download_to_drive(tmp_path)

        analysis, ref = analyze_from_pdf(tmp_path, doc.file_name)
        os.unlink(tmp_path)

        if not analysis:
            await update.message.reply_text("❌ PDF 분석에 실패했습니다.")
            return

        tab_name, _ = route_and_save(analysis, ref)
        await update.message.reply_text(format_reply(analysis, tab_name), parse_mode="Markdown")
    except Exception as e:
        await update.message.reply_text(f"❌ 오류: {e}")

# ------------------------------------------------------------------ #
#  중복 방지 — seen URL 관리                                            #
# ------------------------------------------------------------------ #
def load_seen() -> set:
    if os.path.exists(SEEN_FILE):
        with open(SEEN_FILE) as f:
            return set(json.load(f))
    return set()

def save_seen(seen: set):
    with open(SEEN_FILE, "w") as f:
        json.dump(list(seen), f)

# ------------------------------------------------------------------ #
#  SEC EDGAR 공시 자동 수집                                             #
# ------------------------------------------------------------------ #
def get_edgar_cik(ticker: str) -> str | None:
    try:
        res = requests.get(
            "https://www.sec.gov/files/company_tickers.json",
            headers={"User-Agent": EDGAR_UA}, timeout=10,
        )
        data = res.json()
        lookup = {v["ticker"]: str(v["cik_str"]).zfill(10) for v in data.values()}
        return lookup.get(ticker.upper())
    except Exception as e:
        print(f"⚠️ CIK 조회 실패 [{ticker}]: {e}")
        return None

def fetch_edgar_filings(ticker: str, cik: str, seen: set) -> list[dict]:
    """새로운 공시만 반환 (중복 제외)."""
    new_filings = []
    for form in EDGAR_FORMS:
        url = (
            f"https://www.sec.gov/cgi-bin/browse-edgar"
            f"?action=getcompany&CIK={cik}&type={form}"
            f"&dateb=&owner=include&count=5&output=atom"
        )
        feedparser.USER_AGENT = EDGAR_UA
        feed = feedparser.parse(url)
        for entry in feed.entries:
            link = entry.get("link", "")
            if not link or link in seen:
                continue
            new_filings.append({
                "ticker": ticker,
                "form": form,
                "title": entry.get("title", ""),
                "link": link,
                "date": entry.get("updated", "")[:10],
            })
            seen.add(link)
    return new_filings

def collect_edgar(seen: set) -> int:
    """관심종목 공시 수집 → 기업공시 탭 저장. 저장 건수 반환."""
    cik_map = {}
    try:
        res = requests.get(
            "https://www.sec.gov/files/company_tickers.json",
            headers={"User-Agent": EDGAR_UA}, timeout=10,
        )
        data = res.json()
        cik_map = {v["ticker"]: (str(v["cik_str"]).zfill(10), v["title"])
                   for v in data.values()}
    except Exception as e:
        print(f"⚠️ EDGAR CIK 일괄 조회 실패: {e}")
        return 0

    ws = connect_tab(TAB_FILING)
    today = datetime.now(KST).strftime("%Y-%m-%d")
    count = 0

    for ticker in AUTO_TICKERS:
        info = cik_map.get(ticker.upper())
        if not info:
            print(f"⚠️ CIK 없음: {ticker}")
            continue
        cik, company_name = info

        # 공시 분석 전 공시 목록만 먼저 가져옴
        for form in EDGAR_FORMS:
            url = (
                f"https://www.sec.gov/cgi-bin/browse-edgar"
                f"?action=getcompany&CIK={cik}&type={form}"
                f"&dateb=&owner=include&count=5&output=atom"
            )
            feedparser.USER_AGENT = EDGAR_UA
            feed = feedparser.parse(url)

            for entry in feed.entries:
                link = entry.get("link", "")
                if not link or link in seen:
                    continue

                # 공시 index 페이지에서 실제 문서 URL 추출
                doc_url = _get_edgar_doc_url(link)
                content = get_web_content(doc_url or link)

                if content:
                    analysis = gemini_analyze(content)
                else:
                    # 내용 못 가져오면 제목만으로 간단 저장
                    analysis = {
                        "content_type": "기업공시",
                        "company": company_name,
                        "ticker": ticker,
                        "filing_type": form,
                        "summary": entry.get("title", ""),
                        "details": "",
                        "impact": "",
                    }

                if analysis:
                    analysis["content_type"] = "기업공시"
                    analysis.setdefault("filing_type", form)
                    analysis.setdefault("company", company_name)
                    analysis.setdefault("ticker", ticker)
                    ws.append_row([
                        today,
                        analysis.get("company", company_name),
                        analysis.get("ticker", ticker),
                        analysis.get("filing_type", form),
                        analysis.get("summary", ""),
                        analysis.get("details", ""),
                        analysis.get("impact", ""),
                        link,
                    ])
                    print(f"  📋 [{ticker}] {form} 저장: {entry.get('title','')[:50]}")
                    count += 1

                seen.add(link)

    return count

def _get_edgar_doc_url(index_url: str) -> str | None:
    """EDGAR index 페이지에서 주 문서(htm/txt) URL 추출."""
    try:
        res = requests.get(index_url, headers={"User-Agent": EDGAR_UA}, timeout=10)
        soup = BeautifulSoup(res.text, "html.parser")
        for a in soup.select("table.tableFile a"):
            href = a.get("href", "")
            if href.endswith((".htm", ".html", ".txt")) and "index" not in href.lower():
                return "https://www.sec.gov" + href if href.startswith("/") else href
    except:
        pass
    return None

# ------------------------------------------------------------------ #
#  구글 뉴스 RSS 자동 수집                                              #
# ------------------------------------------------------------------ #
def collect_news(seen: set) -> int:
    """관심종목 뉴스 수집 → 텍스트 탭 저장. 저장 건수 반환."""
    ws = connect_tab(TAB_TEXT)
    today = datetime.now(KST).strftime("%Y-%m-%d")
    count = 0

    for ticker in AUTO_TICKERS:
        url = (
            f"https://news.google.com/rss/search"
            f"?q={ticker}+stock&hl=en-US&gl=US&ceid=US:en"
        )
        feed = feedparser.parse(url)
        saved = 0

        for entry in feed.entries:
            if saved >= 3:
                break
            link = entry.get("link", "")
            if not link or link in seen:
                continue

            title = entry.get("title", "")
            # 구글 뉴스는 리다이렉트 URL → 본문 크롤링 불가, 제목+요약 사용
            source = entry.get("source", {}).get("href", "")
            raw_text = f"{title}. {entry.get('summary', '')}"
            content = (get_web_content(source) if source else None) or raw_text
            analysis = gemini_analyze(content)

            if analysis:
                ws.append_row([
                    today,
                    analysis.get("company", ticker),
                    analysis.get("ticker", ticker),
                    "뉴스",
                    analysis.get("presenter", "Google News"),
                    link,
                    analysis.get("summary", title),
                    analysis.get("investment_points", ""),
                    analysis.get("risks", ""),
                ])
                print(f"  📰 [{ticker}] 뉴스 저장: {title[:50]}")
                count += 1
                saved += 1

            seen.add(link)

    return count

# ------------------------------------------------------------------ #
#  자동 수집 메인 잡 (매일 밤 11시 KST)                                  #
# ------------------------------------------------------------------ #
async def run_auto_collect(context: ContextTypes.DEFAULT_TYPE):
    print(f"\n🔄 자동 수집 시작 [{datetime.now(KST).strftime('%Y-%m-%d %H:%M')} KST]")
    chat_id = load_chat_id()
    seen = load_seen()

    if chat_id:
        await context.bot.send_message(chat_id=chat_id, text="🔄 관심종목 자동 수집 시작...")

    try:
        edgar_count = collect_edgar(seen)
        news_count  = collect_news(seen)
        save_seen(seen)

        msg = (
            f"✅ 자동 수집 완료\n"
            f"📋 공시: {edgar_count}건\n"
            f"📰 뉴스: {news_count}건\n"
            f"(내일 오전 7시 브리핑에 포함됩니다)"
        )
        print(f"✅ 수집 완료 — 공시 {edgar_count}건 / 뉴스 {news_count}건")
        if chat_id:
            await context.bot.send_message(chat_id=chat_id, text=msg)

    except Exception as e:
        print(f"❌ 자동 수집 오류: {e}")
        if chat_id:
            await context.bot.send_message(chat_id=chat_id, text=f"❌ 자동 수집 오류: {e}")

# ------------------------------------------------------------------ #
#  Chat ID 저장/로드                                                   #
# ------------------------------------------------------------------ #
def load_chat_id() -> int | None:
    global BRIEFING_CHAT_ID
    if os.path.exists(CHAT_ID_FILE):
        with open(CHAT_ID_FILE) as f:
            val = f.read().strip()
            if val:
                BRIEFING_CHAT_ID = int(val)
    return BRIEFING_CHAT_ID

def save_chat_id(chat_id: int):
    global BRIEFING_CHAT_ID
    BRIEFING_CHAT_ID = chat_id
    with open(CHAT_ID_FILE, "w") as f:
        f.write(str(chat_id))

# ------------------------------------------------------------------ #
#  주가 조회                                                            #
# ------------------------------------------------------------------ #
def get_prices() -> str:
    lines = []
    for ticker in WATCHLIST:
        try:
            t = yf.Ticker(ticker)
            info = t.fast_info
            price = info.last_price
            prev  = info.previous_close
            change = ((price - prev) / prev * 100) if prev else 0
            sign  = "▲" if change >= 0 else "▼"
            label = ticker.replace("-USD", "")
            lines.append(f"  {label}: ${price:,.2f} {sign}{abs(change):.2f}%")
        except Exception as e:
            lines.append(f"  {ticker.replace('-USD','')}: 조회 실패")
    return "\n".join(lines)

# ------------------------------------------------------------------ #
#  전날 시트 데이터 읽기                                                #
# ------------------------------------------------------------------ #
def get_yesterday_rows(tab_name: str) -> list[dict]:
    try:
        ws = connect_tab(tab_name)
        rows = ws.get_all_records()
        yesterday = (datetime.now(KST) - timedelta(days=1)).strftime("%Y-%m-%d")
        return [r for r in rows if str(r.get("날짜", "")).startswith(yesterday)]
    except:
        return []

# ------------------------------------------------------------------ #
#  Gemini 탭별 요약                                                    #
# ------------------------------------------------------------------ #
def summarize_tab(tab_name: str, rows: list[dict]) -> str:
    if not rows:
        return "어제 저장된 내용 없음"

    if tab_name == TAB_FILING:
        items = "\n".join(
            f"- [{r.get('티커','')}] {r.get('기업명','')} / {r.get('공시유형','')} / "
            f"{r.get('핵심요약','')[:80]} / 투자영향: {r.get('투자영향','')[:60]}"
            for r in rows
        )
        prompt = f"다음 기업공시 목록을 3-5줄로 핵심만 한국어로 요약하세요:\n{items}"
    else:
        col_summary = "핵심요약"
        col_company = "회사명"
        col_ticker  = "티커"
        items = "\n".join(
            f"- [{r.get(col_ticker,'')}] {r.get(col_company,'')} / {r.get(col_summary,'')[:80]}"
            for r in rows
        )
        prompt = f"다음 주식 관련 콘텐츠 목록을 3-5줄로 핵심만 한국어로 요약하세요:\n{items}"

    try:
        response = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=prompt,
        )
        return response.text.strip()
    except:
        return "\n".join(
            f"• [{r.get('티커','?')}] {r.get('회사명', r.get('기업명','?'))}"
            for r in rows
        )

# ------------------------------------------------------------------ #
#  브리핑 메시지 조립 및 발송                                           #
# ------------------------------------------------------------------ #
async def send_briefing(bot: Bot):
    chat_id = load_chat_id()
    if not chat_id:
        print("⚠️ 브리핑 수신자 미등록 — 텔레그램에서 /register 명령어를 보내세요")
        return

    now_kst = datetime.now(KST).strftime("%Y년 %m월 %d일")
    yesterday_kst = (datetime.now(KST) - timedelta(days=1)).strftime("%Y-%m-%d")
    print(f"📨 브리핑 발송 시작 ({now_kst})")

    yt_rows   = get_yesterday_rows(TAB_YOUTUBE)
    txt_rows  = get_yesterday_rows(TAB_TEXT)
    fil_rows  = get_yesterday_rows(TAB_FILING)

    yt_summary  = summarize_tab(TAB_YOUTUBE, yt_rows)
    txt_summary = summarize_tab(TAB_TEXT,    txt_rows)
    fil_summary = summarize_tab(TAB_FILING,  fil_rows)
    prices      = get_prices()

    msg = (
        f"📊 *오늘의 주식 브리핑* ({now_kst})\n"
        f"━━━━━━━━━━━━━━━━━━\n\n"
        f"📌 *어제({yesterday_kst}) 저장된 종목 요약*\n\n"
        f"🎬 *유튜브 탭* ({len(yt_rows)}건)\n{yt_summary}\n\n"
        f"📝 *텍스트/뉴스 탭* ({len(txt_rows)}건)\n{txt_summary}\n\n"
        f"📋 *공시 탭* ({len(fil_rows)}건)\n{fil_summary}\n\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"💰 *관심종목 현재가*\n{prices}"
    )

    await bot.send_message(chat_id=chat_id, text=msg, parse_mode="Markdown")
    print("✅ 브리핑 발송 완료")

# ------------------------------------------------------------------ #
#  /register, /briefing 커맨드 핸들러                                   #
# ------------------------------------------------------------------ #
async def handle_register(update: Update, context: ContextTypes.DEFAULT_TYPE):
    save_chat_id(update.effective_chat.id)
    await update.message.reply_text(
        "✅ 브리핑 수신 등록 완료!\n매일 오전 7시(한국 시간)에 브리핑을 받습니다.\n"
        "/briefing 으로 지금 바로 받아볼 수 있어요."
    )

async def handle_briefing_now(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("📊 브리핑 생성 중...")
    await send_briefing(context.bot)

async def handle_collect_now(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🔄 관심종목 수집 시작... (수분 소요)")
    await run_auto_collect(context)

# ------------------------------------------------------------------ #
#  봇 실행                                                             #
# ------------------------------------------------------------------ #
def run_bot():
    from telegram.ext import CommandHandler
    print("🤖 텔레그램 봇 시작...")
    load_chat_id()

    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()

    # 커맨드 핸들러
    app.add_handler(CommandHandler("register", handle_register))
    app.add_handler(CommandHandler("briefing", handle_briefing_now))
    app.add_handler(CommandHandler("collect",  handle_collect_now))

    # 메시지 핸들러
    app.add_handler(MessageHandler(filters.Document.PDF, handle_document))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    # 매일 오전 7시(KST) 브리핑
    async def briefing_job(context: ContextTypes.DEFAULT_TYPE):
        await send_briefing(context.bot)

    app.job_queue.run_daily(
        briefing_job,
        time=dtime(hour=7, minute=0, second=0, tzinfo=KST),
    )
    print("⏰ 브리핑 스케줄 등록: 매일 오전 7:00 KST", flush=True)

    # 매일 밤 11시(KST) 관심종목 자동 수집
    app.job_queue.run_daily(
        run_auto_collect,
        time=dtime(hour=23, minute=0, second=0, tzinfo=KST),
    )
    print("⏰ 자동 수집 스케줄 등록: 매일 밤 23:00 KST", flush=True)

    app.run_polling()

# ------------------------------------------------------------------ #
#  CLI 실행                                                            #
# ------------------------------------------------------------------ #
def main():
    if "--bot" in sys.argv:
        ensure_all_tabs()
        run_bot()
        return

    if "--setup" in sys.argv:
        ensure_all_tabs()
        print("✅ 탭 설정 완료")
        return

    if len(sys.argv) > 1:
        url = " ".join(a for a in sys.argv[1:]).strip().strip("'\"")
    else:
        url = input("🔗 링크를 붙여넣으세요: ").strip().strip("'\"")

    print("🤖 Gemini 분석 중...")
    analysis, ref = analyze_from_url(url)
    if not analysis:
        print("❌ 분석 실패")
        return
    print(json.dumps(analysis, ensure_ascii=False, indent=2))
    route_and_save(analysis, ref)

if __name__ == "__main__":
    main()
