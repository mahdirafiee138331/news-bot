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
import html as html_lib

# --- خواندن متغیرهای محرمانه ---
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
ADMIN_CHAT_ID = os.environ.get("ADMIN_CHAT_ID")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
ADMIN_NAME = os.environ.get("ADMIN_NAME", "جناب رفیعی")

# --- پیکربندی جمینای ---
try:
    genai.configure(api_key=GEMINI_API_KEY)
    model = genai.GenerativeModel('gemini-pro')
except Exception as e:
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    logging.error(f"خطا در پیکربندی جمینای: {e}")
    model = None

# --- مسیر فایل‌ها (در Railway موقتی هستند) ---
DB_FILE = os.environ.get("DB_FILE", "/tmp/bot_database.json")
URL_FILE = os.environ.get("URL_FILE", "urls.txt")

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
        with open(DB_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    except FileNotFoundError:
        return {"last_sent_links": {}}
    except json.JSONDecodeError:
        logging.warning("فایل دیتابیس خراب است — مقداردهی اولیه مجدد انجام می‌شود.")
        return {"last_sent_links": {}}


def save_data(data):
    try:
        with open(DB_FILE, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=4)
    except Exception as e:
        logging.error(f"خطا در ذخیره‌سازی داده‌ها: {e}")


def clean_html(raw_html):
    if not raw_html:
        return ""
    # حذف تگ‌های HTML به صورت non-greedy
    return re.sub(re.compile('<.*?>'), '', raw_html)


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
    # استفاده از HTML mode برای ساده‌تر شدن escape
    payload = {
        "chat_id": ADMIN_CHAT_ID,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": False
    }
    try:
        r = requests.post(send_url, json=payload, timeout=15)
        r.raise_for_status()
        return True
    except Exception as e:
        logging.error(f"خطا در ارسال پیام تلگرام: {e} - response: {getattr(e, 'response', None)}")
        return False


def process_with_gemini(title, summary):
    # اگر مدل تنظیم نشده بود، fallback
    if model is None:
        logging.warning("مدل جمینای مقداردهی نشده؛ خروجی fallback ارسال می‌شود.")
        return f"{html_lib.escape(title)}\n\n(پردازش با جمینای در دسترس نیست)"

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
        # استفاده از API جی‌پی‌تی/جمینای
        response = model.generate_content(prompt)
        # فرض بر این است که response.text حاوی متن نهایی است
        return response.text
    except Exception as e:
        logging.error(f"خطا در ارتباط با جمینای: {e}")
        return f"{html_lib.escape(title)}\n\n(پردازش با جمینای ناموفق بود)"


def check_news_job():
    database = load_data()
    last_sent_links = database.get("last_sent_links", {})

    logging.info("شروع یک چرخه بررسی فیدها...")
    try:
        with open(URL_FILE, 'r', encoding='utf-8') as f:
            urls = [line.strip() for line in f if line.strip()]
    except FileNotFoundError:
        logging.warning("فایل urls.txt پیدا نشد! یک فایل urls.txt در گیت‌هاب خود بسازید و لینک‌ها را در آن قرار دهید.")
        urls = []
    except Exception as e:
        logging.error(f"خطا در خواندن فایل URLs: {e}")
        urls = []

    # حذف تکراری‌ها در لیست URL
    seen = set()
    filtered_urls = []
    for u in urls:
        if u in seen:
            continue
        seen.add(u)
        filtered_urls.append(u)

    for url in filtered_urls:
        logging.info(f"در حال بررسی سایت: {url}")
        try:
            # تنظیم هدر برای جلوگیری از بلاک شدن
            headers = {"User-Agent": "Telegram-RSS-Bot/1.0 (+https://example.org)"}
            # از feedparser برای تشخیص خودکار استفاده می‌کنیم
            feed = feedparser.parse(url)
            if not feed or not getattr(feed, "entries", None):
                logging.warning(f"فید برای سایت {url} خالی یا نامعتبر است.")
                continue

            # بررسی آخرین 15 ورودی (از آخرین به قدیم)
            for entry in reversed(feed.entries[:15]):
                entry_id = getattr(entry, "id", None) or getattr(entry, "guid", None) or getattr(entry, "link", None)
                entry_link = getattr(entry, "link", None)
                if not entry_id:
                    logging.debug("ورودی بدون id/link یافت شد — از آن عبور می‌کنیم.")
                    continue

                # بررسی اینکه آیا قبلاً ارسال شده است
                already_sent = last_sent_links.get(url)
                if already_sent == entry_id:
                    # اگر همان لینک آخر است، بقیه را هم چک می‌کنیم (ممکنه موارد جدیدتر قبل از این بوده باشند)
                    continue

                title = getattr(entry, "title", "(بدون عنوان)")
                summary_raw = getattr(entry, "summary", "") or getattr(entry, "description", "")
                summary = clean_html(summary_raw)
                full_content_for_cat = f"Title: {title}. Summary: {summary}"
                emojis = categorize_article(full_content_for_cat)

                # پردازش با جمینا (ترجمه عنوان + توضیح مفهومی)
                gemini_output = process_with_gemini(title, summary)

                # آماده‌سازی متن برای ارسال به تلگرام با HTML-escaping
                # gemini_output ممکن است شامل چند خط باشد؛ هرچه هست فرار داده می‌شود
                escaped_output = html_lib.escape(gemini_output).replace("\n", "<br>")
                message_part = f"{emojis} <b>{escaped_output}</b>\n\n"
                if entry_link:
                    message_part += f"🔗 <a href=\"{html_lib.escape(entry_link)}\">لینک مقاله اصلی</a>"

                sent = send_telegram_message(message_part)
                if sent:
                    # ذخیره وضعیت: برای هر URL آخرین entry_id را نگه می‌داریم
                    last_sent_links[url] = entry_id
                    database["last_sent_links"] = last_sent_links
                    save_data(database)
                    logging.info(f"مقاله جدید ارسال شد: {title}")
                    # جلوگیری از اسپم و ریت لیمیت تلگرام
                    time.sleep(4)
                else:
                    logging.error(f"ارسال پیام برای {entry_link} ناموفق بود.")

        except Exception as e:
            logging.error(f"خطای جدی در پردازش فید {url}: {e}")
            continue

    logging.info("پایان یک چرخه بررسی.")


if __name__ == "__main__":
    logging.info("ربات در حال راه‌اندازی...")

    # اگر در محیط GitHub Actions اجرا می‌شود یا متغیر RUN_ONCE=1 تنظیم شده،
    # فقط یک بار چک کن و خارج شو (این برای workflow های زمانبندی‌شده مناسب است)
    if os.environ.get("GITHUB_ACTIONS") == "true" or os.environ.get("RUN_ONCE") == "1":
        check_news_job()
    else:
        # اجرای محلی دائمی با schedule (هر 6 ساعت)
        check_news_job()  # اجرای اولیه
        schedule.every(6).hours.do(check_news_job)
        while True:
            schedule.run_pending()
            time.sleep(1)
