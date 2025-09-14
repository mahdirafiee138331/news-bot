# -*- coding: utf-8 -*-
"""
main.py — Telegram RSS bot (final)
ویژگی‌ها:
- خواندن فیدها از urls.txt (هر خط یک URL)
- فیلتر مقالات قدیمی‌تر از MAX_AGE_DAYS (پیش‌فرض 2 روز)
- تولید عنوان فارسی + توضیح مفهومی با Gemini (در اولویت) و فقط در صورت شکست fallback به OpenAI
- جلوگیری از ارسالِ تکراری در یک run (cross-feed dedupe) و جلوگیری از ارسال مجدد بر اساس last_sent_links ذخیره‌شده
- درج تاریخ انتشار در کپشن پیام تلگرام
- لاگ‌های تشخیصی برای فهمیدن اینکه کدام backend پاسخ داده
"""

import os
import logging
import json
import re
import requests
import time
import feedparser
import html as html_lib
from time import mktime
from datetime import datetime, timezone

# Optional: try import GenAI (google-genai or google.generativeai)
genai = None
try:
    from google import genai as genai  # preferred new package
except Exception:
    try:
        import google.generativeai as genai  # older package name
    except Exception:
        genai = None

# Optional: try import OpenAI (new or legacy)
_openai_lib = None
OpenAIClient = None
try:
    from openai import OpenAI as OpenAIClient  # new client API
    _openai_lib = "new"
except Exception:
    try:
        import openai as _openai_module  # legacy
        _openai_lib = "legacy"
        OpenAIClient = None
    except Exception:
        _openai_lib = None

# ----------------- config / env -----------------
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
ADMIN_CHAT_ID = os.environ.get("ADMIN_CHAT_ID")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
GEMINI_MODEL_ENV = os.environ.get("GEMINI_MODEL")
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
OPENAI_MODEL = os.environ.get("OPENAI_MODEL", "gpt-3.5-turbo")

DB_FILE = os.environ.get("DB_FILE", "/tmp/bot_database.json")
URL_FILE = os.environ.get("URL_FILE", "urls.txt")

MAX_AGE_DAYS = int(os.environ.get("MAX_AGE_DAYS", "2"))
MAX_AGE_SECONDS = MAX_AGE_DAYS * 24 * 3600

DEFAULT_GENAI_MODELS = [
    "gemini-2.5-pro",
    "gemini-2.5-flash",
    "gemini-1.5-flash",
    "gemini-1.5-pro",
    "gemini-pro"
]

# logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# categories (emoji)
KEYWORD_CATEGORIES = {
    "🔵": ['نجوم', 'فیزیک', 'کیهان', 'کوانتوم', 'ستاره', 'کهکشان', 'سیاهچاله', 'اخترشناسی', 'سیاره', 'physics', 'astronomy', 'cosmos', 'galaxy', 'planet'],
    "🟡": ['زیست', 'ژنتیک', 'فرگشت', 'dna', 'سلول', 'مولکول', 'بیولوژی', 'تکامل', 'biology', 'evolution', 'genetic'],
    "⚫": ['هوش مصنوعی', 'یادگیری ماشین', 'شبکه عصبی', 'رباتیک', 'الگوریتم', 'دیپ لرنینگ', 'ai', 'artificial intelligence', 'machine learning'],
    "🔴": ['روانشناسی', 'جامعه شناسی', 'علوم اجتماعی', 'رفتار', 'ذهن', 'روان', 'اجتماعی', 'psychology', 'sociology', 'social'],
    "🟠": ['فلسفه', 'فلسفه علم', 'منطق', 'متافیزیک', 'اخلاق', 'philosophy']
}

# ----------------- utils: DB, html clean -----------------

def load_data():
    try:
        with open(DB_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    except FileNotFoundError:
        return {"last_sent_links": {}}
    except json.JSONDecodeError:
        logging.warning("DB file corrupted; reinitializing.")
        return {"last_sent_links": {}}

def save_data(data):
    try:
        with open(DB_FILE, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logging.exception("Error saving DB: %s", e)

def clean_html(raw_html):
    if not raw_html:
        return ""
    return re.sub(r'<.*?>', '', raw_html)

def categorize_article(text):
    text_lower = (text or "").lower()
    emojis = ""
    for emoji, keywords in KEYWORD_CATEGORIES.items():
        if any(kw in text_lower for kw in keywords):
            emojis += emoji
    return emojis or "📰"

# ----------------- Telegram send (accept chat_id) -----------------

def send_telegram_message(text, chat_id=None, parse_mode="HTML", disable_web_page_preview=False):
    if not TELEGRAM_BOT_TOKEN:
        logging.error("TELEGRAM_BOT_TOKEN is not set.")
        return False
    target = chat_id or ADMIN_CHAT_ID
    if not target:
        logging.error("No chat_id provided and ADMIN_CHAT_ID not set.")
        return False
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": str(target),
        "text": text,
        "disable_web_page_preview": disable_web_page_preview
    }
    if parse_mode:
        payload["parse_mode"] = parse_mode
    try:
        r = requests.post(url, json=payload, timeout=15)
        logging.info("Telegram send status=%s chat=%s", r.status_code, target)
        # log response text on non-200 for debugging
        if r.status_code != 200:
            logging.warning("Telegram response: %s", r.text)
        r.raise_for_status()
        return True
    except requests.RequestException as e:
        logging.error("Telegram send failed: %s", getattr(e, "response", None))
        try:
            if e.response is not None:
                logging.error("Telegram response text: %s", e.response.text)
        except Exception:
            pass
        return False
    except Exception as e:
        logging.exception("Unexpected error sending telegram: %s", e)
        return False

# ----------------- feed fetching helper -----------------

def fetch_and_parse_feed(url):
    """
    Use requests (with UA) then feedparser.parse on content to better handle redirects/headers.
    Returns parsed feed object.
    """
    headers = {"User-Agent": "Telegram-RSS-Bot/1.0 (+https://example.org)"}
    try:
        r = requests.get(url, headers=headers, timeout=15)
        r.raise_for_status()
        parsed = feedparser.parse(r.content)
        return parsed
    except Exception as e:
        logging.warning("Failed to fetch feed via requests for %s: %s. Trying feedparser directly.", url, e)
        # fallback: let feedparser try fetching itself
        try:
            return feedparser.parse(url)
        except Exception as e2:
            logging.error("feedparser direct parse failed for %s: %s", url, e2)
            return None

# ----------------- entry age helper -----------------

def entry_age_seconds(entry):
    """
    returns seconds since published, or -1 if no date available / can't parse.
    Uses published_parsed or updated_parsed provided by feedparser.
    """
    t = getattr(entry, "published_parsed", None) or getattr(entry, "updated_parsed", None)
    if t:
        try:
            entry_ts = mktime(t)
            return time.time() - entry_ts
        except Exception:
            return -1
    return -1

# ----------------- GenAI (Gemini) wrapper -----------------

_genai_client = None

def init_genai_client():
    global _genai_client
    if _genai_client is not None:
        return _genai_client
    if genai is None:
        logging.info("genai library not available.")
        return None
    if not GEMINI_API_KEY:
        logging.info("GEMINI_API_KEY not set.")
        return None
    try:
        # new google-genai client
        if hasattr(genai, "Client"):
            _genai_client = genai.Client(api_key=GEMINI_API_KEY)
            return _genai_client
        # older google.generativeai style
        if hasattr(genai, "configure"):
            genai.configure(api_key=GEMINI_API_KEY)
            _genai_client = genai
            return _genai_client
    except Exception as e:
        logging.exception("init_genai_client failed: %s", e)
        return None
    return None

def genai_generate(title, summary):
    """
    Try to generate Persian title + explanation using Gemini.
    Returns textual output (raw) or raises.
    """
    client = init_genai_client()
    if client is None:
        raise RuntimeError("GenAI client unavailable")
    prompt = (
        "You are an expert science communicator. Perform two steps:\n"
        "1) Translate ONLY the title to fluent Persian (one short line).\n"
        "2) After the title, explain the core idea in 2-4 concise Persian sentences as if teaching an advanced student.\n\n"
        f"Title: {title}\nSummary: {summary}\n\nOutput: Persian title, blank line, explanation."
    )
    candidates = []
    if GEMINI_MODEL_ENV:
        candidates.append(GEMINI_MODEL_ENV)
    candidates += DEFAULT_GENAI_MODELS

    last_exc = None
    for model_id in candidates:
        if not model_id:
            continue
        try:
            logging.info("GenAI trying model: %s", model_id)
            # new client pattern
            if hasattr(client, "models") and hasattr(client.models, "generate_content"):
                resp = client.models.generate_content(model=model_id, contents=prompt)
                text = getattr(resp, "text", None) or getattr(resp, "content", None) or str(resp)
                return text
            # older google.generativeai pattern
            if hasattr(client, "GenerativeModel"):
                model = client.GenerativeModel(model_id)
                resp = model.generate_content(prompt)
                text = getattr(resp, "text", None) or str(resp)
                return text
            # fallback: try client.generate if exists
            if hasattr(client, "generate"):
                resp = client.generate(prompt)
                return str(resp)
        except Exception as e:
            logging.warning("GenAI model %s failed: %s", model_id, e)
            last_exc = e
            continue
    raise RuntimeError(f"All GenAI attempts failed. Last error: {last_exc}")

# ----------------- OpenAI (ChatGPT) wrapper -----------------

_openai_client = None

def init_openai_client():
    global _openai_client
    if _openai_client is not None:
        return _openai_client
    if _openai_lib is None:
        logging.info("openai package not available.")
        return None
    try:
        if _openai_lib == "new" and OpenAIClient:
            _openai_client = OpenAIClient(api_key=OPENAI_API_KEY)
            return _openai_client
        elif _openai_lib == "legacy":
            import openai as _m
            _m.api_key = OPENAI_API_KEY
            _openai_client = _m
            return _openai_client
    except Exception as e:
        logging.exception("init_openai_client failed: %s", e)
        return None
    return None

def openai_generate(title, summary, max_retries=1):
    """
    Use OpenAI to produce Persian title + explanation.
    Returns (title, explanation) or raises.
    """
    client = init_openai_client()
    if client is None:
        raise RuntimeError("OpenAI client unavailable")
    system_prompt = (
        "You are an expert Persian science communicator. Given an English title and short English summary:\n"
        "1) Translate ONLY the title to fluent Persian (one short line).\n"
        "2) Then produce a concise conceptual explanation in Persian (2-4 sentences).\n"
        "Return Persian title on first line, blank line, then explanation."
    )
    user_content = f"Title: {title}\nSummary: {summary}"
    # new client
    if _openai_lib == "new" and OpenAIClient:
        for attempt in range(max_retries + 1):
            try:
                resp = client.chat.completions.create(
                    model=OPENAI_MODEL,
                    messages=[{"role": "system", "content": system_prompt},
                              {"role": "user", "content": user_content}],
                    temperature=0.2,
                    max_tokens=500
                )
                try:
                    content = resp.choices[0].message["content"]
                except Exception:
                    try:
                        content = resp.choices[0].message.content
                    except Exception:
                        content = str(resp)
                parts = [p.strip() for p in content.split("\n\n") if p.strip()]
                if len(parts) >= 2:
                    return parts[0], "\n\n".join(parts[1:])
                elif len(parts) == 1:
                    lines = parts[0].splitlines()
                    return lines[0].strip(), "\n".join(lines[1:]).strip() or "(توضیحی فراهم نشد)"
                else:
                    return title, "(OpenAI responded empty)"
            except Exception as e:
                logging.warning("OpenAI attempt %d failed: %s", attempt+1, e)
                time.sleep(1 + attempt*2)
                continue
        raise RuntimeError("OpenAI all attempts failed.")
    # legacy
    elif _openai_lib == "legacy":
        m = client
        for attempt in range(max_retries + 1):
            try:
                resp = m.ChatCompletion.create(
                    model=OPENAI_MODEL,
                    messages=[{"role": "system", "content": system_prompt},
                              {"role": "user", "content": user_content}],
                    temperature=0.2,
                    max_tokens=500
                )
                content = resp.choices[0].message["content"]
                parts = [p.strip() for p in content.split("\n\n") if p.strip()]
                if len(parts) >= 2:
                    return parts[0], "\n\n".join(parts[1:])
                elif len(parts) == 1:
                    lines = parts[0].splitlines()
                    return lines[0].strip(), "\n".join(lines[1:]).strip() or "(توضیحی فراهم نشد)"
                else:
                    return title, "(OpenAI responded empty)"
            except Exception as e:
                logging.warning("OpenAI legacy attempt %d failed: %s", attempt+1, e)
                time.sleep(1 + attempt*2)
                continue
        raise RuntimeError("OpenAI legacy all attempts failed.")
    else:
        raise RuntimeError("No OpenAI client available")

# ----------------- high-level processing (single backend then fallback) -----------------

def process_article_with_ai(title, summary):
    """
    Try GenAI first. If it returns a valid result, DO NOT call OpenAI.
    If GenAI fails, then try OpenAI. Finally fallback to escaped title.
    Returns: (translated_title, explanation, used_backend)
    used_backend in {"genai","openai","fallback"}
    """
    logging.info("Processing article (AI): %s", title)
    # try GenAI
    try:
        logging.info("Attempt GenAI...")
        raw = genai_generate(title, summary)
        logging.info("GenAI returned length=%d", len(raw) if raw else 0)
        if raw:
            parts = [p.strip() for p in raw.split("\n\n") if p.strip()]
            if len(parts) >= 2:
                return parts[0], "\n\n".join(parts[1:]), "genai"
            elif len(parts) == 1:
                lines = parts[0].splitlines()
                return lines[0].strip(), "\n".join(lines[1:]).strip() or "(توضیحی فراهم نشد)", "genai"
    except Exception as e:
        logging.warning("GenAI failed: %s", e)

    # fallback: OpenAI
    try:
        logging.info("Attempt OpenAI fallback...")
        t, e = openai_generate(title, summary)
        logging.info("OpenAI returned title length=%d", len(t) if t else 0)
        if t:
            return t, e or "(بدون توضیح)", "openai"
    except Exception as e:
        logging.warning("OpenAI fallback failed: %s", e)

    # final fallback
    logging.info("Using final fallback (escaped english title).")
    return html_lib.escape(title), "(پردازش AI ناموفق بود)", "fallback"

# ----------------- main job: read feeds & send -----------------

def check_news_job():
    logging.info("Starting check_news_job. GEMINI present=%s, OPENAI present=%s, genai_lib=%s, openai_lib=%s",
                 bool(GEMINI_API_KEY), bool(OPENAI_API_KEY), genai is not None, _openai_lib is not None)

    database = load_data()
    last_sent_links = database.get("last_sent_links", {})
    sent_this_run = set()  # prevent duplicate sends across different feeds during same run

    # read urls
    try:
        with open(URL_FILE, 'r', encoding='utf-8') as f:
            urls = [line.strip() for line in f if line.strip() and not line.strip().startswith("#")]
    except FileNotFoundError:
        logging.warning("urls.txt not found; create it with one feed URL per line.")
        urls = []
    except Exception as e:
        logging.exception("Error reading urls.txt: %s", e)
        urls = []

    # dedupe provided urls
    seen = set()
    filtered_urls = []
    for u in urls:
        if u in seen:
            continue
        seen.add(u)
        filtered_urls.append(u)

    for url in filtered_urls:
        logging.info("Processing feed: %s", url)
        feed = fetch_and_parse_feed(url)
        if not feed or not getattr(feed, "entries", None):
            logging.warning("No entries in feed: %s", url)
            continue

        # process newest N (feed.entries usually newest-first)
        entries_slice = feed.entries[:15]
        # iterate older->newer for chronological send order
        sliced = list(reversed(entries_slice))

        last_sent_id_for_url = last_sent_links.get(url)
        seen_last = False if last_sent_id_for_url else True

        for entry in sliced:
            entry_id = getattr(entry, "id", None) or getattr(entry, "guid", None) or getattr(entry, "link", None)
            if not entry_id:
                logging.debug("Entry without id/link; skipping.")
                continue

            # age check
            age = entry_age_seconds(entry)
            if age == -1:
                logging.info("Entry has no publish date; skipping (conservative): %s", getattr(entry, "title", entry_id))
                continue
            if age > MAX_AGE_SECONDS:
                logging.info("Skipping old article (> %d days): %s", MAX_AGE_DAYS, getattr(entry, "title", entry_id))
                continue

            # skip until reach last_sent
            if not seen_last:
                if entry_id == last_sent_id_for_url:
                    seen_last = True
                    logging.debug("Reached last_sent for this feed; subsequent items are new.")
                    continue
                else:
                    # older than last sent; skip
                    continue

            # cross-feed dedupe in same run
            if entry_id in sent_this_run:
                logging.info("Already sent this entry in this run (cross-feed): %s", entry_id)
                continue

            title = getattr(entry, "title", "(no title)")
            summary_raw = getattr(entry, "summary", "") or getattr(entry, "description", "")
            summary = clean_html(summary_raw)
            emojis = categorize_article(f"Title: {title}. Summary: {summary}")
            link = getattr(entry, "link", None)

            # process with AI (GenAI -> OpenAI fallback)
            translated_title, explanation, backend_used = process_article_with_ai(title, summary)
            logging.info("Backend used for this article: %s", backend_used)

            # extract publication date (prefer published_parsed)
            pub_iso = None
            try:
                if getattr(entry, "published_parsed", None):
                    dt = datetime.fromtimestamp(mktime(entry.published_parsed), tz=timezone.utc)
                    pub_iso = dt.date().isoformat()
                elif getattr(entry, "updated_parsed", None):
                    dt = datetime.fromtimestamp(mktime(entry.updated_parsed), tz=timezone.utc)
                    pub_iso = dt.date().isoformat()
                else:
                    # try raw published string (less reliable)
                    raw = getattr(entry, "published", None) or getattr(entry, "updated", None)
                    if raw:
                        # try simple ISO parse
                        try:
                            dt2 = datetime.fromisoformat(raw)
                            pub_iso = dt2.date().isoformat()
                        except Exception:
                            pub_iso = None
            except Exception:
                pub_iso = None

            pub_line = f"\n\n🕘 منتشر شده: {pub_iso}" if pub_iso else ""

            # prepare message (HTML-escaped)
            safe_title = html_lib.escape(translated_title)
            safe_expl = html_lib.escape(explanation).replace("\n", "<br>")
            message = f"{emojis} <b>{safe_title}</b>\n\n{safe_expl}{pub_line}"
            if link:
                message += f"\n\n🔗 <a href=\"{html_lib.escape(link)}\">لینک مقاله اصلی</a>"

            # send message (first HTML, then fallback without parse_mode)
            sent = send_telegram_message(message, parse_mode="HTML")
            if not sent:
                logging.warning("Send with HTML failed; retrying without parse_mode.")
                sent = send_telegram_message(html_lib.unescape(message), parse_mode=None)

            if sent:
                logging.info("Article sent: %s (backend=%s)", title, backend_used)
                sent_this_run.add(entry_id)
                last_sent_links[url] = entry_id
                database["last_sent_links"] = last_sent_links
                save_data(database)
                time.sleep(3)  # small delay to avoid rate limits
            else:
                logging.error("Failed to send article: %s", title)

    logging.info("check_news_job complete.")

# ----------------- run -----------------

if __name__ == "__main__":
    logging.info("Bot starting...")
    # single-run mode for GitHub Actions
    if os.environ.get("GITHUB_ACTIONS") == "true" or os.environ.get("RUN_ONCE") == "1":
        check_news_job()
    else:
        # local continuous mode using schedule
        try:
            import schedule
            check_news_job()
            schedule.every(6).hours.do(check_news_job)
            while True:
                schedule.run_pending()
                time.sleep(1)
        except KeyboardInterrupt:
            logging.info("Interrupted by user; exiting.")
