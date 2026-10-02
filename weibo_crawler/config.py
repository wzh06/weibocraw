from __future__ import annotations

from pathlib import Path
from typing import Any

try:
    import yaml
except ModuleNotFoundError:  # pragma: no cover
    yaml = None

from .errors import ConfigurationError
from .models import AccountConfig, CrawlerOptions, OutputConfig, ParentPostConfig, Settings


def _path(value: Any, base: Path) -> Path:
    result = Path(str(value))
    return result if result.is_absolute() else base / result


def _parse_parent_posts(raw: Any) -> list[ParentPostConfig]:
    result: list[ParentPostConfig] = []
    for item in raw or []:
        if isinstance(item, str):
            result.append(ParentPostConfig(parent_id=item, url=item))
        elif isinstance(item, dict):
            parent_id = str(item.get("id") or item.get("parent_id") or "").strip()
            url = str(item.get("url") or "").strip()
            if parent_id and url:
                result.append(ParentPostConfig(parent_id=parent_id, url=url))
    return result


def _parse_accounts(raw: Any, base: Path, legacy_profile: Any) -> list[AccountConfig]:
    result: list[AccountConfig] = []
    for item in raw or []:
        if isinstance(item, dict):
            name = str(item.get("name") or "").strip()
            profile_dir = item.get("profile_dir")
            if name and profile_dir:
                result.append(AccountConfig(name=name, profile_dir=_path(profile_dir, base)))
    if not result:
        result.append(AccountConfig("default", _path(legacy_profile or ".playwright/weibo-profile", base)))
    return result


def load_settings(path: Path) -> Settings:
    if yaml is None:
        raise RuntimeError("PyYAML is not installed. Run: uv sync")
    path = path.resolve()
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    base = path.parent
    legacy_keywords = [str(x).strip() for x in raw.get("keywords", []) if str(x).strip()]
    parent_posts = _parse_parent_posts(raw.get("parent_posts"))
    accounts = _parse_accounts(raw.get("accounts"), base, raw.get("profile_dir"))
    if not parent_posts:
        raise ConfigurationError("Configure at least one parent_posts entry")
    output_raw = raw.get("output") or {}
    output = OutputConfig(
        users_basic_csv=_path(output_raw.get("users_basic_csv", "data/users_basic.csv"), base),
        posts_content_csv=_path(output_raw.get("posts_content_csv", raw.get("output_csv", "data/posts_content.csv")), base),
        report_dir=_path(output_raw.get("report_dir", raw.get("report_dir", "data/reports")), base),
    )
    crawler_raw = raw.get("crawler") or {}
    minimum = float(crawler_raw.get("min_delay_seconds", raw.get("min_delay_seconds", 2.0)))
    maximum = float(crawler_raw.get("max_delay_seconds", raw.get("max_delay_seconds", 5.0)))
    options = CrawlerOptions(
        page_timeout_ms=int(crawler_raw.get("page_timeout_ms", raw.get("page_timeout_ms", 30000))),
        min_delay_seconds=minimum,
        max_delay_seconds=maximum,
        max_retries=int(crawler_raw.get("max_retries", 3)),
        max_comment_pages=int(crawler_raw.get("max_comment_pages", 0)),
        max_post_pages_per_user=int(crawler_raw.get("max_post_pages_per_user", 0)),
        headless=bool(crawler_raw.get("headless", raw.get("headless", False))),
    )
    target = int(raw.get("target_content_count", raw.get("target_count", 12000)))
    start_date = str(raw.get("start_date", ""))
    end_date = str(raw.get("end_date", ""))
    settings = Settings(
        device_id=str(raw.get("device_id", "local")),
        target_content_count=target,
        state_db=_path(raw.get("state_db", "data/state.sqlite3"), base),
        parent_posts=parent_posts,
        accounts=accounts,
        output=output,
        crawler=options,
        keywords=legacy_keywords,
        start_date=start_date,
        end_date=end_date,
        target_count=target,
        output_csv=output.posts_content_csv,
        profile_dir=accounts[0].profile_dir,
        report_dir=output.report_dir,
        min_delay_seconds=minimum,
        max_delay_seconds=maximum,
        page_timeout_ms=options.page_timeout_ms,
        headless=options.headless,
    )
    validate_config(settings)
    return settings


def validate_config(settings: Settings) -> None:
    if not settings.device_id.strip():
        raise ConfigurationError("device_id must not be empty")
    if settings.target_content_count < 1:
        raise ConfigurationError("target_content_count must be at least 1")
    if settings.crawler.min_delay_seconds < 0 or settings.crawler.max_delay_seconds < settings.crawler.min_delay_seconds:
        raise ConfigurationError("delay settings must satisfy 0 <= min <= max")
    if len({x.parent_id for x in settings.parent_posts}) != len(settings.parent_posts):
        raise ConfigurationError("parent_posts IDs must be unique")
    if len({x.name for x in settings.accounts}) != len(settings.accounts):
        raise ConfigurationError("account names must be unique")
