"""سیستم هوشمند جلوگیری از خوابیدن سرور (Keep-Alive) و اتصال به ربات بله.

ویژگی‌ها:
- ارسال پینگ‌های دوره‌ای به آدرس Hugging Face Space (جلوگیری از رفتن به حالت Sleep)
- اتصال به بات بله (Bale Bot API) جهت ارسال وضعیت زنده پیشرفت به پیوی یا کانال کاربر
- پاسخ به دستورات /status و /ping در پیام‌رسان بله برای بیدار کردن آنی سرور
- ارسال خودکار فایل خروجی Word به پیام‌رسان بله پس از اتمام تولید جزوه
"""
import json
import os
import threading
import time
import requests

SETTINGS_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config", "settings.json")
BALE_API_BASE = "https://tapi.bale.ai"

DEFAULT_SETTINGS = {
    "space_url": "",
    "enable_keepalive": True,
    "keepalive_interval_minutes": 12,
    "bale_bot_token": "",
    "bale_chat_id": "",
    "enable_bale_notifications": True
}


def load_settings() -> dict:
    if os.path.exists(SETTINGS_PATH):
        try:
            with open(SETTINGS_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
                return {**DEFAULT_SETTINGS, **data}
        except Exception:
            pass
    return DEFAULT_SETTINGS.copy()


def save_settings(data: dict):
    os.makedirs(os.path.dirname(SETTINGS_PATH), exist_ok=True)
    with open(SETTINGS_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def send_bale_message(token: str, chat_id: str, text: str):
    if not token or not chat_id or not text:
        return False
    url = f"{BALE_API_BASE}/bot{token}/sendMessage"
    payload = {"chat_id": str(chat_id).strip(), "text": text}
    try:
        r = requests.post(url, json=payload, timeout=10)
        return r.status_code == 200
    except Exception:
        return False


def send_bale_document(token: str, chat_id: str, file_path: str, caption: str = ""):
    if not token or not chat_id or not os.path.exists(file_path):
        return False
    url = f"{BALE_API_BASE}/bot{token}/sendDocument"
    try:
        with open(file_path, "rb") as fp:
            files = {"document": (os.path.basename(file_path), fp)}
            data = {"chat_id": str(chat_id).strip(), "caption": caption}
            r = requests.post(url, data=data, files=files, timeout=40)
            return r.status_code == 200
    except Exception:
        return False


class KeepAliveManager:
    def __init__(self, state_getter=None):
        self.state_getter = state_getter
        self.thread = None
        self.stop_event = threading.Event()
        self.bale_offset = 0

    def start(self):
        if self.thread and self.thread.is_alive():
            return
        self.stop_event.clear()
        self.thread = threading.Thread(target=self._loop, daemon=True, name="keep-alive-worker")
        self.thread.start()

    def stop(self):
        self.stop_event.set()

    def _loop(self):
        last_ping_time = 0.0
        while not self.stop_event.is_set():
            cfg = load_settings()
            space_url = (cfg.get("space_url") or "").strip().rstrip("/")
            token = (cfg.get("bale_bot_token") or "").strip()
            chat_id = (cfg.get("bale_chat_id") or "").strip()
            interval_sec = max(60, int(cfg.get("keepalive_interval_minutes", 10)) * 60)
            now = time.time()

            # ۱. پینگ دوره‌ای ضدخواب هر ۱۰ تا ۱۲ دقیقه یک‌بار
            if cfg.get("enable_keepalive") and space_url:
                if now - last_ping_time >= interval_sec:
                    last_ping_time = now
                    try:
                        requests.get(f"{space_url}/api/ping", timeout=10)
                    except Exception:
                        pass

            # ۲. دریافت و پاسخ آنی به دستورات ربات بله (Fast Long Polling)
            if token:
                try:
                    self._check_bale_updates(token, chat_id)
                except Exception:
                    self.stop_event.wait(2)
            else:
                self.stop_event.wait(5)

    def _check_bale_updates(self, token: str, allowed_chat_id: str):
        url = f"{BALE_API_BASE}/bot{token}/getUpdates"
        # timeout=5 seconds allows Bale server to hold connection and respond instantly
        params = {"offset": self.bale_offset, "timeout": 5}
        try:
            resp = requests.get(url, params=params, timeout=12)
            if resp.status_code != 200:
                self.stop_event.wait(2)
                return
            data = resp.json()
        except Exception:
            self.stop_event.wait(2)
            return

        updates = data.get("result", [])
        for u in updates:
            self.bale_offset = max(self.bale_offset, u.get("update_id", 0) + 1)
            msg = u.get("message", {})
            sender_id = str(msg.get("chat", {}).get("id", ""))
            text = (msg.get("text") or "").strip()
            if not text:
                continue

            # ثبت خودکار آیدی کاربر در صورت خالی بودن
            if not allowed_chat_id:
                cfg = load_settings()
                cfg["bale_chat_id"] = str(sender_id)
                save_settings(cfg)
                allowed_chat_id = str(sender_id)

            # پاسخ آنی به دستورات کاربر
            if text in ("/start", "/help", "راهنما", "سلام"):
                reply = (
                    "👋 سلام! ربات هوشمند جزوه‌ساز اندروید (@mytetsbot) فعال و آنلاین است.\n\n"
                    "✅ اتصال شما با موفقیت به عنوان مدیر ثبت گردید.\n\n"
                    "دستورات سریع:\n"
                    "📊 /status - مشاهده وضعیت پیشرفت زنده\n"
                    "🏓 /ping - تست آنلاین بودن سرور جزوه‌ساز\n\n"
                    "🌐 آدرس پنل (در مرورگر گوشی یا کروم باز کنید):\n"
                    "https://api-joze-bale-1.onrender.com\n\n"
                    "💡 فایل Word جزوه پس از پایان تولید، به طور خودکار همینجا برای شما ارسال خواهد شد."
                )
                send_bale_message(token, sender_id, reply)

            elif text in ("/status", "وضعیت"):
                st_text = "ℹ️ وضعیت فعلی جزوه‌ساز:\n"
                if self.state_getter:
                    st = self.state_getter()
                    is_run = st.get("is_running", False)
                    stats = st.get("stats", {})
                    tot = stats.get("total", 0)
                    done = stats.get("done", 0)
                    fail = stats.get("failed", 0)
                    pct = int((done / tot * 100)) if tot > 0 else 0
                    st_text += f"- حالت: {'🚀 در حال کار' if is_run else '💤 آماده'}\n"
                    st_text += f"- پیشرفت: {pct}٪ (قطعه {done} از {tot})\n"
                    st_text += f"- خطاها: {fail}\n"
                    st_text += f"- پروژه فعال: {st.get('active_project_title', 'تعیین نشده')}\n\n"
                    st_text += "🌐 لینک پنل:\nhttps://api-joze-bale-1.onrender.com"
                else:
                    st_text += "سرور فعال و آنلاین است."
                send_bale_message(token, sender_id, st_text)

            elif text in ("/ping", "پینگ"):
                send_bale_message(token, sender_id, "🏓 Pong! سرور جزوه‌ساز آنلاین و کاملاً آماده است.")


GLOBAL_KEEPER = KeepAliveManager()
