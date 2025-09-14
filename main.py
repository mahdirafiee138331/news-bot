# -*- coding: utf-8 -*-
"""
Telegram RSS bot — main.py
ویژگی‌ها:
- خواندن فیدها از urls.txt
- فیلتر مقالات قدیمی‌تر از 2 روز
- تولید عنوان فارسی + توضیح مفهومی با Gemini (در اولویت) و fallback به OpenAI (ChatGPT)
- ارسال پیام به تلگرام با HTML (fallback بدون parse_mode)
- ذخیره‌ی last_sent_links در DB_FILE (JSON)
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
from datetime import datetime, timezone, timedelta

# optional imports for GenAI (Gemini) and OpenAI (ChatGPT)
genai = None
_openai_lib = None
OpenAIClient = None

try:
    # new google genai package
    from google import genai as genai  # type: ignore
except Exception:
    try:
        import google.generativeai as genai  # fallback older package name
    except Exception:
        genai = None

try:
    # new OpenAI client
    from openai import OpenAI as OpenAIClient  # type: ignore
    _openai_lib = "new"
except Exception:
    try:
        import openai as _openai_module  # legacy
        _openai_lib = "legacy"
        OpenAIClient = None
    except Exception:
        _openai_lib = None

# --- config / environment ---
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
ADMIN_CHAT_ID = os.environ.get("ADMIN_CHAT_ID")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
GEMINI_MODEL_ENV = os.environ.get("GEMINI_MODEL")  # optional
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
OPENAI_MODEL = os.environ.get("OPENAI_MODEL", "gpt-3.5-turbo")

DB_FILE = os.environ.get("DB_FILE", "/tmp/bot_database.json")
URL_FILE = os.environ.get("URL_FILE", "urls.txt")

# max age for articles (2 days)
MAX_AGE_DAYS = int(os.environ.get("MAX_AGE_DAYS", "2"))
MAX_AGE_SECONDS = MAX_AGE_DAYS * 24 * 3600

# candidate models if GEMINI_MODEL not set
DEFAULT_GENAI_MODELS = [
    "gemini-2.5-pro",
    "gemini-2.5-flash",
    "gemini-1.5-flash",
    "gemini-1.5-pro",
    "gemini-pro"
]

# logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')

# keyword categories (emoji)
KEYWORD_CATEGORIES = {
    "🔵": ['نجوم', 'فیزیک', 'کیهان', 'کوانتوم', 'ستاره', 'کهکشان', 'سیاهچاله', 'اخترشناسی', 'سیاره', 'physics', 'astronomy', 'cosmos', 'galaxy', 'planet'],
    "🟡": ['زیست', 'ژنتیک', 'فرگشت', 'dna', 'سلول', 'مولکول', 'بیولوژی', 'تکامل', 'biology', 'evolution', 'genetic'],
    "⚫": ['هوش مصنوعی', 'یادگیری ماشین', 'شبکه عصبی', 'رباتیک', 'الگوریتم', 'دیپ لرنینگ', 'ai', 'artificial intelligence', 'machine learning'],
    "🔴": ['روانشناسی', 'جامعه شناسی', 'علوم اجتماعی', 'رفتار', 'ذهن', 'روان', 'اجتماعی', 'psychology', 'sociology', 'social'],
    "🟠": ['فلسفه', 'فلسفه علم', 'منطق', 'متافیزیک', 'اخلاق', 'philosophy']
}

# ----------------- utility: DB -----------------

def load_data():
    try:
        with open(DB_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    except FileNotFoundError:
        return {"last_sent_links": {}}
    except json.JSONDecodeError:
        logging.warning("DB file corrupted, reinitializing.")
        return {"last_sent_links": {}}

def save_data(data):
    try:
        with open(DB_FILE, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logging.error("Error saving DB: %s", e)

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

# ----------------- Telegram send -----------------

def send_telegram_message(text, parse_mode="HTML"):
    if not TELEGRAM_BOT_TOKEN or not ADMIN_CHAT_ID:
        logging.error("Telegram token or ADMIN_CHAT_ID not set.")
        return False
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": ADMIN_CHAT_ID,
        "text": text,
    }
    if parse_mode:
        payload["parse_mode"] = parse_mode
    try:
        r = requests.post(url, json=payload, timeout=15)
        logging.info("Telegram send status=%s response=%s", r.status_code, r.text)
        r.raise_for_status()
        return True
    except requests.RequestException as e:
        # log more detail
        try:
            logging.error("Telegram error: status=%s text=%s", e.response.status_code if e.response else None, e.response.text if e.response else None)
        except Exception:
            logging.error("Telegram send exception: %s", e)
        return False
    except Exception as e:
        logging.exception("Unexpected error sending telegram: %s", e)
        return False

# ----------------- entry age -----------------

def entry_age_seconds(entry):
    # returns seconds since published, or -1 if no date available
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
        logging.info("GenAI library not available.")
        return None
    if not GEMINI_API_KEY:
        logging.info("GEMINI_API_KEY not set.")
        return None
    try:
        # if genai has Client (new google-genai)
        if hasattr(genai, "Client"):
            _genai_client = genai.Client(api_key=GEMINI_API_KEY)
            return _genai_client
        # fallback older library pattern
        if hasattr(genai, "configure"):
            genai.configure(api_key=GEMINI_API_KEY)
            _genai_client = genai
            return _genai_client
    except Exception as e:
        logging.exception("Failed to init GenAI client: %s", e)
        return None
    return None

def genai_generate(title, summary):
    """
    try to generate Persian title + short explanation using Gemini (GenAI).
    returns text or raises exception.
    """
    client = init_genai_client()
    if client is None:
        raise RuntimeError("GenAI client not available")

    prompt = (
        "You are an expert science communicator. Perform two steps:\n"
        "1) Translate ONLY the title to fluent Persian (one short line).\n"
        "2) After the title, explain the core idea in 2-4 concise Persian sentences as if teaching an advanced student.\n\n"
        f"Title: {title}\nSummary: {summary}\n\nOutput: Persian title line, blank line, then explanation."
    )

    # choose model candidates
    candidates = []
    if GEMINI_MODEL_ENV:
        candidates.append(GEMINI_MODEL_ENV)
    candidates += DEFAULT_GENAI_MODELS

    last_exc = None
    for model_id in candidates:
        if not model_id:
            continue
        try:
            logging.info("GenAI: attempting model %s", model_id)
            # new genai client (google.genai)
            if hasattr(client, "models") and hasattr(client.models, "generate_content"):
                resp = client.models.generate_content(model=model_id, contents=prompt)
                text = getattr(resp, "text", None) or getattr(resp, "content", None) or str(resp)
                return text
            # older google.generativeai usage
            if hasattr(client, "GenerativeModel"):
                model = client.GenerativeModel(model_id)
                resp = model.generate_content(prompt)
                text = getattr(resp, "text", None) or str(resp)
                return text
            # otherwise try client.generate (best-effort)
            if hasattr(client, "generate"):
                resp = client.generate(prompt)
                return str(resp)
        except Exception as e:
            logging.warning("GenAI model %s failed: %s", model_id, e)
            last_exc = e
            continue
    raise RuntimeError(f"All GenAI models failed. Last error: {last_exc}")

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
            # legacy openai package
            import openai as _m
            _m.api_key = OPENAI_API_KEY
            _openai_client = _m
            return _openai_client
    except Exception as e:
        logging.exception("Failed to init OpenAI client: %s", e)
        return None
    return None

def openai_generate(title, summary, max_retries=1):
    """
    Use OpenAI Chat completion to produce Persian title + explanation.
    returns (title, explanation) strings or raises exception.
    """
    client = init_openai_client()
    if client is None:
        raise RuntimeError("OpenAI client not available")

    system_prompt = (
        "You are an expert Persian science communicator. Given an English title and a short English summary, "
        "1) Translate ONLY the title to fluent Persian (one short line). "
        "2) Then produce a concise conceptual explanation in Persian (2-4 sentences). "
        "Output the Persian title on the first line, then a blank line, then the explanation."
    )
    user_content = f"Title: {title}\nSummary: {summary}"

    if _openai_lib == "new" and OpenAIClient:
        # new client usage
        for attempt in range(max_retries + 1):
            try:
                resp = client.chat.completions.create(
                    model=OPENAI_MODEL,
                    messages=[{"role": "system", "content": system_prompt},
                              {"role": "user", "content": user_content}],
                    temperature=0.2,
                    max_tokens=500
                )
                # response parsing: try safe extraction
                try:
                    content = resp.choices[0].message["content"]
                except Exception:
                    try:
                        content = resp.choices[0].message.content
                    except Exception:
                        content = str(resp)
                # split
                parts = [p.strip() for p in content.split("\n\n") if p.strip()]
                if len(parts) >= 2:
                    return parts[0], "\n\n".join(parts[1:])
                elif len(parts) == 1:
                    lines = parts[0].splitlines()
                    return lines[0].strip(), "\n".join(lines[1:]).strip() or "(توضیحی فراهم نشد)"
                else:
                    return title, "(OpenAI produced no text)"
            except Exception as e:
                logging.warning("OpenAI attempt %d failed: %s", attempt+1, e)
                time.sleep(1 + attempt*2)
                continue
        raise RuntimeError("OpenAI all attempts failed.")
    elif _openai_lib == "legacy":
        # legacy openai.ChatCompletion.create
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
                content = resp.choices[0].message["content"] if hasattr(resp.choices[0].message, "content") else resp.choices[0].message["content"]
                parts = [p.strip() for p in content.split("\n\n") if p.strip()]
                if len(parts) >= 2:
                    return parts[0], "\n\n".join(parts[1:])
                elif len(parts) == 1:
                    lines = parts[0].splitlines()
                    return lines[0].strip(), "\n".join(lines[1:]).strip() or "(توضیحی فراهم نشد)"
                else:
                    return title, "(OpenAI produced no text)"
            except Exception as e:
                logging.warning("OpenAI (legacy) attempt %d failed: %s", attempt+1, e)
                time.sleep(1 + attempt*2)
                continue
        raise RuntimeError("OpenAI (legacy) all attempts failed.")
    else:
        raise RuntimeError("No OpenAI client available")

# ----------------- high-level article processing -----------------

def process_article_with_ai(title, summary):
    """
    Try: GenAI -> OpenAI fallback -> final fallback (escaped title)
    Return: (translated_title, explanation)
    """
    logging.info("process_article_with_ai: %s", title)
    # 1) try GenAI
    try:
        text = genai_generate(title, summary)
        if text:
            parts = [p.strip() for p in text.split("\n\n") if p.strip()]
            if len(parts) >= 2:
                return parts[0], "\n\n".join(parts[1:])
            elif len(parts) == 1:
                lines = parts[0].splitlines()
                return lines[0].strip(), "\n".join(lines[1:]).strip() or "(توضیحی فراهم نشد)"
    except Exception as e:
        logging.warning("GenAI failed for title '%s': %s", title, e)

    # 2) fallback OpenAI
    try:
        t, e = openai_generate(title, summary)
        if t:
            return t, e or "(بدون توضیح)"
    except Exception as e:
        logging.warning("OpenAI fallback failed for title '%s': %s", title, e)

    # final fallback: escaped title only
    logging.info("Using final fallback for title: %s", title)
    return html_lib.escape(title), "(پردازش AI ناموفق بود)"

# ----------------- main job: check feeds -----------------

def check_news_job():
    # debug summary of available keys and libs
    logging.info("GEMINI key present: %s, OPENAI key present: %s", bool(GEMINI_API_KEY), bool(OPENAI_API_KEY))
    logging.info("genai lib present: %s, openai lib present: %s", genai is not None, _openai_lib is not None)

    database = load_data()
    last_sent_links = database.get("last_sent_links", {})

    logging.info("Starting feed check...")
    try:
        with open(URL_FILE, 'r', encoding='utf-8') as f:
            urls = [line.strip() for line in f if line.strip()]
    except FileNotFoundError:
        logging.warning("urls.txt not found. Create it in repo root with one feed URL per line.")
        urls = []
    except Exception as e:
        logging.exception("Error reading URLs file: %s", e)
        urls = []

    # dedupe
    seen = set()
    filtered_urls = []
    for u in urls:
        if u in seen:
            continue
        seen.add(u)
        filtered_urls.append(u)

    for url in filtered_urls:
        logging.info("Checking feed: %s", url)
        try:
            feed = feedparser.parse(url)
            if not feed or not getattr(feed, "entries", None):
                logging.warning("Feed empty or invalid for %s", url)
                continue

            # take first N entries (newest-first usually)
            entries_slice = feed.entries[:15]
            # we iterate from older->newer so order of sent messages is chronological
            sliced = list(reversed(entries_slice))

            last_sent_id_for_url = last_sent_links.get(url)
            seen_last = False if last_sent_id_for_url else True

            for entry in sliced:
                entry_id = getattr(entry, "id", None) or getattr(entry, "guid", None) or getattr(entry, "link", None)
                if not entry_id:
                    logging.debug("entry without id/link; skipping.")
                    continue

                # age filter
                age = entry_age_seconds(entry)
                if age == -1:
                    logging.info("Entry has no published date; skipping: %s", getattr(entry, "title", entry_id))
                    continue
                if age > MAX_AGE_SECONDS:
                    logging.info("Skipping old article (> %d days): %s", MAX_AGE_DAYS, getattr(entry, "title", entry_id))
                    continue

                # if we have a last_sent recorded, skip until we reach it
                if not seen_last:
                    if entry_id == last_sent_id_for_url:
                        seen_last = True
                        logging.debug("Reached last sent id for this feed; subsequent entries are new.")
                        continue
                    else:
                        # older than last sent: skip
                        continue

                # now this entry is considered new: process it
                title = getattr(entry, "title", "(no title)")
                summary_raw = getattr(entry, "summary", "") or getattr(entry, "description", "")
                summary = clean_html(summary_raw)
                cat_emoji = categorize_article(f"Title: {title}. Summary: {summary}")

                # AI processing: try Gemini then OpenAI
                try:
                    translated_title, explanation = process_article_with_ai(title, summary)
                except Exception as e:
                    logging.exception("AI processing threw unhandled exception: %s", e)
                    translated_title, explanation = html_lib.escape(title), "(پردازش AI ناموفق بود)"

                # prepare message
                safe_title = html_lib.escape(translated_title)
                safe_expl = html_lib.escape(explanation).replace("\n", "<br>")
                message = f"{cat_emoji} <b>{safe_title}</b>\n\n{safe_expl}"
                entry_link = getattr(entry, "link", None)
                if entry_link:
                    message += f"\n\n🔗 <a href=\"{html_lib.escape(entry_link)}\">لینک مقاله اصلی</a>"

                # send: first try HTML parse, then fallback to no parse
                sent = send_telegram_message(message, parse_mode="HTML")
                if not sent:
                    logging.warning("Send with HTML failed; trying without parse_mode.")
                    sent = send_telegram_message(html_lib.unescape(message), parse_mode=None)

                if sent:
                    logging.info("Sent article: %s", title)
                    last_sent_links[url] = entry_id
                    database["last_sent_links"] = last_sent_links
                    save_data(database)
                    time.sleep(3)  # small delay between messages
                else:
                    logging.error("Failed to send article: %s", title)

        except Exception as e:
            logging.exception("Error processing feed %s: %s", url, e)
            continue

    logging.info("Feed check complete.")

# ----------------- run -----------------

if __name__ == "__main__":
    logging.info("Bot starting...")
    # If running in GH Actions or RUN_ONCE=1, do single run and exit
    if os.environ.get("GITHUB_ACTIONS") == "true" or os.environ.get("RUN_ONCE") == "1":
        check_news_job()
    else:
        # local mode: scheduled every 6 hours
        try:
            import schedule
            check_news_job()
            schedule.every(6).hours.do(check_news_job)
            while True:
                schedule.run_pending()
                time.sleep(1)
        except KeyboardInterrupt:
            logging.info("Interrupted by user; exiting.")
