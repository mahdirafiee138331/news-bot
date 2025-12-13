import os
import logging
import feedparser
import requests
import html
import time
from datetime import datetime, timedelta, timezone
import google.generativeai as genai

# --- تنظیمات ---
# کلید شما
GEMINI_API_KEY = "AIzaSyD_N69KfteuikbJtVZS_XJqPn_399MHeGA"
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN") 
ADMIN_CHAT_ID = os.environ.get("ADMIN_CHAT_ID")
URL_FILE = "urls.txt"

# تنظیم دقیقا مثل کد قدیمی (gemini-pro)
genai.configure(api_key=GEMINI_API_KEY)
model = genai.GenerativeModel("gemini-pro")

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
    # پرامپت ساده
    prompt = (
        f"Translate title to Persian and summarize in 2 lines:\n"
        f"Title: {title}\n"
        f"Summary: {summary}\n"
        f"Format:\nTitle: ...\nExplanation: ..."
    )
    try:
        response = model.generate_content(prompt)
        text = response.text.strip()
        
        # تلاش برای تمیز کردن خروجی
        title_fa = title
        expl_fa = text
        
        for line in text.split('\n'):
            if "Title:" in line or "تیتر:" in line:
                title_fa = line.split(":", 1)[1].strip()
            if "Explanation:" in line or "توضیح:" in line:
                expl_fa = line.split(":", 1)[1].strip()
                
        return title_fa.replace("*", ""), expl_fa.replace("Explanation:", "")

    except Exception as e:
        return title, f"(خطا در هوش مصنوعی: {e})"

def check_and_send_news():
    try:
        with open(URL_FILE, "r") as f: urls = [l.strip() for l in f if l.strip() and not l.startswith("#")]
    except: return

    # فقط اخبار ۲۴ ساعت گذشته
    time_threshold = get_tehran_time() - timedelta(hours=24)
    
    for url in urls:
        try:
            feed = feedparser.parse(url)
            for entry in feed.entries[:3]:
                pub_struct = getattr(entry, "published_parsed", None)
                if not pub_struct: continue
                
                pub_date = datetime(*pub_struct[:6], tzinfo=timezone.utc).astimezone(timezone(timedelta(hours=3, minutes=30)))
                
                if pub_date < time_threshold: continue # قدیمی‌ها رو نریز
                
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
