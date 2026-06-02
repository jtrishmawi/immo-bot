"""
immo-bot scheduler — runs notifier.main() every hour from 8h to 22h (Paris time).
This is the Docker entrypoint. Also starts the /health command polling thread.
"""
import logging
import threading
from datetime import datetime
from zoneinfo import ZoneInfo

import cloudscraper
from apscheduler.schedulers.blocking import BlockingScheduler

import notifier

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)

logger = logging.getLogger(__name__)

scheduler = BlockingScheduler(timezone="Europe/Paris")
scheduler.add_job(notifier.main, "cron", hour="8-22", minute=0)

if __name__ == "__main__":
    scraper = cloudscraper.create_scraper(
        browser={"browser": "chrome", "platform": "windows", "mobile": False}
    )

    notifier._run_state["started_at"] = datetime.now(ZoneInfo("Europe/Paris"))

    t = threading.Thread(target=notifier.poll_commands, args=(scraper,), daemon=True)
    t.start()
    logger.info("Telegram command polling started")

    now = datetime.now(ZoneInfo("Europe/Paris")).strftime("%d/%m/%Y à %H:%M")
    notifier.send(scraper, (
        f"✅ <b>immo-bot démarré</b> — {now}\n"
        f"Prochain run à la prochaine heure pile (08h-22h)\n\n"
        f"Commandes disponibles :\n"
        f"/health — état du service et dernier run\n"
        f"/search — lancer une recherche immédiatement"
    ))

    logger.info("Scheduler started — running initial search then hourly 08:00-22:00 Paris time")
    notifier.main()
    scheduler.start()
