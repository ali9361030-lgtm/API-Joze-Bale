"""HTTP regression tests use isolated temporary config/data; never real API keys."""
import base64
import http.client
import io
import json
import os
from pathlib import Path
import queue
import sqlite3
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import server


class ServerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = self.tmp.name
        self.patches = []
        for name, folder in (("BASE_DIR", ""), ("CONFIG_DIR", "config"), ("OUTPUTS_DIR", "outputs"), ("DATA_DIR", "data")):
            path = os.path.join(root, folder)
            os.makedirs(path, exist_ok=True)
            p = patch.object(server, name, path)
            p.start()
            self.patches.append(p)
        self.state = server.AppState()
        p = patch.object(server, "GLOBAL_STATE", self.state)
        p.start()
        self.patches.append(p)
        self.httpd = server.ThreadedHTTPServer(("127.0.0.1", 0), server.RequestHandler)
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()
        self.port = self.httpd.server_port

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join()
        if self.state.runner:
            self.state.discard_runner()
        for p in reversed(self.patches):
            p.stop()
        self.tmp.cleanup()

    def request(self, path, body=None, method=None, headers=None, raw=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=3)
        hdr = {"Content-Type": "application/json"}
        hdr.update(headers or {})
        payload = raw if raw is not None else json.dumps(body).encode() if body is not None else None
        connection.request(method or ("POST" if payload is not None else "GET"), path, payload, hdr)
        response = connection.getresponse()
        content = response.read()
        status, response_headers = response.status, dict(response.getheaders())
        connection.close()
        if response_headers.get("Content-Type", "").startswith("application/json"):
            content = json.loads(content)
        return status, content, response_headers

    def make_project(self, **kwargs):
        project = {"id": "test", "title": "Test", "output": "outputs/test", "input": "", **kwargs}
        status, result, _ = self.request("/api/projects/save", {"project": project})
        self.assertEqual(status, 200, result)
        return project

    def test_fresh_project_can_be_created_without_ready_runner(self):
        self.make_project()
        status, data, _ = self.request("/api/status")
        self.assertEqual(status, 200)
        self.assertFalse(data["ready"])
        self.assertEqual(data["active_project"]["id"], "test")
        self.assertEqual(self.request("/api/projects")[0], 200)
        self.assertEqual(self.request("/api/providers")[0], 200)

    def test_missing_input_does_not_poison_runner(self):
        self.make_project(input="data/missing.txt")
        self.request("/api/status")
        self.assertIsNone(self.state.runner)
        status, _, _ = self.request("/api/upload", {"filename": "a.txt", "content": "متن منبع برای آزمایش"})
        self.assertEqual(status, 200)
        status, data, _ = self.request("/api/status")
        self.assertEqual(status, 200)
        self.assertTrue(data["ready"], data)

    def test_project_validation_and_unknown_switch(self):
        self.make_project()
        for extra in ({"chunk_size": 0}, {"delay": -1}, {"max_tokens": "bad"}, {"chunk_size": True}, {"output": "../escape"}, {"enable_context": "yes"}):
            status, _, _ = self.request("/api/projects/save", {"project": {"id": "bad", "title": "Bad", **extra}})
            self.assertEqual(status, 400, extra)
        self.assertEqual(self.request("/api/projects/switch", {"id": "missing"})[0], 404)
        self.assertEqual(self.state.active_project_id, "test")
        status, _, _ = self.request("/api/projects/save", {"project": {"id": "other", "title": "Other", "output": "outputs/test"}})
        self.assertEqual(status, 400)

    def test_upload_rejects_traversal_empty_and_bad_docx(self):
        self.make_project()
        for filename in ("../outside.txt", "..\\outside.txt", "C:\\outside.txt", "/outside.txt"):
            self.assertEqual(self.request("/api/upload", {"filename": filename, "content": "text"})[0], 400)
        self.assertEqual(self.request("/api/upload", {"filename": "a.txt", "content": " "})[0], 400)
        self.assertEqual(self.request("/api/upload", {"filename": "a.docx", "content": "PK data"})[0], 400)
        self.assertEqual(self.request("/api/upload", {"filename": "a.docx", "encoding": "base64", "content": "not base64!"})[0], 400)
        self.assertEqual(self.request("/api/upload", {"filename": "a.docx", "encoding": "base64", "content": base64.b64encode(b"invalid zip").decode()})[0], 400)

    def test_docx_upload_preserves_binary_bytes(self):
        self.make_project()
        binary = io.BytesIO()
        with zipfile.ZipFile(binary, "w") as archive:
            archive.writestr("word/document.xml", '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>سلام</w:t></w:r></w:p></w:body></w:document>')
        raw = binary.getvalue()
        status, data, _ = self.request("/api/upload", {"filename": "کتاب.docx", "encoding": "base64", "content": base64.b64encode(raw).decode()})
        self.assertEqual(status, 200, data)
        self.assertEqual(Path(server.BASE_DIR, data["path"]).read_bytes(), raw)

    def test_upload_names_do_not_overwrite_existing_source(self):
        self.make_project()
        _, first, _ = self.request("/api/upload", {"filename": "same.txt", "content": "first"})
        _, second, _ = self.request("/api/upload", {"filename": "same.txt", "content": "second"})
        self.assertNotEqual(first["path"], second["path"])
        self.assertEqual(Path(server.BASE_DIR, first["path"]).read_text(), "first")

    def test_secrets_are_masked_and_round_trip_preserves_them(self):
        secret = "test-secret-not-a-real-api-key"
        provider = {"name": "Test", "base_url": "https://example.invalid/v1", "enabled": True, "keys": [{"name": "first", "key": secret}]}
        self.assertEqual(self.request("/api/providers/save", {"providers": [provider]})[0], 200)
        _, data, headers = self.request("/api/providers")
        self.assertNotIn(secret, json.dumps(data))
        self.assertNotIn("Access-Control-Allow-Origin", headers)
        self.assertEqual(headers["Cache-Control"], "no-store")
        public = data["providers"]
        public[0]["enabled"] = False
        self.assertEqual(self.request("/api/providers/save", {"providers": public})[0], 200)
        self.assertEqual(self.state.providers[0]["keys"][0]["key"], secret)
        self.assertFalse(self.state.providers[0]["enabled"])

    def test_unknown_masked_key_rejected_and_bulk_deduplicated(self):
        body = {"provider": "Test", "base_url": "https://example.invalid", "keys": "key-one\nkey-one\nname:key-two"}
        status, data, _ = self.request("/api/keys/bulk", body)
        self.assertEqual(status, 200)
        self.assertEqual(data["added_count"], 2)
        self.assertEqual(self.request("/api/keys/bulk", body)[1]["added_count"], 0)
        _, data, _ = self.request("/api/providers")
        data["providers"][0]["keys"][0]["key_ref"] = "invalid"
        self.assertEqual(self.request("/api/providers/save", {"providers": data["providers"]})[0], 400)

    def test_cross_origin_and_dns_rebinding_are_denied(self):
        for headers in ({"Origin": "https://evil.invalid"}, {"Host": f"evil.invalid:{self.port}"}, {"Sec-Fetch-Site": "cross-site"}):
            self.assertEqual(self.request("/api/providers", headers=headers)[0], 403)
        self.assertEqual(self.request("/api/providers", headers={"Origin": f"http://127.0.0.1:{self.port}"})[0], 200)

    def test_invalid_request_bodies_fail_without_mutation(self):
        for raw in (b"{", b"[]", b"null"):
            self.assertEqual(self.request("/api/providers/save", raw=raw)[0], 400)
        self.assertEqual(self.request("/api/providers/save", raw=b"{}", headers={"Content-Type": "text/plain"})[0], 415)
        self.assertEqual(self.request("/api/unknown")[0], 404)
        self.assertEqual(self.request("/missing.js")[0], 404)

    def test_active_worker_blocks_config_mutations(self):
        self.make_project()
        class Busy:
            is_running = True
        self.state.runner = Busy()
        try:
            for path, body in (("/api/upload", {"filename": "a.txt", "content": "new"}), ("/api/projects/switch", {"id": "test"}), ("/api/providers/save", {"providers": []}), ("/api/projects/save", {"project": {"id": "test", "title": "new"}})):
                self.assertEqual(self.request(path, body)[0], 409, path)
        finally:
            self.state.runner = None

    def test_slow_sse_subscriber_is_not_permanently_detached(self):
        q = queue.Queue(maxsize=1)
        self.state.sse_queues.append(q)
        self.state.broadcast_event("status", {"number": 1})
        self.state.broadcast_event("status", {"number": 2})
        self.assertIn(q, self.state.sse_queues)
        self.assertIn('"number": 2', q.get_nowait())

    def test_download_has_unicode_filename_and_live_sqlite_backup(self):
        path = Path(server.OUTPUTS_DIR, "دفتر.db")
        connection = sqlite3.connect(path)
        try:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("CREATE TABLE sample (value TEXT)")
            connection.execute("INSERT INTO sample VALUES ('persisted')")
            connection.commit()
            from urllib.parse import quote
            status, data, headers = self.request("/api/download/" + quote(path.name))
            self.assertEqual(status, 200)
            self.assertIn("filename*=UTF-8''", headers["Content-Disposition"])
            backup = Path(self.tmp.name, "download.db")
            backup.write_bytes(data)
            with sqlite3.connect(backup) as downloaded:
                self.assertEqual(downloaded.execute("SELECT value FROM sample").fetchone()[0], "persisted")
            downloaded.close()
        finally:
            connection.close()
        self.assertEqual(self.request("/api/download/..%2Fconfig%2Fproviders.json")[0], 400)


if __name__ == "__main__":
    unittest.main()
