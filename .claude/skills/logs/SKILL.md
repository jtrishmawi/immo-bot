---
name: logs
description: View and interpret immo-bot Docker logs. Use when asked to check logs, debug an issue, see what the bot is doing, or diagnose errors.
---

# View Bot Logs

## Tail live logs

```bash
# Notifier (Python bot)
docker compose logs -f notifier --tail=100

# WhatsApp sidecar
docker compose logs -f whatsapp --tail=100
```

## Key log lines to look for

| Log line | Meaning |
|---|---|
| `Scheduler started (<hostname>)` | Bot started successfully |
| `Notifier run started — N search URL(s)` | Number of active searches (must match `.env`) |
| `[Label] Page 1: X items` | Scrape succeeded, found X listings |
| `[Label] N new, M already seen` | Dedup result |
| `[Label] Sent <ID> — <city> <price>€` | Listing dispatched to Telegram/WhatsApp |
| `[Label] Done — N new listings sent` | Search run complete |
| `Run complete — N total new listings sent` | All searches done |
| `WhatsApp send error: ...NameResolutionError...` | WhatsApp sidecar unreachable (normal if WA not running) |
| `immo-bot arrêté` | Bot shutdown notification sent |

## Common problems

### "Notifier run started — 2 search URL(s)" but you have more
`docker-compose.yml` doesn't forward all `SEARCH_URL_*` vars. Run `/add-search` to fix.

### WhatsApp NameResolutionError on every send
The `whatsapp` container isn't running or isn't on the same Docker network. Either start the whatsapp sidecar or remove `WHATSAPP_PHONE` / `WHATSAPP_SERVICE_URL` from `.env` to disable WhatsApp.

### No listings found after a scrape
- For Seloger: Cloudflare may have blocked the request. Check for HTTP 403/429 in debug logs.
- For PAP: `curl-cffi` impersonation may need updating. Check `pap.py` `impersonate` param.
- Search criteria may be too restrictive (check the URL params in `.env`).

### Bot not responding to Telegram commands
- Confirm `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` are set correctly.
- Check the log for `Telegram command polling started`.
- If the poll loop crashed, the bot requires a restart.

## Enable verbose logging

Set `DEBUG=true` in `.env` and restart. The bot will log more detail and send per-run summary messages even when there are no new listings.
