"""Thread-safe, one-request-per-key pool with expiring provider cooldowns."""
import math
import threading
import time


class KeyHandle:
    __slots__ = ("provider", "base_url", "name", "key")

    def __init__(self, provider, base_url, name, key):
        self.provider = provider
        self.base_url = (base_url or "").strip().rstrip("/")
        self.name = name
        self.key = key

    def masked(self):
        return "***"  # Status payloads must not disclose portions of credentials.


class KeyPool:
    def __init__(self, providers, clock=time.monotonic, cooldown_s=180):
        self.clock = clock
        self.cooldown_s = cooldown_s
        self.cv = threading.Condition()
        self.items = []
        self._rr = 0
        self.reload_providers(providers)

    def reload_providers(self, providers):
        with self.cv:
            # Replacing a leased handle would allow a second simultaneous request.
            if any(item["busy"] for item in self.items):
                raise RuntimeError("Cannot reload API keys while a request is in flight")
            previous = {(i["h"].base_url, i["h"].key): i for i in self.items}
            items, seen = [], set()
            for p in providers or []:
                if not p.get("enabled", True):
                    continue
                url = (p.get("base_url") or "").strip().rstrip("/")
                for k in p.get("keys") or []:
                    key = (k.get("key") or "").strip()
                    if not key or k.get("status", "active") in ("dead", "disabled", "exhausted"):
                        continue
                    identity = (url, key)
                    if identity in seen:
                        continue
                    seen.add(identity)
                    old = previous.get(identity)
                    if old:
                        old["h"].provider = p.get("name", "Unknown")
                        old["h"].name = k.get("name", "")
                        items.append(old)
                    else:
                        items.append({
                            "h": KeyHandle(p.get("name", "Unknown"), url, k.get("name", ""), key),
                            "busy": False, "until": 0.0, "state": "active", "streak": 0,
                            "request_count": int(k.get("request_count", 0) or 0), "last_error": None,
                        })
            self.items = items
            self._rr = 0
            self.cv.notify_all()

    def _refresh(self):
        now = self.clock()
        for item in self.items:
            if item["state"] in ("rate_limited", "cooldown") and item["until"] <= now:
                item["state"] = "active"

    def active_count(self):
        # Includes temporarily unavailable keys, not permanently exhausted keys.
        with self.cv:
            self._refresh()
            return sum(i["state"] in ("active", "rate_limited", "cooldown") for i in self.items)

    def total_count(self):
        with self.cv:
            return len(self.items)

    def acquire(self, timeout=60.0, cancel=None):
        end = time.monotonic() + max(0.0, timeout)
        with self.cv:
            while True:
                if cancel is not None and cancel.is_set():
                    return None
                self._refresh()
                now = self.clock()
                n = len(self.items)
                for off in range(n):
                    idx = (self._rr + off) % n
                    it = self.items[idx]
                    if it["state"] == "active" and not it["busy"] and it["until"] <= now:
                        it["busy"] = True
                        it["request_count"] += 1
                        self._rr = (idx + 1) % n
                        return it["h"]
                remaining = end - time.monotonic()
                if remaining <= 0 or self.active_count() == 0:
                    return None
                waits = [i["until"] - now for i in self.items if i["until"] > now]
                self.cv.wait(min([0.1, remaining] + waits))

    def release(self, h, outcome="ok", retry_after=None, err_msg=None):
        with self.cv:
            it = next((i for i in self.items if i["h"] is h), None)
            if it is None or not it["busy"]:
                return
            it["busy"] = False
            now = self.clock()
            # Do not retain provider error bodies: they can echo credentials.
            it["last_error"] = None if outcome in ("ok", "aborted") else outcome
            if outcome == "ok":
                it.update(streak=0, until=0.0, state="active")
            elif outcome == "429":
                it["streak"] += 1
                try:
                    wait_sec = float(retry_after or 0)
                except (TypeError, ValueError):
                    wait_sec = 0
                if not math.isfinite(wait_sec):
                    wait_sec = 0
                if wait_sec > 10 or it["streak"] >= 3:
                    it.update(until=now + max(wait_sec, self.cooldown_s), state="cooldown", streak=0)
                else:
                    it.update(until=now + max(wait_sec, 3.0), state="rate_limited")
            elif outcome in ("auth", "exhausted"):
                it["state"] = "dead" if outcome == "auth" else "exhausted"
            elif outcome in ("server", "network"):
                it.update(until=now + 25.0, state="cooldown")
            self.cv.notify_all()

    def snapshot(self):
        with self.cv:
            self._refresh()
            now = self.clock()
            return [{
                "provider": i["h"].provider, "name": i["h"].name,
                "masked_key": i["h"].masked(), "state": i["state"], "busy": i["busy"],
                "request_count": i["request_count"], "last_error": i["last_error"],
                "cooldown_remaining": max(0, int(i["until"] - now)),
            } for i in self.items]
