# immo-bot

A self-hosted bot that watches French real estate listings and sends new ones straight to your phone — before anyone else sees them.

Supports **Telegram** and **WhatsApp** (via Baileys). Runs hourly, deduplicates across runs, and responds to `/health` pings so you always know it's alive.

---

## How it works

1. You do a search on [Seloger.com](https://www.seloger.com) exactly how you want it — filters, location, price, rooms
2. You copy the URL from your browser
3. You paste it as `SEARCH_URL_1` in your `.env`
4. The bot polls that search every hour and forwards new listings with photos, price, and a direct link

The URL **is** the config. No Python to edit, no JSON files. Add more searches with `SEARCH_URL_2`, `SEARCH_URL_3`, etc.

---

## Quick start

```bash
# 1. Copy the example env file
cp .env.example .env

# 2. Fill in your credentials and search URLs (see Configuration below)
nano .env

# 3. Run
docker compose up -d
```

For WhatsApp support, build the sidecar first:

```bash
docker compose build whatsapp
docker compose up -d
# Then: docker compose logs -f whatsapp  → see pairing code → enter in WhatsApp
```

---

## Configuration

All config lives in `.env`. At least one notification platform is required.

```env
# ── Telegram (optional if WhatsApp is configured) ──────────────────────────
TELEGRAM_BOT_TOKEN="your-token-from-@BotFather"
TELEGRAM_CHAT_ID="your-telegram-user-or-group-id"

# ── WhatsApp via Baileys sidecar (optional if Telegram is configured) ───────
# WHATSAPP_PHONE="33612345678"   # your number to pair, no + (used for pairing)
# WHATSAPP_TO="+33612345678"     # recipient number with + (defaults to WHATSAPP_PHONE)

# ── Search URLs ─────────────────────────────────────────────────────────────
# Copy from your browser after searching on seloger.com
SEARCH_URL_1="https://www.seloger.com/classified-search?..."
SEARCH_URL_2="https://www.seloger.com/classified-search?..."   # optional

# ── Optional ─────────────────────────────────────────────────────────────────
# DB_PATH="/app/data/sent_listings.db"
# DEBUG="false"
```

### Notification modes

| Mode | Env vars needed | Behaviour |
|------|----------------|-----------|
| Telegram only | `TELEGRAM_BOT_TOKEN` + `TELEGRAM_CHAT_ID` | Notifications + interactive commands |
| WhatsApp only | `WHATSAPP_PHONE` (docker-compose sets `WHATSAPP_SERVICE_URL` automatically) | Notifications only (no commands) |
| Both | all of the above | Notifications on both; commands Telegram-only |

### Getting your Telegram credentials

1. Message [@BotFather](https://t.me/BotFather) → `/newbot` → copy the token
2. Message [@userinfobot](https://t.me/userinfobot) → copy your chat ID

### Setting up WhatsApp

1. Set `WHATSAPP_PHONE` (your number, no `+`) in `.env`
2. Run `docker compose up -d`
3. Run `docker compose logs -f whatsapp` → see the 8-digit pairing code
4. Open WhatsApp → **Appareils liés** → **Lier avec un numéro de téléphone** → enter the code
5. Done — session is saved to the data volume and persists across restarts

### Getting your search URL

1. Go to [seloger.com](https://www.seloger.com)
2. Configure your search (location, price, rooms, type, etc.)
3. Copy the URL from the browser address bar — that's your `SEARCH_URL_n`

---

## Bot commands

Send these to your Telegram bot at any time:

| Command | Description |
|---------|-------------|
| `/health` | Uptime and last run info |
| `/search` | Show a numbered menu of your configured searches — reply with the number to trigger immediately |

---

## Local development

```bash
# Setup
make setup          # create venv + install deps

# Run once (useful for testing)
make run

# Run once in debug mode (verbose Telegram messages)
make dev

# Start the hourly scheduler (production mode)
make schedule
```

On Windows, use `.\tasks.ps1 <command>` instead of `make`.

---

## Architecture

```
scheduler.py          ← Docker entrypoint: cron + Telegram polling thread
notifier.py           ← Core: search orchestration, formatting, broadcasting
telegram.py           ← Telegram adapter: send, download_photo, poll_commands
whatsapp.py           ← WhatsApp adapter: HTML→WA markdown, calls Baileys sidecar
seloger.py            ← Seloger URL adapter: parse_url() / build_url()
db.py                 ← SQLite dedup: init_db, is_sent, mark_sent
baileys-service/      ← Node.js 24 sidecar (WhatsApp protocol via Baileys)
```

Each real estate site gets its own adapter (`seloger.py`, `leboncoin.py`, …). Each notification platform gets its own adapter (`telegram.py`, `whatsapp.py`, …).

### Data flow (scheduled run)

```
SEARCH_URL_n (env)
  → parse_url()        extract query from browser URL
  → build_criteria()   translate to Seloger API payload
  → Seloger serp-bff   paginated search (up to 3 pages)
  → fetch details      enrich with photos, address, price
  → SQLite dedup       skip already-sent listings
  → broadcast()        send to all configured platforms
```

### On-demand `/search` (Telegram only)

Same flow, but results go only to Telegram since the command came from there.

---

## Deployment

The Python image is published automatically on every push to `main`:

```
ghcr.io/jtrishmawi/immo-bot:latest
```

The WhatsApp sidecar (`baileys-service/`) is built locally with `docker compose build whatsapp`.

The `docker-compose.yml` in this repo is ready to use — just add your `.env` alongside it and run `docker compose up -d`.

---

## Roadmap

- [x] WhatsApp notifications via Baileys
- [ ] LeBonCoin adapter
- [ ] Discord notifications
- [ ] Filter by floor, DPE, surface
- [ ] Price-drop alerts for tracked listings
