"""
immo-bot scheduler — runs core.main() every hour from 8h to 22h (Paris time).
Docker entrypoint. Starts the Telegram command polling thread if Telegram is configured.
"""
import logging
import os
import signal
import socket
import sys
import threading
from datetime import datetime
from zoneinfo import ZoneInfo

import cloudscraper
from apscheduler.schedulers.blocking import BlockingScheduler

from . import core
from .platforms import telegram as _tg

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)

logger = logging.getLogger(__name__)

# In Docker Swarm the hostname is "<service>.<slot>.<task-id>", e.g. "immo_bot.1.xyz".
# Extract the slot number so multiple replicas are distinguishable in notifications.
_HOSTNAME = os.getenv("HOSTNAME", socket.gethostname())
_parts = _HOSTNAME.split(".")
_REPLICA = f"replica {_parts[1]}" if len(_parts) >= 2 and _parts[1].isdigit() else _HOSTNAME[:16]

scheduler = BlockingScheduler(timezone="Europe/Paris")
scheduler.add_job(core.main, "cron", hour="8-22", minute=0)

if __name__ == "__main__":
    scraper = cloudscraper.create_scraper(
        browser={"browser": "chrome", "platform": "windows", "mobile": False}
    )

    core._run_state["started_at"] = datetime.now(ZoneInfo("Europe/Paris"))

    if _tg._TELEGRAM_ENABLED:
        t = threading.Thread(
            target=_tg.poll_commands,
            args=(
                scraper,
                lambda: core.send_health(scraper),
                lambda: core.send_search_menu(scraper),
                lambda chat_id, text: core.run_on_demand_search(scraper, chat_id, text),
            ),
            daemon=True,
        )
        t.start()
        logger.info("Telegram command polling started")

    def _on_shutdown(sig, frame):
        core.broadcast(scraper, f"⛔ <b>immo-bot arrêté</b> · {_REPLICA}")
        scheduler.shutdown(wait=False)
        sys.exit(0)

    signal.signal(signal.SIGTERM, _on_shutdown)
    signal.signal(signal.SIGINT, _on_shutdown)

    now = datetime.now(ZoneInfo("Europe/Paris")).strftime("%d/%m/%Y à %H:%M")
    startup_msg = (
        f"✅ <b>immo-bot démarré</b> — {now} · {_REPLICA}\n"
        f"Prochain run à la prochaine heure pile (08h-22h)"
    )
    if _tg._TELEGRAM_ENABLED:
        startup_msg += "\n\nCommandes disponibles :\n/health — état du service\n/search — lancer une recherche"
    core.broadcast(scraper, startup_msg)

    logger.info("Scheduler started (%s) — running initial search then hourly 08:00-22:00 Paris time", _REPLICA)
    core.main()
    scheduler.start()
