# -*- coding: utf-8 -*-
"""
main.py — Telegram RSS bot (today-only with grace window + nightly summary)
Behavior:
- Sends articles published TODAY (in Europe/Helsinki by default).
- To avoid misses around midnight, also sends articles published YESTERDAY if current local time
  is within GRACE_HOURS after midnight.
- Only allowed topics (astronomy, cosmology, physics, quantum, philosophy of science, philosophy of mind, epistemology).
- Uses Gemini (GenAI) first; fallback to OpenAI if Gemini fails.
- Stores sent articles per date and sends a nightly summary at SUMMARY_HOUR (local).
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
from datetime import datetime, timezone, timedelta, date

# zoneinfo for timezone-aware date handling
try:
    from zoneinfo import ZoneInfo  # Python 3.9+
except Exception:
    ZoneInfo = None

# Attempt to import genai (Gemini) libs (new or old)
genai = None
try:
    from google import genai as genai
except Exception:
    try:
        import google.generativeai as genai
    except Exception:
        genai = None

# Attempt to import OpenAI (new or legacy)
_openai_lib = None
OpenAIClient = None
try:
    from openai import OpenAI as OpenAIClient
    _openai_lib = "new"
except Exception:
    try:
        import openai as _openai_module
        _openai_lib = "legacy"
        OpenAIClient = None
    except Exception:
        _openai_lib = None

# ----------------- CONFIG / ENV -----------------
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
ADMIN_CHAT_ID = os.environ.get("ADMIN_CHAT_ID")  # admin or your private chat id
DB_FILE = os.environ.get("DB_FILE", "/tmp/bot_database.json")
URL_FILE = os.environ.get("URL_FILE", "urls.txt")

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
GEMINI_MODEL_ENV = os.environ.get("GEMINI_MODEL")
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
OPENAI_MODEL = os.environ.get("OPENAI_MODEL", "gpt-3.5-turbo")

# timezone: default to Europe/Helsinki per your timezone
TIMEZONE_NAME = os.environ.get("TIMEZONE", "Europe/Helsinki")
tzobj = None
if ZoneInfo is not None:
    try:
        tzobj = ZoneInfo(TIMEZONE_NAME)
    except Exception:
        tzobj = None

# Grace hours after midnight to still accept "yesterday" published articles
GRACE_HOURS = int(os.environ.get("GRACE_HOURS", "6"))

# Summary send hour (local) -- when hour >= SUMMARY_HOUR, send summary for that date (once)
SUMMARY_HOUR = int(os.environ.get("SUMMARY_HOUR", "23"))

# max entries per feed to check
MAX_ENTRIES_PER_FEED = int(os.environ.get("MAX_ENTRIES_PER_FEED", "20"))

# Candidate genai models
DEFAULT_GENAI_MODELS = [
    "gemini-2.5-pro",
    "gemini-2.5-flash",
    "gemini-1.5-flash",
    "gemini-1.5-pro",
    "gemini-pro"
]

# logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# ----------------- topic allowlist -----------------
def detect_topic_and_emoji(text):
    text_lower = (text or "").lower()
    astro = ['نجوم','اختر','کیهان','کهکشان','سیاهچاله','astro','astronomy','cosmology','cosmos','astrophys','galaxy']
    physics = ['فیزیک','physics','relativity','thermodynamics','particle','field']
    quantum = ['کوانتوم','کوآنتم','quantum','quantum mechanics','quantum physics']
    philos_science = ['فلسفه علم','philosophy of science','philosophy science']
    philos_mind = ['فلسفه ذهن','philosophy of mind','consciousness','ذهن']
    epist = ['معرفت','معرفت‌شناسی','معرفت شناسی','epistemology','knowledge']

    mappings = [
        (astro, "🔵"),
        (quantum, "⚪"),
        (physics, "⚫"),
        (philos_science, "🟠"),
        (philos_mind, "🟠"),
        (epist, "🟠")
    ]
    for group, emoji in mappings:
        for kw in group:
            if kw in text_lower:
                return emoji
    return None

# ----------------- DB utils -----------------
def load_data():
    try:
        with open(DB_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    except FileNotFoundError:
        # initialize structure
        return {
            "last_sent_links": {},
            "daily_sent": {},       # map date_iso -> list of {"title_fa":..., "link":...}
            "last_summary_date": None,
            "update_offset": 0
        }
    except json.JSONDecodeError:
        logging.warning("DB corrupted; reinitializing.")
        return {
            "last_sent_links": {},
            "daily_sent": {},
            "last_summary_date": None,
            "update_offset": 0
        }

def save_data(data):
    try:
        with open(DB_FILE, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception:
        logging.exception("Failed to save DB")

# ----------------- feed helpers -----------------
def fetch_and_parse_feed(url):
    headers = {"User-Agent": "Telegram-RSS-Bot/1.0 (+https://example.org)"}
    try:
        r = requests.get(url, headers=headers, timeout=15)
        r.raise_for_status()
        parsed = feedparser.parse(r.content)
        return parsed
    except Exception as e:
        logging.warning("Requests fetch failed for %s: %s — falling back to feedparser", url, e)
        try:
            return feedparser.parse(url)
        except Exception as e2:
            logging.error("feedparser direct parse also failed for %s: %s", url, e2)
            return None

def clean_html(raw_html):
    if not raw_html:
        return ""
    return re.sub(r'<.*?>', '', raw_html)

def entry_published_date_in_tz(entry, tz):
    t = getattr(entry, "published_parsed", None) or getattr(entry, "updated_parsed", None)
    if not t:
        return None
    try:
        ts = mktime(t)
        dt_utc = datetime.fromtimestamp(ts, tz=timezone.utc)
        if tz:
            dt_local = dt_utc.astimezone(tz)
        else:
            dt_local = dt_utc
        return dt_local.date()
    except Exception:
        return None

# ----------------- Telegram send -----------------
def send_telegram_message(text, chat_id=None, parse_mode="HTML", disable_web_page_preview=False):
    if not TELEGRAM_BOT_TOKEN:
        logging.error("TELEGRAM_BOT_TOKEN not set.")
        return False
    target = chat_id or ADMIN_CHAT_ID
    if not target:
        logging.error("No target chat_id specified.")
        return False
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": str(target), "text": text, "disable_web_page_preview": disable_web_page_preview}
    if parse_mode:
        payload["parse_mode"] = parse_mode
    try:
        r = requests.post(url, json=payload, timeout=15)
        logging.info("Telegram send status=%s chat=%s", r.status_code, target)
        if r.status_code != 200:
            logging.warning("Telegram response: %s", r.text)
        r.raise_for_status()
        return True
    except Exception as e:
        logging.exception("Telegram send failed: %s", e)
        return False

# ----------------- GenAI (Gemini) -----------------
_genai_client = None
def init_genai_client():
    global _genai_client
    if _genai_client is not None:
        return _genai_client
    if genai is None:
        logging.info("genai lib not available.")
        return None
    if not GEMINI_API_KEY:
        logging.info("GEMINI_API_KEY not set.")
        return None
    try:
        if hasattr(genai, "Client"):
            _genai_client = genai.Client(api_key=GEMINI_API_KEY)
            return _genai_client
        if hasattr(genai, "configure"):
            genai.configure(api_key=GEMINI_API_KEY)
            _genai_client = genai
            return _genai_client
    except Exception:
        logging.exception("init_genai_client failed")
        return None
    return None

def genai_generate(title, summary):
    client = init_genai_client()
    if client is None:
        raise RuntimeError("GenAI client unavailable")
    # Request "روان‌تر" Persian
    prompt = (
        "You are an expert science communicator. Perform two steps based on the English text:\n"
        "1) Translate ONLY the title to fluent, natural Persian (one short line).\n"
        "2) Then explain the core concept in a few fluent, conceptual Persian sentences (2-4 sentences), using clear, natural wording (روان‌تر) suitable for an advanced student.\n\n"
        f"Title: {title}\nSummary: {summary}\n\n"
        "Output exactly: Persian title line, blank line, then the explanation."
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
            if hasattr(client, "models") and hasattr(client.models, "generate_content"):
                resp = client.models.generate_content(model=model_id, contents=prompt)
                text = getattr(resp, "text", None) or getattr(resp, "content", None) or str(resp)
                return text
            if hasattr(client, "GenerativeModel"):
                mod = client.GenerativeModel(model_id)
                resp = mod.generate_content(prompt)
                text = getattr(resp, "text", None) or str(resp)
                return text
            if hasattr(client, "generate"):
                resp = client.generate(prompt)
                return str(resp)
        except Exception as e:
            logging.warning("GenAI model %s failed: %s", model_id, e)
            last_exc = e
            continue
    raise RuntimeError(f"All GenAI attempts failed. Last error: {last_exc}")

# ----------------- OpenAI fallback -----------------
_openai_client = None
def init_openai_client():
    global _openai_client
    if _openai_client is not None:
        return _openai_client
    if _openai_lib is None:
        logging.info("openai lib not available.")
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
    except Exception:
        logging.exception("init_openai_client failed")
        return None
    return None

def openai_generate(title, summary, max_retries=1):
    client = init_openai_client()
    if client is None:
        raise RuntimeError("OpenAI client unavailable")
    system_prompt = (
        "You are an expert Persian science communicator. Given an English title and short English summary:\n"
        "1) Translate ONLY the title to fluent Persian (one short line).\n"
        "2) Produce a concise, fluent conceptual explanation in Persian (2-4 sentences) suitable for an advanced student.\n"
        "Return Persian title on first line, blank line, then the explanation."
    )
    user_content = f"Title: {title}\nSummary: {summary}"
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
        raise RuntimeError("OpenAI all attempts failed")
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
        raise RuntimeError("OpenAI legacy all attempts failed")
    else:
        raise RuntimeError("No OpenAI client available")

# ----------------- process article (GenAI -> OpenAI fallback) -----------------
def process_article_with_ai(title, summary):
    logging.info("Processing article (AI): %s", title)
    # try genai first
    try:
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

    # fallback to OpenAI
    try:
        t, e = openai_generate(title, summary)
        logging.info("OpenAI returned title length=%d", len(t) if t else 0)
        if t:
            return t, e or "(بدون توضیح)", "openai"
    except Exception as e:
        logging.warning("OpenAI fallback failed: %s", e)

    logging.info("Final fallback: escaped title")
    return html_lib.escape(title), "(پردازش AI ناموفق بود)", "fallback"

# ----------------- add to daily_sent -----------------
def add_daily_sent(database, pub_date_iso, title_fa, link):
    ds = database.get("daily_sent", {})
    arr = ds.get(pub_date_iso, [])
    arr.append({"title_fa": title_fa, "link": link})
    ds[pub_date_iso] = arr
    database["daily_sent"] = ds
    save_data(database)

# ----------------- nightly summary -----------------
def build_and_send_summary_for_date(database, date_obj):
    date_iso = date_obj.isoformat()
    entries = database.get("daily_sent", {}).get(date_iso, [])
    # prepare message
    header = f"سلام {os.environ.get('ADMIN_NAME','جناب رفیعی')}\n\nلیست مقالات منتشرشده در تاریخ {date_iso}:\n"
    if not entries:
        body = "\nهیچ مقالهٔ جدیدی ثبت نشده است."
    else:
        lines = []
        for i, e in enumerate(entries, start=1):
            title_html = html_lib.escape(e.get("title_fa", "(بدون عنوان)"))
            link = e.get("link", "")
            if link:
                lines.append(f"{i}- {title_html} — <a href=\"{html_lib.escape(link)}\">لینک</a>")
            else:
                lines.append(f"{i}- {title_html}")
        body = "\n".join(lines)
    message = header + body
    # send to admin
    ok = send_telegram_message(message, chat_id=ADMIN_CHAT_ID, parse_mode="HTML", disable_web_page_preview=False)
    if ok:
        logging.info("Sent nightly summary for %s", date_iso)
        database["last_summary_date"] = date_iso
        save_data(database)
    else:
        logging.error("Failed to send nightly summary for %s", date_iso)

# ----------------- main check job -----------------
def check_news_job():
    logging.info("Starting check_news_job. TZ=%s GRACE_HOURS=%s SUMMARY_HOUR=%s", TIMEZONE_NAME, GRACE_HOURS, SUMMARY_HOUR)
    database = load_data()
    last_sent_links = database.get("last_sent_links", {})
    sent_this_run = set()

    # current local date/time
    now_utc = datetime.now(timezone.utc)
    now_local = now_utc.astimezone(tzobj) if tzobj else now_utc
    today_local = now_local.date()
    yesterday_local = today_local - timedelta(days=1)

    # nightly summary: if hour >= SUMMARY_HOUR and summary not yet sent for today -> send summary for today
    last_summary_date = database.get("last_summary_date")
    if now_local.hour >= SUMMARY_HOUR and last_summary_date != today_local.isoformat():
        # build/send summary for today (includes articles added to daily_sent for today so far)
        try:
            build_and_send_summary_for_date(database, today_local)
        except Exception as e:
            logging.exception("Failed to build/send nightly summary: %s", e)

    # read urls
    try:
        with open(URL_FILE, 'r', encoding='utf-8') as f:
            urls = [line.strip() for line in f if line.strip() and not line.strip().startswith("#")]
    except FileNotFoundError:
        logging.warning("urls.txt not found.")
        urls = []
    except Exception as e:
        logging.exception("Error reading urls.txt: %s", e)
        urls = []

    # dedupe urls
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

        entries_slice = feed.entries[:MAX_ENTRIES_PER_FEED]
        sliced = list(reversed(entries_slice))  # older->newer

        last_sent_id_for_url = last_sent_links.get(url)
        seen_last = False if last_sent_id_for_url else True

        for entry in sliced:
            entry_id = getattr(entry, "id", None) or getattr(entry, "guid", None) or getattr(entry, "link", None)
            if not entry_id:
                logging.debug("Entry without id; skipping.")
                continue

            # compute published date in local tz
            pub_date = entry_published_date_in_tz(entry, tzobj)
            if not pub_date:
                logging.debug("Entry has no published date; skipping conservatively: %s", getattr(entry, "title", entry_id))
                continue

            # Determine whether to send:
            send_flag = False
            # if published today -> send
            if pub_date == today_local:
                send_flag = True
            # if published yesterday and within grace window after midnight -> send
            elif pub_date == yesterday_local:
                # current local time since midnight
                seconds_since_midnight = now_local.hour * 3600 + now_local.minute * 60 + now_local.second
                if seconds_since_midnight <= GRACE_HOURS * 3600:
                    send_flag = True

            if not send_flag:
                logging.debug("Skipping entry not in today's window: %s (pub=%s)", getattr(entry, "title", entry_id), pub_date)
                # still update seen_last logic even if skipping (we only consider age relative to last_sent)
                if not seen_last:
                    if entry_id == last_sent_id_for_url:
                        seen_last = True
                    else:
                        continue
                else:
                    continue

            # skip until last_sent (we still need to skip older than last_sent)
            if not seen_last:
                if entry_id == last_sent_id_for_url:
                    seen_last = True
                    logging.debug("Reached last_sent for this feed; subsequent items are new.")
                    continue
                else:
                    continue

            # cross-feed dedupe within run
            if entry_id in sent_this_run:
                logging.info("Already sent this entry in this run: %s", entry_id)
                continue

            title = getattr(entry, "title", "(no title)")
            summary_raw = getattr(entry, "summary", "") or getattr(entry, "description", "")
            summary = clean_html(summary_raw)
            combined = f"Title: {title}. Summary: {summary}"

            # topic filter (allowlist)
            emoji = detect_topic_and_emoji(combined)
            if not emoji:
                logging.info("Article not in allowed topics; skipping: %s", title)
                # update seen_last skipping behavior
                if not seen_last and entry_id == last_sent_id_for_url:
                    seen_last = True
                continue

            link = getattr(entry, "link", None)

            # process with AI (GenAI then fallback)
            try:
                translated_title, explanation, backend_used = process_article_with_ai(title, summary)
            except Exception as e:
                logging.exception("AI processing failed for %s: %s", title, e)
                translated_title, explanation, backend_used = html_lib.escape(title), "(پردازش AI ناموفق بود)", "fallback"

            # prepare message with publication date shown
            pub_iso = pub_date.isoformat() if pub_date else None
            pub_line = f"\n\n🕘 منتشر شده: {pub_iso}" if pub_iso else ""

            safe_title = html_lib.escape(translated_title)
            safe_expl = html_lib.escape(explanation).replace("\n", "<br>")
            message = f"{emoji} <b>{safe_title}</b>\n\n{safe_expl}{pub_line}"
            if link:
                message += f"\n\n🔗 <a href=\"{html_lib.escape(link)}\">لینک مقاله اصلی</a>"

            # send message
            sent_ok = send_telegram_message(message, parse_mode="HTML")
            if not sent_ok:
                logging.warning("Send with HTML failed; retrying without parse_mode.")
                sent_ok = send_telegram_message(html_lib.unescape(message), parse_mode=None)

            if sent_ok:
                logging.info("Sent article: %s (backend=%s) pub=%s", title, backend_used, pub_iso)
                sent_this_run.add(entry_id)
                last_sent_links[url] = entry_id
                database["last_sent_links"] = last_sent_links
                # add to daily_sent keyed by publication date (so if pub_date == yesterday and sent within grace, it appears in yesterday's summary)
                add_daily_sent(database, pub_iso, translated_title, link)
                save_data(database)
                time.sleep(2)
            else:
                logging.error("Failed to send article: %s", title)

    logging.info("check_news_job finished.")

# ----------------- run -----------------
if __name__ == "__main__":
    logging.info("Bot starting (today-only with grace + nightly summary). TZ=%s", TIMEZONE_NAME)
    # single-run for actions or RUN_ONCE
    if os.environ.get("GITHUB_ACTIONS") == "true" or os.environ.get("RUN_ONCE") == "1":
        check_news_job()
    else:
        # schedule every 6 hours by default
        try:
            import schedule
            check_news_job()
            schedule.every(6).hours.do(check_news_job)
            while True:
                schedule.run_pending()
                time.sleep(1)
        except KeyboardInterrupt:
            logging.info("Interrupted; exiting.")
