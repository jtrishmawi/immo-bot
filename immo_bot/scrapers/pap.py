"""
immo-bot — PAP.fr URL utilities + HTML scraper.

parse_url      : browser URL → params dict
build_url      : params dict → browser URL (round-trip via stored slug)
fetch_listings : GET search pages → list of normalised listing dicts
"""
import json
import re
import time
import logging
from urllib.parse import urlparse

from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

BASE_URL = "https://www.pap.fr/annonce"

_TRANSACTION_RE  = re.compile(r"^(locations?|ventes?)")
_TYPEBIEN_RE     = re.compile(r"(?:^|-)(?P<t>appartement|maison|studio|parking|loft|chambre|terrain|immeuble|local)(?:-|$)")
_LOCATIONS_RE    = re.compile(r"g(\d+)")
_PIECES_RANGE_RE = re.compile(r"du-(\d+)-pieces?-au-(\d+)-pieces?")
_PIECES_RE       = re.compile(r"(\d+)-pieces?")
_PRIX_MAX_RE     = re.compile(r"jusqu-a-(\d+)-euros")
_PRIX_MIN_RE     = re.compile(r"a-partir-de-(\d+)-euros")
_SURFACE_MIN_RE  = re.compile(r"a-partir-de-(\d+)-m2")
_SURFACE_MAX_RE  = re.compile(r"jusqu-a-(\d+)-m2")

# Individual slug tokens that are never part of the city name
_KNOWN_TOKENS = {
    "locations", "location", "ventes", "vente",
    "appartement", "maison", "studio", "parking", "loft", "chambre",
    "terrain", "immeuble", "local",
    "ascenseur", "dernier", "etage", "vide", "meuble", "cave",
    "balcon", "terrasse", "gardien", "digicode", "interphone",
    "calme", "lumineux", "parquet", "porte", "blindee", "duplex",
    "standing", "neuf", "ancien",
    "du", "au",     # room range syntax: "du-3-pieces-au-4-pieces"
}

PAP_HEADERS = {
    "User-Agent":      "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                       "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept":          "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "fr-FR,fr;q=0.9,en-US;q=0.8,en;q=0.7",
    "Referer":         "https://www.pap.fr/",
}


def parse_url(url: str) -> dict:
    """Parse a PAP.fr browser search URL into a params dict."""
    path = urlparse(url).path          # /annonce/locations-appartement-...
    slug = path.split("/annonce/")[-1].rstrip("/")
    params: dict = {"slug": slug}

    m = _TRANSACTION_RE.search(slug)
    if m:
        params["transaction"] = m.group(1)

    m = _TYPEBIEN_RE.search(slug)
    if m:
        params["typebien"] = m.group("t")

    locs = _LOCATIONS_RE.findall(slug)
    if locs:
        params["locations"] = ",".join(locs)

    # City label: text between last known feature and first g-code
    # e.g. "...-vide-paris-75-g439..." → "Paris"
    # e.g. "...-vide-vanves-92170-g43274..." → "Vanves"
    m_g = re.search(r"g\d{4,}", slug)
    if m_g:
        before_g = slug[:m_g.start()].rstrip("-")
        city_tokens: list[str] = []
        for token in before_g.split("-"):
            if token in _KNOWN_TOKENS:
                city_tokens = []        # reset on a known feature/type keyword
            elif re.match(r"^\d+$", token):
                pass                    # skip dept/postal code digits, don't reset
            elif token:
                city_tokens.append(token)
        if city_tokens:
            params["city_label"] = " ".join(city_tokens).title()

    m = _PIECES_RANGE_RE.search(slug)
    if m:
        params["nb_pieces_min"] = m.group(1)
        params["nb_pieces_max"] = m.group(2)
    else:
        m = _PIECES_RE.search(slug)
        if m:
            params["nb_pieces_min"] = m.group(1)

    m = _PRIX_MAX_RE.search(slug)
    if m:
        params["prix_max"] = m.group(1)

    m = _PRIX_MIN_RE.search(slug)
    if m:
        params["prix_min"] = m.group(1)

    m = _SURFACE_MIN_RE.search(slug)
    if m:
        params["surface_min"] = m.group(1)

    m = _SURFACE_MAX_RE.search(slug)
    if m:
        params["surface_max"] = m.group(1)

    return params


def build_url(params: dict) -> str:
    """Reconstruct PAP.fr search URL from a params dict (round-trip from parse_url)."""
    if not params or "slug" not in params:
        raise ValueError("params must contain 'slug' — use parse_url() to create params")
    return f"{BASE_URL}/{params['slug']}"


# ---------------------------------------------------------------------------
# HTTP session — curl-cffi bypasses Cloudflare managed challenge
# ---------------------------------------------------------------------------

_pap_session = None


def _get_session():
    global _pap_session
    if _pap_session is None:
        from curl_cffi import requests as curl_requests
        _pap_session = curl_requests.Session(impersonate="chrome124")
    return _pap_session


# ---------------------------------------------------------------------------
# HTML parsing
#
# PAP.fr is server-rendered PHP (not Next.js/Nuxt).
# Card structure confirmed 2025-06:
#
#   div.search-list-item-alt  ← only those with a child [data-annonce]
#   └─ a.item-title[href][name]
#      ├─ span.item-price         → "1.560 €"
#      ├─ span.h1                 → "Poissy" or "Saint-Leu-La-Forêt (95320)"
#      └─ ul.item-tags li         → ["3 pièces", "2 chambres", "76 m²"]
#
#   h1#list-searchbar-mobile-h1 on page 1 contains the canonical city list,
#   e.g. "Location appartement … Nanterre, Clamart, Meudon, … 3 ou 4 pièces…"
# ---------------------------------------------------------------------------

_H1_SKIP_WORDS = frozenset({
    "Location", "Vente", "Appartement", "Maison", "Studio",
    "Parking", "Loft", "Chambre", "Terrain", "Immeuble",
})


def _extract_valid_cities(h1_text: str) -> set[str]:
    """Parse the canonical city list from PAP's search h1 element.

    The h1 mixes feature keywords (lowercase) and city names (Title Case).
    Cities are the uppercase-starting words/phrases in each comma segment,
    after stripping the trailing criteria (rooms, price, surface).
    """
    text = re.sub(r"\s+\d+\s+(?:ou|à|-)\s+\d+\s+pièces?.*", "", h1_text)
    text = re.sub(r"\s+\d+\s+pièces?.*", "", text)

    cities: set[str] = set()
    for chunk in text.split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        words = chunk.split()
        start_idx = None
        for i, word in enumerate(words):
            w = re.sub(r"\(\d{5}\)", "", word).strip()
            if w and w[0].isupper() and w not in _H1_SKIP_WORDS:
                start_idx = i
                break
        if start_idx is not None:
            city = " ".join(words[start_idx:])
            city = re.sub(r"\s*\(\d{5}\)", "", city).strip()
            if city:
                cities.add(city)
    return cities


def _parse_html(html: str) -> tuple[list[dict], str]:
    soup  = BeautifulSoup(html, "html.parser")

    h1_el   = soup.find(id="list-searchbar-mobile-h1")
    h1_text = h1_el.get_text(strip=True) if h1_el else ""

    cards = [c for c in soup.select("div.search-list-item-alt")
             if c.select_one("[data-annonce]")]

    if not cards:
        logger.warning("pap: no listing cards found — HTML structure may have changed")
        return [], h1_text

    results = []
    for card in cards:
        try:
            a_title = card.select_one("a.item-title")
            lid     = a_title.get("name") if a_title else None
            if not lid:
                a_fav = card.select_one("[data-annonce]")
                lid   = str(json.loads(a_fav["data-annonce"]).get("id", "")) if a_fav else None

            href = a_title["href"] if a_title else ""
            url  = f"https://www.pap.fr{href}" if href.startswith("/") else href

            # Price: "1.560 €" or "1 560\xa0€"
            price_el  = card.select_one("span.item-price")
            price_raw = re.sub(r"[\xa0  .]", "", price_el.get_text()) if price_el else ""
            pm        = re.search(r"(\d+)", price_raw)
            price     = float(pm.group(1)) if pm else None

            # City: "Poissy" or "Saint-Leu-La-Forêt (95320)"
            # PAP injects nearby listings with suffix: "Annecy (74000)à 16km de Paris"
            city_el   = card.select_one("span.h1")
            city_raw  = city_el.get_text(strip=True) if city_el else ""
            is_nearby = bool(re.search(r"\d+\s*km\s+de\s+", city_raw))
            city_raw  = re.sub(r"\s*à\s+\d+.*", "", city_raw)  # strip distance suffix
            zip_m     = re.search(r"\((\d{5})\)", city_raw)
            zip_code  = zip_m.group(1) if zip_m else ""
            city      = re.sub(r"\s*\(\d{5}\)", "", city_raw).strip()

            # Tags: "3 pièces", "76 m²", etc.
            rooms = space = None
            for li in card.select("ul.item-tags li"):
                tag = li.get_text(strip=True)
                m   = re.match(r"(\d+)\s*pi", tag)
                if m:
                    rooms = int(m.group(1))
                    continue
                m = re.match(r"(\d+)\s*m", tag)
                if m:
                    space = int(m.group(1))

            img   = card.select_one("img[src]")
            photo = img["src"] if img else None

            if lid:
                results.append({
                    "id":        str(lid),
                    "title":     "",
                    "price":     price,
                    "city":      city,
                    "zip_code":  zip_code,
                    "rooms":     rooms,
                    "space":     space,
                    "url":       url,
                    "photo":     photo,
                    "is_nearby": is_nearby,
                })
        except Exception as e:
            logger.debug("pap card parse error: %s", e)

    return results, h1_text


# ---------------------------------------------------------------------------
# Client-side criteria filter
# ---------------------------------------------------------------------------

def filter_listings(items: list[dict], params: dict, valid_cities: set[str] | None = None) -> list[dict]:
    """Drop listings that don't match the criteria encoded in params.

    PAP often injects 'similar' listings outside the requested price/surface/
    rooms range or from nearby cities. We re-apply constraints client-side.
    valid_cities: authoritative set from the search h1 element (optional).
    """
    prix_max    = int(params["prix_max"])    if params.get("prix_max")    else None
    prix_min    = int(params["prix_min"])    if params.get("prix_min")    else None
    surface_min = int(params["surface_min"]) if params.get("surface_min") else None
    surface_max = int(params["surface_max"]) if params.get("surface_max") else None
    pieces_min  = int(params["nb_pieces_min"]) if params.get("nb_pieces_min") else None
    pieces_max  = int(params["nb_pieces_max"]) if params.get("nb_pieces_max") else None

    valid_lower = {c.lower() for c in valid_cities} if valid_cities else None

    kept = []
    for item in items:
        price  = item.get("price")
        space  = item.get("space")
        rooms  = item.get("rooms")

        # Drop PAP-injected nearby listings from other cities (tagged "à Xkm de")
        if item.get("is_nearby"):
            logger.debug("pap filter: drop %s nearby listing (%s)", item.get("id"), item.get("city"))
            continue
        if valid_lower and item.get("city") and item["city"].lower() not in valid_lower:
            logger.debug("pap filter: drop %s city %r not in search scope", item.get("id"), item.get("city"))
            continue

        if prix_max    is not None and price  is not None and price  > prix_max:
            logger.debug("pap filter: drop %s price %.0f > %d", item.get("id"), price, prix_max)
            continue
        if prix_min    is not None and price  is not None and price  < prix_min:
            logger.debug("pap filter: drop %s price %.0f < %d", item.get("id"), price, prix_min)
            continue
        if surface_min is not None and space  is not None and space  < surface_min:
            logger.debug("pap filter: drop %s space %d < %d", item.get("id"), space, surface_min)
            continue
        if surface_max is not None and space  is not None and space  > surface_max:
            logger.debug("pap filter: drop %s space %d > %d", item.get("id"), space, surface_max)
            continue
        if pieces_min  is not None and rooms  is not None and rooms  < pieces_min:
            logger.debug("pap filter: drop %s rooms %d < %d", item.get("id"), rooms, pieces_min)
            continue
        if pieces_max  is not None and rooms  is not None and rooms  > pieces_max:
            logger.debug("pap filter: drop %s rooms %d > %d", item.get("id"), rooms, pieces_max)
            continue

        kept.append(item)

    dropped = len(items) - len(kept)
    if dropped:
        logger.info("pap filter: dropped %d out-of-criteria listing(s) (%d kept)", dropped, len(kept))
    return kept


# ---------------------------------------------------------------------------
# Public scraping entry point
# ---------------------------------------------------------------------------

def fetch_listings(scraper, base_url: str, max_pages: int = 3) -> tuple[list[dict], set[str]]:
    """Scrape PAP.fr search pages and return (listings, valid_cities).

    valid_cities is extracted from the h1#list-searchbar-mobile-h1 element on
    page 1 — it contains the authoritative city list for the search.
    Uses curl-cffi (Chrome TLS fingerprint) to bypass Cloudflare.
    Pagination: page 1 = base_url, page N = base_url-N.
    The `scraper` param is kept for API compatibility but is not used.
    """
    session = _get_session()
    results: list[dict] = []
    valid_cities: set[str] = set()

    for page in range(1, max_pages + 1):
        url = base_url if page == 1 else f"{base_url}-{page}"
        try:
            r = session.get(url, headers=PAP_HEADERS, timeout=30)
            r.raise_for_status()
            if "_cf_chl_opt" in r.text:
                logger.error("pap page %d: Cloudflare challenge not bypassed", page)
                break
        except Exception as e:
            logger.error("pap fetch_listings page %d: %s", page, e)
            break

        items, h1_text = _parse_html(r.text)
        logger.info("[PAP] page %d: %d items", page, len(items))

        if page == 1 and h1_text:
            valid_cities = _extract_valid_cities(h1_text)
            logger.debug("pap: %d valid cities from h1", len(valid_cities))

        if not items:
            logger.info("[PAP] page %d: empty — stopping", page)
            break

        results.extend(items)
        time.sleep(2)

    logger.info("[PAP] collected %d listings total", len(results))
    return results, valid_cities
