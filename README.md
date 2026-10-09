# Token Unlock Tracker

Personal tool that watches token unlock schedules on DefiLlama and
sends a Telegram alert when a tracked token's next unlock is coming up.

See `CLAUDE.md` for the full engineering rules, architecture, data
source details, and the self-improvement loop this project follows.

## Quick start

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # fill in TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
python main.py
```

Add tokens to track by editing the `WATCHLIST` list in `config.py`.

Run the tests with `pytest -v` — all 29 should pass before you trust
any change to `scraper.py` or `db.py`.
