"""
Database and dedupe tests. Dedupe is the single most important thing
to verify here — running main.py twice must never send a duplicate
alert. Uses a temporary on-disk SQLite file per test, never the real
unlock_tracker.db.
"""

import os
import tempfile

import pytest

from db import (
    init_db,
    is_already_alerted,
    log_scrape_result,
    mark_alerted,
    upsert_unlock_event,
)


@pytest.fixture
def temp_db_path():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    init_db(db_path=path)
    yield path
    os.remove(path)


def test_upsert_new_event_returns_true(temp_db_path):
    is_new = upsert_unlock_event(
        slug="solana", label="Solana (SOL)", timestamp=1791737724,
        category="staking", unlock_type="linear",
        tokens_unlocking=401600, percent_of_circulating=0.075,
        db_path=temp_db_path,
    )
    assert is_new is True


def test_upsert_existing_event_returns_false(temp_db_path):
    args = dict(
        slug="solana", label="Solana (SOL)", timestamp=1791737724,
        category="staking", unlock_type="linear",
        tokens_unlocking=401600, percent_of_circulating=0.075,
        db_path=temp_db_path,
    )
    upsert_unlock_event(**args)
    is_new_second_time = upsert_unlock_event(**args)
    assert is_new_second_time is False


def test_event_not_alerted_by_default(temp_db_path):
    upsert_unlock_event(
        slug="solana", label="Solana (SOL)", timestamp=1791737724,
        category="staking", unlock_type="linear",
        tokens_unlocking=401600, percent_of_circulating=0.075,
        db_path=temp_db_path,
    )
    assert is_already_alerted("solana", 1791737724, db_path=temp_db_path) is False


def test_mark_alerted_sets_dedupe_flag(temp_db_path):
    upsert_unlock_event(
        slug="solana", label="Solana (SOL)", timestamp=1791737724,
        category="staking", unlock_type="linear",
        tokens_unlocking=401600, percent_of_circulating=0.075,
        db_path=temp_db_path,
    )
    mark_alerted("solana", 1791737724, db_path=temp_db_path)
    assert is_already_alerted("solana", 1791737724, db_path=temp_db_path) is True


def test_dedupe_survives_running_main_twice(temp_db_path):
    """This is the core guarantee: simulate main.py's scrape->store cycle
    running twice in a row and confirm the second run sees the event as
    already alerted."""
    args = dict(
        slug="arbitrum", label="Arbitrum (ARB)", timestamp=1800000000,
        category="team", unlock_type="cliff",
        tokens_unlocking=5_000_000, percent_of_circulating=1.2,
        db_path=temp_db_path,
    )

    # --- First run ---
    upsert_unlock_event(**args)
    assert is_already_alerted("arbitrum", 1800000000, db_path=temp_db_path) is False
    mark_alerted("arbitrum", 1800000000, db_path=temp_db_path)

    # --- Second run (same data re-scraped) ---
    upsert_unlock_event(**args)  # should update in place, not duplicate
    assert is_already_alerted("arbitrum", 1800000000, db_path=temp_db_path) is True


def test_different_timestamps_are_independent_events(temp_db_path):
    """Two unlock events for the same token at different times must be
    tracked and alerted independently."""
    upsert_unlock_event(
        slug="solana", label="Solana (SOL)", timestamp=1111111111,
        category="staking", unlock_type="linear",
        tokens_unlocking=100, percent_of_circulating=0.01,
        db_path=temp_db_path,
    )
    upsert_unlock_event(
        slug="solana", label="Solana (SOL)", timestamp=2222222222,
        category="staking", unlock_type="linear",
        tokens_unlocking=200, percent_of_circulating=0.02,
        db_path=temp_db_path,
    )
    mark_alerted("solana", 1111111111, db_path=temp_db_path)

    assert is_already_alerted("solana", 1111111111, db_path=temp_db_path) is True
    assert is_already_alerted("solana", 2222222222, db_path=temp_db_path) is False


def test_log_scrape_result_records_failure(temp_db_path):
    # Mostly a smoke test that this doesn't raise; detailed querying of
    # scrape_log isn't needed for v1.
    log_scrape_result("solana", success=False, error_message="timeout", db_path=temp_db_path)
    log_scrape_result("solana", success=True, db_path=temp_db_path)
