"""
Threshold checking and Telegram alert sending.

Requires TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID in .env. See README
setup section for how to obtain these from @BotFather.
"""

import html
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


def _redact_token(text: str, token: str) -> str:
    """Strip the bot token out of any string before it's raised/logged.

    requests' exception messages include the full request URL, which
    for Telegram is /bot<TOKEN>/sendMessage.

    Args:
        text: message that may contain the token.
        token: the bot token to remove.
    Returns:
        text with every occurrence of token replaced by "<redacted>".
    """
    return text.replace(token, "<redacted>") if token else text


def send_telegram_message(text: str) -> None:
    """Send an HTML-formatted message to the configured Telegram chat.

    Callers must html.escape() any dynamic content inside `text`.
    Raises AlertSendError on any failure (with the bot token redacted) —
    callers should decide whether that's fatal for the run or just logged.

    Args:
        text: message body using Telegram's HTML parse mode.
    Returns:
        None.
    """
    token, chat_id = _get_telegram_credentials()
    url = f"{TELEGRAM_API_BASE}/bot{token}/sendMessage"
    try:
        resp = requests.post(
            url,
            json={"chat_id": chat_id, "text": text, "parse_mode": "HTML"},
            timeout=10,
        )
    except requests.RequestException as exc:
        raise AlertSendError(
            _redact_token(f"Network error sending Telegram message: {exc}", token)
        ) from None

    if resp.status_code != 200:
        raise AlertSendError(
            _redact_token(f"Telegram API returned {resp.status_code}: {resp.text}", token)
        )


def format_unlock_alert(event: UnlockEvent) -> str:
    """Build the HTML alert message for an upcoming unlock.

    Linear unlocks are described as a rate change, since DefiLlama's
    numbers for them are per-period rates, not a lump sum.

    Args:
        event: the upcoming unlock to describe.
    Returns:
        Telegram-HTML-safe message string.
    """
    pct = event.percent_of_circulating
    pct_str = f"{pct:.2f}%" if pct is not None else "unknown %"
    if event.unlock_type == "linear":
        amount_line = (
            f"Rate change: {event.tokens_before:,.0f} → {event.tokens_after:,.0f} "
            f"tokens per period ({pct_str} of circulating supply)"
        )
    else:
        amount_line = (
            f"Amount: {event.tokens_unlocking:,.0f} tokens "
            f"({pct_str} of circulating supply)"
        )
    return (
        f"🔓 <b>{html.escape(event.label)} unlock approaching</b>\n"
        f"Date: {event.unlock_date.strftime('%Y-%m-%d %H:%M UTC')}\n"
        f"In {event.days_until:.1f} days\n"
        f"{amount_line}\n"
        f"Type: {html.escape(event.unlock_type)} / {html.escape(event.category)}"
    )


def format_scrape_failure_alert(label: str | None, error: str) -> str:
    """Build the HTML alert message sent when a scrape fails outright.

    Args:
        label: token label (may be None/empty).
        error: the ScrapeError message; escaped because it routinely
            contains characters like "__NEXT_DATA__" and "<".
    Returns:
        Telegram-HTML-safe message string.
    """
    return (
        f"⚠️ <b>Scrape failed for {html.escape(event_label_or_unknown(label))}</b>\n"
        f"{html.escape(error)}"
    )


def event_label_or_unknown(label: str | None) -> str:
    """Return the label, or "unknown token" if it's None/empty.

    Args:
        label: token label or None.
    Returns:
        non-empty display string.
    """
    return label or "unknown token"


def check_and_alert(token: WatchedToken, event: UnlockEvent | None) -> str:
    """Given a watchlist token and its (possibly None) upcoming unlock
    event, decide whether to alert and send if so.

    Returns a short status string for logging/CLI output:
    'alerted', 'skipped-not-due', 'skipped-already-passed',
    'skipped-below-threshold', 'skipped-already-alerted', or
    'no-upcoming-event'.
    """
    if event is None:
        return "no-upcoming-event"

    if event.days_until < 0:
        return "skipped-already-passed"

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
