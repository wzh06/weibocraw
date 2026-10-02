#!/usr/bin/env python3
"""Compatibility entry point for the refactored Weibo collector."""

from __future__ import annotations

import re
import sys

from weibo_crawler.cli import main
from weibo_crawler.models import Settings
from weibo_crawler.parsers import clean_text, normalize_count
from weibo_crawler.parsers import extract_location as _extract_location_value


def extract_location(source_text: str) -> str:
    """Legacy helper: return ``是`` when an explicit location is present."""
    return "是" if _extract_location_value(source_text) else "否"


def in_configured_dates(published_at: str, settings: Settings) -> bool:
    match = re.search(r"(20\d{2}-\d{2}-\d{2})", published_at or "")
    if not match or not settings.start_date or not settings.end_date:
        return True
    return settings.start_date <= match.group(1) <= settings.end_date


if __name__ == "__main__":
    sys.exit(main())
