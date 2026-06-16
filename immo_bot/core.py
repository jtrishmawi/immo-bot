"""
immo-bot — Core: scraping, formatting, broadcasting, search orchestration.

Env vars (at least one platform required):
  TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID  — see platforms/telegram.py
  WHATSAPP_SERVICE_URL + WHATSAPP_PHONE  — WHATSAPP_TO defaults to +WHATSAPP_PHONE if unset
  SEARCH_URL_1, SEARCH_URL_2, ...        — Seloger search URLs
  DB_PATH                                — optional, default /app/data/sent_listings.db
  DEBUG                                  — optional, verbose Telegram messages
"""
import os
import re
import sys
import time
import logging
from datetime import datetime
from typing import Optional
from zoneinfo import ZoneInfo

import cloudscraper

from .platforms import telegram as _tg
from .platforms import whatsapp as _wa
from .db import init_db, is_sent, mark_sent
from .scrapers.seloger import build_url, parse_url
from .scrapers import pap as _pap

logger = logging.getLogger(__name__)

_WHATSAPP_ENABLED = bool(
    os.getenv("WHATSAPP_SERVICE_URL") and
    (os.getenv("WHATSAPP_TO") or os.getenv("WHATSAPP_PHONE"))
)

if not _tg._TELEGRAM_ENABLED and not _WHATSAPP_ENABLED:
    sys.exit(
        "No notification platform configured. "
        "Set TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID, "
        "or WHATSAPP_SERVICE_URL + WHATSAPP_PHONE (or WHATSAPP_TO)."
    )

WHATSAPP_SERVICE_URL = os.getenv("WHATSAPP_SERVICE_URL", "").strip() or None
_wa_to_raw           = os.getenv("WHATSAPP_TO", "").strip()
_wa_phone_raw        = os.getenv("WHATSAPP_PHONE", "").strip().lstrip("+")
WHATSAPP_TO          = _wa_to_raw or (f"+{_wa_phone_raw}" if _wa_phone_raw else None)
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


def _detect_site(url: str) -> str:
    return "pap" if "pap.fr" in url else "seloger"


def _label_from_params_pap(params: dict) -> tuple[str, str]:
    t    = params.get("typebien", "").lower()
    city = params.get("city_label", "PAP")
    icon = "🏡" if "maison" in t else "🏠"
    kind = "Maisons" if "maison" in t else "Appartements"
    return (f"{kind} ({city})", icon)

SELOGER_SEARCH_API     = "https://www.seloger.com/serp-bff/search"
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
# Seloger API — criteria + fetch
# ---------------------------------------------------------------------------

def _csv_to_list(value: str) -> list:
    return [v.strip() for v in value.split(",") if v.strip()]


def build_criteria(params: dict) -> dict:
    """Build serp-bff criteria payload from a params dict (from parse_url)."""
    p = params
    place_ids = [c.strip() for c in p["locations"].split(",") if c.strip()]
    criteria = {
        "distributionTypes": _csv_to_list(p["distributionTypes"]),
        "estateTypes":        _csv_to_list(p["estateTypes"]),
        "furnished":          _csv_to_list(p["furnished"]),
        "numberOfRoomsMin":   int(p["numberOfRoomsMin"]),
        "priceMax":           int(p["priceMax"]),
        "spaceMin":           int(p["spaceMin"]),
        "projectTypes":       ["Flatsharing", "Stock"],
        "location":           {"placeIds": place_ids},
    }
    if p.get("featuresIncluded"):
        criteria["featuresIncluded"] = _csv_to_list(p["featuresIncluded"])
    if p.get("locationsInBuildingIncluded"):
        criteria["locationsInBuildingIncluded"] = _csv_to_list(p["locationsInBuildingIncluded"])
    return criteria


def fetch_page(scraper, criteria: dict, page: int, size: int = 50) -> dict:
    payload = {"criteria": criteria, "paging": {"page": page, "size": size, "order": "Default"}}
    try:
        r = scraper.post(SELOGER_SEARCH_API, json=payload, headers=HEADERS, timeout=30)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        logger.error("fetch_page %d: %s", page, e)
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
        logger.error("fetch_details: %s", e)
    return {}


def _collect_all_items(scraper, criteria: dict, label: str) -> list:
    all_items = []
    for page in range(1, 4):
        data = fetch_page(scraper, criteria, page)
        items = data.get("items") or data.get("classifieds", [])
        if not items:
            break
        all_items.extend(items)
        total = data.get("resultsCount") or data.get("totalCount", 0)
        logger.info("[%s] Page %d: %d items (total: %d)", label, page, len(items), total)
        if len(all_items) >= total:
            break
        time.sleep(2)
    logger.info("[%s] Collected %d listings", label, len(all_items))
    return all_items


# ---------------------------------------------------------------------------
# Listing parsing + formatting
# ---------------------------------------------------------------------------

def _clean_price(raw) -> Optional[float]:
    if raw is None:
        return None
    if isinstance(raw, (int, float)):
        return float(raw)
    if isinstance(raw, str):
        m = re.search(r"\d+", raw.replace(" ", "").replace("\xa0", "").replace(" ", ""))
        return float(m.group()) if m else None
    return None


def _first_photo(classified: dict) -> Optional[str]:
    try:
        images = (classified.get("gallery") or {}).get("images", [])
        if images:
            img = images[0]
            return img.get("url") or img.get("src") or img.get("path")
    except Exception:
        pass
    return None


def _extract_price(item: dict, classified: dict) -> Optional[float]:
    hard = classified.get("hardFacts", {}) or {}
    price_block = hard.get("price", {}) if isinstance(hard, dict) else {}

    if DEBUG:
        logger.debug("price_block keys: %s | item keys: %s", list(price_block), list(item))

    candidates = [
        price_block.get("display"),
        price_block.get("pricePerMonth"),
        price_block.get("monthly"),
        price_block.get("amount"),
        (item.get("listing") or {}).get("price", {}).get("value") if isinstance(item.get("listing"), dict) else None,
        item.get("price"),
        price_block.get("value"),
    ]
    for raw in candidates:
        v = _clean_price(raw)
        if v is not None and v >= 100:
            return v
    # nothing plausible — return raw value anyway so the message shows something
    return _clean_price(price_block.get("value"))


def parse_listing(item: dict, classified: dict) -> dict:
    loc      = classified.get("location", {}).get("address", {}) or {}
    raw      = classified.get("rawData", {}) or {}
    hard     = classified.get("hardFacts", {}) or {}
    provider = classified.get("provider", {}) or {}
    return {
        "id":           str(item.get("id")),
        "price":        _extract_price(item, classified),
        "estate_type":  raw.get("propertyType", ""),
        "city":         loc.get("city", ""),
        "zip_code":     loc.get("zipCode", ""),
        "address":      loc.get("address", ""),
        "bedrooms":     raw.get("nbbedroom"),
        "rooms":        raw.get("nbroom"),
        "space":        (raw.get("surface") or {}).get("main"),
        "energy_class": classified.get("energyClass", ""),
        "agency":       (provider.get("intermediaryCard") or {}).get("title", ""),
        "is_private":   provider.get("isPrivateOwner", False),
        "url":          classified.get("url", ""),
        "photo":        _first_photo(classified),
    }


def format_message(l: dict) -> str:
    kind  = "Maison" if "house" in l["estate_type"].lower() else "Appartement"
    parts = [kind]
    if l["bedrooms"]:
        parts.append(f"{l['bedrooms']} ch")
    elif l["rooms"]:
        parts.append(f"{l['rooms']} pièces")
    if l["space"]:
        parts.append(f"{int(l['space'])}m²")
    title = " · ".join(parts)

    location  = l["city"]
    if l["zip_code"]:
        location += f" ({l['zip_code']})"
    price_str = f"{int(l['price']):,} €/mois".replace(",", " ") if l["price"] else "prix N/A"

    lines = [f"🏠 <b>{title}</b>", f"📍 {location}", f"💶 <b>{price_str}</b>"]
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


def format_message_pap(l: dict) -> str:
    parts = ["Appartement"]
    if l.get("rooms"):
        parts.append(f"{l['rooms']} pièces")
    if l.get("space"):
        parts.append(f"{int(l['space'])}m²")
    title = " · ".join(parts)

    location  = l.get("city", "")
    if l.get("zip_code"):
        location += f" ({l['zip_code']})"
    price_str = f"{int(l['price']):,} €/mois".replace(",", " ") if l.get("price") else "prix N/A"

    lines = [f"🏠 <b>{title}</b>  <i>[PAP]</i>", f"📍 {location}", f"💶 <b>{price_str}</b>"]
    if l.get("url"):
        lines.append(f'\n<a href="{l["url"]}">🔗 Voir l\'annonce</a>')
    return "\n".join(lines)


def _send_new_listings_pap(scraper, conn, items: list, label: str, broadcast_mode: bool = False) -> int:
    sent_count = 0
    for item in items:
        lid  = str(item.get("id"))
        text = format_message_pap(item)
        send_fn = broadcast if broadcast_mode else _tg.send
        ok   = send_fn(scraper, text, item.get("photo"))
        if ok:
            mark_sent(conn, lid, item.get("city", ""), item.get("price") or 0)
            sent_count += 1
            logger.info("[%s] Sent %s — %s %s€", label, lid, item.get("city"), item.get("price"))
            time.sleep(0.5)
        else:
            logger.error("[%s] Failed to send %s", label, lid)
    return sent_count


# ---------------------------------------------------------------------------
# Platform broadcasting
# ---------------------------------------------------------------------------

def broadcast(scraper, text: str, photo_url: str = None) -> bool:
    """Send to all configured platforms. Used for scheduled runs."""
    ok = False
    if _tg._TELEGRAM_ENABLED:
        ok = _tg.send(scraper, text, photo_url) or ok
    if _WHATSAPP_ENABLED:
        _wa.send(text, WHATSAPP_SERVICE_URL, WHATSAPP_TO, media_url=photo_url)
        ok = True
    return ok


def debug_send(scraper, text: str):
    if DEBUG:
        _tg.send(scraper, f"🐛 <b>[DEBUG]</b> {text}")


# ---------------------------------------------------------------------------
# Command handlers (called as callbacks from platforms.telegram.poll_commands)
# ---------------------------------------------------------------------------

def _label_from_params(params: dict) -> tuple[str, str]:
    estate = params.get("estateTypes", "Listings").capitalize()
    return _ESTATE_LABELS.get(estate, (estate, "🔍"))


def send_health(scraper) -> None:
    started = _run_state["started_at"].strftime("%d/%m %H:%M") if _run_state["started_at"] else "?"
    last    = _run_state["last_run_at"].strftime("%d/%m %H:%M") if _run_state["last_run_at"] else "jamais"
    sent    = _run_state["last_run_sent"]
    _tg.send(scraper, f"✅ <b>immo-bot actif</b> depuis {started}\nDernier run : {last} — {sent} annonce(s)")


def send_search_menu(scraper) -> None:
    lines = ["🔍 <b>Quelle recherche lancer ?</b>\n"]
    for i, url in enumerate(SEARCH_URLS, 1):
        if _detect_site(url) == "pap":
            label, icon = _label_from_params_pap(_pap.parse_url(url))
            source = "PAP"
        else:
            label, icon = _label_from_params(parse_url(url))
            source = "Seloger"
        lines.append(f'{i}. {icon} {label} — <a href="{url}">{source}</a>')
    lines.append("\nRépondez avec le numéro.")
    _tg.send(scraper, "\n".join(lines))


def run_on_demand_search(scraper, chat_id: str, text: str) -> None:
    try:
        idx = int(text.strip()) - 1
    except ValueError:
        return
    if idx < 0 or idx >= len(SEARCH_URLS):
        _tg.send(scraper, f"Numéro invalide (1–{len(SEARCH_URLS)}).")
        return

    url  = SEARCH_URLS[idx]
    site = _detect_site(url)

    if site == "pap":
        params     = _pap.parse_url(url)
        label, icon = _label_from_params_pap(params)
        search_url  = _pap.build_url(params)
    else:
        params     = parse_url(url)
        label, icon = _label_from_params(params)
        search_url  = build_url(params)

    _tg.send(scraper, f"🔄 Recherche {icon} <b>{label}</b> en cours…")

    conn = init_db()
    try:
        if site == "pap":
            raw_items, valid_cities = _pap.fetch_listings(scraper, search_url)
            items     = _pap.filter_listings(raw_items, params, valid_cities=valid_cities)
            new_items = [i for i in items if not is_sent(conn, str(i.get("id")))]
            already   = len(items) - len(new_items)
            sent      = _send_new_listings_pap(scraper, conn, new_items, label, broadcast_mode=False)
        else:
            items     = _collect_all_items(scraper, build_criteria(params), label)
            new_items = [i for i in items if not is_sent(conn, str(i.get("id")))]
            already   = len(items) - len(new_items)
            sent      = _send_new_listings(scraper, conn, new_items, label, broadcast_mode=False)

        if not new_items:
            _tg.send(scraper, f"ℹ️ Aucune nouvelle annonce pour <b>{label}</b> ({already} déjà vues).")
        _run_state["last_run_at"]   = datetime.now(ZoneInfo("Europe/Paris"))
        _run_state["last_run_sent"] = sent
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Listing dispatch
# ---------------------------------------------------------------------------

def _send_new_listings(scraper, conn, new_items: list, label: str, broadcast_mode: bool = False) -> int:
    """Send filtered listings. broadcast_mode=True sends to all platforms (scheduled only)."""
    sent_count = 0
    for i in range(0, len(new_items), 30):
        batch   = new_items[i:i + 30]
        details = fetch_details(scraper, [str(item["id"]) for item in batch])

        for item in batch:
            lid     = str(item.get("id"))
            listing = parse_listing(item, details.get(lid, {}))
            text    = format_message(listing)
            send_fn = broadcast if broadcast_mode else _tg.send
            ok      = send_fn(scraper, text, listing["photo"])

            if ok:
                mark_sent(conn, lid, listing["city"], listing["price"] or 0)
                sent_count += 1
                logger.info("[%s] Sent %s — %s %s€", label, lid, listing["city"], listing["price"])
                time.sleep(0.5)
            else:
                logger.error("[%s] Failed to send %s", label, lid)

        time.sleep(2)
    return sent_count


# ---------------------------------------------------------------------------
# Main scheduled run
# ---------------------------------------------------------------------------

def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    logger.info("Notifier run started — %d search URL(s)", len(SEARCH_URLS))

    conn    = init_db()
    scraper = cloudscraper.create_scraper(
        browser={"browser": "chrome", "platform": "windows", "mobile": False}
    )

    if DEBUG:
        debug_send(scraper, "Notifier démarré")

    searches = []
    for u in SEARCH_URLS:
        site = _detect_site(u)
        if site == "pap":
            p = _pap.parse_url(u)
            label, icon = _label_from_params_pap(p)
        else:
            p = parse_url(u)
            label, icon = _label_from_params(p)
        searches.append((label, icon, p, site))
    logger.info("Running %d search(es)", len(searches))

    total_sent = 0
    for label, icon, params, site in searches:
        if site == "pap":
            search_url = _pap.build_url(params)
            logger.info("Recherche PAP %s : %s", label, search_url)
            debug_send(scraper, f"Fetching {label}…")

            raw_items, valid_cities = _pap.fetch_listings(scraper, search_url)
            items     = _pap.filter_listings(raw_items, params, valid_cities=valid_cities)
            new_items = [i for i in items if not is_sent(conn, str(i.get("id")))]
            already   = len(items) - len(new_items)
            logger.info("[%s] %d new, %d already seen", label, len(new_items), already)
            debug_send(scraper, f"{label} — {len(items)} trouvées · {already} déjà vues · {len(new_items)} nouvelles")

            if new_items or DEBUG:
                now        = datetime.now(ZoneInfo("Europe/Paris")).strftime("%H:%M")
                count_line = (
                    f"✅ {len(new_items)} nouvelle{'s' if len(new_items) > 1 else ''} annonce{'s' if len(new_items) > 1 else ''}"
                    if new_items else "ℹ️ Aucune nouvelle annonce"
                )
                broadcast(scraper, f'🔍 <b>Recherche {label.lower()}</b> — {now}\n<a href="{search_url}">{icon} Voir les annonces</a>\n\n{count_line}')
            time.sleep(1)

            sent = _send_new_listings_pap(scraper, conn, new_items, label, broadcast_mode=True)
        else:
            search_url = build_url(params)
            logger.info("Recherche Seloger %s : %s", label, search_url)
            debug_send(scraper, f"Fetching {label}…")

            items     = _collect_all_items(scraper, build_criteria(params), label)
            new_items = [i for i in items if not is_sent(conn, str(i.get("id")))]
            already   = len(items) - len(new_items)
            logger.info("[%s] %d new, %d already seen", label, len(new_items), already)
            debug_send(scraper, f"{label} — {len(items)} trouvées · {already} déjà vues · {len(new_items)} nouvelles")

            if new_items or DEBUG:
                now        = datetime.now(ZoneInfo("Europe/Paris")).strftime("%H:%M")
                count_line = (
                    f"✅ {len(new_items)} nouvelle{'s' if len(new_items) > 1 else ''} annonce{'s' if len(new_items) > 1 else ''}"
                    if new_items else "ℹ️ Aucune nouvelle annonce"
                )
                broadcast(scraper, f'🔍 <b>Recherche {label.lower()}</b> — {now}\n<a href="{search_url}">{icon} Voir les annonces</a>\n\n{count_line}')
            time.sleep(1)

            sent = _send_new_listings(scraper, conn, new_items, label, broadcast_mode=True)

        total_sent += sent
        logger.info("[%s] Done — %d new listings sent", label, sent)
        time.sleep(3)

    logger.info("Run complete — %d total new listings sent", total_sent)
    debug_send(scraper, f"Run terminé — {total_sent} annonce{'s' if total_sent > 1 else ''} envoyée{'s' if total_sent > 1 else ''} au total")

    _run_state["last_run_at"]   = datetime.now(ZoneInfo("Europe/Paris"))
    _run_state["last_run_sent"] = total_sent
    conn.close()


if __name__ == "__main__":
    main()
