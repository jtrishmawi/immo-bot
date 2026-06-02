# immo-bot — AI assistant instructions

## Project identity

**immo-bot** is a self-hosted Python bot that scrapes French real estate listings (Seloger.com today, extensible to other sites) and sends new ones to Telegram and/or WhatsApp. It runs hourly via APScheduler inside Docker.

Target audience: French-speaking users who want real-time apartment/house alerts without paying for a premium subscription.

---

## Module structure

```
scheduler.py          Docker entrypoint. Starts the hourly cron and the
                      Telegram poll thread (if Telegram is enabled). Wires
                      notifier callbacks into telegram.poll_commands().

notifier.py           Core logic. Scraping, parsing, formatting, dedup,
                      broadcast(), and command handlers (send_health,
                      send_search_menu, run_on_demand_search). Imports from
                      db, telegram, whatsapp, seloger.

telegram.py           Telegram adapter. send(), download_photo(),
                      poll_commands(callbacks). No circular imports —
                      poll_commands is callback-based.

whatsapp.py           WhatsApp adapter. to_whatsapp() (HTML→WA markdown),
                      send(), health(). Calls the Baileys sidecar via HTTP.

seloger.py            Seloger URL adapter. parse_url() / build_url(). No deps.

db.py                 SQLite dedup. init_db(), is_sent(), mark_sent(). No deps.

baileys-service/      Node.js 24 sidecar. Runs Baileys (WhatsApp protocol),
                      exposes POST /send and GET /health on port 3000.
```

### Dependency graph (no circular imports)

```
db.py, seloger.py, whatsapp.py   → (no internal deps)
telegram.py                       → (no internal deps)
notifier.py                       → db, telegram, whatsapp, seloger
scheduler.py                      → notifier, telegram
```

---

## Core design principle: the URL is the config

Users never edit Python. They configure searches by pasting Seloger.com browser URLs into `.env` as `SEARCH_URL_1`, `SEARCH_URL_2`, etc. The bot parses these URLs at startup via `parse_url()`.

**Never add hardcoded search parameters** (price, rooms, location codes, estate type) to any tracked file. Those are personal and live only in the user's `.env`.

---

## Environment variables

| Var | Required | Purpose |
|-----|----------|---------|
| `TELEGRAM_BOT_TOKEN` | ≥1 platform | Telegram bot token from BotFather |
| `TELEGRAM_CHAT_ID` | ≥1 platform | Target Telegram chat/user ID |
| `WHATSAPP_PHONE` | ≥1 platform | Phone number to pair via Baileys (no `+`) |
| `WHATSAPP_TO` | no | Recipient number with `+` (defaults to `+{WHATSAPP_PHONE}`) |
| `WHATSAPP_SERVICE_URL` | if WA enabled | URL of Baileys sidecar (e.g. `http://whatsapp:3000`) |
| `SEARCH_URL_1` … `N` | yes (≥1) | Seloger search URLs — exits cleanly if none set |
| `DB_PATH` | no | SQLite path (default `/app/data/sent_listings.db`) |
| `DEBUG` | no | Verbose Telegram messages when `true` |

### Notification modes

| Mode | Env vars set | Behaviour |
|------|-------------|-----------|
| Telegram-only | `TELEGRAM_*` | Notifications + interactive commands |
| WhatsApp-only | `WHATSAPP_*` | Notifications only (no Telegram poll thread) |
| Both | all | Notifications on both; commands Telegram-only |
| Neither | — | `sys.exit` with clear error |

---

## Key conventions

### broadcast() vs telegram.send()

- **`broadcast(scraper, text, photo_url)`** — sends to all configured platforms. Used only for scheduled runs and startup messages.
- **`telegram.send(scraper, text, photo_url)`** — Telegram only. Used for command responses (`/health`, `/search` results) because those belong to the Telegram conversation that triggered them.

### Quiet by default

The per-search summary message is only sent when there are new listings **or** `DEBUG=true`. Do not change this — users don't want hourly "nothing found" pings.

### poll_commands — callback pattern

`telegram.poll_commands(scraper, on_health, on_search, on_search_select)` is intentionally callback-based. It knows nothing about search logic. `scheduler.py` wires in lambdas pointing to `notifier.*` functions. This avoids circular imports.

### WhatsApp pairing

On first run without a session, the Baileys sidecar calls `requestPairingCode(WHATSAPP_PHONE)` and logs the 8-digit code to stdout. User runs `docker compose logs -f whatsapp` to get it. Session is persisted in the shared `notifier_data` volume.

---

## What lives where

**In the repo (public):**
- `notifier.py`, `scheduler.py`, `telegram.py`, `whatsapp.py`, `seloger.py`, `db.py`
- `baileys-service/` (Node.js WhatsApp sidecar)
- `requirements.txt`, `Dockerfile`, `docker-compose.yml`
- `.env.example`, `Makefile`, `tasks.ps1`
- `test_params.py`

**Outside the repo (personal, never commit):**
- `.env` — real credentials and search URLs
- `seloger_location_codes.py` — location code reference (at `../`)
- `discover_location_codes.py` — tool for finding new commune codes

---

## Testing

```bash
make setup
.venv/Scripts/pytest test_params.py -v
```

Tests define inline fixture params and set required env vars at the top before any imports.

---

## Adding a new real estate site

1. Create `<site>.py` with `BASE_URL`, `parse_url(url) -> dict`, `build_url(query) -> str`
2. Add a `build_criteria_<site>(params)` in `notifier.py` for that site's API payload
3. Route by domain in `notifier.py`'s fetch logic

## Adding a new notification platform

1. Create `<platform>.py` with `send(text, ...) -> bool` and any HTML conversion needed
2. Add platform env vars (`DISCORD_WEBHOOK_URL`, etc.)
3. Add `_DISCORD_ENABLED` check in `notifier.py`
4. Wire into `broadcast()`

---

## Seloger API notes

- **Search**: `POST https://www.seloger.com/serp-bff/search` — JSON criteria payload
- **Details**: `GET https://www.seloger.com/classifiedList/{id1},{id2},...`
- Uses `cloudscraper` (not `requests`) to bypass Cloudflare on the search endpoint
- Paginated: up to 3 pages of 50 results each
- Location codes: `AD08FR{id}` (commune) or `NBH1FR{id}` (neighbourhood)
