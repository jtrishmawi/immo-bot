---
name: add-search
description: Add a new search URL to immo-bot. Use when asked to add a new search, a new city, a new property type, or a new PAP/Seloger URL.
---

# Add a New Search URL

immo-bot discovers search URLs from `SEARCH_URL_1`, `SEARCH_URL_2`, … `SEARCH_URL_N` in `.env`.
The list must be **consecutive** — a missing number stops loading.

## Step 1 — Get the URL

Copy a search URL from the browser after configuring the search on Seloger.com or PAP.fr.
It must be a full browser URL (not a short link).

Seloger example:
```
https://www.seloger.com/classified-search?distributionTypes=Rent&estateTypes=Apartment&...
```

PAP example:
```
https://www.pap.fr/annonce/locations-appartement-...-du-3-pieces-...
```

## Step 2 — Add to `.env`

Append to `.env` with the **next sequential number**:
```
SEARCH_URL_N=<paste URL here>
```

Never skip numbers. If the last entry is `SEARCH_URL_6`, the new one must be `SEARCH_URL_7`.

## Step 3 — Add to `docker-compose.yml`

In the `notifier` service `environment:` block, add the matching line:
```yaml
- SEARCH_URL_N=${SEARCH_URL_N:-}
```

This is mandatory — Docker Compose only forwards vars listed there.

## Step 4 — Run `/deploy`

Restart the container so it picks up the new env var.

## Step 5 — Verify

Check logs: `Notifier run started — N search URL(s)` should show the new count.
Run `/search` in Telegram — the new entry should appear in the menu.

## Notes

- **Never hardcode** location codes, price, rooms, or estate type in tracked files — those stay in `.env`.
- Seloger URLs are parsed by `immo_bot/scrapers/seloger.py:parse_url`.
- PAP URLs are parsed by `immo_bot/scrapers/pap.py:parse_url`.
- Labels shown in the `/search` menu are derived automatically from the URL by `_label_from_params` / `_label_from_params_pap` in `core.py`.
