"""
immo-bot — Bien'ici scraper.

URL format:
  https://www.bienici.com/recherche/location/{cities}/{proptype}/{rooms}?prix-max=X&surface-min=Y&ascenseur=oui&non-meuble=oui&dernier-etage=oui

  {cities}   — comma-separated slugs like "paris-75000,courbevoie-92400"
  {proptype} — optional: "appartement", "maisonvilla", "loft", "chateau"
  {rooms}    — optional: "3-pieces-et-plus" or "3-pieces"

API:
  GET https://www.bienici.com/realEstateAds.json?filters={JSON}

Zone IDs resolved from URL slugs via:
  GET https://res.bienici.com/place.json?q={slug}&type=city,...&prefix=no
"""
import json
import logging
import re
import time
import unicodedata
from urllib.parse import parse_qs, urlencode, urlparse

logger = logging.getLogger(__name__)

BASE_URL   = "https://www.bienici.com/recherche/location"
_API_URL   = "https://www.bienici.com/realEstateAds.json"
_PLACE_URL = "https://res.bienici.com/place.json"

_HEADERS = {
    "User-Agent":       "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36",
    "Accept":           "application/json, */*",
    "Referer":          "https://www.bienici.com/",
    "X-Requested-With": "XMLHttpRequest",
}

# URL path keyword → (api_property_types, build_url_slug)
_PROP_MAP = {
    "appartement": (["flat"],               "appartement"),
    "maisonvilla": (["house", "townhouse"], "maisonvilla"),
    "loft":        (["loft"],               "loft"),
    "chateau":     (["castle"],             "chateau"),
}
_PROP_KEYWORDS = set(_PROP_MAP)

# API propertyType value → URL slug for listing URLs
_PROP_TYPE_SLUG = {
    "flat":      "appartement",
    "house":     "maisonvilla",
    "loft":      "loft",
    "castle":    "chateau",
    "townhouse": "maisonvilla",
}

_ROOMS_RE = re.compile(r"^(\d+)-pieces(?:-et-plus)?$")


def _slugify(s: str) -> str:
    """'Paris 17e' → 'paris-17e', 'Issy-les-Moulineaux' → 'issy-les-moulineaux'"""
    nfkd = unicodedata.normalize("NFKD", s.lower())
    ascii_s = nfkd.encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", ascii_s).strip("-")


def parse_url(url: str) -> dict:
    """Extract search parameters from a Bien'ici browser URL.

    URL structure: /recherche/location/{cities}/{proptype?}/{rooms?}?{qs}
    Cities always come first (comma-separated). Property type is optional.
    """
    parsed = urlparse(url)
    parts  = [p for p in parsed.path.split("/") if p]
    try:
        idx  = parts.index("recherche")
        tail = parts[idx + 2:]  # skip "recherche", "location"
    except ValueError:
        tail = parts[2:]

    # First segment is always the cities list
    location_slugs = tail[0].split(",") if tail else []
    tail = tail[1:]  # advance past cities

    # Next optional segment is property type
    prop_types: list = []
    prop_url_slug: str = ""
    if tail and tail[0] in _PROP_KEYWORDS:
        prop_url_slug = tail[0]
        prop_types, _ = _PROP_MAP[tail[0]]
        tail = tail[1:]

    # Next optional segment is rooms filter
    min_rooms = max_rooms = 0
    if tail:
        m = _ROOMS_RE.match(tail[0])
        if m:
            min_rooms = int(m.group(1))
            if "et-plus" not in tail[0]:
                max_rooms = min_rooms

    qs = parse_qs(parsed.query)

    def _int(key: str) -> int:
        try:
            return int(float(qs.get(key, ["0"])[0]))
        except (ValueError, TypeError):
            return 0

    def _flag(key: str) -> bool:
        return qs.get(key, [""])[0].lower() == "oui"

    if not min_rooms:
        min_rooms = _int("nb-pieces-min")
    if not max_rooms:
        max_rooms = _int("nb-pieces-max")

    return {
        "location_slugs": location_slugs,
        "filter_type":    "rent",
        "prop_types":     prop_types,
        "prop_url_slug":  prop_url_slug,
        "min_rooms":      min_rooms,
        "max_rooms":      max_rooms,
        "max_price":      _int("prix-max"),
        "min_price":      _int("prix-min"),
        "min_area":       _int("surface-min"),
        "max_area":       _int("surface-max"),
        "has_elevator":   _flag("ascenseur"),
        "is_unfurnished": _flag("non-meuble"),
        "is_last_floor":  _flag("dernier-etage"),
    }


def build_url(params: dict) -> str:
    """Reconstruct canonical Bien'ici search URL from a params dict."""
    slugs        = ",".join(params.get("location_slugs", []))
    prop_slug    = params.get("prop_url_slug", "")
    min_rooms    = params.get("min_rooms", 0)
    max_rooms    = params.get("max_rooms", 0)

    path_parts = [BASE_URL, slugs]
    if prop_slug:
        path_parts.append(prop_slug)
    if min_rooms:
        if max_rooms and max_rooms == min_rooms:
            path_parts.append(f"{min_rooms}-pieces")
        else:
            path_parts.append(f"{min_rooms}-pieces-et-plus")
    path = "/".join(path_parts)

    qs: dict = {}
    if params.get("max_price"):
        qs["prix-max"] = params["max_price"]
    if params.get("min_price"):
        qs["prix-min"] = params["min_price"]
    if params.get("min_area"):
        qs["surface-min"] = params["min_area"]
    if params.get("max_area"):
        qs["surface-max"] = params["max_area"]
    if max_rooms and max_rooms != min_rooms:
        qs["nb-pieces-max"] = max_rooms
    if params.get("has_elevator"):
        qs["ascenseur"] = "oui"
    if params.get("is_unfurnished"):
        qs["non-meuble"] = "oui"
    if params.get("is_last_floor"):
        qs["dernier-etage"] = "oui"

    return f"{path}?{urlencode(qs)}" if qs else path


def _resolve_zone_ids(scraper, slugs: list) -> list:
    """Resolve city slugs to Bien'ici zone IDs via the place.json API."""
    zone_ids = []
    for slug in slugs:
        try:
            r = scraper.get(
                _PLACE_URL,
                params={"q": slug, "type": "city,delegated-city,department,postalCode,region", "prefix": "no"},
                headers=_HEADERS,
                timeout=10,
            )
            if r.ok:
                data = r.json()
                # API returns a single object (not a list)
                item = data[0] if isinstance(data, list) else data
                if item and item.get("zoneIds"):
                    zone_ids.extend(item["zoneIds"])
                    logger.debug("slug %s → zoneIds %s", slug, item["zoneIds"])
                else:
                    logger.warning("[Bien'ici] No zone ID found for slug: %s", slug)
        except Exception as e:
            logger.warning("[Bien'ici] Zone ID resolution failed for %s: %s", slug, e)
        time.sleep(0.2)
    return zone_ids


def _listing_url(ad: dict) -> str:
    city_slug = _slugify(ad.get("city", ""))
    prop_slug = _PROP_TYPE_SLUG.get(ad.get("propertyType", ""), "appartement")
    rooms     = ad.get("roomsQuantity", 0)
    ad_id     = ad.get("id", "")
    return f"https://www.bienici.com/annonce/location/{city_slug}/{prop_slug}/{rooms}pieces/{ad_id}"


def fetch_listings(scraper, url: str) -> list:
    """Fetch all listings from Bien'ici for the given search URL.

    Resolves zone IDs from URL city slugs, then paginates realEstateAds.json.
    Returns a flat deduplicated list of listing dicts, each enriched with
    '_url' (canonical listing page) and '_photo' (first photo CDN URL).
    """
    params   = parse_url(url)
    slugs    = params.get("location_slugs", [])
    zone_ids = _resolve_zone_ids(scraper, slugs)
    if not zone_ids:
        logger.error("[Bien'ici] No zone IDs resolved for slugs %s — aborting", slugs)
        return []

    prop_types = params.get("prop_types") or ["flat", "house", "loft", "castle", "townhouse"]

    filters: dict = {
        "filterType":     "rent",
        "propertyType":   prop_types,
        "onTheMarket":    [True],
        "sortBy":         "relevance",
        "sortOrder":      "desc",
        "zoneIdsByTypes": {"zoneIds": zone_ids},
    }
    if params.get("max_price"):
        filters["maxPrice"]      = params["max_price"]
    if params.get("min_price"):
        filters["minPrice"]      = params["min_price"]
    if params.get("min_rooms"):
        filters["minRooms"]      = params["min_rooms"]
    if params.get("max_rooms"):
        filters["maxRooms"]      = params["max_rooms"]
    if params.get("min_area"):
        filters["minArea"]       = params["min_area"]
    if params.get("max_area"):
        filters["maxArea"]       = params["max_area"]
    if params.get("has_elevator"):
        filters["hasElevator"]   = True
    if params.get("is_unfurnished"):
        filters["isNotFurnished"] = True
    if params.get("is_last_floor"):
        filters["isOnLastFloor"] = True

    all_ads:  list = []
    seen_ids: set  = set()
    page_size = 24

    for page in range(1, 4):
        filters["size"] = page_size
        filters["from"] = (page - 1) * page_size
        filters["page"] = page
        try:
            r = scraper.get(
                _API_URL,
                params={"filters": json.dumps(filters, separators=(",", ":")), "extensionType": "extendedIfNoResult"},
                headers=_HEADERS,
                timeout=20,
            )
            r.raise_for_status()
            data = r.json()
        except Exception as e:
            logger.error("[Bien'ici] Page %d fetch failed: %s", page, e)
            break

        ads   = data.get("realEstateAds", [])
        total = data.get("total", 0)
        logger.info("[Bien'ici] Page %d: %d ads (total: %d)", page, len(ads), total)

        for ad in ads:
            aid = ad.get("id")
            if aid and aid not in seen_ids:
                seen_ids.add(aid)
                photos       = ad.get("photos", [])
                ad["_photo"] = photos[0].get("url") if photos else None
                ad["_url"]   = _listing_url(ad)
                all_ads.append(ad)

        if not ads or len(all_ads) >= total:
            break
        time.sleep(1)

    logger.info("[Bien'ici] Collected %d listings", len(all_ads))
    return all_ads


def filter_listings(items: list, params: dict) -> list:
    """Post-filter Bien'ici listings. Currently a passthrough — zone IDs ensure
    the API already returns only requested cities and criteria."""
    return items
