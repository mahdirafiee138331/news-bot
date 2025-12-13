import os
import logging
import feedparser
import requests
import html
import time
from datetime import datetime, timedelta, timezone
import google.generativeai as genai
from google.generativeai.types import HarmCategory, HarmBlockThreshold

# --- کلید و تنظیمات ---
GEMINI_API_KEY = "AIzaSyD_N69KfteuikbJtVZS_XJqPn_399MHeGA"
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN") 
ADMIN_CHAT_ID = os.environ.get("ADMIN_CHAT_ID")
URL_FILE = "urls.txt"

# استفاده از مدل فلش (سریع و جدید)
genai.configure(api_key=GEMINI_API_KEY)
model = genai.GenerativeModel(
    "gemini-1.5-flash",
    safety_settings={
        HarmCategory.HARM_CATEGORY_HARASSMENT: HarmBlockThreshold.BLOCK_NONE,
        HarmCategory.HARM_CATEGORY_HATE_SPEECH: HarmBlockThreshold.BLOCK_NONE,
        HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT: HarmBlockThreshold.BLOCK_NONE,
        HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT: HarmBlockThreshold.BLOCK_NONE,
    }
)

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')

def get_tehran_time():
    return datetime.now(timezone(timedelta(hours=3, minutes=30)))

def send_telegram_message(text):
    if not TELEGRAM_BOT_TOKEN or not ADMIN_CHAT_ID:
        logging.error("توکن تلگرام نیست!")
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": ADMIN_CHAT_ID, "text": text, "parse_mode": "HTML", "disable_web_page_preview": False}
    try:
        requests.post(url, json=payload, timeout=20)
    except Exception as e:
        logging.error(f"Telegram Error: {e}")

def process_article_with_ai(title, summary):
    prompt = (
        f"Translate title to Persian and summarize in 2 sentences:\n"
        f"Title: {title}\n"
        f"Summary: {summary}\n"
        f"Format:\nTitle: [Persian Title]\nExplanation: [Persian Text]"
    )
    try:
        response = model.generate_content(prompt)
        text = response.text.strip()
        
        title_fa = title
        expl_fa = text
        
        lines = text.split('\n')
        for line in lines:
            if "Title:" in line or "تیتر:" in line:
                title_fa = line.split(":", 1)[1].strip()
            if "Explanation:" in line or "توضیح:" in line:
                expl_fa = line.split(":", 1)[1].strip()
        
        # اگر مدل گیج زد و فرمت رو رعایت نکرد
        if title_fa == title and len(lines) > 1:
             title_fa = lines[0]
             expl_fa = "\n".join(lines[1:])

        return title_fa.replace("*", "").strip(), expl_fa.replace("Explanation:", "").strip()

    except Exception as e:
        return title, f"⚠️ خطا: {str(e)}"

def check_and_send_news():
    try:
        with open(URL_FILE, "r") as f: urls = [l.strip() for l in f if l.strip() and not l.startswith("#")]
    except: return

    # فقط اخبار ۲۴ ساعت گذشته
    time_threshold = get_tehran_time() - timedelta(hours=24)
    
    for url in urls:
        try:
            feed = feedparser.parse(url)
            if not feed.entries: continue
            # فقط ۳ تا خبر اول هر سایت
            for entry in feed.entries[:3]:
                pub_struct = getattr(entry, "published_parsed", None)
                if not pub_struct: continue
                
                pub_date = datetime(*pub_struct[:6], tzinfo=timezone.utc).astimezone(timezone(timedelta(hours=3, minutes=30)))
                
                # اگر قدیمی بود رد شو
                if pub_date < time_threshold: continue
                # اگر مال آینده بود (باگ) رد شو
                if pub_date > get_tehran_time() + timedelta(minutes=10): continue

                title = entry.get("title", "")
                link = entry.get("link", "")
                summary = html.unescape(entry.get("summary", ""))
                
                fa_title, fa_expl = process_article_with_ai(title, summary)
                
                msg = f"⚛️ <b>{html.escape(fa_title)}</b>\n\n{html.escape(fa_expl)}\n\n📅 {pub_date.strftime('%H:%M')}\n🔗 <a href='{link}'>لینک مقاله</a>"
                send_telegram_message(msg)
                time.sleep(5)
        except: continue

if __name__ == "__main__":
    check_and_send_news()
