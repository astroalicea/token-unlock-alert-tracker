# Token Unlock Tracker — Engineering Rules

## Project Purpose
Personal tool: scrape token unlock schedules, store in SQLite,
send Telegram alerts when a tracked token has an unlock within
the configured threshold window.

## Confirmed data source (verified 2026-10-01)
Source: **DefiLlama**, not TokenUnlocks/Tokenomist as originally scoped.
`https://defillama.com/unlocks/{slug}` is a server-rendered Next.js page.
The full unlock dataset — next unlock date, amount, category (team/
investor/community/staking/etc.), cliff vs. linear type, circulating and
max supply — is embedded in a `<script id="__NEXT_DATA__" type="application/
json">` tag in the raw HTML. Confirmed via live network inspection: a
plain `requests.get()` with no JS execution returns the complete data.
No headless browser needed. No API key needed. DefiLlama's official
Emissions API is Pro-only ($300/mo) — this project does NOT use that;
it reads the same data off the free public page instead.

Path to the data in the parsed JSON:
`data["props"]["pageProps"]["emissions"]["upcomingEvent"]` (array; take
`[0]` for the next event) and `data["props"]["pageProps"]["emissions"]
["meta"]` for circulating/max supply. See `scraper.py` for the full
parsing logic — this is the single point of failure if DefiLlama changes
their page, so it's isolated into `extract_next_data()` and
`parse_upcoming_unlock()`, both independently tested against a fixture.

## Engineering Standards (non-negotiable)
- Functions `return` values — never `print()` inside logic functions.
  Formatting/output happens only in main.py or alerts.py's send layer.
- All secrets (TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID) go in `.env`, loaded
  via python-dotenv. Never hardcode, never log secret values.
- Every function has a docstring: purpose, args, return type.
- Descriptive names — no single-letter vars outside loop indices.
- Write a test for every scraper parsing function before moving on.
  Scraper tests use saved HTML fixtures (tests/fixtures/), not live
  network calls.
- Fail loudly: scrape failures/timeouts raise `ScrapeError`, which
  main.py catches and turns into a "scrape failed" Telegram message —
  never fail silently into stale data presented as current.

## Architecture (don't deviate without discussion)
- config.py  — watchlist (token slug/label, thresholds), poll interval
- scraper.py — fetch + parse DefiLlama's embedded JSON, returns UnlockEvent
- db.py      — SQLite schema + CRUD, dedupe via `alerted_at` timestamp
- alerts.py  — threshold check + Telegram send
- main.py    — orchestration only, calls the above in order

## Scraping etiquette
- Poll at most 1-2x/day (`POLL_INTERVAL_HOURS` in config.py) — unlock
  dates don't change hourly.
- Respect robots.txt. If DefiLlama blocks scraping, stop and flag it,
  don't route around it.
- `scrape_log` table in the DB records every attempt (success/failure)
  for debugging and as groundwork for a stale-cache fallback if needed.

## Definition of done for each feature
1. Works for one token end-to-end before scaling to full watchlist.
2. Has a passing test.
3. Dedupe logic verified — running main.py twice in a row must not
   send duplicate alerts. (Covered by
   `test_dedupe_survives_running_main_twice` in tests/test_db.py —
   do not remove or weaken this test.)

## Known fragile points
(Update this every time something breaks — check here first when the
scraper silently stops working. Claude Code: when you fix a break here,
update this section in the same commit as the fix — see "Self-improvement
loop" below.)
- DefiLlama `/unlocks/{slug}` page — depends on the `__NEXT_DATA__`
  script tag existing and containing `props.pageProps.emissions
  .upcomingEvent` and `props.pageProps.emissions.meta`.
  Last verified working: 2026-10-01.
- If `upcomingEvent` moves or is renamed, check `extract_next_data()`'s
  raised error message — it tells you which key went missing.
- DefiLlama may occasionally 503 on the first unlocks-related sub-request
  (observed during manual testing); scraper.py currently treats any
  non-200 as a hard failure. If 503s turn out to be common/transient,
  consider adding one retry with backoff in `fetch_page_html()` before
  treating it as a ScrapeError — note here if you do.

## Self-improvement loop
This project is meant to get better at itself over time, not just run
unchanged. Follow this loop whenever you (Claude Code) touch this repo:

1. **When a scrape breaks**: diagnose using the error message from
   `ScrapeError` (it names the missing key or structural problem), fix
   `scraper.py`, add/update a test fixture reflecting the new page shape,
   and update "Known fragile points" above with what changed and today's
   date — in the same commit as the fix, not a follow-up.
2. **When you add a new watchlist token**: confirm its DefiLlama slug
   actually exists (visit `https://defillama.com/unlocks/{slug}`) before
   adding it to `config.py` — slugs aren't always the ticker.
3. **When you notice a recurring false-positive or noisy alert pattern**:
   that's a signal to adjust `min_percent_of_supply` or
   `alert_days_before` per-token in config.py, not to change the core
   alerting logic.
4. **When this project's pattern (scrape -> dedupe -> alert) gets reused
   for something else** (a second bot, a different data source): don't
   just copy these files. Update the `scraper-alert-bot` skill
   (installed in this account's skill catalog) with whatever new
   wrinkle you hit, so the next bot benefits too. Propose the update,
   don't just note it here.
5. **Never weaken a test to make it pass.** If `test_dedupe_survives_
   running_main_twice` or the scraper fixture tests start failing, the
   fix is in the implementation, not in loosening the assertion.

## Setup
1. python3 -m venv venv
2. source venv/bin/activate
3. pip install -r requirements.txt
4. cp .env.example .env   (fill in TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID)

## Common commands
- python main.py — run one full scrape -> store -> alert cycle
- pytest -v — run all tests
- pytest tests/test_scraper.py -v — scraper tests only
- pytest tests/test_db.py -v — dedupe/DB tests only

## Debugging checklist (before assuming it's a code bug)
1. Did DefiLlama change their page layout? Check "Known fragile points"
   above and re-run `pytest tests/test_scraper.py -v` against a fresh
   fixture pulled from the live page.
2. Is `.env` actually loaded? Test with:
   python3 -c "import os; from dotenv import load_dotenv; load_dotenv(); print(os.getenv('TELEGRAM_BOT_TOKEN'))"
3. Is `unlock_tracker.db` where you expect it, and is `alerted_at`
   actually getting set after a successful send? (`sqlite3
   unlock_tracker.db "SELECT * FROM unlock_events;"`)
4. Check `scrape_log` for a run of failures:
   `sqlite3 unlock_tracker.db "SELECT * FROM scrape_log ORDER BY id DESC LIMIT 10;"`

## Out of scope for v1
(Explicitly listing this keeps Claude Code from over-building.)
- No web dashboard — Telegram only.
- No multi-user support — single chat ID, hardcoded watchlist.
- No historical unlock analytics — just the next unlock per token.
- No retry/backoff logic yet (see "Known fragile points" re: 503s) —
  add only if transient failures prove common in practice.
