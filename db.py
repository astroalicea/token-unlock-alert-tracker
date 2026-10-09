"""
SQLite storage for tracked unlock events, with dedupe so the same
unlock never triggers two alerts.

Dedupe strategy: each unlock event is uniquely identified by
(slug, timestamp) — a token can only have one unlock at a given
timestamp. Before alerting, alerts.py checks whether that exact
(slug, timestamp) pair has already been marked as alerted in this DB.
"""

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone

from config import DB_PATH

SCHEMA = """
CREATE TABLE IF NOT EXISTS unlock_events (
    slug TEXT NOT NULL,
    label TEXT NOT NULL,
    timestamp INTEGER NOT NULL,
    category TEXT,
    unlock_type TEXT,
    tokens_unlocking REAL,
    percent_of_circulating REAL,
    first_seen_at TEXT NOT NULL,
    alerted_at TEXT,
    PRIMARY KEY (slug, timestamp)
);

CREATE TABLE IF NOT EXISTS scrape_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    slug TEXT NOT NULL,
    ran_at TEXT NOT NULL,
    success INTEGER NOT NULL,
    error_message TEXT
);
"""


@contextmanager
def get_connection(db_path: str = DB_PATH):
    """Context-managed SQLite connection with foreign keys/row factory set."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db(db_path: str = DB_PATH) -> None:
    """Create tables if they don't already exist. Safe to call every run."""
    with get_connection(db_path) as conn:
        conn.executescript(SCHEMA)


def upsert_unlock_event(
    slug: str,
    label: str,
    timestamp: int,
    category: str,
    unlock_type: str,
    tokens_unlocking: float,
    percent_of_circulating: float | None,
    db_path: str = DB_PATH,
) -> bool:
    """Insert an unlock event if it's new; update its stats if it already
    exists (unlock size estimates can shift as the date approaches).

    Returns True if this was a newly-seen event, False if it already
    existed in the DB (regardless of whether it's been alerted yet).
    """
    with get_connection(db_path) as conn:
        existing = conn.execute(
            "SELECT 1 FROM unlock_events WHERE slug = ? AND timestamp = ?",
            (slug, timestamp),
        ).fetchone()

        now = datetime.now(timezone.utc).isoformat()

        if existing:
            conn.execute(
                """
                UPDATE unlock_events
                SET category = ?, unlock_type = ?, tokens_unlocking = ?,
                    percent_of_circulating = ?
                WHERE slug = ? AND timestamp = ?
                """,
                (category, unlock_type, tokens_unlocking,
                 percent_of_circulating, slug, timestamp),
            )
            return False

        conn.execute(
            """
            INSERT INTO unlock_events
                (slug, label, timestamp, category, unlock_type,
                 tokens_unlocking, percent_of_circulating, first_seen_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (slug, label, timestamp, category, unlock_type,
             tokens_unlocking, percent_of_circulating, now),
        )
        return True


def is_already_alerted(slug: str, timestamp: int, db_path: str = DB_PATH) -> bool:
    """The core dedupe check. Call this before sending any alert."""
    with get_connection(db_path) as conn:
        row = conn.execute(
            "SELECT alerted_at FROM unlock_events WHERE slug = ? AND timestamp = ?",
            (slug, timestamp),
        ).fetchone()
        return row is not None and row["alerted_at"] is not None


def mark_alerted(slug: str, timestamp: int, db_path: str = DB_PATH) -> None:
    """Flag an event as alerted so it's never sent again."""
    now = datetime.now(timezone.utc).isoformat()
    with get_connection(db_path) as conn:
        conn.execute(
            "UPDATE unlock_events SET alerted_at = ? WHERE slug = ? AND timestamp = ?",
            (now, slug, timestamp),
        )


def log_scrape_result(
    slug: str,
    success: bool,
    error_message: str | None = None,
    db_path: str = DB_PATH,
) -> None:
    """Record every scrape attempt (success or failure) for debugging
    and for the 'scrape failed' alert / stale-cache fallback logic."""
    now = datetime.now(timezone.utc).isoformat()
    with get_connection(db_path) as conn:
        conn.execute(
            "INSERT INTO scrape_log (slug, ran_at, success, error_message) "
            "VALUES (?, ?, ?, ?)",
            (slug, now, int(success), error_message),
        )


def get_last_successful_scrape(slug: str, db_path: str = DB_PATH) -> dict | None:
    """Fetch the most recent successfully-stored unlock event for a token,
    used as a cache fallback when a live scrape fails."""
    with get_connection(db_path) as conn:
        row = conn.execute(
            """
            SELECT * FROM unlock_events
            WHERE slug = ?
            ORDER BY first_seen_at DESC
            LIMIT 1
            """,
            (slug,),
        ).fetchone()
        return dict(row) if row else None
