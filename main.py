# -*- coding: utf-8 -*-
import os
import logging
import re
import requests
import time
import feedparser
import google.generativeai as genai
from urllib.parse import quote
from deta import Deta, App
from fastapi import FastAPI, Response

# --- تنظیمات کامل و آماده ---
TELEGRAM_BOT_TOKEN = "8324914582:AAFMgvcAXENyUxiQFWC8sErK5xxcc7KjgTk"
ADMIN_CHAT_ID = "5529925794"
GEMINI_API_KEY = "YOUR_GEMINI_API_KEY_HERE" # <<<<< کلید API جمینای خود را اینجا قرار دهید
ADMIN_NAME = "جناب رفیعی"

# --- پیکربندی جمینای ---
try:
    genai.configure(api_key=GEMINI_API_KEY)
    model = genai.GenerativeModel('gemini-pro')
except Exception as e:
    logging.error(f"خطا در پیکربندی جمینای: {e}")

# --- اتصال به دیتابیس Deta ---
deta = Deta()
db = deta.Base("bot_database")
url_db = deta.Base("rss_urls")

# --- کد اصلی ربات (بدون نیاز به تغییر) ---
app = App(FastAPI())
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
KEYWORD_CATEGORIES = {
    "🔵": ['نجوم', 'فیزیک', 'کیهان', 'کوانتوم', 'ستاره', 'کهکشان', 'سیاهچاله', 'اخترشناسی', 'سیاره', 'physics', 'astronomy', 'cosmos', 'galaxy', 'planet'],
    "🟡": ['زیست', 'ژنتیک', 'فرگشت', 'dna', 'سلول', 'مولکول', 'بیولوژی', 'تکامل', 'biology', 'evolution', 'genetic'],
    "⚫": ['هوش مصنوعی', 'یادگیری ماشین', 'شبکه عصبی', 'رباتیک', 'الگوریتم', 'دیپ لرنینگ', 'ai', 'artificial intelligence', 'machine learning'],
    "🔴": ['روانشناسی', 'جامعه شناسی', 'علوم اجتماعی', 'رفتار', 'ذهن', 'روان', 'اجتماعی', 'psychology', 'sociology', 'social'],
    "🟠": ['فلسفه', 'فلسفه علم', 'منطق', 'متافیزیک', 'اخلاق', 'philosophy']
}

def clean_html(raw_html): return re.sub(re.compile('<.*?>'), '', raw_html)
def categorize_article(text):
    text_lower = text.lower()
    emojis = ""
    for emoji, keywords in KEYWORD_CATEGORIES.items():
        if any(keyword in text_lower for keyword in keywords): emojis += emoji
    return emojis if emojis else "📰"

def send_telegram_message(text):
    encoded_text = quote(text)
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage?chat_id={ADMIN_CHAT_ID}&text={encoded_text}&parse_mode=Markdown"
    try: requests.get(url, timeout=10)
    except Exception as e: logging.error(f"خطا در ارسال پیام تلگرام: {e}")

def process_with_gemini(title, summary):
    try:
        prompt = (
            "You are an expert science communicator. Your task is to perform two steps:\n"
            "1. Read the following English title and summary of a scientific article. Then, write a fluent and professional Persian translation of ONLY the title.\n"
            "2. After translating the title, provide a conceptual and professional summary of the article in Persian, approximately 10 lines long, based on the provided text. Focus on the core meaning and findings.\n\n"
            f"Title: '{title}'\n"
            f"Summary: '{summary}'\n\n"
            "Your final output should be in Persian and structured like this:\n"
            "[Persian Title]\n\n"
            "[10-line Persian Summary]"
        )
        response = model.generate_content(prompt, request_options={'timeout': 100})
        return response.text
    except Exception as e:
        logging.error(f"خطا در ارتباط با جمینای: {e}")
        return f"{title}\n\n(خلاصه‌سازی با جمینای ناموفق بود)"

def check_news():
    last_sent_links_entry = db.get("last_sent_links")
    last_sent_links = last_sent_links_entry['value'] if last_sent_links_entry else {}
    urls_entry = url_db.fetch().items
    urls = [item['key'] for item in urls_entry]
    new_articles_found = []
    logging.info(f"شروع به کار ربات... {len(urls)} سایت برای بررسی وجود دارد.")
    for url in urls:
        logging.info(f"در حال بررسی سایت: {url}")
        try:
            feed = feedparser.parse(url)
            if not feed or not feed.entries:
                logging.warning(f"فید برای سایت {url} خالی یا نامعتبر است.")
                continue
            for entry in reversed(feed.entries[:15]):
                entry_link = entry.link
                if last_sent_links.get(url) != entry_link and entry_link not in [a['link'] for a in new_articles_found]:
                    title = entry.title
                    summary = clean_html(entry.summary)
                    full_content_for_cat = f"Title: {title}. Summary: {summary}"
                    emojis = categorize_article(full_content_for_cat)
                    gemini_output = process_with_gemini(title, summary)
                    message_part = f"{emojis} *{gemini_output}*\n\n[لینک مقاله اصلی]({entry_link})"
                    new_articles_found.append({'link': entry_link, 'text': message_part})
                    logging.info(f"مقاله جدید پیدا شد: {title}")
            if feed.entries: last_sent_links[url] = feed.entries[0].link
        except Exception as e:
            logging.error(f"خطای جدی در پردازش فید {url}: {e}")
            continue
    if new_articles_found:
        header = f"📬 **بسته‌ی خبری هوشمند (با جمینای) برای شما، {ADMIN_NAME} عزیز:**\n\n---"
        send_telegram_message(header)
        for article in new_articles_found:
            send_telegram_message(article['text'])
            time.sleep(2)
        logging.info("بسته‌ی خبری با موفقیت ارسال شد.")
    else: logging.info("مقاله جدیدی برای ارسال یافت نشد.")
    db.put(last_sent_links, "last_sent_links")
    logging.info("کار ربات تمام شد.")
    return "Job finished."

@app.lib.cron()
def scheduled_task(event): return check_news()
@app.post("/webhook")
async def webhook(request: dict):
    if "message" not in request: return {"status": "not a message"}
    message = request["message"]
    chat_id = message["chat"]["id"]
    text = message.get("text", "")
    if str(chat_id) != ADMIN_CHAT_ID: return {"status": "unauthorized"}
    if text.startswith("/اضافه"):
        parts = text.split(" ", 1)
        if len(parts) > 1 and parts[1].startswith("http"):
            url_db.put({"key": parts[1]})
            send_telegram_message(f"✅ سایت `{parts[1]}` با موفقیت اضافه شد.")
        else: send_telegram_message("❌ فرمت دستور اشتباه است. مثال: `/اضافه https://example.com/rss`")
    elif text.startswith("/حذف"):
        if len(text.split(" ", 1)) > 1:
            url_db.delete(text.split(" ", 1)[1])
            send_telegram_message(f"🗑️ سایت `{text.split(' ', 1)[1]}` حذف شد.")
    elif text == "/لیست":
        urls_entry = url_db.fetch().items
        if not urls_entry: send_telegram_message("لیست سایت‌های شما خالی است.")
        else:
            url_list = "\n".join([f"`{item['key']}`" for item in urls_entry])
            send_telegram_message(f"📜 **لیست سایت‌ها:**\n{url_list}")
    elif text == "/start" or text == "/راهنما":
        send_telegram_message("📖 **دستورات ربات:**\n\n`/اضافه [لینک]`\n`/حذف [لینک]`\n`/لیست`")
    return Response(status_code=200)