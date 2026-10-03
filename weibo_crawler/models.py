from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class AccountConfig:
    name: str
    profile_dir: Path


@dataclass(frozen=True)
class ParentPostConfig:
    parent_id: str
    url: str


@dataclass(frozen=True)
class OutputConfig:
    users_basic_csv: Path = Path("data/users_basic.csv")
    posts_content_csv: Path = Path("data/posts_content.csv")
    report_dir: Path = Path("data/reports")


@dataclass(frozen=True)
class CrawlerOptions:
    page_timeout_ms: int = 30000
    min_delay_seconds: float = 2.0
    max_delay_seconds: float = 5.0
    max_retries: int = 3
    max_comment_pages: int = 0
    max_post_pages_per_user: int = 0
    headless: bool = False


@dataclass(frozen=True)
class UserRecord:
    user_id: str
    profile_url: str = ""
    screen_name: str = ""
    gender: str = ""
    birth_date: str = ""
    education: str = ""
    registered_at: str = ""
    posts_count: int | None = None
    followers_count: int | None = None
    profile_fetched_at: str = ""
    profile_status: str = "pending"
    discovered_from: str = ""


@dataclass(frozen=True)
class PostRecord:
    post_id: str
    user_id: str
    text: str
    location: str = ""
    published_at: str = ""
    is_original: bool | None = None
    url: str = ""
    fetched_at: str = ""
    source_parent_id: str = ""


@dataclass(frozen=True)
class CommenterRef:
    parent_id: str
    user_id: str
    profile_url: str = ""
    screen_name: str = ""


@dataclass(frozen=True)
class Checkpoint:
    scope: str
    cursor: str = ""
    page_number: int = 1
    updated_at: str = ""


@dataclass(frozen=True)
class Settings:
    device_id: str = "local"
    target_content_count: int = 12000
    state_db: Path = Path("data/state.sqlite3")
    parent_posts: list[ParentPostConfig] = field(default_factory=list)
    accounts: list[AccountConfig] = field(default_factory=list)
    output: OutputConfig = field(default_factory=OutputConfig)
    crawler: CrawlerOptions = field(default_factory=CrawlerOptions)
    # Legacy fields remain available for existing helper tests and old configs.
    keywords: list[str] = field(default_factory=list)
    start_date: str = ""
    end_date: str = ""
    target_count: int = 12000
    output_csv: Path = Path("data/weibo_posts.csv")
    profile_dir: Path = Path(".playwright/weibo-profile")
    report_dir: Path = Path("data/reports")
    min_delay_seconds: float = 2.0
    max_delay_seconds: float = 5.0
    page_timeout_ms: int = 30000
    headless: bool = False
    # Eligibility is evaluated against this date so reruns are reproducible.
    eligibility_as_of: str = "2026-10-01"


@dataclass
class CrawlReport:
    started_at: str
    finished_at: str = ""
    target_content_count: int = 0
    actual_content_count: int = 0
    discovered_users: int = 0
    eligible_users: int = 0
    parent_posts_finished: int = 0
    accounts_used: list[str] = field(default_factory=list)
    reason: str = ""
    pages_visited: int = 0
