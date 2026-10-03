from __future__ import annotations

import re
from datetime import date, datetime, timedelta

from .models import UserRecord


def parse_date(value: str) -> date | None:
    value = (value or "").strip()
    match = re.search(r"(19|20)\d{2}\D(\d{1,2})(?:\D(\d{1,2}))?", value)
    if not match:
        return None
    try:
        return date(int(match.group(0)[:4]), int(match.group(2)), int(match.group(3) or 1))
    except ValueError:
        return None


def is_registered_at_least_one_year(registered_at: str, today: date | None = None) -> bool:
    value = parse_date(registered_at)
    return value is not None and value <= (today or date.today()) - timedelta(days=365)


def is_eligible_user(
    user: UserRecord,
    today: date | None = None,
    *,
    max_posts_count: int | None = None,
) -> bool:
    return (
        is_registered_at_least_one_year(user.registered_at, today)
        and user.posts_count is not None and user.posts_count >= 50
        and (max_posts_count is None or max_posts_count <= 0 or user.posts_count <= max_posts_count)
        and user.followers_count is not None and user.followers_count <= 5000
    )
