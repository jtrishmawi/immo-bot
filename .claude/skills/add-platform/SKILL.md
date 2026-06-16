---
name: add-platform
description: Add a new notification platform to immo-bot (e.g. Discord, Email, Signal). Use when asked to send alerts to a new channel.
---

# Add a New Notification Platform

## Architecture rule

Each platform is a standalone module in `immo_bot/platforms/<platform>.py` with **no imports from other internal modules** (no circular deps). `core.py` calls into it.

## Step 1 — Create `immo_bot/platforms/<platform>.py`

Minimum interface:

```python
_<PLATFORM>_ENABLED: bool   # read from env at import time

def send(text: str, photo_url: str | None = None) -> bool:
    """Send a message. Return True on success, False on failure."""
    ...

def health() -> bool:
    """Return True if the platform connection is healthy."""
    ...
```

Read configuration from env vars at module level, e.g.:
```python
import os
_DISCORD_WEBHOOK = os.getenv("DISCORD_WEBHOOK_URL", "").strip() or None
_DISCORD_ENABLED = bool(_DISCORD_WEBHOOK)
```

Reference implementations:
- `immo_bot/platforms/telegram.py` — REST API polling
- `immo_bot/platforms/whatsapp.py` — HTTP sidecar (Baileys)

### HTML → platform markdown

If the platform doesn't accept HTML, add a converter function:
```python
def _to_<platform>(html: str) -> str:
    """Convert Telegram HTML subset to platform-native format."""
    ...
```

See `whatsapp.py:to_whatsapp()` for an example (converts `<b>` → `*`, `<a href>` → inline URL).

## Step 2 — Add env vars

In `.env.example`, add the new vars with a comment:
```
# Discord (optional)
DISCORD_WEBHOOK_URL=
```

## Step 3 — Wire into `core.py`

1. Import:
   ```python
   from .platforms import <platform> as _<platform>
   ```

2. Add an enabled flag in `core.py` (alongside the existing `_WHATSAPP_ENABLED`):
   ```python
   _<PLATFORM>_ENABLED = bool(os.getenv("<PLATFORM>_KEY_VAR"))
   ```

3. Add a call in `broadcast()`:
   ```python
   def broadcast(scraper, text, photo_url=None):
       if _tg._TELEGRAM_ENABLED:
           _tg.send(scraper, text, photo_url)
       if _WHATSAPP_ENABLED:
           _wa.send(text, WHATSAPP_SERVICE_URL, WHATSAPP_TO, media_url=photo_url)
       if _<PLATFORM>_ENABLED:
           _<platform>.send(text, photo_url)
   ```

3. Add health check in `send_health()` if the platform has a `health()` function.

4. Update the startup mode validation block (the `sys.exit` guard) to include the new platform as a valid mode.

## Step 4 — Update `docker-compose.yml`

Add the new env var(s) to the `notifier` service `environment:` block:
```yaml
- DISCORD_WEBHOOK_URL=${DISCORD_WEBHOOK_URL:-}
```

## Step 5 — Update `CLAUDE.md`

Add the new platform to the "Notification modes" table and the env vars table.

## Step 6 — Run tests

```bash
.venv/Scripts/pytest tests/ -v
```

Consider adding a test that verifies `_<PLATFORM>_ENABLED` is false when the env var is unset.
