"""
immo-bot — Seloger notifier. Fetches listings and sends to Telegram.
Safe to run multiple times: SQLite dedup prevents double-sends.

Env vars required:
  TELEGRAM_BOT_TOKEN  — from BotFather
  TELEGRAM_CHAT_ID    — your Telegram user/group ID
  DB_PATH             — optional, default /app/data/sent_listings.db
  SEARCH_URL_1, SEARCH_URL_2, ...  — Seloger search URLs (copy from browser after searching)
"""
import os
import re
import sqlite3
import sys
import time
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

import cloudscraper
import requests
from dotenv import load_dotenv

from seloger import build_url, parse_url

logger = logging.getLogger(__name__)

# Load .env if present (no-op in Docker where vars are injected directly)
_env_file = Path(__file__).parent / ".env"
if _env_file.exists():
    load_dotenv(_env_file)
    logger.debug("Loaded env from %s", _env_file)

_missing = [v for v in ("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID") if not os.getenv(v)]
if _missing:
    sys.exit(f"Missing required env vars: {', '.join(_missing)}. Set them in .env or pass via environment.")

TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
TELEGRAM_CHAT_ID   = os.environ["TELEGRAM_CHAT_ID"]
DB_PATH = os.getenv("DB_PATH", "/app/data/sent_listings.db")
DEBUG = os.getenv("DEBUG", "").lower() in ("1", "true", "yes")


def _load_search_urls() -> list[str]:
    urls, i = [], 1
    while url := os.getenv(f"SEARCH_URL_{i}", "").strip():
        urls.append(url)
        i += 1
    return urls

SEARCH_URLS = _load_search_urls()
if not SEARCH_URLS:
    sys.exit(
        "No search URLs configured. "
        "Set SEARCH_URL_1, SEARCH_URL_2, ... in .env — "
        "paste your Seloger search URLs (copy from browser after searching)."
    )

_ESTATE_LABELS: dict[str, tuple[str, str]] = {
    "Apartment": ("Appartements", "🏢"),
    "House":     ("Maisons",      "🏡"),
}

_run_state: dict = {"started_at": None, "last_run_at": None, "last_run_sent": 0}

TELEGRAM_API = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"
SELOGER_SEARCH_API = "https://www.seloger.com/serp-bff/search"
SELOGER_CLASSIFIED_API = "https://www.seloger.com/classifiedList"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36",
    "Accept": "*/*",
    "Accept-Language": "en-US,en;q=0.7",
    "Content-Type": "application/json; charset=utf-8",
    "Origin": "https://www.seloger.com",
    "Referer": "https://www.seloger.com/classified-search",
    "sec-ch-ua": '"Chromium";v="148", "Brave";v="148", "Not/A)Brand";v="99"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"Windows"',
    "sec-fetch-dest": "empty",
    "sec-fetch-mode": "cors",
    "sec-fetch-site": "same-origin",
    "x-ab-variant": "experiment_ab_quality_score_ai_enriched",
}


# ---------------------------------------------------------------------------
# Build API criteria from parsed URL params
# ---------------------------------------------------------------------------

def _csv_to_list(value: str) -> list:
    return [v.strip() for v in value.split(",") if v.strip()]


def build_criteria(params: dict) -> dict:
    """Build serp-bff criteria payload from a params dict (from parse_url)."""
    p = params
    place_ids = [c.strip() for c in p["locations"].split(",") if c.strip()]

    criteria = {
        "distributionTypes":  _csv_to_list(p["distributionTypes"]),
        "estateTypes":         _csv_to_list(p["estateTypes"]),
        "furnished":           _csv_to_list(p["furnished"]),
        "numberOfRoomsMin": int(p["numberOfRoomsMin"]),
        "priceMax":            int(p["priceMax"]),
        "spaceMin":            int(p["spaceMin"]),
        "projectTypes":        ["Flatsharing", "Stock"],
        "location":            {"placeIds": place_ids},
    }
    if p.get("featuresIncluded"):
        criteria["featuresIncluded"] = _csv_to_list(p["featuresIncluded"])
    if p.get("locationsInBuildingIncluded"):
        criteria["locationsInBuildingIncluded"] = _csv_to_list(
            p["locationsInBuildingIncluded"])
    return criteria


# ---------------------------------------------------------------------------
# SQLite dedup
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Seloger API
# ---------------------------------------------------------------------------

def fetch_page(scraper, criteria: dict, page: int, size: int = 50) -> dict:
    payload = {
        "criteria": criteria,
        "paging": {"page": page, "size": size, "order": "Default"},
    }
    try:
        r = scraper.post(SELOGER_SEARCH_API, json=payload,
                         headers=HEADERS, timeout=30)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        logger.error(f"fetch_page {page}: {e}")
        return {}


def fetch_details(scraper, ids: list[str]) -> dict:
    if not ids:
        return {}
    url = f"{SELOGER_CLASSIFIED_API}/{','.join(ids)}"
    try:
        r = scraper.get(url, headers=HEADERS, timeout=30)
        r.raise_for_status()
        data = r.json()
        if isinstance(data, dict) and "classifieds" in data:
            return {str(item["id"]): item for item in data["classifieds"] if item.get("id")}
        if isinstance(data, list):
            return {str(item["id"]): item for item in data if item.get("id")}
    except Exception as e:
        logger.error(f"fetch_details: {e}")
    return {}


def clean_price(raw) -> Optional[float]:
    if raw is None:
        return None
    if isinstance(raw, (int, float)):
        return float(raw)
    if isinstance(raw, str):
        m = re.search(r"\d+", raw.replace(" ", "").replace(" ", ""))
        return float(m.group()) if m else None
    return None


def parse_listing(item: dict, classified: dict) -> dict:
    loc = classified.get("location", {}).get("address", {}) or {}
    raw = classified.get("rawData", {}) or {}
    hard = classified.get("hardFacts", {}) or {}
    provider = classified.get("provider", {}) or {}

    return {
        "id":              str(item.get("id")),
        "price":           clean_price(hard.get("price", {}).get("value")),
        "estate_type":     raw.get("propertyType", ""),
        "city":            loc.get("city", ""),
        "zip_code":        loc.get("zipCode", ""),
        "address":         loc.get("address", ""),
        "bedrooms":        raw.get("nbbedroom"),
        "rooms":           raw.get("nbroom"),
        "space":           (raw.get("surface") or {}).get("main"),
        "energy_class":    classified.get("energyClass", ""),
        "agency":          (provider.get("intermediaryCard") or {}).get("title", ""),
        "is_private":      provider.get("isPrivateOwner", False),
        "url":             classified.get("url", ""),
        "photo":           _first_photo(classified),
    }


def _first_photo(classified: dict) -> Optional[str]:
    try:
        images = (classified.get("gallery") or {}).get("images", [])
        if images:
            img = images[0]
            return img.get("url") or img.get("src") or img.get("path")
    except Exception:
        pass
    return None


# ---------------------------------------------------------------------------
# Telegram
# ---------------------------------------------------------------------------

def format_message(l: dict) -> str:
    kind = "Maison" if "house" in l["estate_type"].lower() else "Appartement"
    parts = [kind]
    if l["bedrooms"]:
        parts.append(f"{l['bedrooms']} ch")
    elif l["rooms"]:
        parts.append(f"{l['rooms']} pièces")
    if l["space"]:
        parts.append(f"{int(l['space'])}m²")
    title = " · ".join(parts)

    location = l["city"]
    if l["zip_code"]:
        location += f" ({l['zip_code']})"

    price_str = f"{int(l['price']):,} €/mois".replace(",",
                                                      " ") if l["price"] else "prix N/A"

    lines = [
        f"🏠 <b>{title}</b>",
        f"📍 {location}",
        f"💶 <b>{price_str}</b>",
    ]
    if l["address"] and l["address"] != l["city"]:
        lines.append(f"📌 {l['address']}")
    if l["energy_class"]:
        lines.append(f"⚡ DPE {l['energy_class']}")
    if l["is_private"]:
        lines.append("👤 Particulier")
    elif l["agency"]:
        lines.append(f"🏢 {l['agency']}")
    if l["url"]:
        lines.append(f'\n<a href="{l["url"]}">🔗 Voir l\'annonce</a>')

    return "\n".join(lines)


def download_photo(scraper, url: str) -> Optional[bytes]:
    """Download photo via scraper with seloger referer to bypass CDN protection.
    Returns bytes only if content-type is actually an image."""
    try:
        r = scraper.get(url, headers={
            **HEADERS,
            "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
            "Referer": "https://www.seloger.com/",
        }, timeout=15)
        ct = r.headers.get("Content-Type", "")
        if r.ok and r.content and ct.startswith("image/"):
            return r.content
        logger.debug(f"Photo not an image (Content-Type: {ct}), skipping")
    except Exception as e:
        logger.warning(f"Photo download failed: {e}")
    return None


def send(scraper, text: str, photo_url: Optional[str] = None) -> bool:
    if photo_url:
        photo_bytes = download_photo(scraper, photo_url)
        if photo_bytes:
            try:
                r = requests.post(
                    f"{TELEGRAM_API}/sendPhoto",
                    data={"chat_id": TELEGRAM_CHAT_ID, "caption": text,
                          "parse_mode": "HTML"},
                    files={"photo": ("photo.jpg", photo_bytes, "image/jpeg")},
                    timeout=30,
                )
                if r.ok:
                    return True
                logger.warning(
                    f"sendPhoto (upload) failed {r.status_code}: {r.text[:200]}")
            except Exception as e:
                logger.warning(f"sendPhoto (upload) exception: {e}")

    try:
        r = requests.post(
            f"{TELEGRAM_API}/sendMessage",
            json={"chat_id": TELEGRAM_CHAT_ID, "text": text,
                  "parse_mode": "HTML", "disable_web_page_preview": False},
            timeout=15,
        )
        if r.ok:
            return True
        logger.error(f"sendMessage failed {r.status_code}: {r.text[:300]}")
        return False
    except Exception as e:
        logger.error(f"Telegram send failed: {e}")
        return False


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def _collect_all_items(scraper, criteria: dict, label: str) -> list:
    """Fetch up to 3 pages for a given criteria dict. Returns list of raw items."""
    all_items = []
    for page in range(1, 4):
        data = fetch_page(scraper, criteria, page)
        items = data.get("items") or data.get("classifieds", [])
        if not items:
            break
        all_items.extend(items)
        total = data.get("resultsCount") or data.get("totalCount", 0)
        logger.info(
            f"[{label}] Page {page}: {len(items)} items (total available: {total})")
        if len(all_items) >= total:
            break
        time.sleep(2)
    logger.info(f"[{label}] Collected {len(all_items)} listings")
    return all_items


def debug_send(scraper, text: str):
    if DEBUG:
        send(scraper, f"🐛 <b>[DEBUG]</b> {text}")


def _label_from_params(params: dict) -> tuple[str, str]:
    estate = params.get("estateTypes", "Listings").capitalize()
    return _ESTATE_LABELS.get(estate, (estate, "🔍"))


def send_health(scraper) -> None:
    started = _run_state["started_at"].strftime("%d/%m %H:%M") if _run_state["started_at"] else "?"
    last    = _run_state["last_run_at"].strftime("%d/%m %H:%M") if _run_state["last_run_at"] else "jamais"
    sent    = _run_state["last_run_sent"]
    send(scraper, f"✅ <b>immo-bot actif</b> depuis {started}\nDernier run : {last} — {sent} annonce(s)")


_pending_search: dict = {}  # chat_id -> "awaiting_selection"


def _send_search_menu(scraper) -> None:
    lines = ["🔍 <b>Quelle recherche lancer ?</b>"]
    for i, url in enumerate(SEARCH_URLS, 1):
        label, icon = _label_from_params(parse_url(url))
        lines.append(f"{i}. {icon} {label}")
    lines.append("\nRépondez avec le numéro.")
    send(scraper, "\n".join(lines))


def _run_on_demand_search(scraper, text: str, chat_id: str) -> None:
    _pending_search.pop(chat_id, None)
    try:
        idx = int(text.strip()) - 1
    except ValueError:
        return
    if idx < 0 or idx >= len(SEARCH_URLS):
        send(scraper, f"Numéro invalide (1–{len(SEARCH_URLS)}).")
        return

    url = SEARCH_URLS[idx]
    query = parse_url(url)
    label, icon = _label_from_params(query)
    send(scraper, f"🔄 Recherche {icon} <b>{label}</b> en cours…")

    conn = init_db()
    try:
        items = _collect_all_items(scraper, build_criteria(query), label)
        new_items = [i for i in items if not is_sent(conn, str(i.get("id")))]
        sent = _send_new_listings(scraper, conn, new_items, label)
        if not new_items:
            send(scraper, f"ℹ️ Aucune nouvelle annonce pour <b>{label}</b>.")
        _run_state["last_run_at"] = datetime.now(ZoneInfo("Europe/Paris"))
        _run_state["last_run_sent"] = sent
    finally:
        conn.close()


def poll_commands(scraper) -> None:
    """Background thread: long-poll Telegram getUpdates and handle bot commands."""
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
                offset = update["update_id"] + 1
                msg  = update.get("message", {})
                text = msg.get("text", "").strip()
                chat_id = str(msg.get("chat", {}).get("id", ""))

                if text.startswith("/health"):
                    send_health(scraper)
                elif text.startswith("/search"):
                    _send_search_menu(scraper)
                    _pending_search[chat_id] = True
                elif chat_id in _pending_search:
                    _run_on_demand_search(scraper, text, chat_id)
        except Exception as e:
            logger.error("poll_commands error: %s", e)
            time.sleep(5)


def _send_new_listings(scraper, conn, new_items: list, label: str) -> int:
    """Send already-filtered new listings. Returns count sent."""
    sent_count = 0
    batch_size = 30

    for i in range(0, len(new_items), batch_size):
        batch = new_items[i:i + batch_size]
        ids = [str(item["id"]) for item in batch]
        details = fetch_details(scraper, ids)

        for item in batch:
            lid = str(item.get("id"))
            classified = details.get(lid, {})
            listing = parse_listing(item, classified)

            text = format_message(listing)
            ok = send(scraper, text, listing["photo"])

            if ok:
                mark_sent(conn, lid, listing["city"], listing["price"] or 0)
                sent_count += 1
                logger.info(f"[{label}] Sent {lid} — {listing['city']} {listing['price']}€")
                time.sleep(0.5)
            else:
                logger.error(f"[{label}] Failed to send {lid}")

        time.sleep(2)

    return sent_count


def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    logger.info("Notifier run started — %d search URL(s)", len(SEARCH_URLS))

    conn = init_db()
    scraper = cloudscraper.create_scraper(
        browser={"browser": "chrome", "platform": "windows", "mobile": False}
    )

    if DEBUG:
        debug_send(scraper, "Notifier démarré")

    searches = []
    for url in SEARCH_URLS:
        params = parse_url(url)
        label, icon = _label_from_params(params)
        searches.append((label, params, icon))
    logger.info("Running %d search(es)", len(searches))

    total_sent = 0
    for label, params, icon in searches:
        url = build_url(params)
        logger.info("Recherche %s : %s", label, url)

        debug_send(scraper, f"Fetching {label}…")
        items = _collect_all_items(scraper, build_criteria(params), label)
        already_seen = sum(1 for i in items if is_sent(conn, str(i.get("id"))))
        new_items = [i for i in items if not is_sent(conn, str(i.get("id")))]
        logger.info(f"[{label}] {len(new_items)} new (unseen) listings")
        debug_send(scraper, f"{label} — {len(items)} trouvées · {already_seen} déjà vues · {len(new_items)} nouvelles")

        if new_items or DEBUG:
            now = datetime.now(ZoneInfo("Europe/Paris")).strftime("%H:%M")
            count_line = (
                f"✅ {len(new_items)} nouvelle{'s' if len(new_items) > 1 else ''} annonce{'s' if len(new_items) > 1 else ''}"
                if new_items else "ℹ️ Aucune nouvelle annonce"
            )
            send(scraper, f'🔍 <b>Recherche {label.lower()}</b> — {now}\n<a href="{url}">{icon} Voir les annonces</a>\n\n{count_line}')
        time.sleep(1)

        sent = _send_new_listings(scraper, conn, new_items, label)
        total_sent += sent
        logger.info(f"[{label}] Done — {sent} new listings sent")
        time.sleep(3)

    logger.info(f"Run complete — {total_sent} total new listings sent")
    debug_send(scraper, f"Run terminé — {total_sent} annonce{'s' if total_sent > 1 else ''} envoyée{'s' if total_sent > 1 else ''} au total")

    _run_state["last_run_at"] = datetime.now(ZoneInfo("Europe/Paris"))
    _run_state["last_run_sent"] = total_sent
    conn.close()


if __name__ == "__main__":
    main()
