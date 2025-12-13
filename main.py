import os
import logging
import feedparser
import requests
import html
import time
from datetime import datetime, timedelta, timezone
import google.generativeai as genai

# --- تنظیمات اصلی (کلید شما اینجاست) ---
# توجه: اگر پروژه را عمومی (Public) کنید، دیگران این کلید را می‌بینند.
# بهتر است پروژه در گیت‌هاب Private باشد.
GEMINI_API_KEY = "AIzaSyD_N69KfteuikbJtVZS_XJqPn_399MHeGA"

# توکن و آیدی تلگرام را باید از تنظیمات سرور (Environment Variables) بخواند
# یا اگر می‌خواهید دستی وارد کنید، جای os.environ... مستقیم بنویسید
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN") 
ADMIN_CHAT_ID = os.environ.get("ADMIN_CHAT_ID")

# تنظیم مدل روی نسخه پرو (قدرتمندترین نسخه)
genai.configure(api_key=GEMINI_API_KEY)
model = genai.GenerativeModel("gemini-1.5-pro")

# تنظیمات لاگ
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')

# فایل آدرس فیدها
URL_FILE = "urls.txt"

# --- توابع کمکی ---

def get_tehran_time():
    # منطقه زمانی تهران (UTC+3:30)
    return datetime.now(timezone(timedelta(hours=3, minutes=30)))

def clean_html(raw_html):
    import re
    cleanr = re.compile('<.*?>')
    text = re.sub(cleanr, '', raw_html)
    return text.strip()

def send_telegram_message(text):
    if not TELEGRAM_BOT_TOKEN or not ADMIN_CHAT_ID:
        logging.error("توکن تلگرام یا چت آیدی تنظیم نشده است!")
        return
    
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": ADMIN_CHAT_ID,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": False
    }
    try:
        requests.post(url, json=payload, timeout=20)
    except Exception as e:
        logging.error(f"خطا در ارسال تلگرام: {e}")

def process_article_with_ai(title, summary):
    prompt = (
        f"You are an expert academic assistant. \n"
        f"Task 1: Translate this title to fluent, academic Persian: '{title}'\n"
        f"Task 2: Explain the main significance of this article in 2-3 Persian sentences based on this summary: '{summary}'\n"
        f"Output format:\nPersian Title\n\nPersian Explanation"
    )
    
    try:
        # استفاده از جمینای پرو
        response = model.generate_content(prompt)
        text = response.text.strip()
        parts = text.split('\n\n')
        if len(parts) >= 2:
            return parts[0].strip(), parts[1].strip()
        else:
            return parts[0].strip(), text.replace(parts[0], "").strip()
    except Exception as e:
        logging.error(f"AI Error: {e}")
        return f"ترجمه: {title}", "توضیح: (خطا در پردازش هوش مصنوعی)"

# --- تابع اصلی: بررسی اخبار ---
def check_and_send_news():
    logging.info("شروع بررسی اخبار...")
    
    # خواندن آدرس‌ها
    try:
        with open(URL_FILE, "r", encoding='utf-8') as f:
            urls = [line.strip() for line in f if line.strip() and not line.startswith("#")]
    except FileNotFoundError:
        logging.error("فایل urls.txt پیدا نشد!")
        return

    now_tehran = get_tehran_time()
    # بازه زمانی: فقط مقالاتی که در ۶ ساعت گذشته منتشر شده‌اند (برای جلوگیری از تکرار)
    time_threshold = now_tehran - timedelta(hours=6)

    articles_for_summary = []

    for url in urls:
        try:
            feed = feedparser.parse(url)
            if not feed.entries:
                continue
            
            for entry in feed.entries[:5]: # چک کردن ۵ خبر آخر
                # پیدا کردن تاریخ انتشار
                pub_struct = getattr(entry, "published_parsed", None)
                if pub_struct:
                    # تبدیل زمان مقاله به فرمت قابل مقایسه (UTC)
                    pub_date_utc = datetime(*pub_struct[:6], tzinfo=timezone.utc)
                    # تبدیل به وقت تهران برای مقایسه راحت‌تر
                    pub_date_tehran = pub_date_utc.astimezone(timezone(timedelta(hours=3, minutes=30)))
                else:
                    continue # اگر تاریخ نداشت، ریسک نمی‌کنیم و رد می‌شویم

                # شرط ۱: آیا مقاله جدید است؟ (مال ۶ ساعت اخیر)
                if pub_date_tehran > time_threshold:
                    title = entry.get("title", "")
                    link = entry.get("link", "")
                    summary = clean_html(entry.get("summary", ""))
                    
                    logging.info(f"مقاله جدید پیدا شد: {title}")
                    
                    # پردازش هوش مصنوعی
                    fa_title, fa_expl = process_article_with_ai(title, summary)
                    
                    msg = (
                        f"⚛️ <b>{html.escape(fa_title)}</b>\n\n"
                        f"{html.escape(fa_expl)}\n\n"
                        f"📅 {pub_date_tehran.strftime('%H:%M')}\n"
                        f"🔗 <a href='{link}'>لینک مقاله</a>"
                    )
                    send_telegram_message(msg)
                    time.sleep(5) # کمی صبر بین پیام‌ها

        except Exception as e:
            logging.error(f"Error checking feed {url}: {e}")

# --- تابع گزارش شبانه ---
def send_nightly_summary():
    logging.info("در حال تهیه گزارش شبانه...")
    try:
        with open(URL_FILE, "r", encoding='utf-8') as f:
            urls = [line.strip() for line in f if line.strip() and not line.startswith("#")]
    except:
        return

    now_tehran = get_tehran_time()
    today_str = now_tehran.strftime("%Y-%m-%d")
    
    summary_list = []

    for url in urls:
        try:
            feed = feedparser.parse(url)
            for entry in feed.entries[:10]: # ۱۰ تای آخر رو چک کن برای خلاصه
                pub_struct = getattr(entry, "published_parsed", None)
                if pub_struct:
                    pub_date_utc = datetime(*pub_struct[:6], tzinfo=timezone.utc)
                    pub_date_tehran = pub_date_utc.astimezone(timezone(timedelta(hours=3, minutes=30)))
                    
                    # اگر تاریخ مقاله مال "امروز" است
                    if pub_date_tehran.strftime("%Y-%m-%d") == today_str:
                        title = entry.get("title", "No Title")
                        link = entry.get("link", "")
                        # اینجا دیگه ترجمه نمی‌کنیم تا سریع باشه، یا اگر خواستید میشه ترجمه هم کرد
                        # فعلا لینک و تیتر انگلیسی رو می‌ذاریم که لیست پر نشه
                        summary_list.append(f"🔹 <a href='{link}'>{html.escape(title)}</a>")
        except:
            continue

    if summary_list:
        text = f"🌙 <b>جمع‌بندی مقالات علمی امروز ({today_str}):</b>\n\n" + "\n".join(summary_list)
        send_telegram_message(text)
    else:
        logging.info("مقاله‌ای برای گزارش شبانه یافت نشد.")

# --- بدنه اصلی اجرا ---
if __name__ == "__main__":
    # ۱. همیشه اول اخبار جدید رو چک کن
    check_and_send_news()
    
    # ۲. چک کن ساعت چنده؟ اگر حدود ۱۱ شب (۲۳) بود، خلاصه بفرست
    # (بازه‌ی ۲۲:۵۰ تا ۲۳:۵۰ رو در نظر می‌گیریم که مطمئن باشیم اجرا میشه)
    now = get_tehran_time()
    if now.hour == 23 and now.minute < 55:
        send_nightly_summary()
