import sqlite3
import os
import time

DB_PATH = os.path.expanduser("~/.local/share/llm-ibus/memory.db")


def _conn() -> sqlite3.Connection:
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with _conn() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS accepted (
                id INTEGER PRIMARY KEY,
                phrase TEXT NOT NULL,
                context TEXT,
                app_name TEXT,
                count INTEGER DEFAULT 1,
                last_used_at REAL
            );
            CREATE TABLE IF NOT EXISTS rejected (
                id INTEGER PRIMARY KEY,
                phrase TEXT NOT NULL,
                context TEXT,
                app_name TEXT,
                count INTEGER DEFAULT 1
            );
            CREATE INDEX IF NOT EXISTS idx_accepted_phrase ON accepted(phrase);
        """)


def record_accepted(phrase: str, context: str, app_name: str) -> None:
    with _conn() as conn:
        row = conn.execute(
            "SELECT id, count FROM accepted WHERE phrase=? AND app_name=?",
            (phrase, app_name),
        ).fetchone()
        if row:
            conn.execute(
                "UPDATE accepted SET count=?, last_used_at=? WHERE id=?",
                (row["count"] + 1, time.time(), row["id"]),
            )
        else:
            conn.execute(
                "INSERT INTO accepted (phrase, context, app_name, last_used_at) VALUES (?,?,?,?)",
                (phrase, context, app_name, time.time()),
            )


def record_rejected(phrase: str, context: str, app_name: str) -> None:
    with _conn() as conn:
        row = conn.execute(
            "SELECT id, count FROM rejected WHERE phrase=? AND app_name=?",
            (phrase, app_name),
        ).fetchone()
        if row:
            conn.execute(
                "UPDATE rejected SET count=? WHERE id=?",
                (row["count"] + 1, row["id"]),
            )
        else:
            conn.execute(
                "INSERT INTO rejected (phrase, context, app_name) VALUES (?,?,?)",
                (phrase, context, app_name),
            )


def get_top_phrases(context_prefix: str, app_name: str, limit: int = 5) -> list[str]:
    with _conn() as conn:
        rows = conn.execute(
            """
            SELECT phrase FROM accepted
            WHERE context LIKE ? AND (app_name=? OR app_name='')
            ORDER BY count DESC, last_used_at DESC
            LIMIT ?
            """,
            (f"%{context_prefix[-20:]}%", app_name, limit),
        ).fetchall()
    return [r["phrase"] for r in rows]


def get_all_accepted(limit: int = 200) -> list[dict]:
    with _conn() as conn:
        rows = conn.execute(
            "SELECT phrase, app_name, count, last_used_at FROM accepted ORDER BY last_used_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [dict(r) for r in rows]


def delete_phrase(phrase: str) -> None:
    with _conn() as conn:
        conn.execute("DELETE FROM accepted WHERE phrase=?", (phrase,))


def clear_all() -> None:
    with _conn() as conn:
        conn.execute("DELETE FROM accepted")
        conn.execute("DELETE FROM rejected")
