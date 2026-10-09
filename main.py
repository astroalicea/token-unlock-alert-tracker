"""
Orchestration only — no business logic lives here.

Run manually with `python main.py`, or on a schedule via cron:
    0 */12 * * * cd /path/to/unlock-tracker && /path/to/venv/bin/python main.py >> run.log 2>&1
"""

import sys

from alerts import check_and_alert, format_scrape_failure_alert, send_telegram_message, AlertSendError
from config import WATCHLIST
from db import init_db, log_scrape_result, upsert_unlock_event
from scraper import ScrapeError, get_upcoming_unlock


def run_once() -> None:
    """One full scrape -> store -> alert cycle across the whole watchlist."""
    init_db()

    for token in WATCHLIST:
        try:
            event = get_upcoming_unlock(token.slug, token.label)
            log_scrape_result(token.slug, success=True)
        except ScrapeError as exc:
            log_scrape_result(token.slug, success=False, error_message=str(exc))
            print(f"[{token.slug}] SCRAPE FAILED: {exc}")
            try:
                send_telegram_message(format_scrape_failure_alert(token.label, str(exc)))
            except AlertSendError as alert_exc:
                print(f"[{token.slug}] Also failed to send failure alert: {alert_exc}")
            continue

        if event is not None:
            upsert_unlock_event(
                slug=event.slug,
                label=event.label,
                timestamp=event.timestamp,
                category=event.category,
                unlock_type=event.unlock_type,
                tokens_unlocking=event.tokens_unlocking,
                percent_of_circulating=event.percent_of_circulating,
            )

        try:
            status = check_and_alert(token, event)
            print(f"[{token.slug}] {status}")
        except AlertSendError as exc:
            print(f"[{token.slug}] ALERT SEND FAILED: {exc}")


if __name__ == "__main__":
    try:
        run_once()
    except Exception as exc:  # noqa: BLE001 — top-level safety net, re-raise after logging
        print(f"FATAL: unhandled error in run_once(): {exc}", file=sys.stderr)
        raise
