"""db.py — lightweight SQLite store for queue-farmer session state."""

import os
import sqlite3

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "farmer.db")


def _conn() -> sqlite3.Connection:
    con = sqlite3.connect(DB_PATH, check_same_thread=False)
    con.row_factory = sqlite3.Row
    return con


def init_db():
    """Create tables if they don't exist yet."""
    with _conn() as con:
        con.executescript("""
            CREATE TABLE IF NOT EXISTS sessions (
                slot_id     INTEGER PRIMARY KEY,
                account     TEXT,
                status      TEXT DEFAULT 'pending',
                queue_state TEXT DEFAULT '',
                launched_at REAL DEFAULT 0,
                updated_at  REAL DEFAULT 0,
                error       TEXT DEFAULT ''
            );

            CREATE TABLE IF NOT EXISTS logs (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                ts          REAL,
                slot_id     INTEGER,
                message     TEXT
            );
        """)
    print(f"[DB] Initialized: {DB_PATH}")


def upsert_slot(slot_id: int, account: str, status: str,
                queue_state: str = "", launched_at: float = 0.0, error: str = ""):
    import time
    with _conn() as con:
        con.execute("""
            INSERT INTO sessions (slot_id, account, status, queue_state, launched_at, updated_at, error)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(slot_id) DO UPDATE SET
                account     = excluded.account,
                status      = excluded.status,
                queue_state = excluded.queue_state,
                launched_at = excluded.launched_at,
                updated_at  = excluded.updated_at,
                error       = excluded.error
        """, (slot_id, account, status, queue_state, launched_at, time.time(), error))


def log_event(slot_id: int, message: str):
    import time
    with _conn() as con:
        con.execute("INSERT INTO logs (ts, slot_id, message) VALUES (?, ?, ?)",
                    (time.time(), slot_id, message))


def get_all_slots():
    with _conn() as con:
        return con.execute("SELECT * FROM sessions ORDER BY slot_id").fetchall()
