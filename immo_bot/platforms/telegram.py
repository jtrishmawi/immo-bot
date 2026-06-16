"""
immo-bot — Telegram adapter.

Handles all Telegram I/O: sending messages/photos and polling for bot commands.
poll_commands() is callback-based — it knows nothing about search logic.
"""
import os
import time
import logging
from typing import Optional, Callable

import requests

logger = logging.getLogger(__name__)

_TELEGRAM_ENABLED = bool(os.getenv("TELEGRAM_BOT_TOKEN") and os.getenv("TELEGRAM_CHAT_ID"))
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID", "")
TELEGRAM_API       = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}" if _TELEGRAM_ENABLED else ""

# Headers for Seloger photo CDN bypass
_PHOTO_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36",
    "Accept":     "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
    "Referer":    "https://www.seloger.com/",
}

# Tracks which chat IDs are awaiting a numeric /search selection
_pending_search: dict = {}


def download_photo(scraper, url: str) -> Optional[bytes]:
    """Download a Seloger listing photo via cloudscraper to bypass CDN protection."""
    try:
        r = scraper.get(url, headers=_PHOTO_HEADERS, timeout=15)
        ct = r.headers.get("Content-Type", "")
        if r.ok and r.content and ct.startswith("image/"):
            return r.content
        logger.debug("Photo not an image (Content-Type: %s), skipping", ct)
    except Exception as e:
        logger.warning("Photo download failed: %s", e)
    return None


def send(scraper, text: str, photo_url: Optional[str] = None) -> Optional[int]:
    """Send a message (with optional photo) to Telegram.

    Returns the Telegram message_id on success, None on failure.
    """
    if not _TELEGRAM_ENABLED:
        return None

    if photo_url:
        photo_bytes = download_photo(scraper, photo_url)
        if photo_bytes:
            try:
                r = requests.post(
                    f"{TELEGRAM_API}/sendPhoto",
                    data={"chat_id": TELEGRAM_CHAT_ID, "caption": text, "parse_mode": "HTML"},
                    files={"photo": ("photo.jpg", photo_bytes, "image/jpeg")},
                    timeout=30,
                )
                if r.ok:
                    return r.json()["result"]["message_id"]
                logger.warning("sendPhoto failed %s: %s", r.status_code, r.text[:200])
            except Exception as e:
                logger.warning("sendPhoto exception: %s", e)

    try:
        r = requests.post(
            f"{TELEGRAM_API}/sendMessage",
            json={"chat_id": TELEGRAM_CHAT_ID, "text": text,
                  "parse_mode": "HTML", "disable_web_page_preview": False},
            timeout=15,
        )
        if r.ok:
            return r.json()["result"]["message_id"]
        logger.error("sendMessage failed %s: %s", r.status_code, r.text[:300])
    except Exception as e:
        logger.error("Telegram send failed: %s", e)
    return None


def delete_messages(message_ids: list) -> int:
    """Delete Telegram messages by ID. Returns count of successfully deleted messages."""
    if not _TELEGRAM_ENABLED or not message_ids:
        return 0
    deleted = 0
    for mid in message_ids:
        try:
            r = requests.post(
                f"{TELEGRAM_API}/deleteMessage",
                json={"chat_id": TELEGRAM_CHAT_ID, "message_id": mid},
                timeout=10,
            )
            if r.ok:
                deleted += 1
        except Exception as e:
            logger.debug("deleteMessage %s failed: %s", mid, e)
    return deleted


def flush_pending_updates() -> None:
    """Advance past all pending updates so old commands aren't replayed on restart."""
    if not _TELEGRAM_ENABLED:
        return
    try:
        r = requests.get(f"{TELEGRAM_API}/getUpdates", params={"offset": -1, "timeout": 1}, timeout=5)
        updates = r.json().get("result", []) if r.ok else []
        if updates:
            last_id = updates[-1]["update_id"]
            requests.get(f"{TELEGRAM_API}/getUpdates", params={"offset": last_id + 1, "timeout": 1}, timeout=5)
            logger.info("Flushed %d pending Telegram update(s)", len(updates))
    except Exception as e:
        logger.warning("flush_pending_updates: %s", e)


def poll_commands(
    scraper,
    on_health:        Callable[[], None],
    on_search:        Callable[[], None],
    on_search_select: Callable[[str, str], None],
    on_help:          Callable[[], None] | None = None,
    on_cleandb:       Callable[[], None] | None = None,
    on_searchall:     Callable[[], None] | None = None,
) -> None:
    """Background thread: long-poll getUpdates and dispatch to callbacks.

    on_health()                      → called on /health
    on_search()                      → called on /search (show menu)
    on_search_select(chat_id, text)  → called when a pending chat sends a number
    on_help()                        → called on /help
    on_cleandb()                     → called on /cleandb
    on_searchall()                   → called on /searchall
    """
    offset = 0
    while True:
        try:
            r = requests.get(
                f"{TELEGRAM_API}/getUpdates",
                params={"offset": offset, "timeout": 30, "allowed_updates": ["message"]},
                timeout=35,
            )
            if not r.ok:
                time.sleep(5)
                continue
            for update in r.json().get("result", []):
                offset  = update["update_id"] + 1
                msg     = update.get("message", {})
                text    = msg.get("text", "").strip()
                chat_id = str(msg.get("chat", {}).get("id", ""))

                if text.startswith("/start"):
                    if on_help:
                        on_help()
                elif text.startswith("/health"):
                    on_health()
                elif text.startswith("/searchall"):
                    if on_searchall:
                        on_searchall()
                elif text.startswith("/search"):
                    on_search()
                    _pending_search[chat_id] = True
                elif text.startswith("/help"):
                    if on_help:
                        on_help()
                elif text.startswith("/cleandb"):
                    if on_cleandb:
                        on_cleandb()
                elif chat_id in _pending_search:
                    _pending_search.pop(chat_id, None)
                    on_search_select(chat_id, text)
        except Exception as e:
            logger.error("poll_commands error: %s", e)
            time.sleep(5)
