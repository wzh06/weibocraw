from __future__ import annotations

import json
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

from .database import StateStore
from .models import CrawlReport, Settings


def write_exports(state: StateStore, settings: Settings) -> None:
    state.export_users_basic_csv(settings.output.users_basic_csv)
    state.export_posts_content_csv(settings.output.posts_content_csv)


def write_report(report: CrawlReport, settings: Settings) -> Path:
    settings.output.report_dir.mkdir(parents=True, exist_ok=True)
    report.finished_at = datetime.now(UTC).isoformat(timespec="seconds")
    path = settings.output.report_dir / f"run-{datetime.now().strftime('%Y%m%d-%H%M%S')}.json"
    path.write_text(json.dumps(asdict(report), ensure_ascii=False, indent=2), encoding="utf-8")
    return path
