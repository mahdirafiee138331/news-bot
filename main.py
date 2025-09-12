for url in urls:
        logging.info(f"در حال بررسی سایت: {url}")
        try:
            feed = feedparser.parse(url)
            if not feed or not feed.entries:
                logging.warning(f"فید برای سایت {url} خالی یا نامعتبر است.")
                continue
            
            for entry in reversed(feed.entries[:15]):
                entry_link = entry.link
                if last_sent_links.get(url) != entry_link:
                    title = entry.title
                    summary = clean_html(entry.summary)
                    full_content_for_cat = f"Title: {title}. Summary: {summary}"
                    emojis = categorize_article(full_content_for_cat)
                    gemini_output = process_with_gemini(title, summary)
                    message_part = f"{emojis} *{gemini_output}*\n\n[لینک مقاله اصلی]({entry_link})"
                    send_telegram_message(message_part)
                    last_sent_links[url] = entry_link
                    # ذخیره بعد از هر ارسال موفق
                    save_data({"last_sent_links": last_sent_links})
                    logging.info(f"مقاله جدید ارسال شد: {title}")
                    time.sleep(5)
        except Exception as e:
            logging.error(f"خطای جدی در پردازش فید {url}: {e}")
            continue

    logging.info("پایان یک چرخه بررسی.")

if name == "__main__":
    logging.info("ربات در حال راه‌اندازی برای اجرای دائمی است...")
    # اجرای اولیه برای تست در شروع کار
    check_news_job()
    # تنظیم زمان‌بندی برای اجرا هر ۶ ساعت
    schedule.every(6).hours.do(check_news_job)
    
    while True:
        schedule.run_pending()
        time.sleep(1)
