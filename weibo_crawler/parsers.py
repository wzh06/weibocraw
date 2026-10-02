from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any
from urllib.parse import parse_qs, urlparse

from .models import CommenterRef, PostRecord, UserRecord
from .selectors import COMMENT_USER_FALLBACK_SELECTORS, COMMENT_USER_SELECTORS, POST_CARD_SELECTORS


def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def clean_text(value: str) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


def normalize_count(value: str) -> int | None:
    value = re.sub(r"\s+", "", value or "")
    match = re.search(r"(\d+(?:\.\d+)?)(万|亿)?", value)
    if not match:
        return None
    return int(float(match.group(1)) * {"万": 10_000, "亿": 100_000_000}.get(match.group(2), 1))


def extract_location(source_text: str) -> str:
    cleaned = clean_text(source_text)
    for pattern in (r"(?:发布于|位置[：:]?|定位于)\s*([^\s|·]+)", r"\[([^\]]{1,40})\]$"):
        match = re.search(pattern, cleaned)
        if match:
            return match.group(1).strip()
    return ""


def extract_user_id(url: str) -> str | None:
    parsed = urlparse(url or "")
    query_id = parse_qs(parsed.query).get("uid", [None])[0]
    if query_id:
        return str(query_id)
    match = re.search(r"/(?:u|profile)/([A-Za-z0-9_-]+)", parsed.path)
    return match.group(1) if match else None


def extract_post_id(url: str = "", element: Any | None = None) -> str | None:
    if element is not None:
        for attribute in ("mid", "data-mid", "id"):
            value = getattr(element, "get", lambda *_: None)(attribute)
            if value:
                match = re.search(r"(\d{5,})", str(value))
                if match:
                    return match.group(1)
    parsed = urlparse(url or "")
    for key in ("mid", "id"):
        if parsed.query:
            value = parse_qs(parsed.query).get(key, [None])[0]
            if value:
                return str(value)
    match = re.search(r"/(?:detail|status)/([A-Za-z0-9]+)", parsed.path)
    return match.group(1) if match else None


def extract_gender(profile_text: str) -> str:
    match = re.search(r"(?:性别|Gender)\s*[：:]?\s*(男|女|其他)", profile_text or "", re.I)
    return match.group(1) if match else ""


def extract_birth_date(profile_text: str) -> str:
    match = re.search(r"(?:生日|出生日期|Birthday)\s*[：:]?\s*((?:19|20)\d{2}[年/-]\d{1,2}[月/-]\d{1,2}日?)", profile_text or "", re.I)
    return match.group(1) if match else ""


def extract_registered_at(profile_text: str) -> str:
    match = re.search(r"(?:注册时间|注册日期|加入时间|加入于|注册于)\s*[：:]?\s*((?:19|20)\d{2}[年/-]\d{1,2}(?:[月/-]\d{1,2}日?)?)", profile_text or "")
    return match.group(1) if match else ""


async def _inner_text(locator: Any) -> str:
    try:
        return clean_text(await locator.inner_text(timeout=1500))
    except Exception:
        return ""


async def _attr(locator: Any, name: str) -> str:
    try:
        return (await locator.get_attribute(name, timeout=1500)) or ""
    except Exception:
        return ""


async def parse_commenters(page: Any, parent_id: str) -> list[CommenterRef]:
    links = page.locator(COMMENT_USER_SELECTORS)
    if await links.count() == 0:
        links = page.locator(COMMENT_USER_FALLBACK_SELECTORS)
    result: dict[str, CommenterRef] = {}
    for index in range(await links.count()):
        link = links.nth(index)
        url = await _attr(link, "href")
        user_id = extract_user_id(url)
        if not user_id:
            continue
        if url.startswith("//"):
            url = "https:" + url
        elif url.startswith("/"):
            url = "https://weibo.com" + url
        result.setdefault(user_id, CommenterRef(parent_id, user_id, url, await _inner_text(link)))
    return list(result.values())


async def parse_user_profile(page: Any, user_id: str, profile_url: str) -> UserRecord:
    text = await _inner_text(page.locator("body"))
    posts = None
    followers = None
    post_match = re.search(r"微博\s*([\d.]+(?:万|亿)?)", text)
    follower_match = re.search(r"粉丝\s*([\d.]+(?:万|亿)?)", text)
    if post_match:
        posts = normalize_count(post_match.group(1))
    if follower_match:
        followers = normalize_count(follower_match.group(1))
    education_match = re.search(r"(?:大学|教育|学校)\s*[：:]?\s*([^\n|]{1,80})", text)
    return UserRecord(
        user_id=user_id,
        profile_url=profile_url,
        gender=extract_gender(text),
        birth_date=extract_birth_date(text),
        education=clean_text(education_match.group(1)) if education_match else "",
        registered_at=extract_registered_at(text),
        posts_count=posts,
        followers_count=followers,
        profile_fetched_at=utc_now(),
        profile_status="success",
    )


async def parse_post_cards(page: Any, user_id: str, source_parent_id: str = "") -> list[PostRecord]:
    cards = page.locator(POST_CARD_SELECTORS)
    posts: list[PostRecord] = []
    for index in range(await cards.count()):
        card = cards.nth(index)
        mid = await _attr(card, "mid") or await _attr(card, "data-mid")
        if not mid:
            mid = extract_post_id(await _attr(card.locator("a[href*='/status/']").first, "href")) or ""
        if not mid:
            continue
        text = await _inner_text(card.locator("p.txt, .txt, [node-type='feed_list_content']").first)
        if not text:
            continue
        source = await _inner_text(card.locator("p.from, .from").first)
        time_link = card.locator("p.from a, .from a").first
        url = await _attr(time_link, "href")
        if url.startswith("//"):
            url = "https:" + url
        elif url.startswith("/"):
            url = "https://weibo.com" + url
        posts.append(PostRecord(
            post_id=mid,
            user_id=user_id,
            text=text,
            location=extract_location(source),
            published_at=await _inner_text(time_link),
            is_original=await card.locator("[node-type='feed_list_forwardContent'], .card-comment").count() == 0,
            url=url,
            fetched_at=utc_now(),
            source_parent_id=source_parent_id,
        ))
    return posts
