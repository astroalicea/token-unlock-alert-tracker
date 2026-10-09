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
        return datetime.fromtimestamp(self.timestamp, tz=timezone.utc)

    @property
    def tokens_unlocking(self) -> float:
        """The size of this unlock event, in tokens."""
        return abs(self.tokens_after - self.tokens_before)

    @property
    def percent_of_circulating(self) -> float | None:
        if not self.circ_supply:
            return None
        return (self.tokens_unlocking / self.circ_supply) * 100

    @property
    def days_until(self) -> float:
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
    Raises ScrapeError if the expected keys are missing entirely, which
    usually means DefiLlama changed their data shape.
    """
    try:
        page_props = next_data["props"]["pageProps"]
        emissions = page_props["emissions"]
    except KeyError as exc:
        raise ScrapeError(
            f"Expected key missing from __NEXT_DATA__: {exc}. "
            "DefiLlama's data shape may have changed."
        ) from exc

    upcoming = page_props.get("upcomingEvent") or emissions.get("upcomingEvent")
    if not upcoming:
        return None

    event = upcoming[0]
    meta = emissions.get("meta", {})
    tokens = event.get("noOfTokens", [0, 0])
    tokens_before = tokens[0] if len(tokens) > 0 else 0
    tokens_after = tokens[-1] if len(tokens) > 0 else 0

    return UnlockEvent(
        slug=slug,
        label=label,
        timestamp=event["timestamp"],
        category=event.get("category", "unknown"),
        unlock_type=event.get("unlockType", "unknown"),
        tokens_before=tokens_before,
        tokens_after=tokens_after,
        circ_supply=meta.get("circSupply"),
        max_supply=meta.get("maxSupply"),
    )


def get_upcoming_unlock(slug: str, label: str) -> UnlockEvent | None:
    """End-to-end: fetch + parse the next upcoming unlock for one token.

    This is the function main.py calls for each watchlist entry.
    Raises ScrapeError on any failure (network, parsing, or shape change).
    """
    html = fetch_page_html(slug)
    next_data = extract_next_data(html)
    return parse_upcoming_unlock(next_data, slug, label)
