# send_test_message.py
import os
import requests
import logging

logging.basicConfig(level=logging.INFO)

# این مقادیر از همان Secrets گیت‌هاب خوانده می‌شوند
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
ADMIN_CHAT_ID = os.environ.get("ADMIN_CHAT_ID")

if not TELEGRAM_BOT_TOKEN or not ADMIN_CHAT_ID:
    logging.error("TELEGRAM_BOT_TOKEN یا ADMIN_CHAT_ID تنظیم نشده.")
    raise SystemExit(1)

text = "این یک پیام تست برای بررسی اتصال است.\n\nاگر این پیام را دریافت کردید یعنی توکن و آیدی شما کاملاً صحیح است."
payload = {
    "chat_id": ADMIN_CHAT_ID,
    "text": text
}

url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
try:
    r = requests.post(url, json=payload, timeout=15)
    logging.info("Status Code: %s", r.status_code)
    logging.info("Response Text: %s", r.text)
    r.raise_for_status() # اگر خطا باشد، برنامه متوقف می‌شود
    logging.info("پیام تست با موفقیت ارسال شد.")
except Exception as e:
    logging.exception("ارسال پیام تست ناموفق بود:")
    raise
