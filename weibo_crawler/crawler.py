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
from .filters import is_eligible_user, parse_date
from .models import AccountConfig, CrawlReport, ParentPostConfig, Settings, UserRecord
from .parsers import (
    comment_payload_max_id,
    extract_mblog_id,
    extract_post_owner_id,
    fetch_build_comments,
    fetch_post_detail_id,
    fetch_profile_counts,
    mblogid_to_numeric,
    parse_api_posts,
    parse_comment_payload,
    parse_commenters,
    parse_post_cards,
    parse_user_profile,
)
from .selectors import COMMENT_NEXT_SELECTORS, COMMENT_USER_FALLBACK_SELECTORS, PROFILE_POSTS_ENDPOINT

try:
    from playwright.async_api import (
        Error as PlaywrightError,
        TimeoutError as PlaywrightTimeoutError,
        async_playwright,
    )
except ModuleNotFoundError:  # pragma: no cover
    PlaywrightTimeoutError = TimeoutError
    PlaywrightError = Exception
    async_playwright = None


async def _close_context(context: Any) -> None:
    """Close a context without masking an earlier browser/page failure."""
    if context is None:
        return
    try:
        await context.close()
    except PlaywrightError:
        # The browser may already have closed itself (for example after the
        # user closes the visible window).  Keep the original crawl error and
        # let the caller persist its checkpoint/report normally.
        logging.debug("browser context was already closed")


async def _next_page(page: Any) -> bool:
    button = page.locator(COMMENT_NEXT_SELECTORS).last
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


async def _settle_dynamic_page(page: Any, minimum_links: int = 2) -> None:
    """Allow Weibo's client-rendered comments/profile content to appear."""
    try:
        await page.wait_for_function(
            "([selector, minimum]) => document.querySelectorAll(selector).length >= minimum",
            [COMMENT_USER_FALLBACK_SELECTORS, minimum_links],
            timeout=5000,
        )
    except Exception:
        # A post can legitimately have fewer than ``minimum_links`` visible
        # users. Keep a short settle delay before parsing that page anyway.
        await page.wait_for_timeout(1500)


async def _settle_profile_page(page: Any) -> None:
    """Wait until the client-rendered profile replaces the loading shell."""
    try:
        await page.wait_for_function(
            "() => /全部微博\\s*[（(]\\s*\\d+/.test(document.body?.innerText || '') || /粉丝/.test(document.body?.innerText || '')",
            timeout=5000,
        )
    except Exception:
        await page.wait_for_timeout(1500)


async def _collect_parent_commenters_from_api(
    page: Any,
    parent: ParentPostConfig,
    state: StateStore,
    settings: Settings,
    *,
    numeric_id: str,
    owner_id: str,
) -> tuple[int, int, bool]:
    """Collect commenters via ``buildComments``.

    Returns ``(new_commenters, pages_completed, finished)``.  A transient API
    failure after at least one successful page leaves ``finished`` false so the
    checkpoint can be resumed on the next run instead of being discarded.
    """
    checkpoint = state.get_parent_checkpoint(parent.parent_id)
    page_number = checkpoint.page_number
    max_id = checkpoint.cursor or ""
    total_new = 0
    seen_pages = 0
    while True:
        payload = await fetch_build_comments(
            page,
            numeric_id=numeric_id,
            owner_id=owner_id,
            max_id=max_id,
        )
        if payload is None:
            # Transient/blocked request: keep the last successful checkpoint
            # and let the caller fall back or resume later.
            break
        refs = parse_comment_payload(payload, parent.parent_id)
        if not refs:
            # An empty first page means the API path is not usable here;
            # otherwise it is simply the end of the comment stream.
            if seen_pages == 0:
                break
            state.set_parent_checkpoint(parent.parent_id, page_number + seen_pages, "")
            return total_new, seen_pages, True
        for ref in refs:
            total_new += int(state.upsert_commenter(ref))
        seen_pages += 1
        next_max_id = comment_payload_max_id(payload)
        state.set_parent_checkpoint(parent.parent_id, page_number + seen_pages, next_max_id)
        if settings.crawler.max_comment_pages and seen_pages >= settings.crawler.max_comment_pages:
            return total_new, seen_pages, True
        if not next_max_id or next_max_id == max_id:
            return total_new, seen_pages, True
        max_id = next_max_id
        await asyncio.sleep(random.uniform(settings.crawler.min_delay_seconds, settings.crawler.max_delay_seconds))
    return total_new, seen_pages, False


async def _collect_parent_commenters_from_dom(
    page: Any,
    parent: ParentPostConfig,
    state: StateStore,
    settings: Settings,
) -> tuple[int, bool]:
    """Fallback DOM collection using the visible comment links."""
    checkpoint = state.get_parent_checkpoint(parent.parent_id)
    page_number = checkpoint.page_number
    await _advance_to_page(page, page_number)
    total_new = 0
    seen_pages = 0
    while True:
        refs = await parse_commenters(page, parent.parent_id)
        for ref in refs:
            total_new += int(state.upsert_commenter(ref))
        seen_pages += 1
        state.set_parent_checkpoint(parent.parent_id, page_number + seen_pages)
        if settings.crawler.max_comment_pages and seen_pages >= settings.crawler.max_comment_pages:
            return total_new, True
        if not await _next_page(page):
            return total_new, True
        page_number += 1
        await asyncio.sleep(random.uniform(settings.crawler.min_delay_seconds, settings.crawler.max_delay_seconds))


async def collect_parent_commenters(page: Any, parent: ParentPostConfig, state: StateStore, settings: Settings) -> int:
    await page.goto(parent.url, wait_until="domcontentloaded", timeout=settings.crawler.page_timeout_ms)
    await detect_block_page(page)

    total_new = 0
    owner_id = extract_post_owner_id(parent.url)
    mblog_id = extract_mblog_id(parent.url)
    if owner_id and mblog_id:
        numeric_id = await fetch_post_detail_id(page, mblog_id)
        if not numeric_id and mblog_id.isdigit():
            numeric_id = mblog_id
        if not numeric_id:
            numeric_id = mblogid_to_numeric(mblog_id)
        if numeric_id:
            api_total, api_pages, api_finished = await _collect_parent_commenters_from_api(
                page,
                parent,
                state,
                settings,
                numeric_id=numeric_id,
                owner_id=owner_id,
            )
            total_new += api_total
            logging.debug(
                "buildComments parent=%s users=%s pages=%s finished=%s",
                parent.parent_id,
                api_total,
                api_pages,
                api_finished,
            )
            if api_pages or api_finished:
                if api_finished:
                    state.mark_parent_finished(parent.parent_id)
                return total_new

    await _settle_dynamic_page(page)
    dom_total, dom_finished = await _collect_parent_commenters_from_dom(page, parent, state, settings)
    total_new += dom_total
    if dom_finished:
        state.mark_parent_finished(parent.parent_id)
    return total_new


async def collect_user_profile(page: Any, user_id: str, state: StateStore, settings: Settings) -> bool:
    user = state.get_user(user_id)
    if not user or not user.profile_url:
        return False
    await page.goto(user.profile_url, wait_until="domcontentloaded", timeout=settings.crawler.page_timeout_ms)
    await detect_block_page(page)
    posts_count = followers_count = None
    if settings.max_posts_count is not None and settings.max_posts_count > 0:
        posts_count, followers_count = await fetch_profile_counts(page, user_id)
        if posts_count is not None and posts_count > settings.max_posts_count:
            logging.debug(
                "skip profile user=%s posts_count=%s > max_posts_count=%s",
                user_id,
                posts_count,
                settings.max_posts_count,
            )
            state.upsert_user(UserRecord(
                user_id=user_id,
                profile_url=user.profile_url,
                posts_count=posts_count,
                followers_count=followers_count,
                profile_fetched_at=datetime.now(UTC).isoformat(timespec="seconds"),
                profile_status="excluded",
            ))
            return False
    await _settle_profile_page(page)
    profile = await parse_user_profile(
        page,
        user_id,
        user.profile_url,
        max_posts_count=settings.max_posts_count,
        posts_count=posts_count,
        followers_count=followers_count,
    )
    eligibility_date = parse_date(settings.eligibility_as_of) or datetime.now(UTC).date()
    eligible = is_eligible_user(profile, eligibility_date, max_posts_count=settings.max_posts_count)
    logging.debug(
        "profile parsed user=%s url=%s title=%r registered_at=%r posts_count=%r followers_count=%r eligible=%s",
        user_id,
        page.url,
        await page.title(),
        profile.registered_at,
        profile.posts_count,
        profile.followers_count,
        eligible,
    )
    state.upsert_user(profile.__class__(**{**profile.__dict__, "profile_status": "success" if eligible else "excluded"}))
    return eligible


async def collect_user_posts(page: Any, user: Any, state: StateStore, settings: Settings) -> int:
    checkpoint = state.get_post_checkpoint(user.user_id)
    await page.goto(user.profile_url, wait_until="domcontentloaded", timeout=settings.crawler.page_timeout_ms)
    await detect_block_page(page)
    await _settle_profile_page(page)
    page_number = checkpoint.page_number
    inserted = 0
    seen_pages = 0
    while state.count_posts() < settings.target_content_count:
        endpoint = PROFILE_POSTS_ENDPOINT.format(user_id=user.user_id, page_number=page_number)
        posts = []
        try:
            payload = await page.evaluate(
                """async (endpoint) => {
                    const response = await fetch(endpoint, {credentials: 'include'});
                    return response.ok ? await response.json() : {};
                }""",
                endpoint,
            )
            posts = parse_api_posts(payload, user.user_id)
        except Exception:
            logging.debug("profile feed API unavailable for user=%s page=%s", user.user_id, page_number)
        if not posts:
            posts = await parse_post_cards(page, user.user_id)
        for post in posts:
            inserted += int(state.upsert_post(post))
        seen_pages += 1
        state.set_post_checkpoint(user.user_id, page_number + 1)
        if not posts:
            break
        if settings.crawler.max_post_pages_per_user and seen_pages >= settings.crawler.max_post_pages_per_user:
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
                    if state.count_posts() >= settings.target_content_count:
                        report.reason = "target_reached"
                    elif not state.get_eligible_users():
                        report.reason = "no_eligible_users"
                    else:
                        report.reason = "all_parent_posts_exhausted"
                    break
                except CrawlStopped as exc:
                    state.log_account_event(account.name, "blocked", str(exc))
                    print(f"账户 {account.name} 触发登录/验证/频控：{exc}")
                    if context:
                        await _close_context(context)
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
            await _close_context(context)
        report.actual_content_count = state.count_posts()
        report.discovered_users = state.count_users()
        report.eligible_users = len(state.get_eligible_users())
        write_exports(state, settings)
        write_report(report, settings)
        state.close()
    return report
