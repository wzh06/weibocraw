from __future__ import annotations

import asyncio
import logging
import random
from datetime import UTC, datetime
from typing import Any

from .browser import detect_block_page, launch_account_context, open_account_page
from .database import StateStore
from .errors import CrawlStopped
from .exporter import write_exports, write_report
from .filters import is_eligible_user
from .models import AccountConfig, CrawlReport, ParentPostConfig, Settings
from .parsers import parse_commenters, parse_post_cards, parse_user_profile

try:
    from playwright.async_api import TimeoutError as PlaywrightTimeoutError, async_playwright
except ModuleNotFoundError:  # pragma: no cover
    PlaywrightTimeoutError = TimeoutError
    async_playwright = None


async def _next_page(page: Any) -> bool:
    selectors = "a.next, a:has-text('下一页'), a:has-text('查看更多评论'), button:has-text('查看更多评论')"
    button = page.locator(selectors).last
    if await button.count() == 0:
        return False
    try:
        await button.click(timeout=2000)
        await page.wait_for_timeout(800)
        return True
    except Exception:
        return False


async def _advance_to_page(page: Any, target_page: int) -> None:
    for _ in range(max(0, target_page - 1)):
        if not await _next_page(page):
            break


async def collect_parent_commenters(page: Any, parent: ParentPostConfig, state: StateStore, settings: Settings) -> int:
    checkpoint = state.get_parent_checkpoint(parent.parent_id)
    await page.goto(parent.url, wait_until="domcontentloaded", timeout=settings.crawler.page_timeout_ms)
    await detect_block_page(page)
    page_number = checkpoint.page_number
    await _advance_to_page(page, page_number)
    total_new = 0
    seen_pages = 0
    while True:
        refs = await parse_commenters(page, parent.parent_id)
        for ref in refs:
            total_new += int(state.upsert_commenter(ref))
        seen_pages += 1
        state.set_parent_checkpoint(parent.parent_id, page_number + 1)
        if settings.crawler.max_comment_pages and seen_pages >= settings.crawler.max_comment_pages:
            break
        if not await _next_page(page):
            break
        page_number += 1
        await asyncio.sleep(random.uniform(settings.crawler.min_delay_seconds, settings.crawler.max_delay_seconds))
    state.mark_parent_finished(parent.parent_id)
    return total_new


async def collect_user_profile(page: Any, user_id: str, state: StateStore, settings: Settings) -> bool:
    user = state.get_user(user_id)
    if not user or not user.profile_url:
        return False
    await page.goto(user.profile_url, wait_until="domcontentloaded", timeout=settings.crawler.page_timeout_ms)
    await detect_block_page(page)
    profile = await parse_user_profile(page, user_id, user.profile_url)
    eligible = is_eligible_user(profile)
    state.upsert_user(profile.__class__(**{**profile.__dict__, "profile_status": "success" if eligible else "excluded"}))
    return eligible


async def collect_user_posts(page: Any, user: Any, state: StateStore, settings: Settings) -> int:
    checkpoint = state.get_post_checkpoint(user.user_id)
    await page.goto(user.profile_url, wait_until="domcontentloaded", timeout=settings.crawler.page_timeout_ms)
    await detect_block_page(page)
    page_number = checkpoint.page_number
    await _advance_to_page(page, page_number)
    inserted = 0
    seen_pages = 0
    while state.count_posts() < settings.target_content_count:
        posts = await parse_post_cards(page, user.user_id)
        for post in posts:
            inserted += int(state.upsert_post(post))
        seen_pages += 1
        state.set_post_checkpoint(user.user_id, page_number + 1)
        if settings.crawler.max_post_pages_per_user and seen_pages >= settings.crawler.max_post_pages_per_user:
            break
        if not await _next_page(page):
            break
        page_number += 1
        await asyncio.sleep(random.uniform(settings.crawler.min_delay_seconds, settings.crawler.max_delay_seconds))
    return inserted


async def crawl_device(settings: Settings) -> CrawlReport:
    if async_playwright is None:
        raise RuntimeError("Playwright is not installed. Run: uv sync && uv run playwright install chromium")
    state = StateStore(settings.state_db)
    for parent in settings.parent_posts:
        state.upsert_parent_post(parent)
    report = CrawlReport(started_at=datetime.now(UTC).isoformat(timespec="seconds"), target_content_count=settings.target_content_count)
    account_index = 0
    context = None
    page = None
    try:
        async with async_playwright() as playwright:
            while account_index < len(settings.accounts):
                account = settings.accounts[account_index]
                report.accounts_used.append(account.name)
                try:
                    context = await launch_account_context(playwright, account, settings)
                    page = await open_account_page(context, settings)
                    for parent in settings.parent_posts:
                        if state.is_parent_finished(parent.parent_id):
                            continue
                        await collect_parent_commenters(page, parent, state, settings)
                    for user_id in state.get_pending_user_ids():
                        try:
                            await collect_user_profile(page, user_id, state, settings)
                        except CrawlStopped:
                            raise
                    for user in state.get_eligible_users():
                        if state.count_posts() >= settings.target_content_count:
                            break
                        await collect_user_posts(page, user, state, settings)
                    report.reason = "target_reached" if state.count_posts() >= settings.target_content_count else "all_parent_posts_exhausted"
                    break
                except CrawlStopped as exc:
                    state.log_account_event(account.name, "blocked", str(exc))
                    print(f"账户 {account.name} 触发登录/验证/频控：{exc}")
                    if context:
                        await context.close()
                    context = None
                    choice = input("输入下一个账户编号继续，输入 q 停止：").strip()
                    if choice.lower() == "q":
                        report.reason = "manual_stop"
                        break
                    try:
                        selected = int(choice)
                        if selected < 0 or selected >= len(settings.accounts):
                            raise ValueError
                        account_index = selected
                    except ValueError:
                        account_index += 1
                except PlaywrightTimeoutError:
                    report.reason = "page_timeout"
                    break
            report.actual_content_count = state.count_posts()
            report.discovered_users = state.count_users()
            report.eligible_users = len(state.get_eligible_users())
    except KeyboardInterrupt:
        report.reason = "manual_stop"
    except Exception:
        logging.exception("crawl failed")
        report.reason = "unexpected_error"
    finally:
        if context:
            await context.close()
        report.actual_content_count = state.count_posts()
        report.discovered_users = state.count_users()
        report.eligible_users = len(state.get_eligible_users())
        write_exports(state, settings)
        write_report(report, settings)
        state.close()
    return report
