for url in urls: # <-- این خط اصلاح شده است
        logging.info(f"در حال بررسی سایت: {url}")
        try:
            feed = feedparser.parse(url)
            if not feed or not feed.entries:
                logging.warning(f"فید برای سایت {url} خالی یا نامعتبر است.")
                continue
            
            for entry in reversed(feed.entries[:15]):
                entry_link = entry.get('id', entry.link)
                if last_sent_links.get(url) != entry_link:
                    title = entry.title
                    summary = clean_html(entry.summary)
                    full_content_for_cat = f"Title: {title}. Summary: {summary}"
                    emojis = categorize_article(full_content_for_cat)
                    gemini_output = process_with_gemini(title, summary)
                    message_part = f"{emojis} *{gemini_output}*\n\n[لینک مقاله اصلی]({entry.link})"
                    send_telegram_message(message_part)
                    last_sent_links[url] = entry_link
                    save_data({"last_sent_links": last_sent_links})
                    logging.info(f"مقاله جدید ارسال شد: {title}")
                    time.sleep(5)
                else:
                    break
        except Exception as e:
            logging.error(f"خطای جدی در پردازش فید {url}: {e}")
            continue

    logging.info("پایان یک چرخه بررسی.")

if name == "__main__":
    check_news_job()
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

# --- خواندن متغیرهای محرمانه از محیط گیت‌هاب ---
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
ADMIN_CHAT_ID = os.environ.get("ADMIN_CHAT_ID")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
ADMIN_NAME = "جناب رفیعی"

# --- پیکربندی جمینای ---
if GEMINI_API_KEY:
    try:
        genai.configure(api_key=GEMINI_API_KEY)
        model = genai.GenerativeModel('gemini-pro')
    except Exception as e:
        logging.error(f"خطا در پیکربندی جمینای: {e}")
else:
    logging.error("کلید API جمینای پیدا نشد!")

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
        with open(DB_FILE, 'r') as f:
            return json.load(f)
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
    new_articles_found = []
    logging.info("شروع چرخه بررسی اخبار...")
    try:
        with open(URL_FILE, 'r') as f:
            urls = [line.strip() for line in f if line.strip() and not line.startswith('#')]
    except FileNotFoundError:
        logging.warning("فایل urls.txt پیدا نشد!")
        urls = []
