# immo-bot — AI assistant instructions

## Project identity

**immo-bot** is a self-hosted Python Telegram bot that scrapes French real estate listings (Seloger.com today, extensible to other sites) and sends new ones to a Telegram chat. It runs hourly via APScheduler inside Docker.

Target audience: French-speaking users who want real-time apartment/house alerts without paying for a premium subscription.

---

## Architecture

```
scheduler.py   ← Docker entrypoint. Starts the hourly cron job and the /health
                 polling thread (daemon). Sets _run_state["started_at"].

notifier.py    ← All business logic:
                   • _load_search_urls()   scan SEARCH_URL_1, SEARCH_URL_2, …
                   • build_criteria()      URL query dict → Seloger API payload
                   • fetch_page()          POST serp-bff/search (paginated)
                   • fetch_details()       GET classifiedList/{ids}
                   • _send_new_listings()  dedup + Telegram send
                   • poll_commands()       long-poll Telegram for /health
                   • send_health()         reply to /health

seloger.py     ← Seloger URL adapter (thin, no business logic):
                   • parse_url(url)   browser URL → query dict
                   • build_url(query) query dict  → browser URL
```

Future site adapters follow the same pattern: `leboncoin.py` with its own `parse_url` / `build_url`.

---

## Core design principle: the URL is the config

Users never edit Python. They configure searches by pasting Seloger.com browser URLs into `.env` as `SEARCH_URL_1`, `SEARCH_URL_2`, etc. The bot parses these URLs at startup via `parse_url()`.

**Never add hardcoded search parameters** (price, rooms, location codes, estate type) to any tracked file. Those are personal and live only in the user's `.env`.

---

## Key conventions

### Environment variables
| Var | Required | Purpose |
|-----|----------|---------|
| `TELEGRAM_BOT_TOKEN` | yes | Telegram bot token from BotFather |
| `TELEGRAM_CHAT_ID` | yes | Target chat/user ID |
| `SEARCH_URL_1` … `SEARCH_URL_N` | yes (≥1) | Seloger search URLs — bot crashes cleanly if none set |
| `DB_PATH` | no | SQLite path (default `/app/data/sent_listings.db`) |
| `DEBUG` | no | Verbose Telegram messages when `true` |

### Search URL loading
`_load_search_urls()` scans `SEARCH_URL_1`, `SEARCH_URL_2`, … and stops at the first missing index. If the result is empty, the app exits immediately with a clear message.

### Label/icon inference
`_label_from_params()` reads `estateTypes` from the parsed URL query to decide the Telegram label and emoji. Extend `_ESTATE_LABELS` in `notifier.py` for new property types.

### Quiet by default
The per-search summary message is only sent to Telegram when there are new listings **or** `DEBUG=true`. Do not change this — users don't want hourly "nothing found" pings.

### Bot commands (`poll_commands`)
`poll_commands()` runs in a daemon thread started by `scheduler.py`. It long-polls `getUpdates` and handles:

| Command | Behaviour |
|---------|-----------|
| `/health` | Reply with uptime + last run info from `_run_state` |
| `/search` | Send a numbered menu of configured searches; next reply triggers that search immediately |

State between `/search` and the user's numeric reply is tracked in `_pending_search` (dict keyed by `chat_id`). The on-demand search runs synchronously in the poll thread (acceptable for a personal bot) and updates `_run_state` like a scheduled run.

---

## What lives where

**In the repo (public):**
- `notifier.py`, `scheduler.py`, `seloger.py` — core logic
- `requirements.txt`, `Dockerfile`, `docker-compose.yml` — packaging
- `.env.example`, `Makefile`, `tasks.ps1` — user tooling
- `test_params.py` — test suite

**Outside the repo (personal, never commit):**
- `.env` — real credentials and search URLs
- `seloger_location_codes.py` — location code reference (at `../`)
- `discover_location_codes.py` — tool for finding new commune codes
- `location_codes.json` — raw autocomplete API output

---

## Testing

```bash
make setup          # first time
.venv/Scripts/pytest test_params.py -v
```

Tests use inline fixture params (`_PARAMS` dict in `test_params.py`) — never import personal params from outside the repo. The test file sets required env vars at the top before any imports.

---

## Adding a new real estate site

1. Create `<site>.py` with `BASE_URL`, `parse_url(url) -> dict`, `build_url(query) -> str`
2. The domain in `SEARCH_URL_n` will identify which adapter to use (detection logic TBD in `notifier.py`)
3. Add a `build_criteria_<site>()` function in `notifier.py` for the site's API payload format

## Adding a new notification platform

1. Create a `<platform>_notifier.py` with a `send(text, photo=None)` function
2. Add platform-specific env vars (e.g. `DISCORD_WEBHOOK_URL`)
3. Route in `notifier.py` based on which platform vars are set

---

## Seloger API notes

- **Search**: `POST https://www.seloger.com/serp-bff/search` with JSON criteria payload
- **Details**: `GET https://www.seloger.com/classifiedList/{id1},{id2},...`
- **Location discovery**: `POST https://www.seloger.com/search-mfe-bff/autocomplete`
- Uses `cloudscraper` (not plain `requests`) to bypass Cloudflare on the search endpoint
- Location codes format: `AD08FR{id}` (commune level) or `NBH1FR{id}` (neighbourhood level)
- Paginated: fetches up to 3 pages of 50 results each
