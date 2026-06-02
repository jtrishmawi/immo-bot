"""
immo-bot — SQLite dedup store.
Tracks which listing IDs have already been sent to avoid duplicates.
"""
import os
import sqlite3

DB_PATH = os.getenv("DB_PATH", "/app/data/sent_listings.db")


def init_db() -> sqlite3.Connection:
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS sent_listings (
            id       TEXT PRIMARY KEY,
            sent_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            city     TEXT,
            price    REAL
        )
    """)
    conn.commit()
    return conn


def is_sent(conn: sqlite3.Connection, listing_id: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sent_listings WHERE id = ?", (listing_id,)
    ).fetchone() is not None


def mark_sent(conn: sqlite3.Connection, listing_id: str, city: str = "", price: float = 0.0):
    conn.execute(
        "INSERT OR IGNORE INTO sent_listings (id, city, price) VALUES (?, ?, ?)",
        (listing_id, city, price)
    )
    conn.commit()
