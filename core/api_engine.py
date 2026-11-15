"""موتور اختصاصی API کلاد برای جزوه‌ساز اندروید (برگرفته از موتور Mix jozve).

امکانات ویژه:
- کلاینت SSE استریمینگ زنده
- حالت‌های تفکر (Thinking: low, medium, high, adaptive)
- سیستم بازآزمایی در صورت برخورد به max_tokens (افزایش هوشمند تا ۶۴۰۰۰ توکن)
- حذف خودکار cache_control در صورتی که ارائه‌دهنده پشتیبانی نکند
- گزارش بی‌درنگ توکن‌ها و محاسبه تخمینی هزینه
- کاملاً سبک بدون وابستگی‌های سنگین
"""
import json
import time
from urllib.parse import urlsplit
import requests

MAX_TOKENS_CAP = 64000

EFFORT_BUDGET_TOKENS = {
    "low": 2048,
    "medium": 8192,
    "high": 24576,
}

MODEL_OPTIONS = {
    # عناوین استاندارد و خودکار (تطبیق هوشمند با بهترین مدل فعال هر ارائه‌دهنده)
    "Claude Sonnet 5.5": "claude-sonnet-5-5",
    "Claude Opus 5.5": "claude-opus-5-5",
    "Claude Haiku 5.5": "claude-haiku-5-5",
    "Claude 3.7 Sonnet": "claude-3-7-sonnet-20250219",
    "Claude 3.5 Sonnet": "claude-3-5-sonnet-20241022",
    # مدل‌های فعال اختصاصی Coarena AI
    "GPT-6 Astra [Coarena]": "gpt-6-astra",
    "GPT-6.1 Sol [Coarena]": "gpt-6-1-sol",
    "GPT-6.1 Terra [Coarena]": "gpt-6-1-terra",
    "Gemini 4 Argon [Coarena]": "gemini-4-argon",
    "Gemini 4 Carbon [Coarena]": "gemini-4-carbon",
    "Gemini 3.8 Flash [Coarena]": "gemini-3-8-flash",
    "DeepSeek V4.1 Flash [Coarena]": "deepseek-v4-1-flash-1017",
    "Kimi K3.1 Pro [Coarena]": "kimi-k3-1-pro",
    "Grok 4.7 [Coarena]": "grok-4-7",
    # مدل‌های کلیدی Radox و OpenRouter
    "Claude Fable 5 [Radox]": "claude-fable-5",
    "Gemini Pro": "gemini-1.5-pro",
    "Gemini Flash": "gemini-1.5-flash",
}

COARENA_MODEL_MAPPINGS = {
    "claude-sonnet-5-5": "gpt-6-astra",
    "claude-3-7-sonnet-20250219": "gpt-6-astra",
    "claude-3-5-sonnet-20241022": "gpt-6-astra",
    "claude-fable-5-2": "gpt-6-astra",
    "claude-fable-5-1": "gpt-6-astra",
    "claude-fable-5": "gpt-6-astra",
    "claude-opus-5-5": "gpt-6-1-sol",
    "claude-3-opus-20240229": "gpt-6-1-sol",
    "claude-haiku-5-5": "gemini-3-8-flash",
    "claude-3-5-haiku-20241022": "gemini-3-8-flash",
    "gemini-1.5-pro": "gemini-4-argon",
    "gemini-pro": "gemini-4-argon",
    "gemini-1.5-flash": "gemini-3-8-flash",
    "gemini-flash": "gemini-3-8-flash",
}

PRICING_DEFAULTS = {
    "claude-sonnet-5-5": {"input": 3.0, "output": 15.0, "cache_read": 0.3, "cache_write": 3.75},
    "claude-opus-5-5": {"input": 15.0, "output": 75.0, "cache_read": 1.5, "cache_write": 18.75},
    "claude-haiku-5-5": {"input": 0.8, "output": 4.0, "cache_read": 0.08, "cache_write": 1.0},
    "gpt-6-astra": {"input": 3.0, "output": 15.0, "cache_read": 0.3, "cache_write": 3.75},
    "gpt-6-1-sol": {"input": 15.0, "output": 75.0, "cache_read": 1.5, "cache_write": 18.75},
    "gemini-4-argon": {"input": 2.0, "output": 10.0, "cache_read": 0.2, "cache_write": 2.5},
    "gemini-3-8-flash": {"input": 0.5, "output": 2.0, "cache_read": 0.05, "cache_write": 0.5},
    "deepseek-v4-1-flash-1017": {"input": 0.3, "output": 1.2, "cache_read": 0.03, "cache_write": 0.3},
}

_MODELS_CACHE = {"ts": 0.0, "models": []}


def resolve_model_id(display_or_id: str) -> str:
    val = (display_or_id or "").strip()
    if val in MODEL_OPTIONS:
        return MODEL_OPTIONS[val]
    raw = val.split(" [")[0].strip()
    if raw in MODEL_OPTIONS:
        return MODEL_OPTIONS[raw]
    low = raw.lower()
    for k, v in MODEL_OPTIONS.items():
        if k.lower() == low or k.lower().startswith(low):
            return v
    return raw or "claude-sonnet-5-5"


def map_model_for_provider(provider_name: str, model_id: str) -> str:
    m = (model_id or "").strip()
    pname = (provider_name or "").lower()

    # ۱. نگاشت هوشمند مدل‌ها در Coarena AI (دقیقاً مشابه موتور پروکسی در Mix jozve)
    if "coarena" in pname:
        m_lower = m.lower()
        if m_lower in COARENA_MODEL_MAPPINGS:
            return COARENA_MODEL_MAPPINGS[m_lower]
        clean_m = m_lower.replace("claude-", "")
        if clean_m in COARENA_MODEL_MAPPINGS:
            return COARENA_MODEL_MAPPINGS[clean_m]
        return m

    # ۲. نگاشت در OpenRouter
    if "openrouter" in pname and "/" not in m:
        if m.startswith("claude-"):
            return "anthropic/" + m
        if m.startswith("gemini-"):
            return "google/" + m
        if m.startswith("deepseek-"):
            return "deepseek/" + m

    # ۳. نگاشت در Radox AI
    if "radox" in pname:
        if m in ("claude-sonnet-5-5", "claude-opus-5-5"):
            return "claude-fable-5"

    # ۴. نگاشت در ChatGPT / Xingchi API
    if "xingchi" in pname or "chatgpt" in pname:
        m_lower = m.lower()
        if "astra" in m_lower or "sonnet" in m_lower:
            return "gpt-6-astra"
        if "sol" in m_lower or "opus" in m_lower:
            return "gpt-6.1-sol"
        if "haiku" in m_lower or "flash" in m_lower:
            return "gpt-4o"
        if m_lower in ("gpt-6.1 sol", "gpt-6.1-sol"):
            return "gpt-6.1-sol"
        if m_lower in ("gpt-6 astra", "gpt-6-astra"):
            return "gpt-6-astra"
        return m or "gpt-6-astra"

    return m


def fetch_available_models(providers: list, force: bool = False) -> list:
    """دریافت زنده و پویای تمام مدل‌های فعال بر اساس کلیدهای موجود در ارائه‌دهندگان."""
    global _MODELS_CACHE
    now = time.time()
    if not force and _MODELS_CACHE["models"] and (now - _MODELS_CACHE["ts"] < 300):
        return _MODELS_CACHE["models"]

    models = []
    seen = set()

    def add_item(val, label=None):
        if not val or val in seen:
            return
        seen.add(val)
        models.append({"id": val, "name": label or val, "raw": val})

    # ۱. مدل‌های پیش‌فرض و شاخص
    add_item("Claude Sonnet 5.5", "Claude Sonnet 5.5 (پیشنهادی - هوشمند)")
    add_item("Claude Opus 5.5", "Claude Opus 5.5 (تحلیل عمیق)")
    add_item("Claude Haiku 5.5", "Claude Haiku 5.5 (سریع و سبک)")
    add_item("Claude 3.7 Sonnet", "Claude 3.7 Sonnet")
    add_item("Claude 3.5 Sonnet", "Claude 3.5 Sonnet")

    # ۲. استعلام زنده از ارائه‌دهندگان فعال
    for prov in providers:
        if not prov.get("enabled", True):
            continue
        pname = prov.get("name", "")
        base_url = (prov.get("base_url") or "").strip().rstrip("/")
        keys = prov.get("keys", [])
        active_key = next((k["key"] for k in keys if k.get("status") == "active" and k.get("key")), None)
        if not active_key or not base_url or "127.0.0.1" in base_url:
            continue

        headers = {"Authorization": f"Bearer {active_key}", "x-api-key": active_key}
        for ep in ("/v1/models", "/models"):
            try:
                r = requests.get(f"{base_url}{ep}", headers=headers, timeout=3.5)
                if r.status_code == 200:
                    d = r.json()
                    raw_list = d.get("data") if isinstance(d.get("data"), list) else d.get("models") if isinstance(d.get("models"), list) else d if isinstance(d, list) else []
                    for item in raw_list:
                        if isinstance(item, dict):
                            mid = item.get("id") or item.get("name")
                            mname = item.get("display_name") or item.get("name") or mid
                        elif isinstance(item, str):
                            mid = item.strip()
                            mname = mid
                        else:
                            continue
                        if mid and mid not in seen:
                            if "openrouter" in pname.lower():
                                if not any(x in mid.lower() for x in ("claude", "gemini", "gpt", "deepseek", "qwen")):
                                    continue
                            disp = f"{mname} [{pname}]" if f"[{pname}]" not in mname else mname
                            add_item(mid, disp)
                    break
            except Exception:
                pass

    # ۳. مدل‌های شناخته‌شده در صورت در دسترس نبودن اینترنت در لحظه استعلام
    known_defaults = [
        ("gpt-6-astra", "GPT-6 Astra [Coarena AI]"),
        ("gpt-6-1-sol", "GPT-6.1 Sol [Coarena AI]"),
        ("gemini-4-argon", "Gemini 4 Argon [Coarena AI]"),
        ("gemini-3-8-flash", "Gemini 3.8 Flash [Coarena AI]"),
        ("deepseek-v4-1-flash-1017", "DeepSeek V4.1 Flash [Coarena AI]"),
        ("kimi-k3-1-pro", "Kimi K3.1 Pro [Coarena AI]"),
        ("grok-4-7", "Grok 4.7 [Coarena AI]"),
        ("claude-fable-5", "Claude Fable 5 [Radox AI]"),
        ("gpt-6.1-sol", "GPT-6.1 Sol [Xingchi API]"),
        ("gpt-6-astra", "GPT-6 Astra [Xingchi API]"),
        ("gpt-4o", "GPT-4o [Xingchi API]"),
        ("anthropic/claude-3.7-sonnet", "Claude 3.7 Sonnet [OpenRouter]"),
        ("google/gemini-pro-1.5", "Gemini 1.5 Pro [OpenRouter]"),
    ]
    for mid, disp in known_defaults:
        add_item(mid, disp)

    _MODELS_CACHE = {"ts": now, "models": models}
    return models


def build_messages_url(base_url: str) -> str:
    clean = (base_url or "").strip().rstrip("/")
    if clean.endswith("/v1/messages"):
        return clean
    if clean.endswith("/v1"):
        return f"{clean}/messages"
    return f"{clean}/v1/messages"


def estimate_cost(model_id: str, usage: dict) -> float:
    p = PRICING_DEFAULTS.get(model_id, PRICING_DEFAULTS["claude-sonnet-5-5"])
    if not usage:
        return 0.0
    m = 1_000_000.0
    inp = (usage.get("input_tokens", 0) or 0) * p["input"] / m
    out = (usage.get("output_tokens", 0) or 0) * p["output"] / m
    cread = (usage.get("cache_read_input_tokens", 0) or 0) * p.get("cache_read", 0.0) / m
    cwrite = (usage.get("cache_creation_input_tokens", 0) or 0) * p.get("cache_write", 0.0) / m
    return round(inp + out + cread + cwrite, 5)


class EngineError(Exception):
    def __init__(self, kind, message, retry_after_s=None):
        super().__init__(f"[{kind}] {message}")
        self.kind = kind
        self.message = message
        self.retry_after_s = retry_after_s


class EngineResult:
    def __init__(self, text, stop_reason, usage, model, thinking, latency_ms, cost_usd):
        self.text = text
        self.stop_reason = stop_reason
        self.usage = usage or {}
        self.model = model
        self.thinking = thinking
        self.latency_ms = latency_ms
        self.cost_usd = cost_usd


class ApiEngine:
    def __init__(self, key_pool, model_display="Claude Sonnet 5.5", effort="medium", max_tokens=32000, timeout=400):
        self.pool = key_pool
        self.model_display = model_display
        self.model_id = resolve_model_id(model_display)
        self.effort = effort if effort in ("low", "medium", "high") else "medium"
        self.max_tokens = int(max_tokens)
        if self.max_tokens <= 0:
            raise ValueError("max_tokens must be positive")
        self.max_tokens = min(self.max_tokens, MAX_TOKENS_CAP)
        self.timeout = timeout
        self.supports_cache = True
        self._cache_checked = 0
        self._cache_hits = 0
        self.session = requests.Session()

    def _variants(self, model_id, system_text, messages, max_tokens):
        sys_field = ([{"type": "text", "text": system_text, "cache_control": {"type": "ephemeral"}}]
                     if self.supports_cache else system_text)
        base = {"model": model_id, "stream": True, "system": sys_field, "messages": messages}
        budget = EFFORT_BUDGET_TOKENS.get(self.effort, 8192)
        return [
            {**base, "max_tokens": max_tokens, "thinking": {"type": "adaptive"}, "output_config": {"effort": self.effort}},
            {**base, "max_tokens": max_tokens, "thinking": {"type": "enabled", "budget_tokens": min(budget, max_tokens - 1)}},
            {**base, "max_tokens": max_tokens},
        ]

    def close(self):
        self.session.close()

    @staticmethod
    def _usage(raw):
        result = {k: v for k, v in (raw or {}).items()
                  if isinstance(v, int) and not isinstance(v, bool) and v >= 0}
        if "prompt_tokens" in result:
            result["input_tokens"] = result.pop("prompt_tokens")
        if "completion_tokens" in result:
            result["output_tokens"] = result.pop("completion_tokens")
        return result

    def _consume_stream(self, resp, cancel=None, on_delta=None):
        parts, usage, stop = [], {}, None
        terminal = False

        def check_cancel():
            if cancel is not None and cancel.is_set():
                raise EngineError("ABORTED", "Request cancelled")

        def add(text):
            if not isinstance(text, str):
                raise EngineError("PROTOCOL", "Invalid text response")
            parts.append(text)
            if on_delta and text:
                on_delta(text)

        def consume(ev, streaming=True):
            nonlocal stop, terminal
            if not isinstance(ev, dict):
                raise EngineError("PROTOCOL", "Invalid response object")
            if ev.get("error") or ev.get("type") == "error":
                error_data = ev.get("error") or {}
                kind = error_data.get("type", "")
                status = error_data.get("status")
                mapped = {"rate_limit_error": "RATE_LIMIT", "authentication_error": "AUTH",
                          "permission_error": "AUTH"}.get(kind)
                if mapped is None:
                    mapped = "AUTH" if status in (401, 403) else "RATE_LIMIT" if status == 429 else "NETWORK"
                raise EngineError(mapped, "Provider returned an error event")
            t = ev.get("type")
            usage.update(self._usage(ev.get("usage")))
            if t == "message_start":
                usage.update(self._usage((ev.get("message") or {}).get("usage")))
            elif t == "message_delta":
                stop = (ev.get("delta") or {}).get("stop_reason") or stop
            elif t == "message_stop":
                terminal = True
            elif t == "content_block_start":
                block = ev.get("content_block") or {}
                if block.get("type") == "text":
                    add(block.get("text", ""))
            elif t == "content_block_delta":
                delta = ev.get("delta") or {}
                if delta.get("type") == "text_delta":
                    add(delta.get("text", ""))
            if not streaming:
                content = ev.get("content", [])
                if isinstance(content, str):
                    add(content)
                elif isinstance(content, list):
                    for block in content:
                        if isinstance(block, dict) and block.get("type") == "text":
                            add(block.get("text", ""))
                stop = ev.get("stop_reason") or stop
            for choice in ev.get("choices") or []:
                # Multiple choices are alternatives, not text to concatenate.
                if choice.get("index", 0) != 0:
                    continue
                content = (choice.get("delta" if streaming else "message") or {}).get("content")
                if content:
                    add(content)
                reason = choice.get("finish_reason")
                if reason:
                    stop = {"stop": "end_turn", "length": "max_tokens"}.get(reason, reason)

        check_cancel()
        content_type = resp.headers.get("Content-Type", "").lower()
        if "application/json" in content_type:
            try:
                data = resp.json()
            except ValueError as exc:
                raise EngineError("PROTOCOL", "Invalid JSON response") from exc
            consume(data, streaming=False)
            terminal = True
        else:
            frame, plain = [], []
            saw_sse = False

            def dispatch():
                nonlocal terminal
                if not frame:
                    return
                payload = "\n".join(frame)
                frame.clear()
                if payload.strip() == "[DONE]":
                    terminal = True
                    return
                try:
                    event = json.loads(payload)
                except ValueError as exc:
                    raise EngineError("PROTOCOL", "Malformed streaming event") from exc
                consume(event)

            for raw in resp.iter_lines(decode_unicode=False):
                check_cancel()
                line = raw.decode("utf-8", errors="strict") if isinstance(raw, bytes) else raw
                if line.startswith("data:"):
                    saw_sse = True
                    frame.append(line[5:].lstrip(" "))
                elif not line:
                    dispatch()
                    if terminal:
                        break
                elif not saw_sse and not line.startswith(("event:", ":", "id:", "retry:")):
                    plain.append(line)
            dispatch()
            if not saw_sse and plain:
                try:
                    consume(json.loads("\n".join(plain)), streaming=False)
                    terminal = True
                except ValueError as exc:
                    raise EngineError("PROTOCOL", "Invalid JSON response") from exc
        check_cancel()
        if not terminal or stop is None:
            raise EngineError("NETWORK", "Response ended before a completion marker")
        if stop not in ("end_turn", "stop_sequence", "max_tokens"):
            raise EngineError("PROTOCOL", "Provider did not complete the text response")
        return "".join(parts).strip(), usage, stop

    def _track_cache(self, usage):
        if not self.supports_cache:
            return
        self._cache_checked += 1
        if (usage.get("cache_read_input_tokens") or 0) > 0:
            self._cache_hits += 1
        if self._cache_checked >= 5 and self._cache_hits == 0:
            self.supports_cache = False

    def generate(self, system_prompt: str, user_content: str, cancel=None,
                 on_delta=None, on_reset=None) -> EngineResult:
        t0 = time.monotonic()
        max_tokens = self.max_tokens
        last_err = None
        requests_sent = 0
        while requests_sent < 20:
            if cancel is not None and cancel.is_set():
                raise EngineError("ABORTED", "Request cancelled")
            handle = self.pool.acquire(timeout=60, cancel=cancel)
            if handle is None:
                if cancel is not None and cancel.is_set():
                    raise EngineError("ABORTED", "Request cancelled")
                kind = "KEY_EXHAUSTED" if self.pool.active_count() == 0 else "RATE_LIMIT"
                raise last_err or EngineError(kind, "No API key is currently available")
            outcome, retry_after = "aborted", None
            retry_with_next_key = False
            try:
                model = map_model_for_provider(handle.provider, self.model_id)
                endpoint_host = urlsplit(handle.base_url).hostname or ""
                openai = ("openrouter" in handle.provider.lower() or "openrouter.ai" in endpoint_host.lower()
                          or "xingchi" in handle.provider.lower() or "xingchi" in endpoint_host.lower()
                          or "chatgpt" in handle.provider.lower()
                          or endpoint_host.lower() == "openai.com" or endpoint_host.lower().endswith(".openai.com"))
                headers = {"Content-Type": "application/json", "Accept": "text/event-stream, application/json"}
                if openai:
                    headers["Authorization"] = "Bearer " + handle.key
                    clean = handle.base_url.rstrip("/")
                    url = clean if clean.endswith("/chat/completions") else clean + ("/chat/completions" if clean.endswith("/v1") else "/v1/chat/completions")
                else:
                    headers.update({"x-api-key": handle.key, "Authorization": "Bearer " + handle.key,
                                    "anthropic-version": "2023-06-01"})
                    url = build_messages_url(handle.base_url)
                if urlsplit(url).scheme not in ("http", "https") or not urlsplit(url).netloc:
                    raise EngineError("CONFIG", "Invalid API endpoint URL")
                vi = 0
                cache_enabled = True
                while requests_sent < 20:
                    if cancel is not None and cancel.is_set():
                        raise EngineError("ABORTED", "Request cancelled")
                    self.supports_cache = cache_enabled
                    variants = self._variants(model, system_prompt,
                                              [{"role": "user", "content": user_content}], max_tokens)
                    if openai:
                        payload = {"model": model, "stream": True, "max_tokens": max_tokens,
                                   "stream_options": {"include_usage": True},
                                   "messages": [{"role": "system", "content": system_prompt},
                                                {"role": "user", "content": user_content}]}
                    else:
                        payload = variants[vi]
                    if requests_sent and on_reset:
                        on_reset()
                    requests_sent += 1
                    with self.session.post(url, headers=headers, json=payload, stream=True,
                                           timeout=(15, self.timeout), allow_redirects=False) as resp:
                        sc = resp.status_code
                        if sc == 200:
                            text, usage, stop = self._consume_stream(resp, cancel=cancel, on_delta=on_delta)
                            if not text:
                                raise EngineError("NETWORK", "Provider returned empty text")
                            if stop == "max_tokens":
                                if max_tokens >= MAX_TOKENS_CAP:
                                    raise EngineError("TRUNCATED", "Response exceeded the token limit; no partial result saved")
                                max_tokens = min(MAX_TOKENS_CAP, max(max_tokens + 1, int(max_tokens * 1.5)))
                                continue
                            outcome = "ok"
                            return EngineResult(text, stop, usage, model, self.effort,
                                                int((time.monotonic() - t0) * 1000),
                                                estimate_cost(self.model_id, usage))
                        body = (resp.text or "")[:4000].lower()
                        if sc in (400, 422) and not openai:
                            if "cache_control" in body and cache_enabled:
                                cache_enabled = False
                                continue
                            if vi < len(variants) - 1 and any(word in body for word in
                                ("output_config", "effort", "thinking", "adaptive", "budget_tokens", "unsupported", "unknown")):
                                vi += 1
                                continue
                        if sc == 429:
                            outcome = "429"
                            try:
                                retry_after = float(resp.headers.get("Retry-After", "") or 0) or None
                            except ValueError:
                                retry_after = None
                            kind = "RATE_LIMIT"
                        elif sc in (401, 403):
                            outcome, kind = "auth", "AUTH"
                        elif sc == 402:
                            outcome, kind = "exhausted", "KEY_EXHAUSTED"
                        elif 400 <= sc < 500:
                            raise EngineError("CONFIG", "Provider rejected request (HTTP %s)" % sc)
                        else:
                            outcome, kind = "server", "NETWORK"
                        last_err = EngineError(kind, "Provider request failed (HTTP %s)" % sc, retry_after)
                        retry_with_next_key = True
                        break
                else:
                    raise EngineError("PROTOCOL", "Provider retry budget exhausted")
            except requests.exceptions.RequestException:
                outcome = "network"
                last_err = EngineError("NETWORK", "API transport failed")
                retry_with_next_key = True
            except EngineError as exc:
                if exc.kind in ("ABORTED", "CONFIG", "TRUNCATED", "PROTOCOL"):
                    raise
                outcome = {"RATE_LIMIT": "429", "AUTH": "auth", "KEY_EXHAUSTED": "exhausted"}.get(exc.kind, "network")
                last_err = exc
                retry_with_next_key = True
            finally:
                self.pool.release(handle, outcome=outcome, retry_after=retry_after,
                                  err_msg=last_err.kind if last_err else None)
            if not retry_with_next_key:
                break
        raise last_err or EngineError("NETWORK", "API retry budget exhausted")
