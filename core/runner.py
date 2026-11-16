"""هماهنگ‌کننده‌ی اصلی اجرای تولید جزوه در اندروید (برگرفته از Mix jozve).

وظایف:
- اجرای امن در پس‌زمینه بدون مسدودسازی رابط کاربری
- ارتباط زنده با کلاینت اندروید از طریق ارسال رویدادها (SSE)
- قطعه‌بندی منبع و راه‌اندازی دفترچه SQLite
- چرخش کلیدها و ارسال درخواست به ApiEngine
- ذخیره آنی خروجی‌ها در صورت اتمام هر قطعه
"""
import os
import re
import copy
import math
import threading
import time

from .api_engine import ApiEngine, EngineError
from .chunker import build_chunks_from_source
from .key_pool import KeyPool
from .ledger import Ledger
from .prompt_builder import build_system_and_user_prompts
from .renderer import render_all_outputs

SAMPLE_TEXT = """بسم الله الرحمن الرحيم
الحمد لله رب العالمين والصلاة والسلام على سيدنا محمد وآله الطاهرين.

الفصل الأول: في تعريف علم الأصول وموضوعه
علم الأصول هو العلم بالقواعد الممهدة لاستنباط الأحكام الشرعية الفرعية عن أدلتها المعتبرة. وموضوع هذا العلم هو كل ما يقع في طريق استنباط الحكم الشرعي من الأدلة المشتركة في الفقه.

وقد اختلف الأعلام في تحديد موضوع هذا العلم، فذهب صاحب الكفاية قدس سره إلى أن موضوع كل علم هو ما يبحث فيه عن عوارضه الذاتية، فلا يشترط وجود موضوع واحد جامع لجميع مسائل العلم، بل يكفي ترابط المسائل بالغاية والغرض المترتب عليها.

المبحث الثاني: في حجية القطع وأقسامه
القطع حجة بذاته، وحجيته غير قابلة للجعل تشريعاً ولا للسلب نفياً، لأن القطع هو الرؤية والانكشاف التام، ومع الانكشاف يمتنع التعبد بالخلاف أو نفي الحجية. وينقسم القطع إلى طريقي وموضوعي.
فالطريقي هو ما كان وسيلة ومحض مرآة للواقع، والموضوعي هو ما أُخذ في لسان الدليل جزءاً من الموضوع أو تمام الموضوع للحكم الشرعي.
"""


class Runner:
    def __init__(self, project: dict, providers: list, base_dir: str, event_callback=None):
        self.project = copy.deepcopy(project)
        self.providers = copy.deepcopy(providers)
        self.base_dir = base_dir
        self.event_cb = event_callback or (lambda evt, data: None)

        self._state_lock = threading.Lock()
        self._render_lock = threading.Lock()
        self._prepared = False
        self.key_pool = KeyPool(providers)
        self.cancel_event = threading.Event()
        self.is_running = False
        self.worker_thread = None
        self.current_chunk_idx = None
        self.current_streaming_text = ""

        # Calculate clean, predictable output filename matching project title
        title = (project.get("title") or project.get("doc_title") or "").strip()
        safe_title = re.sub(r'[\\/*?:"<>|]', '_', title).strip() or "جزوه"
        out_path = (project.get("output") or "").strip()
        if not out_path or "project-" in out_path or re.match(r"^outputs/[0-9a-f]{12,64}$", out_path):
            out_path = os.path.join(base_dir, "outputs", safe_title)
        elif not os.path.isabs(out_path):
            out_path = os.path.join(base_dir, out_path)
        self.output_base = out_path
        self.ledger_path = self.output_base + ".mixledger.db"
        self.ledger = Ledger(self.ledger_path)

    def _emit(self, event, data):
        data = {**data, "project_id": self.project.get("id")}
        try:
            self.event_cb(event, data)
        except Exception:
            # A disconnected UI must never fail paid generation or ledger writes.
            pass

    def log(self, level: str, msg: str):
        self._emit("log", {"level": level, "message": msg, "time": time.strftime("%H:%M:%S")})

    def update_status(self):
        stats = self.ledger.get_stats()
        self._emit("status", {
            "is_running": self.is_running,
            "current_idx": self.current_chunk_idx,
            "streaming_text": self.current_streaming_text,
            "stats": stats,
            "active_keys": self.key_pool.active_count(),
            "total_keys": self.key_pool.total_count()
        })

    def prepare(self):
        if self.is_running or (self.worker_thread and self.worker_thread.is_alive()):
            raise RuntimeError("Cannot prepare a running project")
        self._prepared = False
        inp = (self.project.get("input") or "").strip()
        if not inp:
            raise ValueError("Select an input document before starting")
        if not os.path.isabs(inp):
            inp = os.path.join(self.base_dir, inp)
        inp = os.path.abspath(inp)
        chunk_size = int(self.project.get("chunk_size", 1300))
        single_chunk = bool(self.project.get("single_chunk", False))
        if chunk_size <= 0:
            raise ValueError("chunk_size must be positive")
        chunks, meta = build_chunks_from_source(inp, chunk_size=chunk_size, single_chunk=single_chunk)
        # Rendering titles/delays can change without invalidating paid content.
        meta["generation"] = {k: self.project.get(k) for k in (
            "id", "main_prompt", "context_prompt", "enable_context", "context_mode",
            "model", "effort", "max_tokens", "single_chunk")}
        self.ledger.recover_claimed()
        archive = self.ledger.init_chunks(chunks, meta)
        if archive:
            self.log("warning", "Source/settings changed; previous ledger preserved: " + os.path.basename(archive))
        self._prepared = True
        self.update_status()

    def start(self):
        with self._state_lock:
            if self.is_running or (self.worker_thread and self.worker_thread.is_alive()):
                return False
            # Detect edits to the input file/project after construction, archiving old work if needed.
            self.prepare()
            self.cancel_event.clear()
            self.is_running = True
            thread = threading.Thread(target=self._run_loop, daemon=True)
            self.worker_thread = thread
            try:
                thread.start()
            except BaseException:
                self.is_running = False
                self.worker_thread = None
                raise
        self.log("info", "Generation started")
        self.update_status()
        return True

    def stop(self):
        # Remain running until the worker has released its claim/resources.
        if self.is_running:
            self.cancel_event.set()
            self.log("warning", "Stop requested; waiting for current request to unwind")
            self.update_status()

    def close(self):
        if self.is_running or (self.worker_thread and self.worker_thread.is_alive()):
            raise RuntimeError("Stop the runner and wait for its worker before closing")
        self.ledger.close()

    def retry_failed(self):
        self.ledger.retry_all_failed()
        self.log("info", "تمام قطعات ناموفق مجدداً به صف انجام بازگردانده شدند.")
        self.update_status()

    def render_outputs(self):
        with self._render_lock:
            chunks = self.ledger.get_done_chunks()
            if not any(chunk["state"] == "done" for chunk in chunks):
                return {}
            title = self.project.get("doc_title") or self.project.get("title") or "جزوه"
            paths = render_all_outputs(chunks, title, self.output_base)
        self.log("success", "Output files updated")
        return paths

    def _safe_render(self):
        try:
            self.render_outputs()
        except Exception as exc:
            self.log("error", "Output export failed; completed text is safe in the ledger: " + type(exc).__name__)

    def _notify_bale_completion(self):
        try:
            from .keep_alive import load_settings, send_bale_message, send_bale_document
            cfg = load_settings()
            if cfg.get("enable_bale_notifications") and cfg.get("bale_bot_token") and cfg.get("bale_chat_id"):
                tok = cfg["bale_bot_token"]
                cid = cfg["bale_chat_id"]
                send_bale_message(tok, cid, f"🎉 پروژه «{self.project.get('title')}» با موفقیت تکمیل شد!\nفایل‌های جزوه تولید شدند.")
                docx_path = self.output_base + ".docx"
                if os.path.exists(docx_path):
                    send_bale_document(tok, cid, docx_path, caption=f"📄 فایل Word جزوه: {self.project.get('title')}")
                html_path = self.output_base + ".html"
                if os.path.exists(html_path):
                    send_bale_document(tok, cid, html_path, caption=f"🌐 کتابخوان HTML جزوه: {self.project.get('title')}")
        except Exception:
            pass

    def _run_loop(self):
        engine = None
        claimed = None
        try:
            engine = ApiEngine(
                key_pool=self.key_pool,
                model_display=self.project.get("model", "Claude Sonnet 5.5"),
                effort=self.project.get("effort", "medium"),
                max_tokens=self.project.get("max_tokens", 32000))
            delay_s = float(self.project.get("delay", 2.0))
            if not math.isfinite(delay_s) or delay_s < 0:
                raise ValueError("delay must be finite and nonnegative")
            self.ledger.recover_claimed()
            while not self.cancel_event.is_set():
                chunk = self.ledger.claim_next(worker_id="android-api-worker")
                if chunk is None:
                    stats = self.ledger.get_stats()
                    if stats["failed"] or stats["claimed"]:
                        self.log("warning", "Queue finished with incomplete chunks")
                    else:
                        self.log("success", "All chunks completed")
                        self._notify_bale_completion()
                    self._safe_render()
                    break
                idx = claimed = chunk["idx"]
                self.current_chunk_idx = idx
                self.current_streaming_text = ""
                self.update_status()

                def on_delta(delta):
                    self.current_streaming_text += delta
                    self._emit("stream", {"idx": idx, "delta": delta, "full": self.current_streaming_text})

                def on_reset():
                    self.current_streaming_text = ""
                    self._emit("stream", {"idx": idx, "delta": "", "full": ""})

                try:
                    prev_ar, prev_pe = self.ledger.get_previous_context(idx)
                    system, user = build_system_and_user_prompts(
                        self.project, chunk, prev_arabic=prev_ar, prev_persian=prev_pe)
                    result = engine.generate(system_prompt=system, user_content=user,
                                             cancel=self.cancel_event, on_delta=on_delta, on_reset=on_reset)
                    if self.cancel_event.is_set():
                        self.ledger.release_chunk(idx)
                        break
                    self.ledger.complete_chunk(
                        idx=idx, persian_text=result.text, model=result.model, thinking=result.thinking,
                        input_tokens=result.usage.get("input_tokens", 0),
                        output_tokens=result.usage.get("output_tokens", 0),
                        latency_ms=result.latency_ms, cost_usd=result.cost_usd)
                    claimed = None
                    self._safe_render()
                except EngineError as exc:
                    if self.cancel_event.is_set() or exc.kind == "ABORTED":
                        self.ledger.release_chunk(idx)
                        break
                    if exc.kind in ("KEY_EXHAUSTED", "AUTH", "RATE_LIMIT", "CONFIG"):
                        self.ledger.release_chunk(idx)
                        self.log("error", exc.message)
                        break
                    self.ledger.fail_chunk(idx, exc.message)
                    self.log("error", exc.message)
                except Exception as exc:
                    if self.cancel_event.is_set():
                        self.ledger.release_chunk(idx)
                        break
                    self.ledger.fail_chunk(idx, "Generation failed: " + type(exc).__name__)
                    self.log("error", "Generation failed: " + type(exc).__name__)
                finally:
                    # Also covers failures in prompt construction or ledger completion.
                    self.ledger.release_chunk(idx)
                    claimed = None
                    self.current_chunk_idx = None
                    self.current_streaming_text = ""
                    self.update_status()
                if self.cancel_event.wait(delay_s):
                    break
        except Exception as exc:
            self.log("error", "Runner failed: " + type(exc).__name__)
        finally:
            try:
                if claimed is not None:
                    self.ledger.release_chunk(claimed)
                if engine is not None:
                    engine.close()
                self.current_chunk_idx = None
                self.current_streaming_text = ""
                # Close this worker's SQLite connection before publishing idle.
                self.ledger.close(current_thread_only=True)
            finally:
                with self._state_lock:
                    self.is_running = False
                try:
                    self.update_status()
                finally:
                    self.ledger.close(current_thread_only=True)
