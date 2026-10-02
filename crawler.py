#!/usr/bin/env python3
"""Collect public Weibo keyword search results using an authenticated local browser.

The program deliberately does not solve CAPTCHAs, bypass access controls, or use
undocumented credentials. It only reads result pages available to the user's
normal logged-in browser session.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
import logging
import random
import re
import sqlite3
import sys
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote_plus

try:
    import yaml
except ModuleNotFoundError:  # Allows pure helper tests to run before setup.
    yaml = None
YAML_ERROR = getattr(yaml, "YAMLError", RuntimeError)

try:
    from playwright.async_api import BrowserContext, Locator, Page, TimeoutError as PlaywrightTimeoutError, async_playwright
except ModuleNotFoundError:  # pragma: no cover - exercised only before dependency setup.
    BrowserContext = Any
    Locator = Any
    Page = Any
    PlaywrightTimeoutError = TimeoutError
    async_playwright = None


CSV_FIELDS = [
    "weibo_id",
    "text",
    "author",
    "published_at",
    "url",
    "reposts_count",
    "comments_count",
    "likes_count",
    "location",
    "is_original",
    "fetched_at",
    "keyword",
]

SEARCH_URL = (
    "https://s.weibo.com/weibo?q={query}&typeall=1&suball=1"
    "&timescope=custom:{start_date}-0:{end_date}-23&page={page}"
)
LOGIN_URL = "https://weibo.com/login.php"


class CrawlStopped(RuntimeError):
    """Raised when the site requires an intentional user action."""


@dataclass(frozen=True)
class Settings:
    keywords: list[str]
    start_date: str
    end_date: str
    target_count: int
    output_csv: Path
    state_db: Path
    profile_dir: Path
    report_dir: Path
    min_delay_seconds: float
    max_delay_seconds: float
    page_timeout_ms: int
    headless: bool


@dataclass
class Post:
    weibo_id: str
    text: str
    author: str
    published_at: str
    url: str
    reposts_count: int | None
    comments_count: int | None
    likes_count: int | None
    location: str
    is_original: bool
    fetched_at: str
    keyword: str


def load_settings(path: Path) -> Settings:
    if yaml is None:
        raise RuntimeError("PyYAML is not installed. Run: pip install -r requirements.txt")
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    required = ("keywords", "start_date", "end_date")
    missing = [key for key in required if not raw.get(key)]
    if missing:
        raise ValueError(f"Missing required config values: {', '.join(missing)}")

    keywords = [str(item).strip() for item in raw["keywords"] if str(item).strip()]
    if not keywords:
        raise ValueError("keywords must contain at least one non-empty keyword")

    start = datetime.strptime(str(raw["start_date"]), "%Y-%m-%d").date()
    end = datetime.strptime(str(raw["end_date"]), "%Y-%m-%d").date()
    if start > end:
        raise ValueError("start_date must be on or before end_date")

    target = int(raw.get("target_count", 10000))
    if target < 1:
        raise ValueError("target_count must be at least 1")

    minimum = float(raw.get("min_delay_seconds", 2.0))
    maximum = float(raw.get("max_delay_seconds", 5.0))
    if minimum < 0 or maximum < minimum:
        raise ValueError("delay settings must satisfy 0 <= min_delay_seconds <= max_delay_seconds")

    return Settings(
        keywords=keywords,
        start_date=start.isoformat(),
        end_date=end.isoformat(),
        target_count=target,
        output_csv=Path(raw.get("output_csv", "data/weibo_posts.csv")),
        state_db=Path(raw.get("state_db", "data/weibo_state.sqlite3")),
        profile_dir=Path(raw.get("profile_dir", ".playwright/weibo-profile")),
        report_dir=Path(raw.get("report_dir", "data/reports")),
        min_delay_seconds=minimum,
        max_delay_seconds=maximum,
        page_timeout_ms=int(raw.get("page_timeout_ms", 30000)),
        headless=bool(raw.get("headless", False)),
    )


def normalize_count(value: str) -> int | None:
    """Convert displayed interaction count such as '1.2万' to an integer."""
    value = re.sub(r"\s+", "", value or "")
    found = re.search(r"(\d+(?:\.\d+)?)(万|亿)?", value)
    if not found:
        return None
    number = float(found.group(1))
    multiplier = {"万": 10_000, "亿": 100_000_000}.get(found.group(2), 1)
    return int(number * multiplier)


def clean_text(value: str) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


def extract_location(source_text: str) -> str:
    """Return whether Weibo shows an explicitly labelled publication location.

    The generic Chinese source label usually denotes a posting client rather
    than a physical location, so it is intentionally not treated as location.
    The return value is the CSV-friendly string ``"是"`` or ``"否"``.
    """
    cleaned = clean_text(source_text)
    patterns = (
        r"(?:发布于|位置[：:]?|定位于)\s*([^\s|·]+)",
        r"\[([^\]]{1,40})\]$",
    )
    for pattern in patterns:
        match = re.search(pattern, cleaned)
        if match:
            return "是"
    return "否"


class StateStore:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(path)
        self.conn.execute(
            """CREATE TABLE IF NOT EXISTS posts (
                weibo_id TEXT PRIMARY KEY,
                row_json TEXT NOT NULL,
                inserted_at TEXT NOT NULL
            )"""
        )
        self.conn.execute(
            """CREATE TABLE IF NOT EXISTS checkpoints (
                keyword TEXT PRIMARY KEY,
                next_page INTEGER NOT NULL,
                updated_at TEXT NOT NULL
            )"""
        )
        self.conn.commit()

    def count(self) -> int:
        return int(self.conn.execute("SELECT COUNT(*) FROM posts").fetchone()[0])

    def insert(self, post: Post) -> bool:
        cursor = self.conn.execute(
            "INSERT OR IGNORE INTO posts (weibo_id, row_json, inserted_at) VALUES (?, ?, ?)",
            (post.weibo_id, json.dumps(asdict(post), ensure_ascii=False), post.fetched_at),
        )
        self.conn.commit()
        return cursor.rowcount == 1

    def get_page(self, keyword: str) -> int:
        row = self.conn.execute("SELECT next_page FROM checkpoints WHERE keyword = ?", (keyword,)).fetchone()
        return int(row[0]) if row else 1

    def set_page(self, keyword: str, next_page: int) -> None:
        self.conn.execute(
            """INSERT INTO checkpoints(keyword, next_page, updated_at) VALUES (?, ?, ?)
            ON CONFLICT(keyword) DO UPDATE SET next_page=excluded.next_page, updated_at=excluded.updated_at""",
            (keyword, next_page, utc_now()),
        )
        self.conn.commit()

    def export_csv(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        rows = self.conn.execute("SELECT row_json FROM posts ORDER BY inserted_at, weibo_id").fetchall()
        with path.open("w", newline="", encoding="utf-8-sig") as handle:
            writer = csv.DictWriter(handle, fieldnames=CSV_FIELDS)
            writer.writeheader()
            for (row_json,) in rows:
                writer.writerow(json.loads(row_json))

    def close(self) -> None:
        self.conn.close()


def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


async def text_or_empty(locator: Locator) -> str:
    try:
        return clean_text(await locator.inner_text(timeout=1500))
    except PlaywrightTimeoutError:
        return ""


async def first_attr(locator: Locator, attribute: str) -> str:
    try:
        return (await locator.get_attribute(attribute, timeout=1500)) or ""
    except PlaywrightTimeoutError:
        return ""


def in_configured_dates(published_at: str, settings: Settings) -> bool:
    """Return true when an ISO-like date is inside the requested inclusive range.

    Weibo can display relative dates; those are retained because the page does not
    expose enough information to apply a reliable local-date comparison.
    """
    match = re.search(r"(20\d{2}-\d{2}-\d{2})", published_at)
    return not match or settings.start_date <= match.group(1) <= settings.end_date


async def detect_block(page: Page) -> None:
    title = (await page.title()).lower()
    content = (await page.locator("body").inner_text(timeout=3000)).lower()
    indicators = ("验证码", "安全验证", "访问频繁", "login")
    if any(token in title or token in content for token in indicators):
        raise CrawlStopped("Login, verification, or rate-limit page detected. Complete any required action manually, then resume.")


async def parse_cards(page: Page, keyword: str, settings: Settings) -> list[Post]:
    cards = page.locator("div.card-wrap[action-type='feed_list_item'], div.card-wrap[mid]")
    count = await cards.count()
    posts: list[Post] = []
    for index in range(count):
        card = cards.nth(index)
        weibo_id = (await card.get_attribute("mid")) or (await card.get_attribute("data-mid")) or ""
        if not weibo_id:
            continue

        text = await text_or_empty(card.locator("p.txt").first)
        author_link = card.locator("a[nick-name], a.name").first
        author = (await first_attr(author_link, "nick-name")) or await text_or_empty(author_link)
        source_line = await text_or_empty(card.locator("p.from").first)
        time_link = card.locator("p.from a").first
        published_at = await text_or_empty(time_link)
        location = extract_location(source_line)
        url = await first_attr(time_link, "href")
        if url.startswith("//"):
            url = f"https:{url}"
        elif url.startswith("/"):
            url = f"https://weibo.com{url}"

        action_text = await text_or_empty(card.locator("div.card-act").first)
        counts = re.findall(r"(?:转发|评论|赞|点赞)\s*(\d+(?:\.\d+)?(?:万|亿)?)", action_text)
        reposts = normalize_count(counts[0]) if len(counts) > 0 else None
        comments = normalize_count(counts[1]) if len(counts) > 1 else None
        likes = normalize_count(counts[2]) if len(counts) > 2 else None
        forward_content = card.locator("[node-type='feed_list_forwardContent'], div.card-comment")
        is_original = await forward_content.count() == 0

        if text and in_configured_dates(published_at, settings):
            posts.append(
                Post(
                    weibo_id=weibo_id,
                    text=text,
                    author=author,
                    published_at=published_at,
                    url=url,
                    reposts_count=reposts,
                    comments_count=comments,
                    likes_count=likes,
                    location=location,
                    is_original=is_original,
                    fetched_at=utc_now(),
                    keyword=keyword,
                )
            )
    return posts


async def launch_context(playwright: Any, settings: Settings, headless: bool) -> BrowserContext:
    if async_playwright is None:
        raise RuntimeError("Playwright is not installed. Run: pip install -r requirements.txt && playwright install chromium")
    settings.profile_dir.mkdir(parents=True, exist_ok=True)
    return await playwright.chromium.launch_persistent_context(
        user_data_dir=str(settings.profile_dir),
        headless=headless,
        viewport={"width": 1440, "height": 1000},
        locale="zh-CN",
    )


async def login(settings: Settings) -> None:
    async with async_playwright() as playwright:
        context = await launch_context(playwright, settings, headless=False)
        page = context.pages[0] if context.pages else await context.new_page()
        await page.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=settings.page_timeout_ms)
        print("A browser window is open. Log in manually, then return here and press Enter.")
        await asyncio.to_thread(input)
        await context.close()


async def crawl(settings: Settings) -> dict[str, Any]:
    state = StateStore(settings.state_db)
    reason = "target_reached"
    pages_visited = 0
    try:
        async with async_playwright() as playwright:
            context = await launch_context(playwright, settings, settings.headless)
            page = context.pages[0] if context.pages else await context.new_page()
            page.set_default_timeout(settings.page_timeout_ms)

            for keyword in settings.keywords:
                page_number = state.get_page(keyword)
                while state.count() < settings.target_count:
                    url = SEARCH_URL.format(
                        query=quote_plus(keyword),
                        start_date=settings.start_date,
                        end_date=settings.end_date,
                        page=page_number,
                    )
                    logging.info("Keyword=%s page=%s", keyword, page_number)
                    try:
                        await page.goto(url, wait_until="domcontentloaded", timeout=settings.page_timeout_ms)
                        await detect_block(page)
                        await page.wait_for_selector("div.card-wrap", timeout=settings.page_timeout_ms)
                    except CrawlStopped:
                        raise
                    except PlaywrightTimeoutError:
                        reason = "page_timeout_or_no_results"
                        logging.warning("No usable result page for keyword=%s page=%s", keyword, page_number)
                        break

                    posts = await parse_cards(page, keyword, settings)
                    pages_visited += 1
                    inserted = sum(state.insert(post) for post in posts)
                    logging.info("Parsed=%s inserted=%s total=%s", len(posts), inserted, state.count())
                    state.set_page(keyword, page_number + 1)

                    next_button = page.locator("a.next").first
                    if await next_button.count() == 0 or inserted == 0:
                        reason = "no_next_page_or_no_new_posts"
                        break

                    page_number += 1
                    await asyncio.sleep(random.uniform(settings.min_delay_seconds, settings.max_delay_seconds))

                if state.count() >= settings.target_count:
                    break

            if state.count() < settings.target_count and reason == "target_reached":
                reason = "all_keywords_exhausted"
            await context.close()
    except CrawlStopped as exc:
        reason = f"stopped_for_manual_action: {exc}"
        logging.error("%s", exc)
    finally:
        state.export_csv(settings.output_csv)
        report = {
            "finished_at": utc_now(),
            "actual_count": state.count(),
            "target_count": settings.target_count,
            "pages_visited": pages_visited,
            "reason": reason,
            "output_csv": str(settings.output_csv),
        }
        settings.report_dir.mkdir(parents=True, exist_ok=True)
        report_path = settings.report_dir / f"run-{datetime.now().strftime('%Y%m%d-%H%M%S')}.json"
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        state.close()
    return report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=Path("config.yaml"), help="Path to YAML configuration")
    parser.add_argument("--login", action="store_true", help="Open browser for one-time manual login")
    parser.add_argument("--verbose", action="store_true", help="Enable detailed logging")
    return parser


async def main() -> int:
    args = build_parser().parse_args()
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    try:
        settings = load_settings(args.config)
        if args.login:
            await login(settings)
            return 0
        report = await crawl(settings)
        logging.info("Finished: %s/%s records (%s)", report["actual_count"], report["target_count"], report["reason"])
        return 0 if report["actual_count"] >= report["target_count"] else 2
    except (OSError, ValueError, RuntimeError, YAML_ERROR) as exc:
        logging.error("Configuration error: %s", exc)
        return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
