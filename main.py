import os
import logging
import feedparser
import requests
import html
import time
from datetime import datetime, timedelta, timezone
import google.generativeai as genai
from google.generativeai.types import HarmCategory, HarmBlockThreshold

# --- کلید اختصاصی شما ---
GEMINI_API_KEY = "AIzaSyD_N69KfteuikbJtVZS_XJqPn_399MHeGA"

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN") 
ADMIN_CHAT_ID = os.environ.get("ADMIN_CHAT_ID")

# تنظیمات هوش مصنوعی (مدل سریع‌تر + خاموش کردن فیلترها)
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
    # پرامپت ساده‌تر و مستقیم‌تر
    prompt = (
        f"Translate the following science news title to Persian and summarize the content in 2 sentences in Persian.\n\n"
        f"Title: {title}\n"
        f"Summary: {summary}\n\n"
        f"Format:\nTitle: [Persian Title]\nExplanation: [Persian Explanation]"
    )
    try:
        response = model.generate_content(prompt)
        text = response.text.strip()
        
        # استخراج هوشمندانه خروجی
        title_fa = title
        expl_fa = text
        
        # تلاش برای تمیز کردن خروجی
        lines = text.split('\n')
        for line in lines:
            if line.startswith("Title:") or line.startswith("تیتر:"):
                title_fa = line.split(":", 1)[1].strip()
            if line.startswith("Explanation:") or line.startswith("توضیح:"):
                expl_fa = line.split(":", 1)[1].strip()
                
        # اگر مدل فرمت رو رعایت نکرد، کل متن رو به عنوان توضیح برگردون
        if title_fa == title and len(lines) > 1:
             title_fa = lines[0]
             expl_fa = "\n".join(lines[1:])

        return title_fa, expl_fa

    except Exception as e:
        # اگر خطا داد، متن خطا رو برمی‌گردونیم تا توی تلگرام ببینیم مشکل چیه
        return f"{title}", f"⚠️ خطا در هوش مصنوعی: {str(e)}"

def check_and_send_news():
    logging.info("Checking news...")
    try:
        with open(URL_FILE, "r") as f: urls = [l.strip() for l in f if l.strip() and not l.startswith("#")]
    except: return

    # بررسی ۶ ساعت گذشته
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
                    summary_text = html.unescape(entry.get("summary", ""))
                    
                    # پردازش با هوش مصنوعی
                    fa_title, fa_expl = process_article_with_ai(title, summary_text)
                    
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
