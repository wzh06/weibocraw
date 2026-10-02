from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

from .browser import login_account
from .config import load_settings
from .crawler import crawl_device
from .database import StateStore
from .merge import merge_databases


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Authenticated, resumable Weibo public-page collector")
    sub = parser.add_subparsers(dest="command", required=True)
    login = sub.add_parser("login")
    login.add_argument("--config", type=Path, default=Path("config.yaml"))
    login.add_argument("--account", default="all")
    crawl = sub.add_parser("crawl")
    crawl.add_argument("--config", type=Path, default=Path("config.yaml"))
    crawl.add_argument("--verbose", action="store_true")
    merge = sub.add_parser("merge-db")
    merge.add_argument("--output", type=Path, required=True)
    merge.add_argument("inputs", type=Path, nargs="+")
    export = sub.add_parser("export")
    export.add_argument("--config", type=Path, default=Path("config.yaml"))
    return parser


async def _login(settings, account_name: str) -> None:
    from playwright.async_api import async_playwright
    selected = settings.accounts if account_name == "all" else [x for x in settings.accounts if x.name == account_name]
    if not selected:
        raise ValueError(f"Unknown account: {account_name}")
    async with async_playwright() as playwright:
        for account in selected:
            await login_account(playwright, account, settings)


def main() -> int:
    args = build_parser().parse_args()
    try:
        if args.command == "merge-db":
            merge_databases(args.inputs, args.output)
            return 0
        settings = load_settings(args.config)
        if args.command == "login":
            asyncio.run(_login(settings, args.account))
            return 0
        if args.command == "export":
            state = StateStore(settings.state_db)
            try:
                state.export_users_basic_csv(settings.output.users_basic_csv)
                state.export_posts_content_csv(settings.output.posts_content_csv)
            finally:
                state.close()
            return 0
        logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
        report = asyncio.run(crawl_device(settings))
        logging.info("Finished: %s/%s (%s)", report.actual_content_count, report.target_content_count, report.reason)
        return 0 if report.actual_content_count >= report.target_content_count else 2
    except (OSError, ValueError, RuntimeError) as exc:
        logging.error("%s", exc)
        return 1


if __name__ == "__main__":
    sys.exit(main())
