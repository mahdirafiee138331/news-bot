# -*- coding: utf-8 -*-
import os
import logging
import json
import re
import requests
import time
import feedparser
import google.generativeai as genai
import html as html_lib # استفاده از کتابخانه html برای امنیت بیشتر

# --- خواندن متغیرهای محرمانه ---
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
ADMIN_CHAT_ID = os.environ.get("ADMIN_CHAT_ID")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
ADMIN_NAME = os.environ.get("ADMIN_NAME", "جناب رفیعی")

# --- پیکربندی جمینای ---
model = None
if GEMINI_API_KEY:
    try:
        genai.configure(api_key=GEMINI_API_KEY)
        model = genai.GenerativeModel('gemini-pro')
    except Exception as e:
        logging.error(f"خطا در پیکربندی جمینای: {e}")

# --- مسیر فایل‌ها ---
DB_FILE = "bot_database.json"
URL_FILE = "urls.txt"

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')

KEYWORD_CATEGORIES = {
    "🔵": ['نجوم', 'فیزیک', 'کیهان', 'کوانتوم', 'ستاره', 'کهکشان', 'سیاهچاله', 'اخترشناسی', 'سیاره', 'physics', 'astronomy', 'cosmos', 'galaxy', 'planet'],
    "🟡": ['زیست', 'ژنتیک', 'فرگشت', 'dna', 'سلول', 'مولکول', 'بیولوژی', 'تکامل', 'biology', 'evolution', 'genetic'],
    "⚫": ['هوش مصنوعی', 'یادگیری ماشین', 'شبکه عصبی', 'رباتیک', 'الگوریتم', 'دیپ لرنینگ', 'ai', 'artificial intelligence', 'machine learning'],
    "🔴": ['روانشناسی', 'جامعه شناسی', 'علوم اجتماعی', 'رفتار', 'ذهن', 'روان', 'اجتماعی', 'psychology', 'sociology', 'social'],
    "🟠": ['فلسفه', 'فلسفه علم', 'منطق', 'متافیزیک', 'اخلاق', 'philosophy']
}

def load_data():
    if os.path.exists(DB_FILE):
        try:
            with open(DB_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except (json.JSONDecodeError, FileNotFoundError):
            logging.warning("فایل دیتابیس خراب یا پیدا نشد. یک فایل جدید ساخته می‌شود.")
            return {"last_sent_links": {}}
    return {"last_sent_links": {}}

def save_data(data):
    try:
        with open(DB_FILE, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=4)
    except Exception as e:
        logging.error(f"خطا در ذخیره‌سازی داده‌ها: {e}")

def clean_html(raw_html):
    return re.sub(re.compile('<.*?>'), '', raw_html or "")

def categorize_article(text):
    text_lower = (text or "").lower()
    emojis = ""
    for emoji, keywords in KEYWORD_CATEGORIES.items():
        if any(keyword in text_lower for keyword in keywords):
            emojis += emoji
    return emojis if emojis else "📰"

def send_telegram_message(text):
    if not TELEGRAM_BOT_TOKEN or not ADMIN_CHAT_ID:
        logging.error("توکن تلگرام یا شناسه ادمین تنظیم نشده است.")
        return False

    send_url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": ADMIN_CHAT_ID,
        "text": text,
        "parse_mode": "HTML",
    }
    try:
        r = requests.post(send_url, json=payload, timeout=15)
        logging.info("Telegram send status: %s", r.status_code)
        logging.info("Telegram response: %s", r.text)
        r.raise_for_status()
        return True
    except Exception as e:
        logging.error(f"خطا در ارسال پیام تلگرام: {e}")
        return False

def process_with_gemini(title, summary):
    if model is None:
        logging.warning("مدل جمینای مقداردهی نشده؛ خروجی fallback ارسال می‌شود.")
        return f"<b>{html_lib.escape(title)}</b>\n\n(پردازش با جمینای در دسترس نیست)"

    try:
        prompt = (
            "You are an expert science communicator. Your task is to perform two steps based on the following English text:\n"
            "1. Provide a fluent and professional Persian translation of ONLY the title.\n"
            "2. After the title, explain the core concept of the article in a few simple and conceptual Persian sentences, as if you are explaining it to an expert student (like شاگردم in Persian).\n\n"
            f"Title: '{title}'\n"
            f"Summary: '{summary}'\n\n"
            "Your final output must be in Persian and structured exactly like this:\n"
            "[Persian Title]\n\n"
            "[Simple and conceptual Persian explanation]"
        )
        response = model.generate_content(prompt)
        return response.text
    except Exception as e:
        logging.error(f"خطا در ارتباط با جمینای: {e}")
        return f"<b>{html_lib.escape(title)}</b>\n\n(پردازش با جمینای ناموفق بود)"

def check_news_job():
    database = load_data()
    last_sent_links = database.get("last_sent_links", {})
    
    logging.info("شروع چرخه بررسی اخبار...")
    try:
        with open(URL_FILE, 'r', encoding='utf-8') as f:
            urls = [line.strip() for line in f if line.strip() and not line.startswith('#')]
    except FileNotFoundError:
        logging.warning("فایل urls.txt پیدا نشد!")
        urls = []

    for url in urls:
        logging.info(f"در حال بررسی سایت: {url}")
        try:
            feed = feedparser.parse(url)
            if not feed or not feed.entries:
                logging.warning(f"فید برای سایت {url} خالی یا نامعتبر است.")
                continue
            
            for entry in reversed(feed.entries[:15]):
                entry_id = entry.get('id', entry.link)
                if last_sent_links.get(url) == entry_id:
                    continue
                
                title = entry.get("title", "(بدون عنوان)")
                summary = clean_html(entry.get("summary", "") or entry.get("description", ""))
                full_content_for_cat = f"Title: {title}. Summary: {summary}"
                emojis = categorize_article(full_content_for_cat)
                
                gemini_output_raw = process_with_gemini(title, summary)
                # آماده‌سازی متن برای ارسال به صورت HTML
                gemini_output_escaped = html_lib.escape(gemini_output_raw)
                message_part = f"{emojis} <b>{gemini_output_escaped.splitlines()[0]}</b>\n\n"
                if len(gemini_output_escaped.splitlines()) > 1:
                    message_part += "\n".join(gemini_output_escaped.splitlines()[1:])
                
                message_part += f"\n\n🔗 <a href='{html_lib.escape(entry.link)}'>لینک مقاله اصلی</a>"

                if send_telegram_message(message_part):
                    last_sent_links[url] = entry_id
                    logging.info(f"مقاله جدید ارسال شد: {title}")
                    time.sleep(5)
                else:
                    logging.error(f"ارسال پیام برای مقاله {title} ناموفق بود.")
                    break
        except Exception as e:
            logging.error(f"خطای جدی در پردازش فید {url}: {e}")
            continue
    
    save_data({"last_sent_links": last_sent_links})
    logging.info("پایان یک چرخه بررسی.")

if __name__ == "__main__":
    check_news_job()
