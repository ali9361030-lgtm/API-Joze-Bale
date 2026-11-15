"""اسکریپت سبک پینگر مستقل برای جلوگیری از به خواب رفتن Hugging Face Space.

نحوه استفاده:
این اسکریپت را می‌توانید روی کامپیوتر، سرور مجازی یا در کنار هر ربات بله اجرا کنید تا هر ۱۲ دقیقه یکبار به اسپیس هاگینگ‌فیس شما درخواست بفرستد و آن را همیشه آنلاین نگه دارد.
"""
import time
import requests

BALE_BOT_TOKEN = "557138530:NcixazutcFRJTSDf4OkXJMc7Ik1TjIcdsaY"
# مثال: SPACE_URL = "https://your-username-jozvesaz.hf.space"
SPACE_URL = "https://YOUR_SPACE_URL.hf.space"

INTERVAL_MINUTES = 12


def ping_server():
    url = f"{SPACE_URL.rstrip('/')}/api/ping"
    try:
        r = requests.get(url, timeout=20)
        now = time.strftime("%Y-%m-%d %H:%M:%S")
        if r.status_code == 200:
            print(f"[{now}] ✅ سرور بیدار است: {r.json()}")
        else:
            print(f"[{now}] ⚠️ پاسخ سرور: HTTP {r.status_code}")
    except Exception as e:
        now = time.strftime("%Y-%m-%d %H:%M:%S")
        print(f"[{now}] ❌ خطای اتصال به سرور: {e}")


def main():
    print(f"🚀 پینگر خودکار برای {SPACE_URL} شروع به کار کرد (هر {INTERVAL_MINUTES} دقیقه)...")
    while True:
        ping_server()
        time.sleep(INTERVAL_MINUTES * 60)


if __name__ == "__main__":
    main()
