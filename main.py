# -*- coding: utf-8 -*-
import os
import logging
import json
import re
import requests
import time
import feedparser
import google.generativeai as genai
from urllib.parse import quote
import schedule

# --- خواندن متغیرهای محرمانه از محیط Railway ---
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
ADMIN_CHAT_ID = os.environ.get("ADMIN_CHAT_ID")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
ADMIN_NAME = "جناب رفیعی"

# --- پیکربندی جمینای ---
# این بخش باید بعد از خواندن متغیرها باشد
if GEMINI_API_KEY:
    try:
        genai.configure(api_key=GEMINI_API_KEY)
        model = genai.GenerativeModel('gemini-pro')
    except Exception as e:
        logging.error(f"خطا در پیکربندی جمینای: {e}")
else:
    logging.error("کلید API جمینای پیدا نشد!")

# --- مسیر فایل‌ها ---
DB_FILE = "/tmp/bot_database.json" # از حافظه موقت Railway استفاده می‌کنیم
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
    try:
        with open(DB_FILE, 'r') as f:
            return json.load(f)
    except FileNotFoundError:
        return {"last_sent_links": {}}

def save_data(data):
    with open(DB_FILE, 'w') as f:
        json.dump(data, f, indent=4)

def clean_html(raw_html):
    return re.sub(re.compile('<.*?>'), '', raw_html)

def categorize_article(text):
    text_lower = text.lower()
    emojis = ""
    for emoji, keywords in KEYWORD_CATEGORIES.items():
        if any(keyword in text_lower for keyword in keywords):
            emojis += emoji
    return emojis if emojis else "📰"

def send_telegram_message(text):
    encoded_text = quote(text)
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage?chat_id={ADMIN_CHAT_ID}&text={encoded_text}&parse_mode=Markdown"
    try:
        requests.get(url, timeout=10)
    except Exception as e:
        logging.error(f"خطا در ارسال پیام تلگرام: {e}")

def process_with_gemini(title, summary):
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
        return f"{title}\n\n(پردازش با جمینای ناموفق بود)"

def check_news_job():
    database = load_data()
    last_sent_links = database.get("last_sent_links", {})
    
    logging.info("شروع چرخه بررسی اخبار...")
    try:
        with open(URL_FILE, 'r') as f:
            urls = [line.strip() for line in f if line.strip() and not line.startswith('#')]
    except FileNotFoundError:
        logging.warning("فایل urls.txt پیدا نشد!")
        urls = []
for url in urls:
        logging.info(f"در حال بررسی سایت: {url}")
        new_articles_found = []
        try:
            feed = feedparser.parse(url)
            if not feed or not feed.entries:
                logging.warning(f"فید برای سایت {url} خالی یا نامعتبر است.")
                continue
            
            for entry in reversed(feed.entries[:15]): # تا ۱۵ مقاله آخر هر فید را چک می‌کند
                entry_link = entry.get('id', entry.link) # استفاده از ID در صورت وجود
                
                if last_sent_links.get(url) != entry_link:
                    title = entry.title
                    summary = clean_html(entry.summary)
                    full_content_for_cat = f"Title: {title}. Summary: {summary}"
                    emojis = categorize_article(full_content_for_cat)
                    gemini_output = process_with_gemini(title, summary)
                    message_part = f"{emojis} *{gemini_output}*\n\n[لینک مقاله اصلی]({entry.link})"
                    new_articles_found.append(message_part)
                    
                    logging.info(f"مقاله جدید پیدا شد: {title}")
                else:
                    # وقتی به آخرین مقاله ارسال شده رسیدیم، بقیه قدیمی‌تر هستند
                    break

            # ارسال مقالات پیدا شده برای این سایت
            if new_articles_found:
                header = f"📬 **اخبار جدید از سایت {url.split('//')[1].split('/')[0]} برای شما، {ADMIN_NAME} عزیز:**\n\n---"
                send_telegram_message(header)
                for article_text in new_articles_found:
                    send_telegram_message(article_text)
                    time.sleep(3)
            
            # بروزرسانی آخرین لینک دیده شده برای این سایت
            if feed.entries:
                last_sent_links[url] = feed.entries[0].get('id', feed.entries[0].link)

        except Exception as e:
            logging.error(f"خطای جدی در پردازش فید {url}: {e}")
            continue
    
    # ذخیره نهایی دیتابیس بعد از بررسی همه سایت‌ها
    save_data({"last_sent_links": last_sent_links})
    logging.info("پایان یک چرخه بررسی.")

if name == "__main__":
    logging.info("ربات در حال راه‌اندازی برای اجرای دائمی است...")
    # زمان‌بندی برای اجرا هر ۴ ساعت
    schedule.every(4).hours.do(check_news_job)
    
    # اجرای اولیه برای تست در شروع کار
    check_news_job()
    
    while True:
        schedule.run_pending()
        time.sleep(60) # هر دقیقه یک‌بار چک می‌کند که آیا زمان اجرای وظیفه رسیده یا نه
