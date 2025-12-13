import os
import logging
import feedparser
import requests
import html
import time
from datetime import datetime, timedelta, timezone
import google.generativeai as genai
from google.generativeai.types import HarmCategory, HarmBlockThreshold

# --- تنظیمات ---
# کلید شما
GEMINI_API_KEY = "AIzaSyD_N69KfteuikbJtVZS_XJqPn_399MHeGA"
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN") 
ADMIN_CHAT_ID = os.environ.get("ADMIN_CHAT_ID")
URL_FILE = "urls.txt"

# تنظیم مدل روی نسخه Pro (قدرتمند)
genai.configure(api_key=GEMINI_API_KEY)
model = genai.GenerativeModel(
    "gemini-1.5-pro",
    safety_settings={
        HarmCategory.HARM_CATEGORY_HARASSMENT: HarmBlockThreshold.BLOCK_NONE,
        HarmCategory.HARM_CATEGORY_HATE_SPEECH: HarmBlockThreshold.BLOCK_NONE,
        HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT: HarmBlockThreshold.BLOCK_NONE,
        HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT: HarmBlockThreshold.BLOCK_NONE,
    }
)

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')

def get_tehran_time():
    # ساعت الان به وقت تهران
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
        f"Act as a professional science communicator.\n"
        f"1. Translate this title to fluent Persian: '{title}'\n"
        f"2. Write a short explanation (2 sentences) in Persian about why this is important, based on: '{summary}'\n"
        f"Format your output exactly like this:\n"
        f"Title: [Persian Title Here]\n"
        f"Explanation: [Persian Explanation Here]"
    )
    try:
        response = model.generate_content(prompt)
        text = response.text.strip()
        
        # استخراج تمیز خروجی
        title_fa = title
        expl_fa = text
        
        for line in text.split('\n'):
            if "Title:" in line or "تیتر:" in line:
                title_fa = line.split(":", 1)[1].strip()
            if "Explanation:" in line or "توضیح:" in line:
                expl_fa = line.split(":", 1)[1].strip()
                
        # پاکسازی اضافی
        title_fa = title_fa.replace("*", "").strip()
        expl_fa = expl_fa.replace("Explanation:", "").replace("توضیح:", "").strip()
        
        return title_fa, expl_fa

    except Exception as e:
        return title, f"⚠️ خطا در هوش مصنوعی: {str(e)}"

def check_and_send_news():
    logging.info("Checking news...")
    try:
        with open(URL_FILE, "r") as f: urls = [l.strip() for l in f if l.strip() and not l.startswith("#")]
    except: return

    # فقط خبرهای ۲۴ ساعت گذشته (برای جلوگیری از خبرهای قدیمی مثل جولای)
    now = get_tehran_time()
    time_threshold = now - timedelta(hours=24)
    
    for url in urls:
        try:
            feed = feedparser.parse(url)
            if not feed.entries: continue
            
            # فقط ۳ خبر اول هر سایت را چک کن
            for entry in feed.entries[:3]:
                # پیدا کردن تاریخ خبر
                pub_struct = getattr(entry, "published_parsed", None)
                if pub_struct:
                    pub_date_utc = datetime(*pub_struct[:6], tzinfo=timezone.utc)
                    pub_date_tehran = pub_date_utc.astimezone(timezone(timedelta(hours=3, minutes=30)))
                else:
                    # اگر خبر تاریخ نداشت، ریسک نکن و رد شو (ممکنه قدیمی باشه)
                    continue

                # اگر خبر قدیمی‌تر از دیروز است، نادیده بگیر
                if pub_date_tehran < time_threshold:
                    continue

                # اگر خبر مربوط به آینده است (باگ ساعت سرور)، نادیده بگیر
                if pub_date_tehran > now + timedelta(minutes=10):
                    continue

                title = entry.get("title", "")
                link = entry.get("link", "")
                summary = html.unescape(entry.get("summary", ""))
                
                # پردازش و ارسال
                fa_title, fa_expl = process_article_with_ai(title, summary)
                
                msg = (
                    f"⚛️ <b>{html.escape(fa_title)}</b>\n\n"
                    f"{html.escape(fa_expl)}\n\n"
                    f"📅 {pub_date_tehran.strftime('%H:%M')}\n"
                    f"🔗 <a href='{link}'>لینک مقاله</a>"
                )
                send_telegram_message(msg)
                time.sleep(5) # استراحت بین پیام‌ها

        except Exception as e:
            logging.error(f"Feed Error: {e}")

def send_nightly_summary():
    try:
        with open(URL_FILE, "r") as f: urls = [l.strip() for l in f if l.strip() and not l.startswith("#")]
    except: return
    
    now = get_tehran_time()
    today_str = now.strftime("%Y-%m-%d")
    summary = []
    
    for url in urls:
        try:
            feed = feedparser.parse(url)
            for entry in feed.entries[:5]:
                pub = getattr(entry, "published_parsed", None)
                if pub:
                    p_date = datetime(*pub[:6], tzinfo=timezone.utc).astimezone(timezone(timedelta(hours=3, minutes=30)))
                    if p_date.strftime("%Y-%m-%d") == today_str:
                        summary.append(f"🔹 <a href='{entry.link}'>{html.escape(entry.title)}</a>")
        except: continue
        
    if summary: 
        send_telegram_message(f"🌙 <b>لیست مقالات امروز ({today_str}):</b>\n\n" + "\n".join(summary))

if __name__ == "__main__":
    check_and_send_news()
    # اگر ساعت بین ۲۳:۰۰ تا ۲۳:۳۰ بود، خلاصه بفرست
    if get_tehran_time().hour == 23 and get_tehran_time().minute < 30:
        send_nightly_summary()
