# Repository Guidelines

## Project Structure

This is a Python 3.11+ Weibo collector using Playwright. `crawler.py` is the
backward-compatible executable entry point; the implementation lives in
`weibo_crawler/`:

- `config.py` loads YAML settings and `models.py` defines records.
- `browser.py` manages persistent browser profiles and user login.
- `parsers.py` and `selectors.py` isolate page extraction and Weibo selectors.
- `crawler.py` coordinates collection queues; `database.py` stores checkpoints
  and deduplicated records; `filters.py` applies eligibility rules.
- `exporter.py` writes CSV/report output and `merge.py` combines device databases.
- `tests/` contains unit tests for parsing, filtering, database, and checkpoint behavior.

Keep `config.yaml`, browser profiles, SQLite state, exports, and other local
runtime data under ignored paths. Use `config.example.yaml` as the template.

## Development Commands

```bash
uv sync
uv run playwright install chromium
uv run python crawler.py login --config config.yaml --account all
uv run python crawler.py crawl --config config.yaml
uv run python crawler.py export --config config.yaml
uv run python -m unittest discover -s tests -v
python3 -m py_compile crawler.py
```

Use `merge-db` to combine separate worker databases, for example:
`uv run python crawler.py merge-db --output data/merged.sqlite3 data/state-a.sqlite3 data/state-b.sqlite3`.

## Coding and Testing Conventions

Use four-space indentation, type hints, and focused functions. Name modules and
functions in `snake_case`, classes in `PascalCase`, and constants in
`UPPER_SNAKE_CASE`. Keep selectors and URL patterns centralized so markup
changes do not spread through the crawler. Add focused `unittest` cases for
pure parsing, filtering, deduplication, and resumability; test methods should
be named `test_<behavior>`.

## Commits and Pull Requests

Use short, action-oriented commit subjects consistent with the existing history
(for example, `add linux support`). Pull requests should describe the behavior
change, affected commands or data formats, and validation performed. Include
tests for parser or schema changes and call out any migration or configuration
impact.

## Safety and Configuration

Only collect public fields visible to the authenticated account. Never bypass
login, CAPTCHA, access controls, privacy settings, or rate limits. If Weibo
shows verification, login, or rate limiting, stop and preserve SQLite state so
the next run can resume safely.
