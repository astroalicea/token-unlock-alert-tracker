"""
Watchlist and thresholds for the token unlock tracker.

Data source: DefiLlama's server-rendered unlocks page at
https://defillama.com/unlocks/{slug}

Confirmed 2026-10-01: the page embeds a __NEXT_DATA__ <script> tag
containing the full unlock dataset (no separate API call, no JS
execution needed — a plain `requests.get()` returns it server-rendered).
See scraper.py for the parsing logic and CLAUDE.md "Known fragile points"
for what to check if this breaks.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class WatchedToken:
    """One token to track.

    slug: the path segment in https://defillama.com/unlocks/{slug}
          (this is DefiLlama's internal protocol slug, not always the
          same as the ticker — e.g. Solana is "solana", but some
          tokens use a project name instead of a ticker)
    label: human-readable name for alert messages
    alert_days_before: send an alert when the next unlock is within
          this many days (checked each run; dedup prevents re-alerting)
    min_percent_of_supply: only alert if the unlock is at least this
          % of circulating supply (filters out noise from tiny
          routine linear unlocks). Set to 0 to alert on everything.
    """
    slug: str
    label: str
    alert_days_before: int = 7
    min_percent_of_supply: float = 0.0


WATCHLIST: list[WatchedToken] = [
    WatchedToken(slug="solana", label="Solana (SOL)", alert_days_before=7),
    WatchedToken(slug="hyperliquid", label="Hyperliquid (HYPE)", alert_days_before=7),
    WatchedToken(slug="chainlink", label="Chainlink (LINK)", alert_days_before=7),
    WatchedToken(slug="celo", label="Celo (CELO)", alert_days_before=7),
    WatchedToken(slug="zksync-era", label="zkSync Era (ZK)", alert_days_before=7),
    WatchedToken(slug="ether.fi", label="ether.fi (ETHFI)", alert_days_before=7),
    WatchedToken(slug="ethereum", label="Ethereum (ETH)", alert_days_before=7),
    WatchedToken(slug="arbitrum", label="Arbitrum (ARB)", alert_days_before=7),
    # Not added: XLM (Stellar) and XRP (Ripple) have no DefiLlama unlocks
    # page (no VC-style vesting schedule tracked for either). MNDE
    # (Marinade) also didn't resolve to a valid slug as of 2026-10-01 -
    # re-check https://defillama.com/unlocks/<slug> if you want to retry it.
]

# How long to wait between scrapes, in hours. DefiLlama's unlock data
# doesn't change intraday, so there's no benefit to polling more often.
POLL_INTERVAL_HOURS = 12

# Path to the SQLite database file.
DB_PATH = "unlock_tracker.db"

# Base URL template for the DefiLlama unlocks page.
DEFILLAMA_UNLOCKS_URL = "https://defillama.com/unlocks/{slug}"

# HTTP request timeout, in seconds.
REQUEST_TIMEOUT_SECONDS = 15
