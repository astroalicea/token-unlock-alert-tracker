"""
Scraper parsing tests — run against saved HTML fixtures, never live
network calls (per CLAUDE.md engineering standards).
"""

import os

import pytest

from scraper import ScrapeError, extract_next_data, parse_upcoming_unlock

FIXTURES_DIR = os.path.join(os.path.dirname(__file__), "fixtures")


def load_fixture(filename: str) -> str:
    with open(os.path.join(FIXTURES_DIR, filename), encoding="utf-8") as f:
        return f.read()


def test_extract_next_data_parses_valid_fixture():
    html = load_fixture("solana_unlocks_page.html")
    data = extract_next_data(html)
    assert "props" in data
    assert "pageProps" in data["props"]
    assert "emissions" in data["props"]["pageProps"]


def test_extract_next_data_raises_on_missing_script_tag():
    html = "<html><body>no next data here</body></html>"
    with pytest.raises(ScrapeError, match="__NEXT_DATA__"):
        extract_next_data(html)


def test_extract_next_data_raises_on_malformed_json():
    html = '<script id="__NEXT_DATA__" type="application/json">{not valid json</script>'
    with pytest.raises(ScrapeError, match="not valid JSON"):
        extract_next_data(html)


def test_parse_upcoming_unlock_returns_event_from_fixture():
    html = load_fixture("solana_unlocks_page.html")
    data = extract_next_data(html)
    event = parse_upcoming_unlock(data, slug="solana", label="Solana (SOL)")

    assert event is not None
    assert event.slug == "solana"
    assert event.label == "Solana (SOL)"
    assert event.timestamp == 1791737724
    assert event.category == "staking"
    assert event.unlock_type == "linear"
    assert event.tokens_before == 0
    assert event.tokens_after == 401600
    assert event.circ_supply == 538000000
    assert event.max_supply == 591000000


def test_parse_upcoming_unlock_computes_tokens_unlocking():
    html = load_fixture("solana_unlocks_page.html")
    data = extract_next_data(html)
    event = parse_upcoming_unlock(data, slug="solana", label="Solana (SOL)")
    assert event.tokens_unlocking == 401600


def test_parse_upcoming_unlock_computes_percent_of_circulating():
    html = load_fixture("solana_unlocks_page.html")
    data = extract_next_data(html)
    event = parse_upcoming_unlock(data, slug="solana", label="Solana (SOL)")
    expected_pct = (401600 / 538000000) * 100
    assert event.percent_of_circulating == pytest.approx(expected_pct)


def test_parse_upcoming_unlock_raises_on_missing_emissions_key():
    malformed = {"props": {"pageProps": {}}}
    with pytest.raises(ScrapeError, match="Expected key missing"):
        parse_upcoming_unlock(malformed, slug="solana", label="Solana (SOL)")


def test_parse_upcoming_unlock_returns_none_when_no_upcoming_event():
    data = {
        "props": {
            "pageProps": {
                "emissions": {
                    "meta": {"circSupply": 1000},
                    "upcomingEvent": [],
                }
            }
        }
    }
    event = parse_upcoming_unlock(data, slug="fully-unlocked-token", label="Test")
    assert event is None
