# Weibo keyword collector

This is a local Python + Playwright collector for public Weibo keyword search
results visible to your normal logged-in account. It does **not** bypass
logins, verification challenges, rate limits, or access controls.

## Setup

    python3 -m venv .venv
    source .venv/bin/activate
    pip install -r requirements.txt
    playwright install chromium
    cp config.example.yaml config.yaml

Edit config.yaml to add real keywords and the inclusive start_date and end_date.
Keep config.yaml, data/, and .playwright/ private: they are ignored by Git
because they can contain local collection state and browser data.

## Login and run

    python crawler.py --login
    python crawler.py --config config.yaml

The login command opens a normal browser window. Complete login yourself, then
press Enter in the terminal. The run command resumes from its SQLite checkpoint
and writes all unique records to the configured CSV path.

If the requested date range and keywords yield fewer than target_count, the
script leaves the partial CSV in place and writes a JSON report to data/reports.
It deliberately does not expand the time range or add queries.

## CSV columns

weibo_id, text, author, published_at, url, reposts_count, comments_count,
likes_count, location, is_original, fetched_at, and keyword.

location records only whether Weibo displays an explicit publication location:
`是` means a location label was detected and `否` means it was not. It does
not contain the specific place name. is_original is true for a post without a
forwarded-post block and false for a detected repost; it does not infer text
ownership or truthfulness.

## Notes

- Search-page markup can change. If no cards are collected, inspect the current
  public page structure and update the parsing selectors in parse_cards.
- Run at a conservative rate and only collect information you are permitted to
  access and process under Weibo's terms and applicable law.
