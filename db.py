"""SQLite persistence for Provenance Guard.

Two tables back the audit log:
  - submissions: one row per classified piece of content (the decision).
  - appeals:     one row per appeal, linked to a submission by content_id.

get_log() joins them so a reviewer sees the original decision, both signal
scores, the combined confidence, and any appeal reasoning in one place.
"""

import os
import sqlite3

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "provenance.db")


def _connect():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    """Create tables if they do not exist. Safe to call on every startup."""
    with _connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS submissions (
                content_id    TEXT PRIMARY KEY,
                creator_id    TEXT NOT NULL,
                timestamp     TEXT NOT NULL,
                text_excerpt  TEXT,
                attribution   TEXT NOT NULL,
                confidence    REAL NOT NULL,
                llm_score     REAL,
                style_score   REAL,
                combined_p    REAL NOT NULL,
                signals_used  TEXT NOT NULL,
                status        TEXT NOT NULL DEFAULT 'classified'
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS appeals (
                id                INTEGER PRIMARY KEY AUTOINCREMENT,
                content_id        TEXT NOT NULL,
                creator_reasoning TEXT NOT NULL,
                timestamp         TEXT NOT NULL,
                FOREIGN KEY (content_id) REFERENCES submissions (content_id)
            )
            """
        )


def insert_submission(record: dict):
    """Persist a classification decision. `record` keys mirror the columns."""
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO submissions (
                content_id, creator_id, timestamp, text_excerpt, attribution,
                confidence, llm_score, style_score, combined_p, signals_used, status
            ) VALUES (
                :content_id, :creator_id, :timestamp, :text_excerpt, :attribution,
                :confidence, :llm_score, :style_score, :combined_p, :signals_used, :status
            )
            """,
            record,
        )


def get_submission(content_id: str):
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM submissions WHERE content_id = ?", (content_id,)
        ).fetchone()
        return dict(row) if row else None


def update_status(content_id: str, status: str) -> bool:
    with _connect() as conn:
        cur = conn.execute(
            "UPDATE submissions SET status = ? WHERE content_id = ?",
            (status, content_id),
        )
        return cur.rowcount > 0


def insert_appeal(content_id: str, creator_reasoning: str, timestamp: str):
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO appeals (content_id, creator_reasoning, timestamp)
            VALUES (?, ?, ?)
            """,
            (content_id, creator_reasoning, timestamp),
        )


def get_log(limit: int = 50):
    """Return recent submissions (most recent first), each with its appeal history."""
    with _connect() as conn:
        subs = conn.execute(
            "SELECT * FROM submissions ORDER BY timestamp DESC LIMIT ?", (limit,)
        ).fetchall()
        entries = []
        for sub in subs:
            entry = dict(sub)
            appeals = conn.execute(
                "SELECT creator_reasoning, timestamp FROM appeals "
                "WHERE content_id = ? ORDER BY timestamp ASC",
                (entry["content_id"],),
            ).fetchall()
            entry["appeals"] = [dict(a) for a in appeals]
            entries.append(entry)
        return entries
