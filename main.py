import os
import logging
import feedparser
import requests
import html
import time
from datetime import datetime, timedelta, timezone
import google.generativeai as genai

# --- کلید اختصاصی شما ---
GEMINI_API_KEY = "AIzaSyD_N69KfteuikbJtVZS_XJqPn_399MHeGA"

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN") 
ADMIN_CHAT_ID = os.environ.get("ADMIN_CHAT_ID")

genai.configure(api_key=GEMINI_API_KEY)
model = genai.GenerativeModel("gemini-1.5-pro")

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')
URL_FILE = "urls.txt"

def get_tehran_time():
    return datetime.now(timezone(timedelta(hours=3, minutes=30)))

def send_telegram_message(text):
    if not TELEGRAM_BOT_TOKEN or not ADMIN_CHAT_ID:
        logging.error("توکن تلگرام تنظیم نشده!")
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": ADMIN_CHAT_ID, "text": text, "parse_mode": "HTML", "disable_web_page_preview": False}
    try:
        requests.post(url, json=payload, timeout=20)
    except Exception as e:
        logging.error(f"Telegram Error: {e}")

def process_article_with_ai(title, summary):
    prompt = (
        f"You are an expert academic assistant. \n"
        f"Task 1: Translate this title to fluent, academic Persian: '{title}'\n"
        f"Task 2: Explain the significance in 2-3 Persian sentences based on: '{summary}'\n"
        f"Output format:\nPersian Title\n\nPersian Explanation"
    )
    try:
        response = model.generate_content(prompt)
        text = response.text.strip()
        parts = text.split('\n\n')
        return (parts[0].strip(), parts[1].strip()) if len(parts) >= 2 else (parts[0], text)
    except:
        return f"ترجمه: {title}", "توضیح: (خطا در هوش مصنوعی)"

def check_and_send_news():
    logging.info("Checking news...")
    try:
        with open(URL_FILE, "r") as f: urls = [l.strip() for l in f if l.strip() and not l.startswith("#")]
    except: return

    time_threshold = get_tehran_time() - timedelta(hours=6)
    
    for url in urls:
        try:
            feed = feedparser.parse(url)
            if not feed.entries: continue
            for entry in feed.entries[:5]:
                pub_struct = getattr(entry, "published_parsed", None)
                if not pub_struct: continue
                pub_date = datetime(*pub_struct[:6], tzinfo=timezone.utc).astimezone(timezone(timedelta(hours=3, minutes=30)))
                
                if pub_date > time_threshold:
                    title, link = entry.get("title", ""), entry.get("link", "")
                    # پردازش و ارسال
                    fa_title, fa_expl = process_article_with_ai(title, html.unescape(entry.get("summary", "")))
                    msg = f"⚛️ <b>{html.escape(fa_title)}</b>\n\n{html.escape(fa_expl)}\n\n📅 {pub_date.strftime('%H:%M')}\n🔗 <a href='{link}'>لینک مقاله</a>"
                    send_telegram_message(msg)
                    time.sleep(5)
        except Exception as e: logging.error(f"Feed Error: {e}")

def send_nightly_summary():
    try:
        with open(URL_FILE, "r") as f: urls = [l.strip() for l in f if l.strip() and not l.startswith("#")]
    except: return
    today = get_tehran_time().strftime("%Y-%m-%d")
    summary = []
    for url in urls:
        try:
            feed = feedparser.parse(url)
            for entry in feed.entries[:10]:
                pub = getattr(entry, "published_parsed", None)
                if pub:
                    p_date = datetime(*pub[:6], tzinfo=timezone.utc).astimezone(timezone(timedelta(hours=3, minutes=30)))
                    if p_date.strftime("%Y-%m-%d") == today:
                        summary.append(f"🔹 <a href='{entry.link}'>{html.escape(entry.title)}</a>")
        except: continue
    if summary: send_telegram_message(f"🌙 <b>لیست مقالات امروز ({today}):</b>\n\n" + "\n".join(summary))

if __name__ == "__main__":
    check_and_send_news()
    if get_tehran_time().hour == 23: send_nightly_summary()
