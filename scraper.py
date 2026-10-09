"""
Fetch and parse token unlock data from DefiLlama's unlocks pages.

DefiLlama's unlocks pages (https://defillama.com/unlocks/{slug}) are
server-rendered Next.js pages. The full unlock dataset is embedded in a
<script id="__NEXT_DATA__" type="application/json"> tag in the initial
HTML response — confirmed by inspecting the live page's network traffic
and raw HTML on 2026-10-01. No JS execution or headless browser is
required: a plain GET request returns the complete data.

If this stops working, see "Known fragile points" in CLAUDE.md — the
most likely break is DefiLlama changing the __NEXT_DATA__ shape (e.g.
renaming "upcomingEvent" or "meta") or switching to a client-side-only
fetch that no longer appears in the server HTML.
"""

import json
from dataclasses import dataclass
from datetime import datetime, timezone

import requests
from bs4 import BeautifulSoup

from config import DEFILLAMA_UNLOCKS_URL, REQUEST_TIMEOUT_SECONDS

USER_AGENT = "Mozilla/5.0 (compatible; personal-unlock-tracker/1.0)"


class ScrapeError(Exception):
    """Raised when a scrape fails or returns unparseable data.

    main.py/alerts.py should catch this and send a "scrape failed"
    notification rather than silently falling through with stale data.
    """


@dataclass
class UnlockEvent:
    """A single upcoming unlock event for one token."""
    slug: str
    label: str
    timestamp: int
    category: str
    unlock_type: str
    tokens_before: float
    tokens_after: float
    circ_supply: float | None
    max_supply: float | None

    @property
    def unlock_date(self) -> datetime:
        """The unlock time as a timezone-aware UTC datetime."""
        return datetime.fromtimestamp(self.timestamp, tz=timezone.utc)

    @property
    def tokens_unlocking(self) -> float:
        """The size of this unlock event, in tokens.

        For cliff unlocks this is the lump sum released (parsing stores
        cliffs as before=0, after=amount). For linear unlocks it's the
        size of the rate change, since DefiLlama reports linear events as
        "rate goes from X to Y tokens per period".
        """
        return abs(self.tokens_after - self.tokens_before)

    @property
    def percent_of_circulating(self) -> float | None:
        """tokens_unlocking as a % of circulating supply, or None if
        circulating supply is unknown/zero."""
        if not self.circ_supply:
            return None
        return (self.tokens_unlocking / self.circ_supply) * 100

    @property
    def days_until(self) -> float:
        """Days from now until the unlock (negative if already passed)."""
        now = datetime.now(timezone.utc)
        return (self.unlock_date - now).total_seconds() / 86400


def fetch_page_html(slug: str) -> str:
    """Fetch the raw HTML of a DefiLlama unlocks page.

    Raises ScrapeError on any network failure or non-200 response —
    never returns partial/stale content silently.
    """
    url = DEFILLAMA_UNLOCKS_URL.format(slug=slug)
    try:
        resp = requests.get(
            url,
            headers={"User-Agent": USER_AGENT},
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
    except requests.RequestException as exc:
        raise ScrapeError(f"Network error fetching {url}: {exc}") from exc

    if resp.status_code != 200:
        raise ScrapeError(f"Unexpected status {resp.status_code} fetching {url}")

    return resp.text


def extract_next_data(html: str) -> dict:
    """Pull and parse the __NEXT_DATA__ JSON blob out of a page's HTML.

    Raises ScrapeError if the script tag is missing or not valid JSON —
    this is the single point of failure if DefiLlama changes their
    page structure, so keep it isolated and well-tested.
    """
    soup = BeautifulSoup(html, "html.parser")
    tag = soup.find("script", id="__NEXT_DATA__")
    if tag is None or not tag.string:
        raise ScrapeError(
            "Could not find __NEXT_DATA__ script tag — DefiLlama may have "
            "changed their page structure. Check 'Known fragile points' "
            "in CLAUDE.md."
        )
    try:
        return json.loads(tag.string)
    except json.JSONDecodeError as exc:
        raise ScrapeError(f"__NEXT_DATA__ was not valid JSON: {exc}") from exc


def parse_upcoming_unlock(next_data: dict, slug: str, label: str) -> UnlockEvent | None:
    """Extract the next upcoming unlock event from a parsed NEXT_DATA blob.

    Returns None if the token has no scheduled upcoming unlock (this is
    a valid state, not an error — some tokens are fully unlocked).
    Raises ScrapeError if the expected keys are missing or malformed,
    which usually means DefiLlama changed their data shape.

    If several events share the earliest timestamp (e.g. a team cliff and
    an investor cliff on the same day) they're merged into one
    UnlockEvent with summed token amounts.

    Args:
        next_data: parsed __NEXT_DATA__ dict from extract_next_data().
        slug: DefiLlama slug, copied onto the returned event.
        label: human-readable token name, copied onto the returned event.
    Returns:
        UnlockEvent for the soonest upcoming unlock, or None.
    """
    try:
        page_props = next_data["props"]["pageProps"]
        emissions = page_props["emissions"]
        upcoming = page_props.get("upcomingEvent") or emissions.get("upcomingEvent")
    except (KeyError, TypeError, AttributeError) as exc:
        raise ScrapeError(
            f"Expected key missing from __NEXT_DATA__: {exc}. "
            "DefiLlama's data shape may have changed."
        ) from exc

    if not upcoming:
        return None

    try:
        next_timestamp = min(int(event["timestamp"]) for event in upcoming)
        same_time_events = [
            event for event in upcoming if int(event["timestamp"]) == next_timestamp
        ]
        tokens_before = 0.0
        tokens_after = 0.0
        for event in same_time_events:
            before, after = _event_token_range(event)
            tokens_before += before
            tokens_after += after
        meta = emissions.get("meta") or {}
        circ_supply = meta.get("circSupply")
        max_supply = meta.get("maxSupply")
    except (KeyError, TypeError, ValueError, AttributeError) as exc:
        raise ScrapeError(
            f"Malformed upcomingEvent entry in __NEXT_DATA__ ({type(exc).__name__}: "
            f"{exc}). DefiLlama's data shape may have changed."
        ) from exc

    return UnlockEvent(
        slug=slug,
        label=label,
        timestamp=next_timestamp,
        category=_join_unique(event.get("category") for event in same_time_events),
        unlock_type=_join_unique(event.get("unlockType") for event in same_time_events),
        tokens_before=tokens_before,
        tokens_after=tokens_after,
        circ_supply=circ_supply,
        max_supply=max_supply,
    )


def _event_token_range(event: dict) -> tuple[float, float]:
    """Return (tokens_before, tokens_after) for one raw DefiLlama event.

    DefiLlama's `noOfTokens` shape depends on the unlock type:
      - cliff:  [amount]            -> (0, amount)
      - linear: [old_rate, new_rate] -> (old_rate, new_rate)

    Args:
        event: one entry of emissions["upcomingEvent"].
    Returns:
        tuple of (before, after) token counts as floats.
    """
    tokens = event.get("noOfTokens") or []
    if event.get("unlockType") == "cliff" or len(tokens) == 1:
        return 0.0, float(sum(tokens))
    if not tokens:
        return 0.0, 0.0
    return float(tokens[0]), float(tokens[-1])


def _join_unique(values) -> str:
    """Join distinct non-empty strings with '+', preserving order.

    Args:
        values: iterable of str or None.
    Returns:
        e.g. "team+investors", or "unknown" if nothing usable was given.
    """
    distinct = list(dict.fromkeys(value for value in values if value))
    return "+".join(distinct) if distinct else "unknown"


def get_upcoming_unlock(slug: str, label: str) -> UnlockEvent | None:
    """End-to-end: fetch + parse the next upcoming unlock for one token.

    This is the function main.py calls for each watchlist entry.
    Raises ScrapeError on any failure (network, parsing, or shape change).
    """
    html = fetch_page_html(slug)
    next_data = extract_next_data(html)
    return parse_upcoming_unlock(next_data, slug, label)
