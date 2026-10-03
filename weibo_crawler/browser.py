from __future__ import annotations

import asyncio
from typing import Any

from .errors import CrawlStopped
from .models import AccountConfig, Settings

try:
    from playwright.async_api import BrowserContext, Page, TimeoutError as PlaywrightTimeoutError
except ModuleNotFoundError:  # pragma: no cover
    BrowserContext = Any
    Page = Any
    PlaywrightTimeoutError = TimeoutError


LOGIN_URL = "https://weibo.com/login.php"


async def launch_account_context(playwright: Any, account: AccountConfig, settings: Settings) -> BrowserContext:
    account.profile_dir.mkdir(parents=True, exist_ok=True)
    return await playwright.chromium.launch_persistent_context(
        user_data_dir=str(account.profile_dir),
        headless=settings.crawler.headless,
        viewport={"width": 1440, "height": 1000},
        locale="zh-CN",
    )


async def open_account_page(context: BrowserContext, settings: Settings) -> Page:
    page = context.pages[0] if context.pages else await context.new_page()
    page.set_default_timeout(settings.crawler.page_timeout_ms)
    return page


async def login_account(playwright: Any, account: AccountConfig, settings: Settings) -> None:
    context = await launch_account_context(playwright, account, settings)
    try:
        page = await open_account_page(context, settings)
        await page.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=settings.crawler.page_timeout_ms)
        print(f"[{account.name}] 浏览器已打开，请手动登录，完成后回到终端按 Enter。")
        await asyncio.to_thread(input)
    finally:
        await context.close()


async def detect_block_page(page: Page) -> None:
    try:
        current_url = (page.url or "").lower()
        title = (await page.title()).lower()
        body = (await page.locator("body").inner_text(timeout=3000)).lower()
    except PlaywrightTimeoutError:
        return
    indicators = ("验证码", "安全验证", "访问频繁", "账号异常", "login.php")
    if "login.php" in current_url or any(item in title or item in body for item in indicators):
        raise CrawlStopped("页面要求登录、验证或降低访问频率")
