"""
Alert decision + formatting tests. Telegram is never actually called:
send/dedupe functions are monkeypatched, and network errors are faked.
"""

import time

import pytest
import requests

import alerts
from alerts import (
    AlertSendError,
    check_and_alert,
    format_scrape_failure_alert,
    format_unlock_alert,
    send_telegram_message,
)
from config import WatchedToken
from scraper import UnlockEvent

FAKE_TOKEN = "123456:SECRET-bot-token"


def make_event(days_from_now: float, tokens_after: float = 1_000_000,
               unlock_type: str = "cliff") -> UnlockEvent:
    """Build an UnlockEvent `days_from_now` days in the future."""
    return UnlockEvent(
        slug="arbitrum", label="Arbitrum (ARB)",
        timestamp=int(time.time() + days_from_now * 86400),
        category="insiders", unlock_type=unlock_type,
        tokens_before=0, tokens_after=tokens_after,
        circ_supply=100_000_000, max_supply=10_000_000_000,
    )


@pytest.fixture
def sent_messages(monkeypatch):
    """Capture outgoing messages and stub the DB dedupe layer."""
    sent: list[str] = []
    alerted: set[tuple[str, int]] = set()
    monkeypatch.setattr(alerts, "send_telegram_message", sent.append)
    monkeypatch.setattr(alerts, "is_already_alerted",
                        lambda slug, ts: (slug, ts) in alerted)
    monkeypatch.setattr(alerts, "mark_alerted",
                        lambda slug, ts: alerted.add((slug, ts)))
    return sent


TOKEN = WatchedToken(slug="arbitrum", label="Arbitrum (ARB)",
                     alert_days_before=7, min_percent_of_supply=0.5)


def test_no_event(sent_messages):
    assert check_and_alert(TOKEN, None) == "no-upcoming-event"
    assert sent_messages == []


def test_not_due_yet(sent_messages):
    assert check_and_alert(TOKEN, make_event(30)) == "skipped-not-due"
    assert sent_messages == []


def test_already_passed_is_not_alerted(sent_messages):
    assert check_and_alert(TOKEN, make_event(-2)) == "skipped-already-passed"
    assert sent_messages == []


def test_below_threshold(sent_messages):
    tiny = make_event(3, tokens_after=1_000)  # 0.001% of circ
    assert check_and_alert(TOKEN, tiny) == "skipped-below-threshold"
    assert sent_messages == []


def test_alerts_once_then_dedupes(sent_messages):
    event = make_event(3)
    assert check_and_alert(TOKEN, event) == "alerted"
    assert check_and_alert(TOKEN, event) == "skipped-already-alerted"
    assert len(sent_messages) == 1


def test_scrape_failure_alert_escapes_html():
    msg = format_scrape_failure_alert("A<b>", "Could not find __NEXT_DATA__ <script> tag")
    assert "A&lt;b&gt;" in msg
    assert "&lt;script&gt;" in msg
    assert "__NEXT_DATA__" in msg


def test_cliff_alert_shows_amount():
    msg = format_unlock_alert(make_event(3, tokens_after=92_650_000))
    assert "Amount: 92,650,000 tokens" in msg


def test_linear_alert_shows_rate_change():
    event = make_event(3, unlock_type="linear")
    event.tokens_before, event.tokens_after = 100, 400
    assert "Rate change: 100 → 400" in format_unlock_alert(event)


def test_network_error_redacts_bot_token(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", FAKE_TOKEN)
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "42")

    def fake_post(url, **kwargs):
        raise requests.ConnectionError(f"Max retries exceeded with url: {url}")

    monkeypatch.setattr(alerts.requests, "post", fake_post)
    with pytest.raises(AlertSendError) as exc_info:
        send_telegram_message("hi")
    assert FAKE_TOKEN not in str(exc_info.value)
    assert "<redacted>" in str(exc_info.value)
    assert exc_info.value.__cause__ is None
