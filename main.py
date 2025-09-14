# -*- coding: utf-8 -*-
import os
import logging
import json
import re
import requests
import time
import feedparser
import html as html_lib
from time import mktime
from datetime import datetime, timezone, timedelta

# --- کتابخانه‌های هوش مصنوعی (اختیاری) ---
try:
    from google import genai
except ImportError:
    genai = None
try:
    from openai import OpenAI as OpenAIClient
except ImportError:
    OpenAIClient = None

# --- خواندن متغیرهای محرمانه ---
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
ADMIN_CHAT_ID = os.environ.get("ADMIN_CHAT_ID")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
GEMINI_MODEL_ENV = os.environ.get("GEMINI_MODEL")
OPENAI_MODEL_ENV = os.environ.get("OPENAI_MODEL", "gpt-3.5-turbo")

# --- مسیر فایل‌ها ---
DB_FILE = "bot_database.json"
URL_FILE = "urls.txt"

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')

# دسته‌بندی موضوعی
KEYWORD_CATEGORIES = {
    "🔵": ['نجوم', 'فیزیک', 'کیهان', 'کوانتوم', 'ستاره', 'کهکشان', 'سیاهچاله', 'اخترشناسی', 'سیاره', 'physics', 'astronomy', 'cosmos', 'galaxy', 'planet'],
    "🟡": ['زیست', 'ژنتیک', 'فرگشت', 'dna', 'سلول', 'مولکول', 'بیولوژی', 'تکامل', 'biology', 'evolution', 'genetic'],
    "⚫": ['هوش مصنوعی', 'یادگیری ماشین', 'شبکه عصبی', 'رباتیک', 'الگوریتم', 'دیپ لرنینگ', 'ai', 'artificial intelligence', 'machine learning'],
    "🔴": ['روانشناسی', 'جامعه شناسی', 'علوم اجتماعی', 'رفتار', 'ذهن', 'روان', 'اجتماعی', 'psychology', 'sociology', 'social'],
    "🟠": ['فلسفه', 'فلسفه علم', 'منطق', 'متافیزیک', 'اخلاق', 'philosophy']
}

# --- تنظیمات ---
MAX_AGE_DAYS = 2
MAX_AGE_SECONDS = MAX_AGE_DAYS * 24 * 60 * 60
DEFAULT_GEMINI_MODELS = ["gemini-1.5-flash", "gemini-1.5-pro"]

# --- توابع کمکی ---
def load_data():
    if os.path.exists(DB_FILE):
        try:
            with open(DB_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except (json.JSONDecodeError, FileNotFoundError):
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

def entry_age_seconds(entry):
    t = entry.get("published_parsed") or entry.get("updated_parsed")
    if t:
        entry_ts = mktime(t)
        now_ts = time.time()
        return now_ts - entry_ts
    return -1 # -۱ یعنی تاریخ موجود نیست

# --- توابع ارسال و هوش مصنوعی ---
def send_telegram_message(text):
    if not TELEGRAM_BOT_TOKEN or not ADMIN_CHAT_ID:
        logging.error("توکن تلگرام یا شناسه ادمین تنظیم نشده است.")
        return False
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": ADMIN_CHAT_ID, "text": text, "parse_mode": "HTML"}
    try:
        r = requests.post(url, json=payload, timeout=20)
        logging.info("Telegram response: %s", r.text)
        r.raise_for_status()
        return True
    except requests.exceptions.HTTPError as e:
        if "can't parse entities" in e.response.text:
            logging.warning("خطای Parse_mode، تلاش برای ارسال به صورت متن ساده...")
            payload.pop('parse_mode', None)
            try:
                r = requests.post(url, json=payload, timeout=20)
                r.raise_for_status()
                return True
            except Exception as inner_e:
                logging.error(f"ارسال به صورت متن ساده هم ناموفق بود: {inner_e}")
        return False
    except Exception as e:
        logging.error(f"خطای کلی در ارسال پیام تلگرام: {e}")
        return False

def openai_fallback(title, summary):
    if not OpenAIClient or not OPENAI_API_KEY:
        return None
    try:
        client = OpenAIClient(api_key=OPENAI_API_KEY)
        system_prompt = "You are a Persian science communicator. First, translate the user's article title to Persian. Then, on a new line, provide a simple, conceptual explanation of the article in 2-4 Persian sentences."
        user_content = f"Title: {title}\nSummary: {summary}"
        response = client.chat.completions.create(
            model=OPENAI_MODEL_ENV,
            messages=[{"role": "system", "content": system_prompt}, {"role": "user", "content": user_content}]
        )
        return response.choices[0].message.content
    except Exception as e:
        logging.error(f"فال‌بک OpenAI ناموفق بود: {e}")
        return None

def process_with_gemini(title, summary):
    if not genai or not GEMINI_API_KEY:
        logging.warning("کتابخانه یا کلید Gemini در دسترس نیست. تلاش برای فال‌بک...")
        return openai_fallback(title, summary)
    try:
        client = genai.Client(api_key=GEMINI_API_KEY)
        prompt = "You are an expert science communicator. Perform two steps based on the English text:\n1) Provide a fluent and professional Persian translation of ONLY the title.\n2) After the title, explain the core concept of the article in a few simple and conceptual Persian sentences.\n\nTitle: '{title}'\nSummary: '{summary}'\n\nOutput exactly in Persian. Structure:\n[Persian Title]\n\n[Persian explanation]".format(title=title, summary=summary)
        models_to_try = [GEMINI_MODEL_ENV] if GEMINI_MODEL_ENV else DEFAULT_GEMINI_MODELS
        for model_name in models_to_try:
            try:
                model = client.get_model(f"models/{model_name}")
                response = model.generate_content(prompt)
                return response.text
            except Exception as e:
                logging.warning(f"مدل {model_name} ناموفق بود: {e}")
        raise RuntimeError("تمام مدل‌های جمینای ناموفق بودند.")
    except Exception as e:
        logging.error(f"خطای کلی در ارتباط با جمینای: {e}. تلاش برای فال‌بک با OpenAI...")
        return openai_fallback(title, summary)

# --- تابع اصلی ربات ---
def check_news_job():
    database = load_data()
    last_sent_links = database.get("last_sent_links", {})
    logging.info("شروع چرخه بررسی اخبار...")
    try:
        with open(URL_FILE, 'r', encoding='utf-8') as f:
            urls = {line.strip() for line in f if line.strip() and not line.startswith('#')}
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

            last_sent_id_for_url = last_sent_links.get(url)
            new_articles_to_send = []
            
            for entry in feed.entries:
                entry_id = entry.get('id', entry.link)
                if not entry_id: continue
                if entry_id == last_sent_id_for_url:
                    break
                new_articles_to_send.append(entry)

            for entry in reversed(new_articles_to_send):
                age_sec = entry_age_seconds(entry)
                if age_sec != -1 and age_sec > MAX_AGE_SECONDS:
                    logging.info(f"رد کردن مقاله قدیمی: {entry.get('title')}")
                    continue

                title = entry.get("title", "(بدون عنوان)")
                summary = clean_html(entry.get("summary", "") or entry.get("description", ""))
                
                ai_output = process_with_gemini(title, summary)
                if not ai_output:
                    ai_output = f"<b>{html_lib.escape(title)}</b>\n\n(پردازش با هوش مصنوعی ناموفق بود)"

                emojis = categorize_article(f"Title: {title}. Summary: {summary}")
                message_part = f"{emojis} {ai_output}\n\n🔗 <a href='{html_lib.escape(entry.link)}'>لینک مقاله اصلی</a>"

                if send_telegram_message(message_part):
                    last_sent_links[url] = entry.get('id', entry.link)
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
