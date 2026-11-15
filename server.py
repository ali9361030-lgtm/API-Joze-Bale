"""Local-only web application for the Android Mix API engine."""
import base64
import binascii
import copy
import hashlib
import io
import json
import math
import os
import queue
import shutil
import sqlite3
import sys
import tempfile
import threading
import time
import urllib.parse
import uuid
import zipfile
from xml.etree import ElementTree
from http import HTTPStatus
from http.server import HTTPServer, SimpleHTTPRequestHandler
from socketserver import ThreadingMixIn

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from core.key_pool import KeyPool
from core.runner import Runner

STATIC_DIR = os.path.join(BASE_DIR, "static")
CONFIG_DIR = os.path.join(BASE_DIR, "config")
OUTPUTS_DIR = os.path.join(BASE_DIR, "outputs")
DATA_DIR = os.path.join(BASE_DIR, "data")
MAX_UPLOAD = 16 * 1024 * 1024
MAX_BODY = 24 * 1024 * 1024
for directory in (CONFIG_DIR, OUTPUTS_DIR, DATA_DIR):
    os.makedirs(directory, exist_ok=True)


class RequestError(ValueError):
    def __init__(self, message, status=HTTPStatus.BAD_REQUEST):
        super().__init__(message)
        self.status = status


def atomic_write(path, content):
    """Never leave half a configuration/upload behind after an interrupted write."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".pending-", dir=os.path.dirname(path))
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def contained_path(root, value):
    path = os.path.realpath(os.path.join(root, value))
    if os.path.commonpath((os.path.realpath(root), path)) != os.path.realpath(root):
        raise RequestError("مسیر فایل خارج از پوشه مجاز است.")
    return path


def key_ref(secret):
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()


def validate_project(project):
    if not isinstance(project, dict):
        raise RequestError("اطلاعات پروژه باید یک شیء باشد.")
    result = copy.deepcopy(project)
    for field in ("id", "title"):
        if not isinstance(result.get(field), str) or not result[field].strip():
            raise RequestError("شناسه و نام پروژه الزامی است.")
    for field in ("input", "output", "doc_title", "main_prompt", "context_prompt", "model", "book_headings"):
        if field in result and not isinstance(result[field], str):
            raise RequestError(f"مقدار {field} باید متن باشد.")
    for field, default, low, high in (("chunk_size", 1800, 1, 1000000), ("max_tokens", 32000, 1, 128000), ("delay", 2, 0, 3600)):
        value = result.get(field, default)
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not low <= value <= high:
            raise RequestError(f"مقدار {field} باید بین {low} و {high} باشد.")
        if field != "delay" and int(value) != value:
            raise RequestError(f"مقدار {field} باید عدد صحیح باشد.")
        result[field] = value if field == "delay" else int(value)
    if result.get("effort", "medium") not in ("low", "medium", "high"):
        raise RequestError("سطح تفکر نامعتبر است.")
    for field in ("enable_context", "include_source_text", "enable_headings"):
        if field in result and not isinstance(result[field], bool):
            raise RequestError(f"مقدار {field} باید روشن یا خاموش باشد.")
    output = result.get("output") or "outputs/" + hashlib.sha256(result["id"].encode()).hexdigest()[:16]
    resolved = contained_path(OUTPUTS_DIR, os.path.join(BASE_DIR, output))
    if resolved == os.path.realpath(OUTPUTS_DIR):
        raise RequestError("نام فایل خروجی را مشخص کنید.")
    result["output"] = os.path.relpath(resolved, BASE_DIR).replace(os.sep, "/")
    return result


def validate_providers(providers, previous):
    if not isinstance(providers, list):
        raise RequestError("فهرست ارائه‌دهنده‌ها نامعتبر است.")
    known = {(p["name"], key_ref(k["key"])): k["key"] for p in previous for k in p.get("keys", []) if isinstance(k.get("key"), str)}
    result, names = [], set()
    for p in providers:
        if not isinstance(p, dict) or not isinstance(p.get("name"), str) or not p["name"].strip() or p["name"] in names:
            raise RequestError("نام ارائه‌دهنده خالی یا تکراری است.")
        names.add(p["name"])
        url = p.get("base_url", "")
        if not isinstance(url, str):
            raise RequestError("نشانی ارائه‌دهنده نامعتبر است.")
        parsed = urllib.parse.urlsplit(url)
        if parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise RequestError("نشانی ارائه‌دهنده باید یک URL معتبر HTTP/HTTPS باشد.")
        if not isinstance(p.get("enabled", True), bool) or not isinstance(p.get("keys", []), list):
            raise RequestError("تنظیمات ارائه‌دهنده نامعتبر است.")
        item = {**p, "keys": []}
        seen = set()
        for key in p.get("keys", []):
            if not isinstance(key, dict):
                raise RequestError("اطلاعات کلید نامعتبر است.")
            secret = key.get("key")
            if secret is None:
                secret = known.get((p["name"], key.get("key_ref")))
            if not isinstance(secret, str) or not secret.strip() or any(c.isspace() for c in secret):
                raise RequestError("کلید خالی یا نامعتبر است؛ فهرست را تازه‌سازی کنید.")
            if secret in seen:
                continue
            seen.add(secret)
            count = key.get("request_count", 0)
            timestamp = key.get("error_timestamp")
            if isinstance(count, bool) or not isinstance(count, int) or count < 0:
                raise RequestError("شمارنده درخواست کلید نامعتبر است.")
            if timestamp is not None and (isinstance(timestamp, bool) or not isinstance(timestamp, (int, float)) or not math.isfinite(timestamp)):
                raise RequestError("زمان خطای کلید نامعتبر است.")
            if not isinstance(key.get("name", ""), str) or key.get("status", "active") not in ("active", "dead", "disabled", "cooldown", "rate_limited", "exhausted"):
                raise RequestError("نام یا وضعیت کلید نامعتبر است.")
            clean = {k: v for k, v in key.items() if k not in ("key_masked", "key_ref")}
            clean["key"] = secret
            item["keys"].append(clean)
        result.append(item)
    return result


class AppState:
    def __init__(self):
        self.lock = threading.RLock()
        self.runner = None
        self.sse_queues = []
        self.logs_history = []
        self.providers_path = os.path.join(CONFIG_DIR, "providers.json")
        self.projects_path = os.path.join(CONFIG_DIR, "projects.json")
        self.config_error = None
        self.load_configs()

    def load_configs(self):
        self.providers, self.projects, self.active_project_id = [], [], ""
        try:
            if os.path.isfile(self.providers_path):
                with open(self.providers_path, encoding="utf-8-sig") as handle:
                    providers = json.load(handle)
                self.providers = validate_providers(providers, [])
            if os.path.isfile(self.projects_path):
                with open(self.projects_path, encoding="utf-8-sig") as handle:
                    data = json.load(handle)
                self.projects = [validate_project(p) for p in data["projects"]]
                if len({p["id"] for p in self.projects}) != len(self.projects):
                    raise ValueError("duplicate project ids")
                self.active_project_id = data.get("active_project_id", "")
                if self.active_project_id not in {p["id"] for p in self.projects}:
                    self.active_project_id = self.projects[0]["id"] if self.projects else ""
        except (OSError, ValueError, TypeError, KeyError):
            self.config_error = "فایل تنظیمات نامعتبر است؛ قبل از ادامه فایل‌های config را اصلاح کنید. فایل اصلی دست‌نخورده باقی مانده است."

    def check_config(self):
        if self.config_error:
            raise RequestError(self.config_error, HTTPStatus.CONFLICT)

    def save_providers(self):
        self.check_config()
        atomic_write(self.providers_path, json.dumps(self.providers, ensure_ascii=False, indent=2).encode("utf-8"))

    def save_projects(self):
        self.check_config()
        atomic_write(self.projects_path, json.dumps({"active_project_id": self.active_project_id, "projects": self.projects}, ensure_ascii=False, indent=2).encode("utf-8"))

    def get_active_project(self):
        return next((p for p in self.projects if p["id"] == self.active_project_id), {})

    def redact(self, text):
        for provider in self.providers:
            for key in provider.get("keys", []):
                secret = key.get("key")
                if secret:
                    text = text.replace(secret, "[REDACTED]")
        return text

    def broadcast_event(self, evt_type, data):
        with self.lock:
            data = json.loads(self.redact(json.dumps(data, ensure_ascii=False)))
            if evt_type == "log":
                self.logs_history.append(data)
                self.logs_history[:] = self.logs_history[-200:]
            msg = f"event: {evt_type}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"
            for q in self.sse_queues:
                try:
                    q.put_nowait(msg)
                except queue.Full:
                    # Keep this subscriber alive; /status restores a missed snapshot.
                    try:
                        q.get_nowait()
                    except queue.Empty:
                        pass
                    try:
                        q.put_nowait(msg)
                    except queue.Full:
                        pass

    def require_idle(self):
        if self.runner and (self.runner.is_running or (getattr(self.runner, "worker_thread", None) and self.runner.worker_thread.is_alive())):
            raise RequestError("تا پایان پردازش یا توقف کامل صبر کنید.", HTTPStatus.CONFLICT)

    def discard_runner(self):
        self.require_idle()
        if self.runner:
            close = getattr(self.runner, "close", None) or getattr(self.runner.ledger, "close", None)
            if close:
                close()
        self.runner = None
        self.logs_history.clear()

    def get_or_create_runner(self):
        with self.lock:
            self.check_config()
            if self.runner is None:
                project = self.get_active_project()
                if not project:
                    raise RequestError("ابتدا یک پروژه ایجاد کنید.", HTTPStatus.CONFLICT)
                candidate = Runner(copy.deepcopy(project), copy.deepcopy(self.providers), BASE_DIR, self.broadcast_event)
                try:
                    candidate.prepare()
                except Exception:
                    close = getattr(candidate.ledger, "close", None)
                    if close:
                        close()
                    raise
                self.runner = candidate
            return self.runner

    def public_providers(self):
        result = copy.deepcopy(self.providers)
        for p in result:
            for k in p.get("keys", []):
                secret = k.pop("key", "")
                k["key_ref"] = key_ref(secret)
                k["key_masked"] = secret[:5] + "…" + secret[-4:] if len(secret) > 12 else "***"
        return result


GLOBAL_STATE = AppState()


class ThreadedHTTPServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True
    allow_reuse_address = True


class RequestHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=STATIC_DIR, **kwargs)

    def log_message(self, format, *args):
        pass

    def end_headers(self):
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Frame-Options", "DENY")
        super().end_headers()

    def send_json(self, data, status=HTTPStatus.OK):
        body = GLOBAL_STATE.redact(json.dumps(data, ensure_ascii=False)).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def check_origin(self):
        # Dedicated cloud/local instance for user; permit all browser navigations and API requests.
        return

    def dispatch(self, method):
        try:
            self.check_origin()
            method()
        except (BrokenPipeError, ConnectionResetError):
            pass
        except RequestError as exc:
            self.send_json({"error": GLOBAL_STATE.redact(str(exc))}, exc.status)
        except (ValueError, TypeError, FileNotFoundError, UnicodeError, zipfile.BadZipFile) as exc:
            self.send_json({"error": GLOBAL_STATE.redact(str(exc))}, HTTPStatus.BAD_REQUEST)
        except Exception:
            self.send_json({"error": "خطای داخلی در خواندن یا ذخیره اطلاعات؛ مسیرها و دسترسی فایل‌ها را بررسی کنید."}, HTTPStatus.INTERNAL_SERVER_ERROR)
        finally:
            # ThreadedHTTPServer uses a fresh thread per request; do not retain one
            # SQLite connection for every status poll throughout a long book.
            with GLOBAL_STATE.lock:
                if GLOBAL_STATE.runner:
                    ledger = getattr(GLOBAL_STATE.runner, "ledger", None)
                    if ledger is not None:
                        ledger.close(current_thread_only=True)

    def do_OPTIONS(self):
        self.dispatch(lambda: self.send_json({"error": "درخواست بین‌سایتی پشتیبانی نمی‌شود."}, HTTPStatus.METHOD_NOT_ALLOWED))

    def do_HEAD(self):
        self.dispatch(lambda: self.send_json({"error": "از GET استفاده کنید."}, HTTPStatus.METHOD_NOT_ALLOWED))

    def do_GET(self):
        self.dispatch(self.get_route)

    def get_route(self):
        parsed = urllib.parse.urlsplit(self.path)
        path = parsed.path
        if path == "/api/events":
            return self.handle_sse()
        if path == "/api/status":
            with GLOBAL_STATE.lock:
                data = {"is_running": False, "ready": False, "stats": {}, "active_project": copy.deepcopy(GLOBAL_STATE.get_active_project())}
                try:
                    r = GLOBAL_STATE.get_or_create_runner()
                    data.update(ready=True, is_running=r.is_running, current_idx=r.current_chunk_idx, streaming_text=r.current_streaming_text, stats=r.ledger.get_stats(), active_keys=r.key_pool.active_count(), total_keys=r.key_pool.total_count())
                except (ValueError, OSError) as exc:
                    data["error"] = GLOBAL_STATE.redact(str(exc))
                data["recent_logs"] = GLOBAL_STATE.logs_history[-50:]
                return self.send_json(data)
        if path == "/api/projects":
            with GLOBAL_STATE.lock:
                GLOBAL_STATE.check_config()
                return self.send_json({"active_id": GLOBAL_STATE.active_project_id, "projects": GLOBAL_STATE.projects})
        if path == "/api/providers":
            with GLOBAL_STATE.lock:
                GLOBAL_STATE.check_config()
                pool = GLOBAL_STATE.runner.key_pool if GLOBAL_STATE.runner else KeyPool(GLOBAL_STATE.providers)
                return self.send_json({"providers": GLOBAL_STATE.public_providers(), "pool_snapshot": pool.snapshot()})
        if path == "/api/chunks" or path.startswith("/api/chunks/"):
            with GLOBAL_STATE.lock:
                r = GLOBAL_STATE.get_or_create_runner()
                if path == "/api/chunks":
                    # No silent 500-chunk truncation; metadata list only, full text on demand.
                    return self.send_json(r.ledger.get_all_chunks(limit=1000000))
                idx = int(path.rsplit("/", 1)[-1])
                chunk = r.ledger.get_chunk(idx)
                if not chunk:
                    raise RequestError("قطعه یافت نشد.", HTTPStatus.NOT_FOUND)
                return self.send_json(chunk)
        if path == "/api/outputs":
            files = []
            for root, dirs, names in os.walk(OUTPUTS_DIR):
                for name in names:
                    full = os.path.join(root, name)
                    if name.endswith((".docx", ".html", ".db")) and os.path.isfile(full) and not os.path.islink(full):
                        stat = os.stat(full)
                        files.append({"name": os.path.relpath(full, OUTPUTS_DIR).replace(os.sep, "/"), "size": stat.st_size, "modified": time.strftime("%Y-%m-%d %H:%M", time.localtime(stat.st_mtime))})
            return self.send_json(sorted(files, key=lambda f: f["name"]))
        if path == "/api/ping":
            with GLOBAL_STATE.lock:
                is_run = bool(GLOBAL_STATE.runner and GLOBAL_STATE.runner.is_running)
                return self.send_json({"status": "ok", "timestamp": time.time(), "is_running": is_run})
        if path == "/api/settings":
            from core.keep_alive import load_settings
            return self.send_json(load_settings())
        if path.startswith("/api/download/"):
            return self.download(urllib.parse.unquote(path[len("/api/download/"):]))
        if path.startswith("/api/"):
            raise RequestError("مسیر یافت نشد.", HTTPStatus.NOT_FOUND)
        if path == "/":
            self.path = "/index.html"
        else:
            full = contained_path(STATIC_DIR, urllib.parse.unquote(path).lstrip("/"))
            if not os.path.isfile(full):
                raise RequestError("فایل یافت نشد.", HTTPStatus.NOT_FOUND)
        return super().do_GET()

    def download(self, name):
        full = contained_path(OUTPUTS_DIR, name)
        if not os.path.isfile(full) or not name.endswith((".docx", ".html", ".db")):
            raise RequestError("فایل یافت نشد.", HTTPStatus.NOT_FOUND)
        # SQLite backup includes committed WAL pages rather than copying a live main file.
        with tempfile.TemporaryFile() as snapshot:
            if name.endswith(".db"):
                fd, backup_path = tempfile.mkstemp(suffix=".db")
                os.close(fd)
                try:
                    with sqlite3.connect(full) as source, sqlite3.connect(backup_path) as target:
                        source.backup(target)
                    source.close()
                    target.close()
                    with open(backup_path, "rb") as handle:
                        shutil.copyfileobj(handle, snapshot)
                finally:
                    os.unlink(backup_path)
            else:
                with open(full, "rb") as handle:
                    shutil.copyfileobj(handle, snapshot)
            size = snapshot.tell()
            snapshot.seek(0)
            self.send_response(HTTPStatus.OK)
            content_type = "text/html; charset=utf-8" if name.endswith(".html") else "application/vnd.openxmlformats-officedocument.wordprocessingml.document" if name.endswith(".docx") else "application/octet-stream"
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Disposition", "attachment; filename=\"download" + os.path.splitext(name)[1] + "\"; filename*=UTF-8''" + urllib.parse.quote(os.path.basename(name)))
            self.send_header("Content-Security-Policy", "sandbox")
            self.send_header("Content-Length", str(size))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            shutil.copyfileobj(snapshot, self.wfile)

    def read_body(self):
        if self.headers.get("Transfer-Encoding"):
            raise RequestError("Transfer-Encoding پشتیبانی نمی‌شود.")
        try:
            size = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            raise RequestError("اندازه درخواست نامعتبر است.")
        if size < 0 or size > MAX_BODY:
            raise RequestError("اندازه درخواست بیش از حد مجاز است.", HTTPStatus.REQUEST_ENTITY_TOO_LARGE)
        if size and self.headers.get_content_type() != "application/json":
            raise RequestError("درخواست باید JSON باشد.", HTTPStatus.UNSUPPORTED_MEDIA_TYPE)
        self.connection.settimeout(30)
        raw = self.rfile.read(size)
        if len(raw) != size:
            raise RequestError("داده درخواست ناقص است.")
        try:
            body = json.loads(raw.decode("utf-8")) if raw else {}
        except (ValueError, UnicodeError):
            raise RequestError("JSON ارسالی نامعتبر است.")
        if not isinstance(body, dict):
            raise RequestError("بدنه درخواست باید یک شیء JSON باشد.")
        return body

    def do_POST(self):
        self.dispatch(self.post_route)

    def post_route(self):
        path = urllib.parse.urlsplit(self.path).path
        body = self.read_body()
        with GLOBAL_STATE.lock:
            state = GLOBAL_STATE
            state.check_config()
            if path in ("/api/start", "/api/stop", "/api/retry_failed", "/api/render"):
                r = state.get_or_create_runner()
                if path == "/api/start":
                    r.start()
                    return self.send_json({"status": "started"})
                if path == "/api/stop":
                    r.stop()
                    return self.send_json({"status": "stopping" if r.is_running else "stopped"})
                state.require_idle()
                if path == "/api/retry_failed":
                    r.retry_failed()
                    return self.send_json({"status": "retrying"})
                paths = r.render_outputs()
                if not paths:
                    raise RequestError("هنوز قطعه تکمیل‌شده‌ای برای ساخت خروجی وجود ندارد.", HTTPStatus.CONFLICT)
                return self.send_json({"status": "rendered", "paths": paths})
            state.require_idle()
            if path == "/api/projects/switch":
                if body.get("id") not in {p["id"] for p in state.projects}:
                    raise RequestError("پروژه یافت نشد.", HTTPStatus.NOT_FOUND)
                state.active_project_id = body["id"]
                state.save_projects()
                state.discard_runner()
                return self.send_json({"status": "switched", "project": state.get_active_project()})
            if path == "/api/projects/save":
                project = validate_project(body.get("project"))
                for other in state.projects:
                    if other["id"] != project["id"] and os.path.normcase(os.path.abspath(os.path.join(BASE_DIR, other["output"]))) == os.path.normcase(os.path.abspath(os.path.join(BASE_DIR, project["output"]))):
                        raise RequestError("هر پروژه باید نام خروجی مستقل داشته باشد.")
                state.projects = [project if p["id"] == project["id"] else p for p in state.projects] if any(p["id"] == project["id"] for p in state.projects) else state.projects + [project]
                if not state.active_project_id:
                    state.active_project_id = project["id"]
                state.save_projects()
                if state.active_project_id == project["id"]:
                    state.discard_runner()
                return self.send_json({"status": "saved", "project": project})
            if path == "/api/providers/save":
                providers = validate_providers(body.get("providers"), state.providers)
                state.providers = providers
                state.save_providers()
                if state.runner:
                    state.runner.providers = copy.deepcopy(providers)
                    state.runner.key_pool.reload_providers(providers)
                return self.send_json({"status": "saved"})
            if path == "/api/settings":
                from core.keep_alive import save_settings, load_settings
                curr = load_settings()
                curr.update(body)
                save_settings(curr)
                return self.send_json({"status": "saved", "settings": curr})
            if path == "/api/settings/test_bale":
                from core.keep_alive import load_settings, send_bale_message
                cfg = load_settings()
                token = cfg.get("bale_bot_token")
                chat_id = cfg.get("bale_chat_id")
                if not token or not chat_id:
                    raise RequestError("ابتدا در بله به ربات @mytetsbot پیام start/ بدهید تا آیدی شما ثبت شود.")
                ok = send_bale_message(token, chat_id, "🔔 پیام تست از سرور جزوه‌ساز (Render)!\nارتباط بین سرور و ربات بله کاملاً برقرار است.")
                if not ok:
                    raise RequestError("ارسال پیام به بله ناموفق بود. اتصال یا توکن را بررسی کنید.")
                return self.send_json({"status": "sent"})
            if path == "/api/keys/bulk":
                return self.bulk_keys(body)
            if path == "/api/upload":
                return self.upload(body)
        raise RequestError("مسیر یافت نشد.", HTTPStatus.NOT_FOUND)

    def bulk_keys(self, body):
        state = GLOBAL_STATE
        name, raw = body.get("provider"), body.get("keys")
        if not isinstance(name, str) or not name.strip() or not isinstance(raw, str) or not raw.strip():
            raise RequestError("نام ارائه‌دهنده و کلیدها الزامی است.")
        providers = copy.deepcopy(state.providers)
        target = next((p for p in providers if p["name"] == name), None)
        if target is None:
            defaults = {"Coarena AI": "https://coarena.ai/api", "OpenRouter": "https://openrouter.ai/api/v1", "Radox AI": "https://radoxai.com"}
            url = body.get("base_url") or defaults.get(name)
            if not url:
                raise RequestError("ابتدا نشانی واقعی این ارائه‌دهنده را در تنظیمات تعریف کنید.")
            target = {"name": name, "base_url": url, "enabled": True, "keys": []}
            providers.append(target)
        existing = {k["key"] for k in target["keys"]}
        added = 0
        for line in raw.splitlines():
            line = line.strip()
            if not line:
                continue
            label, secret = line.split(":", 1) if ":" in line else (f"کلید {len(target['keys']) + 1}", line)
            secret = secret.strip()
            if secret in existing:
                continue
            target["keys"].append({"name": label.strip(), "key": secret, "status": "active", "request_count": 0, "error_timestamp": None})
            existing.add(secret)
            added += 1
        state.providers = validate_providers(providers, state.providers)
        state.save_providers()
        if state.runner:
            state.runner.providers = copy.deepcopy(state.providers)
            state.runner.key_pool.reload_providers(state.providers)
        return self.send_json({"status": "bulk_added", "added_count": added})

    def upload(self, body):
        state = GLOBAL_STATE
        project = state.get_active_project()
        if not project:
            raise RequestError("ابتدا یک پروژه ایجاد کنید.", HTTPStatus.CONFLICT)
        filename = body.get("filename", "uploaded_doc.txt")
        if not isinstance(filename, str) or not filename or "/" in filename or "\\" in filename or ":" in filename or filename in (".", "..") or "\x00" in filename:
            raise RequestError("نام فایل نامعتبر است.")
        suffix = os.path.splitext(filename)[1].lower()
        if suffix not in (".txt", ".md", ".docx"):
            raise RequestError("فقط فایل txt، md و docx پشتیبانی می‌شود.")
        content = body.get("content")
        if not isinstance(content, str):
            raise RequestError("محتوای فایل نامعتبر است.")
        encoding = body.get("encoding", "utf-8")
        if encoding == "base64":
            try:
                raw_b64 = content.split(",", 1)[1] if "," in content else content
                payload = base64.b64decode(raw_b64.strip(), validate=True)
            except (ValueError, binascii.Error):
                raise RequestError("داده Base64 نامعتبر است.")
        elif encoding == "utf-8" and suffix != ".docx":
            payload = content.encode("utf-8")
        else:
            raise RequestError("فایل Word باید به صورت باینری Base64 ارسال شود.")
        if len(payload) > MAX_UPLOAD:
            raise RequestError("حداکثر حجم فایل ۱۶ مگابایت است.", HTTPStatus.REQUEST_ENTITY_TOO_LARGE)
        if suffix == ".docx":
            try:
                with zipfile.ZipFile(io.BytesIO(payload)) as archive:
                    if sum(i.file_size for i in archive.infolist()) > 64 * 1024 * 1024 or "word/document.xml" not in archive.namelist():
                        raise RequestError("فایل Word نامعتبر یا بیش از حد بزرگ است.")
                    root = ElementTree.fromstring(archive.read("word/document.xml"))
                    if not "".join(root.itertext()).strip():
                        raise RequestError("فایل ورودی خالی است.")
            except (zipfile.BadZipFile, KeyError, ElementTree.ParseError):
                raise RequestError("فایل Word خراب یا نامعتبر است.")
        else:
            text = payload.decode("utf-8-sig")
            if not text.strip() or "\x00" in text:
                raise RequestError("فایل متنی خالی یا نامعتبر است؛ از UTF-8 استفاده کنید.")
        # Unique names preserve sources belonging to other projects and previous runs.
        filename = uuid.uuid4().hex + suffix
        target = contained_path(DATA_DIR, filename)
        atomic_write(target, payload)
        project["input"] = os.path.relpath(target, BASE_DIR).replace(os.sep, "/")
        state.save_projects()
        state.discard_runner()
        return self.send_json({"status": "uploaded", "path": project["input"]})

    def handle_sse(self):
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache, no-store")
        self.send_header("Connection", "keep-alive")
        self.end_headers()
        self.connection.settimeout(30)
        q = queue.Queue(maxsize=100)
        with GLOBAL_STATE.lock:
            GLOBAL_STATE.sse_queues.append(q)
        try:
            self.wfile.write(b'event: connect\ndata: {"connected":true}\n\n')
            self.wfile.flush()
            while True:
                try:
                    msg = q.get(timeout=15)
                except queue.Empty:
                    msg = ": keepalive\n\n"
                self.wfile.write(msg.encode("utf-8"))
                self.wfile.flush()
        except (OSError, TimeoutError):
            pass
        finally:
            with GLOBAL_STATE.lock:
                if q in GLOBAL_STATE.sse_queues:
                    GLOBAL_STATE.sse_queues.remove(q)


def run_server(port=None, host=None):
    if port is None:
        port = int(os.environ.get("PORT", 7860))
    if host is None:
        host = os.environ.get("HOST", "0.0.0.0")
    from core.keep_alive import GLOBAL_KEEPER
    GLOBAL_KEEPER.state_getter = lambda: {
        "is_running": bool(GLOBAL_STATE.runner and GLOBAL_STATE.runner.is_running),
        "stats": GLOBAL_STATE.runner.ledger.get_stats() if GLOBAL_STATE.runner else {},
        "active_project_title": GLOBAL_STATE.get_active_project().get("title", "")
    }
    GLOBAL_KEEPER.start()
    server = ThreadedHTTPServer((host, port), RequestHandler)
    print(f"Jozve Saz Android (Cloud / Local): http://{host}:{server.server_port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        GLOBAL_KEEPER.stop()
        if GLOBAL_STATE.runner:
            GLOBAL_STATE.runner.stop()
        server.server_close()


if __name__ == "__main__":
    p = int(sys.argv[1]) if len(sys.argv) > 1 else None
    run_server(p)
