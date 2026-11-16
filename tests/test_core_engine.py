"""Offline regression tests. All network calls use fakes and data is temporary."""
import json
import sqlite3
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core.api_engine import ApiEngine, EngineError, EngineResult, resolve_model_id
from core.chunker import build_chunks_from_source, slice_raw_text
from core.key_pool import KeyPool
from core.ledger import Ledger
from core.prompt_builder import strip_end_marker, build_system_and_user_prompts
from core.runner import Runner


def providers(keys=("test-credential-one",), name="Fake", url="https://invalid.example/v1"):
    return [{"name": name, "base_url": url, "keys": [{"key": key} for key in keys]}]


class Response:
    def __init__(self, events=None, data=None, status=200, text="", headers=None):
        self.status_code = status
        self.text = text
        self.data = data
        self.events = events or []
        self.headers = headers or {"Content-Type": "application/json" if data is not None else "text/event-stream"}

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def json(self):
        return self.data

    def iter_lines(self, **kwargs):
        for event in self.events:
            if isinstance(event, bytes):
                yield event
            else:
                yield ("data: " + json.dumps(event)).encode()
                yield b""


def stream(text="completed", stop="end_turn", complete=True):
    events = [
        {"type": "message_start", "message": {"usage": {"input_tokens": 5}}},
        {"type": "content_block_delta", "delta": {"type": "text_delta", "text": text}},
        {"type": "message_delta", "delta": {"stop_reason": stop}, "usage": {"output_tokens": 7}},
    ]
    if complete:
        events.append({"type": "message_stop"})
    return Response(events)


class KeyPoolTests(unittest.TestCase):
    def test_cooldowns_expire_and_count_as_live(self):
        clock = [100.0]
        pool = KeyPool(providers(), clock=lambda: clock[0])
        handle = pool.acquire(timeout=0)
        pool.release(handle, "429", retry_after=1)
        self.assertEqual(pool.active_count(), 1)
        self.assertIsNone(pool.acquire(timeout=0))
        clock[0] += 3
        self.assertIs(pool.acquire(timeout=0), handle)
        pool.release(handle, "network")
        clock[0] += 25
        self.assertIs(pool.acquire(timeout=0), handle)

    def test_duplicates_blank_disabled_and_reload_leases(self):
        p = providers(("", "test-credential-one", "test-credential-one"))
        p[0]["keys"].append({"key": "dead", "status": "exhausted"})
        pool = KeyPool(p)
        self.assertEqual(pool.total_count(), 1)
        h = pool.acquire(timeout=0)
        with self.assertRaises(RuntimeError):
            pool.reload_providers(p)
        self.assertIsNone(pool.acquire(timeout=0))
        pool.release(h, "auth", err_msg="secret")
        pool.reload_providers(p)
        self.assertEqual(pool.active_count(), 0)
        self.assertNotIn("secret", json.dumps(pool.snapshot()))
        self.assertNotIn("test-credential", json.dumps(pool.snapshot()))

    def test_waiting_acquisition_is_cancellable(self):
        pool = KeyPool(providers())
        h = pool.acquire(timeout=0)
        cancel = threading.Event()
        result = []
        worker = threading.Thread(target=lambda: result.append(pool.acquire(timeout=5, cancel=cancel)))
        worker.start()
        cancel.set()
        worker.join(1)
        self.assertFalse(worker.is_alive())
        self.assertEqual(result, [None])
        pool.release(h)


class LedgerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.ledger = Ledger(str(Path(self.temp.name) / "ledger.db"))
        self.addCleanup(self.ledger.close)
        self.chunks = [{"idx": 0, "heading": None, "arabic_text": "original"}]
        self.ledger.init_chunks(self.chunks, {"source_path": "one", "generation": {"model": "a"}})

    def test_mismatch_archives_wal_and_changes_project_generation(self):
        self.ledger.claim_next()
        self.ledger.complete_chunk(0, "paid result", "a", "low", 1, 2, 3, .1)
        archive = self.ledger.init_chunks(self.chunks, {"source_path": "one", "generation": {"model": "b"}})
        self.assertIsNotNone(archive)
        conn = sqlite3.connect(archive)
        try:
            self.assertEqual(conn.execute("SELECT persian_text FROM chunks").fetchone()[0], "paid result")
        finally:
            conn.close()
        self.assertEqual(self.ledger.get_stats()["pending"], 1)
        self.assertIsNone(self.ledger.init_chunks(self.chunks, {"source_path": "one", "generation": {"model": "b"}}))

    def test_changed_source_and_invalid_reset_never_delete_old_data(self):
        with self.assertRaises(ValueError):
            self.ledger.init_chunks([self.chunks[0], self.chunks[0]], {}, reset=True)
        self.assertEqual(self.ledger.get_chunk(0)["arabic_text"], "original")
        changed = [{"idx": 0, "arabic_text": "replacement"}]
        self.assertTrue(self.ledger.init_chunks(changed, {}))
        self.assertEqual(self.ledger.get_chunk(0)["arabic_text"], "replacement")

    def test_release_and_crash_recovery_do_not_consume_retry_budget(self):
        self.ledger.claim_next()
        self.ledger.release_chunk(0)
        self.assertEqual(self.ledger.get_chunk(0)["attempts"], 0)
        self.ledger.claim_next()
        self.ledger.recover_claimed()
        self.assertEqual(self.ledger.get_stats()["pending"], 1)
        self.assertEqual(self.ledger.get_chunk(0)["attempts"], 0)

    def test_done_never_downgraded_and_failed_completion_rolls_back(self):
        with self.assertRaises(ValueError):
            self.ledger.complete_chunk(0, "bad", "a", "low", 0, 0, 0, 0)
        self.assertFalse(self.ledger.conn.in_transaction)
        self.ledger.claim_next()
        self.ledger.complete_chunk(0, "good", "a", "low", 0, 0, 0, 0)
        self.ledger.fail_chunk(0, "render failed")
        self.assertEqual(self.ledger.get_stats()["done"], 1)

    def test_close_cleans_connections_created_by_other_threads(self):
        worker = threading.Thread(target=self.ledger.get_stats)
        worker.start()
        worker.join()
        self.assertEqual(len(self.ledger._connections), 2)
        self.ledger.close()
        self.assertEqual(self.ledger._connections, [])


class ApiTests(unittest.TestCase):
    def setUp(self):
        self.pool = KeyPool(providers())
        self.engine = ApiEngine(self.pool, max_tokens=32000)
        self.addCleanup(self.engine.close)

    def test_complete_anthropic_and_unicode_stream(self):
        deltas = []
        text, usage, stop = self.engine._consume_stream(stream("متن فارسی"), on_delta=deltas.append)
        self.assertEqual(text, "متن فارسی")
        self.assertEqual(deltas, [text])
        self.assertEqual(usage, {"input_tokens": 5, "output_tokens": 7})
        self.assertEqual(stop, "end_turn")

    def test_truncated_or_malformed_stream_rejected(self):
        for response in (stream(complete=False), Response([b"data: not-json", b""])):
            with self.subTest(response=response), self.assertRaises(EngineError):
                self.engine._consume_stream(response)

    def test_json_fallback_and_openai_usage_finish_reason(self):
        response = Response(data={"choices": [{"message": {"content": "result"}, "finish_reason": "length"}],
                                  "usage": {"prompt_tokens": 2, "completion_tokens": 3}})
        text, usage, stop = self.engine._consume_stream(response)
        self.assertEqual((text, usage, stop), ("result", {"input_tokens": 2, "output_tokens": 3}, "max_tokens"))

    def test_multiline_sse_and_alternative_choices(self):
        response = Response([b'data: {"choices":', b'data: [{"index":0,"delta":{"content":"one"},"finish_reason":"stop"},',
                             b'data: {"index":1,"delta":{"content":"two"}}],"usage":{"prompt_tokens":9}}', b'', b'data: [DONE]', b''])
        text, usage, stop = self.engine._consume_stream(response)
        self.assertEqual(text, "one")
        self.assertEqual(usage["input_tokens"], 9)

    def test_token_retry_resets_stream_and_totals_usage(self):
        reset = []
        with patch.object(self.engine.session, "post", side_effect=[stream("partial", "max_tokens"), stream("final")]) as post:
            result = self.engine.generate("sys", "user", on_reset=lambda: reset.append(True))
        self.assertEqual(result.text, "final")
        # Usage belongs only to the accepted full response, not discarded partial retries.
        self.assertEqual(result.usage, {"input_tokens": 5, "output_tokens": 7})
        self.assertEqual(len(reset), 1)
        self.assertEqual(post.call_count, 2)
        self.assertFalse(self.pool.snapshot()[0]["busy"])

    def test_max_token_cap_never_saves_truncated_output(self):
        self.engine.max_tokens = 64000
        with patch.object(self.engine.session, "post", return_value=stream("partial", "max_tokens")):
            with self.assertRaises(EngineError) as err:
                self.engine.generate("sys", "user")
        self.assertEqual(err.exception.kind, "TRUNCATED")
        self.assertFalse(self.pool.snapshot()[0]["busy"])

    def test_repeated_cache_error_is_bounded_and_not_disclosed(self):
        bad = Response(status=400, text="cache_control invalid test-credential-one")
        with patch.object(self.engine.session, "post", return_value=bad) as post:
            with self.assertRaises(EngineError) as err:
                self.engine.generate("sys", "user")
        self.assertLessEqual(post.call_count, 4)
        self.assertNotIn("test-credential-one", str(err.exception))

    def test_auth_error_rotates_key_and_quarantines(self):
        self.engine.pool = KeyPool(providers(("test-credential-one", "test-credential-two")))
        with patch.object(self.engine.session, "post", side_effect=[Response(status=401), stream()]):
            self.engine.generate("sys", "user")
        self.assertEqual([i["state"] for i in self.engine.pool.snapshot()], ["dead", "active"])

    def test_openrouter_uses_chat_payload_not_anthropic_messages(self):
        self.engine.pool = KeyPool(providers(name="OpenRouter", url="https://openrouter.ai/api/v1"))
        with patch.object(self.engine.session, "post", return_value=stream()) as post:
            result = self.engine.generate("sys", "user")
        args, kwargs = post.call_args
        self.assertEqual(args[0], "https://openrouter.ai/api/v1/chat/completions")
        self.assertEqual(kwargs["json"]["messages"][0]["role"], "system")
        self.assertNotIn("x-api-key", kwargs["headers"])
        self.assertFalse(kwargs["allow_redirects"])
        self.assertEqual(result.model, "anthropic/claude-sonnet-5-5")
        self.assertEqual(resolve_model_id("claude-sonnet-4-20250514"), "claude-sonnet-4-20250514")

    def test_pre_cancel_never_sends_or_leases(self):
        cancel = threading.Event()
        cancel.set()
        with patch.object(self.engine.session, "post") as post, self.assertRaises(EngineError):
            self.engine.generate("sys", "user", cancel=cancel)
        post.assert_not_called()
        self.assertFalse(self.pool.snapshot()[0]["busy"])


class ChunkerPromptTests(unittest.TestCase):
    def test_headings_single_newlines_and_long_paragraphs_are_not_lost(self):
        text = "# Heading\r\n" + "longword " * 50 + "\r\n# Last"
        chunks = slice_raw_text(text, 40)
        joined = " ".join(c["arabic_text"] for c in chunks)
        self.assertIn("Heading", joined)
        self.assertIn("Last", joined)
        self.assertEqual(joined.count("longword"), 50)
        self.assertTrue(all(len(c["arabic_text"]) <= 40 for c in chunks))
        self.assertEqual([c["idx"] for c in chunks], list(range(len(chunks))))

    def test_invalid_sizes_empty_and_binary_sources_rejected(self):
        with self.assertRaises(ValueError):
            slice_raw_text("text", 0)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "source.png"
            path.write_bytes(b"binary")
            with self.assertRaises(ValueError):
                build_chunks_from_source(str(path))
            path = Path(directory) / "empty.txt"
            path.write_text("", encoding="utf-8")
            with self.assertRaises(ValueError):
                build_chunks_from_source(str(path))

    def test_end_marker_only_stripped_at_end_not_internal_lines(self):
        self.assertEqual(strip_end_marker("first\n#پایان\nnext\n#پایان"), "first\n#پایان\nnext")
        _, prompt = build_system_and_user_prompts({"context_mode": "full"}, {"arabic_text": "current"}, "before", "response\n#پایان")
        self.assertEqual(prompt.count("#پایان"), 1)


class RunnerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.source = Path(self.temp.name) / "source.txt"
        self.source.write_text("source text", encoding="utf-8")
        self.project = {"input": str(self.source), "output": "result", "delay": 0, "id": "project"}
        self.events = []
        self.runner = Runner(self.project, providers(), self.temp.name, lambda e, d: self.events.append((e, d)))
        self.addCleanup(self.runner.close)

    def run_and_join(self, fake):
        with patch("core.runner.ApiEngine", fake), patch.object(self.runner, "render_outputs", return_value={}):
            self.runner.start()
            self.runner.worker_thread.join(3)
        self.assertFalse(self.runner.worker_thread.is_alive())

    def test_missing_or_empty_source_never_uses_sample(self):
        self.runner.project["input"] = ""
        with self.assertRaises(ValueError):
            self.runner.prepare()
        self.runner.project["input"] = "missing.txt"
        with self.assertRaises(FileNotFoundError):
            self.runner.prepare()
        self.assertEqual(self.runner.ledger.get_stats()["total"], 0)

    def test_stop_prevents_overlap_and_returns_claim_without_attempt(self):
        entered, release = threading.Event(), threading.Event()
        class BlockingEngine:
            def __init__(self, **kwargs): pass
            def close(self): pass
            def generate(self, **kwargs):
                entered.set()
                release.wait(3)
                raise EngineError("ABORTED", "cancelled")
        self.runner.prepare()
        with patch("core.runner.ApiEngine", BlockingEngine):
            self.assertTrue(self.runner.start())
            self.assertTrue(entered.wait(2))
            old_worker = self.runner.worker_thread
            self.runner.stop()
            self.assertTrue(self.runner.is_running)
            self.assertFalse(self.runner.start())
            self.assertIs(self.runner.worker_thread, old_worker)
            release.set()
            old_worker.join(3)
        self.assertFalse(self.runner.is_running)
        chunk = self.runner.ledger.get_chunk(0)
        self.assertEqual((chunk["state"], chunk["attempts"]), ("pending", 0))
        self.assertTrue(all(data["project_id"] == "project" for _, data in self.events))

    def test_export_failure_keeps_done_and_ui_callback_failure_isolated(self):
        class SuccessEngine:
            def __init__(self, **kwargs): pass
            def close(self): pass
            def generate(self, **kwargs):
                kwargs["on_delta"]("result")
                return EngineResult("result", "end_turn", {}, "model", "low", 1, 0)
        self.runner.prepare()
        self.runner.event_cb = lambda *args: (_ for _ in ()).throw(RuntimeError("UI gone"))
        with patch("core.runner.ApiEngine", SuccessEngine), patch.object(self.runner, "render_outputs", side_effect=OSError("disk")):
            self.runner.start()
            self.runner.worker_thread.join(3)
        self.assertFalse(self.runner.is_running)
        self.assertEqual(self.runner.ledger.get_stats()["done"], 1)

    def test_engine_initialization_failure_restores_idle(self):
        self.runner.prepare()
        with patch("core.runner.ApiEngine", side_effect=ValueError("bad setting")):
            self.runner.start()
            self.runner.worker_thread.join(3)
        self.assertFalse(self.runner.is_running)
        self.assertEqual(self.runner.ledger.get_stats()["pending"], 1)

    def test_exhausted_pool_pauses_without_failing_every_chunk(self):
        class EmptyEngine:
            def __init__(self, **kwargs): pass
            def close(self): pass
            def generate(self, **kwargs): raise EngineError("KEY_EXHAUSTED", "no keys")
        self.runner.prepare()
        self.run_and_join(EmptyEngine)
        chunk = self.runner.ledger.get_chunk(0)
        self.assertEqual((chunk["state"], chunk["attempts"]), ("pending", 0))


if __name__ == "__main__":
    unittest.main()
