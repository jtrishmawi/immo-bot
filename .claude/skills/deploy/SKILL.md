---
name: deploy
description: Deploy or restart immo-bot Docker containers. Use when asked to deploy, restart, update, or pull the latest image in production.
---

# Deploy immo-bot

Deploys the latest Docker images and restarts the running containers.

## Prerequisites

- Docker (or Docker Swarm) is running on the target host.
- The GitHub Actions CI workflow has already built and pushed the images to GHCR:
  - `ghcr.io/jtrishmawi/immo-bot:latest` — Python notifier
  - `ghcr.io/jtrishmawi/immo-bot-whatsapp:latest` — Baileys WhatsApp sidecar
- `.env` is present in the project directory with all required vars.

## Steps

### Standard docker compose restart (local or single server)

```bash
docker compose pull
docker compose up -d
```

`docker compose pull` fetches the latest `:latest` tags from GHCR before restarting.

### Verify it started correctly

```bash
docker compose logs -f notifier --tail=50
```

Expected startup lines:
```
INFO Telegram command polling started
INFO Scheduler started (<hostname>) — running initial search then hourly 08:00-22:00 Paris time
```

Also check that the bot sent a startup message on Telegram/WhatsApp.

### Verify all search URLs loaded

The log line `Notifier run started — N search URL(s)` must show the expected count.
If it shows fewer than expected:
1. Check `.env` has all `SEARCH_URL_1` … `SEARCH_URL_N` set consecutively (no gaps).
2. Check `docker-compose.yml` forwards all those variables in the `environment:` block.
3. Restart again after fixing.

## After adding new SEARCH_URLs

Always run `/add-search` skill first to update both `.env` and `docker-compose.yml`, then deploy.

## Swarm mode (multiple replicas)

```bash
docker stack deploy -c docker-compose.yml immo
```

Each replica identifies itself in startup/shutdown messages as `replica 1`, `replica 2`, etc.
