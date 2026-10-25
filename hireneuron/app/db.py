"""SQLite persistence (stdlib). JSON columns keep the schema small for a hackathon prototype."""
import json
import os
import sqlite3
import threading
from pathlib import Path

DATA_DIR = Path(os.environ.get("HN_DATA_DIR", Path(__file__).resolve().parent.parent / "data" / "runtime"))
_lock = threading.RLock()
_conn: sqlite3.Connection | None = None

SCHEMA = """
CREATE TABLE IF NOT EXISTS interviews (
  id TEXT PRIMARY KEY, token TEXT UNIQUE NOT NULL, created_at TEXT NOT NULL, status TEXT NOT NULL,
  job TEXT, candidate TEXT, resume TEXT, match TEXT, config TEXT, questions TEXT, consent TEXT,
  started_at TEXT, ended_at TEXT, evaluation TEXT, overrides TEXT, decision TEXT, state TEXT, live TEXT);
CREATE TABLE IF NOT EXISTS answers (
  id INTEGER PRIMARY KEY AUTOINCREMENT, interview_id TEXT NOT NULL, idx INTEGER NOT NULL, parent_idx INTEGER,
  question TEXT NOT NULL, category TEXT, answer TEXT, status TEXT NOT NULL, duration_s REAL, created_at TEXT NOT NULL,
  revisions INTEGER NOT NULL DEFAULT 0, history TEXT);
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS events (
  id INTEGER PRIMARY KEY AUTOINCREMENT, interview_id TEXT NOT NULL, t_offset REAL, created_at TEXT NOT NULL,
  source TEXT, type TEXT NOT NULL, kind TEXT NOT NULL, severity TEXT, description TEXT, evidence TEXT,
  basis TEXT, review TEXT);
CREATE TABLE IF NOT EXISTS audit (
  id INTEGER PRIMARY KEY AUTOINCREMENT, interview_id TEXT NOT NULL, seq INTEGER NOT NULL, at TEXT NOT NULL,
  actor TEXT NOT NULL, action TEXT NOT NULL, detail TEXT NOT NULL, prev TEXT NOT NULL, hash TEXT NOT NULL,
  UNIQUE(interview_id, seq));
CREATE INDEX IF NOT EXISTS ix_answers ON answers(interview_id);
CREATE INDEX IF NOT EXISTS ix_events ON events(interview_id);
"""
JSON_COLS = {"history", "live", "job", "candidate", "resume", "match", "config", "questions", "consent", "evaluation", "overrides", "decision", "state", "basis", "review"}


def conn() -> sqlite3.Connection:
    global _conn
    with _lock:
        if _conn is None:
            DATA_DIR.mkdir(parents=True, exist_ok=True)
            _conn = sqlite3.connect(DATA_DIR / "hireneuron.db", check_same_thread=False)
            _conn.row_factory = sqlite3.Row
            _conn.executescript(SCHEMA)
            _migrate(_conn)
        return _conn


def _migrate(c: sqlite3.Connection):
    """Databases created by earlier versions lack newer columns; add them in place."""
    def cols(t):
        return {r["name"] for r in c.execute(f"PRAGMA table_info({t})")}
    if "live" not in cols("interviews"):
        c.execute("ALTER TABLE interviews ADD COLUMN live TEXT")
    if "revisions" not in cols("answers"):
        c.execute("ALTER TABLE answers ADD COLUMN revisions INTEGER NOT NULL DEFAULT 0")
    if "history" not in cols("answers"):
        c.execute("ALTER TABLE answers ADD COLUMN history TEXT")
    c.commit()


def reset(path: Path | None = None):
    """Used by tests to point at a fresh database."""
    global _conn, DATA_DIR
    with _lock:
        if _conn:
            _conn.close()
        _conn = None
        if path:
            DATA_DIR = path


def _row(r: sqlite3.Row | None) -> dict | None:
    if r is None:
        return None
    d = dict(r)
    for k in JSON_COLS & d.keys():
        d[k] = json.loads(d[k]) if d[k] else None
    return d


def execute(sql: str, params: tuple = ()) -> int:
    with _lock:
        cur = conn().execute(sql, params)
        conn().commit()
        return cur.lastrowid


def one(sql: str, params: tuple = ()) -> dict | None:
    with _lock:
        return _row(conn().execute(sql, params).fetchone())


def many(sql: str, params: tuple = ()) -> list[dict]:
    with _lock:
        return [_row(r) for r in conn().execute(sql, params).fetchall()]


def dumps(v) -> str | None:
    return None if v is None else json.dumps(v, ensure_ascii=False)


def update_interview(iid: str, **fields):
    cols = ", ".join(f"{k}=?" for k in fields)
    execute(f"UPDATE interviews SET {cols} WHERE id=?",
            tuple(dumps(v) if k in JSON_COLS else v for k, v in fields.items()) + (iid,))
