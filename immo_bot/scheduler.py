"""
immo-bot scheduler — runs core.main() every hour from 8h to 22h (Paris time).
Docker entrypoint. Starts the Telegram command polling thread if Telegram is configured.
"""
import logging
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

    now = datetime.now(ZoneInfo("Europe/Paris")).strftime("%d/%m/%Y à %H:%M")
    startup_msg = (
        f"✅ <b>immo-bot démarré</b> — {now}\n"
        f"Prochain run à la prochaine heure pile (08h-22h)"
    )
    if _tg._TELEGRAM_ENABLED:
        startup_msg += "\n\nCommandes disponibles :\n/health — état du service\n/search — lancer une recherche"
    core.broadcast(scraper, startup_msg)

    logger.info("Scheduler started — running initial search then hourly 08:00-22:00 Paris time")
    core.main()
    scheduler.start()
