# immo-bot

A self-hosted Telegram bot that watches French real estate listings and sends new ones straight to your phone — before anyone else sees them.

Runs hourly, deduplicates across runs, and responds to `/health` pings so you always know it's alive.

---

## How it works

1. You do a search on [Seloger.com](https://www.seloger.com) exactly how you want it — filters, location, price, rooms
2. You copy the URL from your browser
3. You paste it as `SEARCH_URL_1` in your `.env`
4. The bot polls that search every hour and forwards new listings to Telegram with photos, price, and a direct link

The URL **is** the config. No Python to edit, no JSON files. Add more searches with `SEARCH_URL_2`, `SEARCH_URL_3`, etc.

---

## Quick start

```bash
# 1. Copy the example env file
cp .env.example .env

# 2. Fill in your Telegram credentials and search URLs
#    (see Configuration below)
nano .env

# 3. Run
docker compose up -d
```

---

## Configuration

All config lives in `.env`:

```env
TELEGRAM_BOT_TOKEN="your-token-from-@BotFather"
TELEGRAM_CHAT_ID="your-telegram-user-or-group-id"

# Paste your Seloger search URL here (copy from your browser after searching)
SEARCH_URL_1="https://www.seloger.com/classified-search?..."

# Optional: add more searches (different property type, different area, etc.)
SEARCH_URL_2="https://www.seloger.com/classified-search?..."

# Optional
DB_PATH="/app/data/sent_listings.db"   # default
DEBUG="false"                           # set true to get verbose Telegram messages
```

### Getting your Telegram credentials

1. Message [@BotFather](https://t.me/BotFather) → `/newbot` → copy the token
2. Message [@userinfobot](https://t.me/userinfobot) → copy your chat ID

### Getting your search URL

1. Go to [seloger.com](https://www.seloger.com)
2. Configure your search (location, price, rooms, type, etc.)
3. Copy the URL from the browser address bar — that's your `SEARCH_URL_n`

---

## Bot commands

Send these to your bot at any time:

| Command | Description |
|---------|-------------|
| `/health` | Reply with uptime and last run info |
| `/search` | Show a numbered menu of your configured searches — reply with the number to trigger it immediately |

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
scheduler.py        ← hourly cron (APScheduler) + /health polling thread
notifier.py         ← fetch listings → deduplicate → send to Telegram
seloger.py          ← URL adapter: parse_url() / build_url()
```

Each real estate site gets its own adapter module (`seloger.py`, `leboncoin.py`, …). The domain in `SEARCH_URL_n` identifies which adapter to use. Notification platforms (Telegram today, Discord/WhatsApp tomorrow) follow the same pattern.

### Data flow

```
SEARCH_URL_n (env)
  → parse_url()          extract search query from browser URL
  → build_criteria()     translate to Seloger API payload
  → Seloger serp-bff     paginated search (up to 3 pages)
  → fetch details        enrich with photos, address, price
  → SQLite dedup         skip already-sent listings
  → Telegram             send new listings with photo
```

---

## Deployment

The Docker image is published automatically on every push to `main`:

```
ghcr.io/jtrishmawi/immo-bot:latest
```

The `docker-compose.yml` in this repo is ready to use — just add your `.env` alongside it and run `docker compose up -d`.

---

## Roadmap

- [ ] LeBonCoin adapter
- [ ] Discord / WhatsApp notification platforms
- [ ] Filter by floor, DPE, surface
- [ ] Price-drop alerts for tracked listings
