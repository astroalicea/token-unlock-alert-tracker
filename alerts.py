"""
Threshold checking and Telegram alert sending.

Requires TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID in .env. See README
setup section for how to obtain these from @BotFather.
"""

import os

import requests
from dotenv import load_dotenv

from config import WatchedToken
from db import is_already_alerted, mark_alerted
from scraper import UnlockEvent

load_dotenv()

TELEGRAM_API_BASE = "https://api.telegram.org"


class AlertSendError(Exception):
    """Raised when a Telegram message fails to send."""


def _get_telegram_credentials() -> tuple[str, str]:
    """Read bot token + chat id from environment. Raises if either is
    missing, rather than silently skipping alerts."""
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        raise AlertSendError(
            "TELEGRAM_BOT_TOKEN and/or TELEGRAM_CHAT_ID not set in .env"
        )
    return token, chat_id


def send_telegram_message(text: str) -> None:
    """Send a plain-text message to the configured Telegram chat.

    Raises AlertSendError on any failure — callers should decide
    whether that's fatal for the run or just logged.
    """
    token, chat_id = _get_telegram_credentials()
    url = f"{TELEGRAM_API_BASE}/bot{token}/sendMessage"
    try:
        resp = requests.post(
            url,
            json={"chat_id": chat_id, "text": text, "parse_mode": "Markdown"},
            timeout=10,
        )
    except requests.RequestException as exc:
        raise AlertSendError(f"Network error sending Telegram message: {exc}") from exc

    if resp.status_code != 200:
        raise AlertSendError(
            f"Telegram API returned {resp.status_code}: {resp.text}"
        )


def format_unlock_alert(event: UnlockEvent) -> str:
    """Build the human-readable alert message for an upcoming unlock."""
    pct = event.percent_of_circulating
    pct_str = f"{pct:.2f}%" if pct is not None else "unknown %"
    return (
        f"🔓 *{event.label} unlock approaching*\n"
        f"Date: {event.unlock_date.strftime('%Y-%m-%d %H:%M UTC')}\n"
        f"In {event.days_until:.1f} days\n"
        f"Amount: {event.tokens_unlocking:,.0f} tokens ({pct_str} of circulating supply)\n"
        f"Type: {event.unlock_type} / {event.category}"
    )


def format_scrape_failure_alert(label: str, error: str) -> str:
    """Build the alert message sent when a scrape fails outright."""
    return f"⚠️ *Scrape failed for {event_label_or_unknown(label)}*\n{error}"


def event_label_or_unknown(label: str | None) -> str:
    return label or "unknown token"


def check_and_alert(token: WatchedToken, event: UnlockEvent | None) -> str:
    """Given a watchlist token and its (possibly None) upcoming unlock
    event, decide whether to alert and send if so.

    Returns a short status string for logging/CLI output:
    'alerted', 'skipped-not-due', 'skipped-below-threshold',
    'skipped-already-alerted', or 'no-upcoming-event'.
    """
    if event is None:
        return "no-upcoming-event"

    if event.days_until > token.alert_days_before:
        return "skipped-not-due"

    pct = event.percent_of_circulating
    if pct is not None and pct < token.min_percent_of_supply:
        return "skipped-below-threshold"

    if is_already_alerted(event.slug, event.timestamp):
        return "skipped-already-alerted"

    send_telegram_message(format_unlock_alert(event))
    mark_alerted(event.slug, event.timestamp)
    return "alerted"
