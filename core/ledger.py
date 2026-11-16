"""دفتر کل SQLite برای مدیریت حالت قطعات در اندروید (برگرفته از Mix jozve).

تضمین‌ها:
- تراکنش‌های ایمن و ذخیره خودکار وضعیت بعد از اتمام هر قطعه
- امکان توقف و ازسرگیری (Resume) بدون از دست رفتن هیچ قطعه‌ای
- ثبت دقیق توکن‌ها، هزینه‌ها، متن تولیدی و متن عربی مبدأ
"""
import hashlib
import json
import uuid
from contextlib import contextmanager
import os
import sqlite3
import threading
import time

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS chunks (
    idx INTEGER PRIMARY KEY,
    heading TEXT,
    arabic_text TEXT NOT NULL,
    char_len INTEGER NOT NULL,
    state TEXT NOT NULL DEFAULT 'pending',          -- pending|claimed|done|failed
    worker_id TEXT,
    attempts INTEGER DEFAULT 0,
    model TEXT,
    thinking TEXT,
    persian_text TEXT,
    persian_html TEXT,
    started_at REAL,
    finished_at REAL,
    fail_reason TEXT,
    input_tokens INTEGER DEFAULT 0,
    output_tokens INTEGER DEFAULT 0,
    latency_ms INTEGER DEFAULT 0,
    cost_usd REAL DEFAULT 0.0
);
CREATE INDEX IF NOT EXISTS ix_chunks_state ON chunks(state);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL,
    idx INTEGER,
    kind TEXT,
    detail TEXT
);
"""


class Ledger:
    def __init__(self, db_path: str):
        self.db_path = db_path
        self._tl = threading.local()
        self._connections = []
        self._connections_lock = threading.Lock()
        os.makedirs(os.path.dirname(os.path.abspath(db_path)), exist_ok=True)
        self.conn.executescript(SCHEMA)

    @property
    def conn(self) -> sqlite3.Connection:
        c = getattr(self._tl, "conn", None)
        if c is None:
            c = sqlite3.connect(self.db_path, timeout=30.0, isolation_level=None, check_same_thread=False)
            c.row_factory = sqlite3.Row
            c.execute("PRAGMA journal_mode=WAL")
            c.execute("PRAGMA synchronous=NORMAL")
            c.execute("PRAGMA busy_timeout=30000")
            self._tl.conn = c
            with self._connections_lock:
                self._connections.append(c)
        return c

    def get_meta(self, key, default=None):
        r = self.conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return json.loads(r["value"]) if r else default

    def set_meta(self, key, value):
        self.conn.execute(
            "INSERT INTO meta(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, json.dumps(value, ensure_ascii=False))
        )

    @contextmanager
    def transaction(self):
        conn = self.conn
        conn.execute("BEGIN IMMEDIATE")
        try:
            yield conn
            conn.execute("COMMIT")
        except BaseException:
            conn.execute("ROLLBACK")
            raise

    def close(self, current_thread_only=False):
        """Close all connections only when idle, or a worker's own at exit."""
        with self._connections_lock:
            if current_thread_only:
                conn = getattr(self._tl, "conn", None)
                if conn in self._connections:
                    conn.close()
                    self._connections.remove(conn)
                if conn is not None:
                    del self._tl.conn
            else:
                for conn in self._connections:
                    conn.close()
                self._connections.clear()
                self._tl = threading.local()

    def init_chunks(self, chunks: list, meta: dict, reset: bool = False):
        normalized = [(c["idx"], c.get("heading"), c["arabic_text"]) for c in chunks]
        if [c[0] for c in normalized] != list(range(len(normalized))):
            raise ValueError("Chunk indices must be contiguous and start at zero")
        if not normalized or any(not text.strip() for _, _, text in normalized):
            raise ValueError("The input source contains no usable text")
        signature = hashlib.sha256(json.dumps(
            {"chunks": normalized, "meta": meta}, ensure_ascii=False, sort_keys=True
        ).encode("utf-8")).hexdigest()
        existing = [tuple(r) for r in self.conn.execute(
            "SELECT idx, heading, arabic_text FROM chunks ORDER BY idx")]
        old_signature = self.get_meta("input_signature")
        changed = bool(existing) and (reset or existing != normalized or
                  (old_signature is not None and old_signature != signature))
        # Legacy databases have no fingerprint: compare persisted metadata as well.
        if existing and old_signature is None:
            # Old versions never recorded prompt/model identity. Preserve them,
            # but do not assume their paid output belongs to this project.
            changed = True
        archive_path = None
        if changed:
            if self.get_stats()["claimed"]:
                raise ValueError("Cannot replace a ledger while chunks are claimed")
            archive_path = self.db_path + ".archive-" + time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:8] + ".db"
            # SQLite backup includes WAL content without renaming live database files.
            backup = sqlite3.connect(archive_path)
            try:
                self.conn.backup(backup)
            finally:
                backup.close()
        with self.transaction() as conn:
            if changed:
                conn.execute("DELETE FROM chunks")
                conn.execute("DELETE FROM events")
                conn.execute("DELETE FROM meta")
            if not existing or changed:
                conn.executemany(
                    "INSERT INTO chunks(idx, heading, arabic_text, char_len) VALUES (?, ?, ?, ?)",
                    [(idx, heading, text, len(text)) for idx, heading, text in normalized])
            for k, v in {**meta, "input_signature": signature}.items():
                self.set_meta(k, v)
        return archive_path

    def recover_claimed(self):
        """Recover interrupted work; call only while the owning runner is idle."""
        self.conn.execute("""UPDATE chunks SET state='pending', worker_id=NULL,
                          started_at=NULL, attempts=MAX(0, attempts-1)
                          WHERE state='claimed'""")

    def release_chunk(self, idx):
        self.conn.execute("""UPDATE chunks SET state='pending', worker_id=NULL,
                          started_at=NULL, attempts=MAX(0, attempts-1)
                          WHERE idx=? AND state='claimed'""", (idx,))

    def claim_next(self, worker_id: str = "worker-1"):
        with self.transaction() as cur:
            row = cur.execute("""SELECT idx, heading, arabic_text FROM chunks
                              WHERE state='pending' ORDER BY idx LIMIT 1""").fetchone()
            if row is None:
                return None
            cur.execute("""UPDATE chunks SET state='claimed', worker_id=?, started_at=?,
                          finished_at=NULL, attempts=attempts+1 WHERE idx=?""",
                        (worker_id, time.time(), row["idx"]))
            return dict(row)

    def complete_chunk(self, idx: int, persian_text: str, model: str, thinking: str,
                       input_tokens: int, output_tokens: int, latency_ms: int, cost_usd: float):
        with self.transaction() as cur:
            updated = cur.execute(
                """UPDATE chunks SET state='done', persian_text=?, model=?, thinking=?,
                   input_tokens=?, output_tokens=?, latency_ms=?, cost_usd=?,
                   finished_at=?, fail_reason=NULL, worker_id=NULL
                   WHERE idx=? AND state='claimed'""",
                (persian_text, model, thinking, input_tokens, output_tokens, latency_ms,
                 cost_usd, time.time(), idx))
            if updated.rowcount != 1:
                raise ValueError("Only a claimed chunk may be completed")
            cur.execute("INSERT INTO events(ts, idx, kind, detail) VALUES (?, ?, 'DONE', ?)",
                        (time.time(), idx, model))

    def fail_chunk(self, idx: int, reason: str, max_retries: int = 3):
        with self.transaction() as cur:
            row = cur.execute("SELECT attempts FROM chunks WHERE idx=? AND state='claimed'", (idx,)).fetchone()
            if row is None:
                return
            state = 'failed' if row['attempts'] >= max_retries else 'pending'
            cur.execute("""UPDATE chunks SET state=?, fail_reason=?, finished_at=?,
                           worker_id=NULL WHERE idx=?""", (state, reason, time.time(), idx))
            cur.execute("INSERT INTO events(ts, idx, kind, detail) VALUES (?, ?, 'FAIL', ?)",
                        (time.time(), idx, reason))

    def get_stats(self):
        rows = self.conn.execute(
            """SELECT state, COUNT(*) as cnt,
                      SUM(input_tokens) as in_tok,
                      SUM(output_tokens) as out_tok,
                      SUM(cost_usd) as total_cost
               FROM chunks GROUP BY state"""
        ).fetchall()

        stats = {
            "total": 0, "pending": 0, "claimed": 0, "done": 0, "failed": 0,
            "input_tokens": 0, "output_tokens": 0, "total_cost_usd": 0.0
        }
        for r in rows:
            st = r["state"]
            cnt = r["cnt"]
            stats[st] = cnt
            stats["total"] += cnt
            stats["input_tokens"] += (r["in_tok"] or 0)
            stats["output_tokens"] += (r["out_tok"] or 0)
            stats["total_cost_usd"] += (r["total_cost"] or 0.0)

        stats["total_cost_usd"] = round(stats["total_cost_usd"], 4)
        return stats

    def get_previous_context(self, current_idx: int):
        """بازیابی متن قطعه قبلی و پاسخ فارسی آن جهت حفظ پیوستگی."""
        if current_idx <= 0:
            return None, None
        row = self.conn.execute(
            """SELECT arabic_text, persian_text FROM chunks
               WHERE idx = ? AND state = 'done'""",
            (current_idx - 1,)
        ).fetchone()
        if row:
            return row["arabic_text"], row["persian_text"]
        return None, None

    def get_all_chunks(self, limit: int = 200):
        rows = self.conn.execute(
            """SELECT idx, heading, char_len, state, model, input_tokens, output_tokens,
                      latency_ms, cost_usd, fail_reason,
                      substr(arabic_text, 1, 150) as arabic_preview,
                      substr(persian_text, 1, 150) as persian_preview
               FROM chunks ORDER BY idx ASC LIMIT ?""",
            (limit,)
        ).fetchall()
        return [dict(r) for r in rows]

    def get_chunk(self, idx: int):
        row = self.conn.execute("SELECT * FROM chunks WHERE idx = ?", (idx,)).fetchone()
        return dict(row) if row else None

    def get_done_chunks(self):
        rows = self.conn.execute(
            """SELECT idx, heading, arabic_text, persian_text, model, thinking, state, fail_reason
               FROM chunks ORDER BY idx ASC"""
        ).fetchall()
        return [dict(r) for r in rows]

    def retry_all_failed(self):
        with self.transaction() as cur:
            cur.execute("""UPDATE chunks SET state='pending', attempts=0, fail_reason=NULL,
                           worker_id=NULL, started_at=NULL, finished_at=NULL WHERE state='failed'""")
