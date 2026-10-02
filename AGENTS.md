# AGENTS.md

## Project purpose

This repository contains a local, authenticated Weibo collector. It is intended
to read public pages available to the user's normal logged-in browser session,
collect comment participants from configured parent posts, and then collect
those participants' public profile and post data.

## Operating boundaries

- Do not bypass login, CAPTCHA, access controls, privacy settings, or rate
  limits.
- The user completes login in the visible browser and presses Enter in the
  terminal before collection starts.
- Stop and persist state when Weibo shows a verification, login, or rate-limit
  page. The next run must resume from the SQLite checkpoints.
- Collect only fields configured by the user and only from pages the account is
  allowed to view. Do not infer sensitive attributes that are not displayed.
- Keep `config.yaml`, browser profiles, SQLite databases, and generated exports
  under ignored local paths.

## Architecture

- `crawler.py` is a compatibility executable entry point. The implementation is
  organized under `weibo_crawler/`: `config.py` parses YAML, `models.py` holds
  records, `browser.py` owns Playwright profiles/login, `parsers.py` extracts
  page fields, `database.py` owns SQLite state, `filters.py` applies eligibility
  rules, `crawler.py` orchestrates the queues, `exporter.py` writes CSV/report
  files, and `merge.py` combines device databases.
- SQLite is the source of truth. Inserts are idempotent and all work queues
  have checkpoints so an interrupted run can continue.
- Parent posts are configured as work items. Multiple machines may use the same
  database on a shared filesystem, or separate databases with disjoint parent
  post assignments; use a unique `device_id` for each worker.
- Accounts are configured as named persistent Playwright profiles. A worker
  rotates to the next account after a manual-stop condition and resumes from
  the saved queue position.
- `data/users_basic.csv` and `data/posts_content.csv` are the two stable export
  templates requested by the project.

## Development commands

Use `uv` for dependency and environment management:

```bash
uv sync
uv run playwright install chromium
uv run python crawler.py login --config config.yaml --account all
uv run python crawler.py crawl --config config.yaml
uv run python crawler.py export --config config.yaml
uv run python crawler.py merge-db --output data/merged.sqlite3 data/state-mac.sqlite3 data/state-linux.sqlite3
uv run python -m unittest discover -s tests -v
```

Do not commit local data, browser profiles, or `config.yaml`.

## Change checklist

1. Preserve resumability and deduplication when changing schemas or parsers.
2. Add or update focused tests for pure parsing, filtering, and checkpoint
   behavior.
3. Run the unit test suite and `python -m py_compile crawler.py` before
   delivering changes.
4. Keep selectors and URL patterns isolated so Weibo markup changes are easy to
   update.
