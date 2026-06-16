---
name: add-scraper
description: Add a new real estate scraping source to immo-bot (e.g. LeBonCoin, Bien'ici, Logic-Immo). Use when asked to support a new site.
---

# Add a New Real Estate Scraper

## Architecture rule

Each site gets its own module in `immo_bot/scrapers/<site>.py`.
`core.py` routes by domain using `_detect_site(url)`.

## Step 1 — Create `immo_bot/scrapers/<site>.py`

The module must expose:

```python
BASE_URL: str           # canonical search URL prefix
parse_url(url: str) -> dict   # extract search params from a browser URL
build_url(params: dict) -> str  # reconstruct canonical URL from params
fetch_listings(url: str) -> list[dict]  # scrape and return listings
```

Each listing dict must contain at minimum:
```python
{
    "id": str,        # unique listing ID for dedup
    "title": str,     # display title
    "price": str,     # e.g. "1 500 €/mois"
    "city": str,
    "url": str,       # direct link to listing
    "photo_url": str | None,
}
```

Reference implementations:
- `immo_bot/scrapers/seloger.py` — JSON API via `cloudscraper`
- `immo_bot/scrapers/pap.py` — HTML scraping via `curl-cffi` + BeautifulSoup

### Bypassing bot protection

| Protection | Solution |
|---|---|
| Cloudflare (JS challenge) | `curl-cffi` with `impersonate="chrome124"` |
| Cloudflare (basic) | `cloudscraper.create_scraper()` |
| None / simple | `requests` |

## Step 2 — Register in `core.py`

1. Import at top:
   ```python
   from .scrapers import <site> as _<site>
   ```

2. Add domain detection in `_detect_site()`:
   ```python
   def _detect_site(url: str) -> str:
       if "pap.fr" in url: return "pap"
       if "<site>.fr" in url: return "<site>"
       return "seloger"
   ```

3. Add a label helper `_label_from_params_<site>(params) -> tuple[str, str]`:
   Returns `(label_str, emoji_icon)`, e.g. `("Appartements (Paris)", "🏢")`.

4. Wire into `send_search_menu()` — add an `elif` branch for the new site.

5. Wire into `run_on_demand_search()` — add fetch call for the new site.

6. Wire into `main()` loop — add fetch call inside `for u in SEARCH_URLS`.

## Step 3 — Format the listing message

Add a `format_message_<site>(listing: dict) -> str` function that returns Telegram HTML.
Match the style of the existing `format_message` (Seloger, `core.py:227`) and `format_message_pap` (PAP, `core.py:257`).

Also add a `_send_new_listings_<site>` helper following the pattern of `_send_new_listings_pap` (`core.py:276`) — it iterates items, calls `format_message_<site>`, dispatches via `broadcast` or `_tg.send`, then calls `mark_sent`.

## Step 4 — Add tests

In `tests/test_core.py` (or a new `tests/test_<site>.py`):
- `test_<site>_parse_url_extracts_*` — check all key fields
- `test_<site>_parse_url_roundtrip` — `build_url(parse_url(url)) == url`
- `test_<site>_build_url_requires_slug_or_key` — invalid input raises

## Step 5 — Update `.env.example`

Add a commented example URL for the new site so new users know the format.

## Step 6 — Run tests

```bash
.venv/Scripts/pytest tests/ -v
```
