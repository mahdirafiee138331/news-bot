# -*- coding: utf-8 -*-
import os
import json
import time
import logging
import re
import requests
import feedparser
import schedule
from datetime import datetime, timedelta, timezone
from time import mktime
from urllib.parse import quote
from zoneinfo import ZoneInfo

# Optional fallback to OpenAI
try:
    import openai
    OPENAI_AVAILABLE = True
except Exception:
    OPENAI_AVAILABLE = False

# Google Gemini client
import google.generativeai as genai

# ------------- CONFIG -------------
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
ADMIN_CHAT_ID = os.environ.get("ADMIN_CHAT_ID")  # -100xxx یا @channelusername
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-1.5-flash")
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")  # اختیاری برای fallback

URL_FILE = "urls.txt"
DB_FILE = "bot_database.json"   # این فایل بین اجراها با artifact منتقل می‌شود
TIMEZONE_NAME = os.environ.get("TIMEZONE", "Asia/Tehran")
TZ = ZoneInfo(TIMEZONE_NAME)

CHECK_INTERVAL_HOURS = int(os.environ.get("CHECK_INTERVAL_HOURS", "6"))
GRACE_HOURS = int(os.environ.get("GRACE_HOURS", "6"))
MAX_ENTRIES_PER_FEED = int(os.environ.get("MAX_ENTRIES_PER_FEED", "20"))

ALLOWED_KEYWORDS = [
    "astronomy","astrophysics","cosmology","galaxy","black hole",
    "physics","quantum",
    "philosophy of science","epistemology","philosophy of mind",
    "نجوم","کیهان","کیهان‌شناسی","فیزیک","کوانتوم",
    "فلسفه علم","معرفت‌شناسی","فلسفه ذهن"
]

# ------------- LOGGING -------------
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

# ------------- AI SETUP -------------
if not GEMINI_API_KEY:
    logging.warning("GEMINI_API_KEY پیدا نشد. Gemini غیرفعال خواهد بود.")
else:
    genai.configure(api_key=GEMINI_API_KEY)

if OPENAI_API_KEY and OPENAI_AVAILABLE:
    openai.api_key = OPENAI_API_KEY

# ------------- DB helpers -------------
def load_db():
    if not os.path.exists(DB_FILE):
        return {"sent_ids": [], "daily": {}}
    with open(DB_FILE, "r", encoding="utf-8") as f:
        return json.load(f)

def save_db(db):
    with open(DB_FILE, "w", encoding="utf-8") as f:
        json.dump(db, f, ensure_ascii=False, indent=2)

# ------------- utils -------------
def clean_html(raw):
    if not raw:
        return ""
    return re.sub(r"<.*?>", "", raw)

def entry_unique_id(entry):
    return entry.get("id") or entry.get("guid") or entry.get("link")

def parse_entry_published(entry):
    try:
        if getattr(entry, "published_parsed", None):
            ts = mktime(entry.published_parsed)
            return datetime.fromtimestamp(ts, tz=timezone.utc).astimezone(TZ)
        if getattr(entry, "updated_parsed", None):
            ts = mktime(entry.updated_parsed)
            return datetime.fromtimestamp(ts, tz=timezone.utc).astimezone(TZ)
    except Exception:
        logging.debug("خطا در parse کردن تاریخ entry", exc_info=True)
    return None

def is_article_in_window(published_dt, grace_hours=GRACE_HOURS):
    if not published_dt:
        return False
    now = datetime.now(TZ)
    if published_dt.date() == now.date():
        return True
    if published_dt.date() == (now.date() - timedelta(days=1)):
        # اجازه تا حدود grace_hours پس از نیمه شب
        since_midnight = (now - datetime(now.year, now.month, now.day, tzinfo=TZ)).total_seconds()
        return since_midnight <= (grace_hours * 3600)
    return False

def allowed_by_topic(text):
    if not text:
        return False
    t = text.lower()
    for k in ALLOWED_KEYWORDS:
        if k.lower() in t:
            return True
    return False

def send_telegram(html_text):
    if not TELEGRAM_BOT_TOKEN or not ADMIN_CHAT_ID:
        logging.error("توکن تلگرام یا ADMIN_CHAT_ID تنظیم نشده‌اند.")
        return False
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": str(ADMIN_CHAT_ID),
        "text": html_text,
        "parse_mode": "HTML",
        "disable_web_page_preview": False
    }
    try:
        r = requests.post(url, data=payload, timeout=15)
        if r.status_code != 200:
            logging.error("خطای ارسال تلگرام: %s %s", r.status_code, r.text)
            return False
        return True
    except Exception:
        logging.exception("ارسال به تلگرام خطا داد")
        return False

# ------------- AI processing (Gemini with retry + OpenAI fallback) -------------
def ai_process_gemini(title, summary, max_retries=3):
    if not GEMINI_API_KEY:
        return None, None, "no_gemini_key"
    attempt = 0
    while attempt < max_retries:
        attempt += 1
        try:
            model = genai.GenerativeModel(GEMINI_MODEL)
            prompt = (
                "شما یک مروج علمی حرفه‌ای هستید.\n"
                "۱) تنها عنوان را به فارسی روان و دقیق ترجمه کنید (یک خط).\n"
                "۲) سپس در ۲-۴ جمله مفهوم اصلی مقاله را به فارسی و به صورت ساده و مفهومی توضیح دهید.\n"
                "فقط فارسی بنویسید.\n\n"
                f"Title: {title}\n\nSummary: {summary}\n\n"
                "خروجی را اینگونه برگردانید:\n[TITLE]\n\n[EXPLANATION]"
            )
            resp = model.generate_content(prompt)
            text = resp.text.strip()
            parts = [p.strip() for p in text.split("\n\n") if p.strip()]
            if len(parts) >= 2:
                title_fa = parts[0].splitlines()[0].strip()
                explanation = "\n\n".join(parts[1:]).strip()
                return title_fa, explanation, "gemini"
            # fallback parsing
            lines = [l.strip() for l in text.splitlines() if l.strip()]
            if lines:
                title_fa = lines[0]
                explanation = "\n".join(lines[1:]) if len(lines) > 1 else ""
                return title_fa, explanation, "gemini"
        except Exception as e:
            logging.warning("خطا در تماس با Gemini (attempt %s): %s", attempt, e)
            time.sleep(1 + attempt)
            continue
    return None, None, "gemini_failed"

def ai_process_openai(title, summary):
    if not OPENAI_API_KEY or not OPENAI_AVAILABLE:
        return None, None, "no_openai"
    try:
        prompt = (
            f"You are an expert science communicator. Translate the TITLE to Persian (one line) and then explain the core idea in 2-3 simple Persian sentences.\n\n"
            f"Title: {title}\nSummary: {summary}\n"
        )
        resp = openai.ChatCompletion.create(
            model="gpt-3.5-turbo",
            messages=[{"role":"user","content":prompt}],
            max_tokens=300,
            temperature=0.2
        )
        text = resp.choices[0].message.content.strip()
        parts = [p.strip() for p in text.split("\n\n") if p.strip()]
        if len(parts) >= 2:
            return parts[0].splitlines()[0].strip(), "\n\n".join(parts[1:]).strip(), "openai"
        lines = [l.strip() for l in text.splitlines() if l.strip()]
        if lines:
            return lines[0], "\n".join(lines[1:]) if len(lines)>1 else "", "openai"
    except Exception as e:
        logging.exception("OpenAI error:")
    return None, None, "openai_failed"

def process_article(title, summary):
    # try Gemini first
    title_fa, explanation, source = ai_process_gemini(title, summary)
    if title_fa:
        return title_fa, explanation, source
    # fallback to OpenAI if available
    title_fa, explanation, source = ai_process_openai(title, summary)
    if title_fa:
        return title_fa, explanation, source
    # ultimate fallback
    return title, "(پردازش AI ناموفق بود)", "fallback"

# ------------- main feed logic -------------
def check_feeds():
    logging.info("شروع بررسی فیدها")
    db = load_db()
    sent = set(db.get("sent_ids", []))
    today_key = datetime.now(TZ).date().isoformat()
    db.setdefault("daily", {})
    db["daily"].setdefault(today_key, [])

    try:
        with open(URL_FILE, "r", encoding="utf-8") as f:
            urls = [u.strip() for u in f if u.strip() and not u.strip().startswith("#")]
    except FileNotFoundError:
        logging.error("urls.txt پیدا نشد.")
        return

    for url in urls:
        logging.info("پردازش فید: %s", url)
        try:
            feed = feedparser.parse(url)
        except Exception as e:
            logging.warning("خطا در parse فید %s: %s", url, e)
            continue

        entries = feed.entries or []
        for entry in entries[:MAX_ENTRIES_PER_FEED]:
            uid = entry_unique_id(entry)
            if not uid:
                continue
            if uid in sent:
                continue

            published = parse_entry_published(entry)
            if not is_article_in_window(published):
                continue

            title = entry.get("title", "") or ""
            summary = clean_html(entry.get("summary", "") or entry.get("description", ""))

            if not allowed_by_topic(title + " " + summary):
                continue

            title_fa, explanation, src = process_article(title, summary)
            pub_str = published.strftime("%Y-%m-%d %H:%M") if published else "نامشخص"

            safe_title = title_fa.replace("<","&lt;").replace(">","&gt;")
            safe_expl = explanation.replace("<","&lt;").replace(">","&gt;")
            html_msg = f"<b>{safe_title}</b>\n\n{safe_expl}\n\n🕘 منتشر شده: {pub_str}\n\n🔗 <a href=\"{entry.get('link')}\">لینک مقاله اصلی</a>"

            ok = send_telegram(html_msg)
            if ok:
                sent.add(uid)
                db["daily"][today_key].append({"title": title_fa, "link": entry.get("link"), "time": pub_str})
                db["sent_ids"] = list(sent)
                save_db(db)
                logging.info("ارسال شد: %s (%s)", title_fa, src)
                time.sleep(2)
            else:
                logging.warning("ارسال ناموفق برای: %s", uid)

    logging.info("پایان بررسی فیدها")

# ------------- nightly summary -------------
def send_daily_summary():
    logging.info("ارسال خلاصه شبانه")
    db = load_db()
    today_key = datetime.now(TZ).date().isoformat()
    items = db.get("daily", {}).get(today_key, [])
    if not items:
        logging.info("امروز آیتمی نیست")
        return
    lines = []
    for i, it in enumerate(items, 1):
        lines.append(f"{i}- {it.get('title','(بدون عنوان)')} ({it.get('link','')})")
    text = f"سلام جناب رفیعی 🌙\nلیست مقالات منتشرشده امروز ({today_key}):\n\n" + "\n".join(lines)
    send_telegram(text)

# ------------- entrypoint -------------
if __name__ == "__main__":
    RUN_ONCE = os.environ.get("RUN_ONCE","0") == "1"
    SEND_SUMMARY = os.environ.get("SEND_DAILY_SUMMARY","0") == "1"

    if RUN_ONCE:
        if SEND_SUMMARY:
            send_daily_summary()
        else:
            check_feeds()
    else:
        check_feeds()
        schedule.every(CHECK_INTERVAL_HOURS).hours.do(check_feeds)
        schedule.every().day.at("23:59").do(send_daily_summary)
        while True:
            schedule.run_pending()
            time.sleep(5)
