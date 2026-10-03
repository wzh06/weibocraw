from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any
from urllib.parse import parse_qs, urljoin, urlparse

from .models import CommenterRef, PostRecord, UserRecord
from .selectors import (
    BUILD_COMMENTS_ENDPOINT,
    COMMENT_USER_FALLBACK_SELECTORS,
    COMMENT_USER_SELECTORS,
    POST_CARD_SELECTORS,
    POST_DETAIL_ENDPOINT,
)


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


def extract_labeled_count(source_text: str, label: str) -> int | None:
    """Extract a count adjacent to a Chinese label such as ``粉丝``.

    Weibo currently renders profile counters in the form ``308粉丝`` (number
    attached directly before label).  Try the unambiguous attached forms first,
    then the older ``粉丝 308`` form.
    """
    text = clean_text(source_text)
    escaped_label = re.escape(label)
    attached_before = re.search(rf"([\d.]+(?:万|亿)?){escaped_label}", text)
    if attached_before:
        return normalize_count(attached_before.group(1))
    attached_after = re.search(rf"{escaped_label}([\d.]+(?:万|亿)?)", text)
    if attached_after:
        return normalize_count(attached_after.group(1))
    spaced_after = re.search(rf"{escaped_label}\s*[：:]?\s*([\d.]+(?:万|亿)?)", text)
    if spaced_after:
        return normalize_count(spaced_after.group(1))
    return None


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
    match = re.search(r"/(?:u|profile)/([A-Za-z0-9_-]+)(?:/|$)", parsed.path)
    if not match:
        return None
    candidate = match.group(1)
    # Navigation links such as /u/page/follow/... are not user profiles.
    if candidate.lower() in {"page", "home", "follow", "fans", "friends"}:
        return None
    return candidate


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


def extract_mblog_id(url: str) -> str | None:
    """Return the short Weibo status id from a post URL.

    Accepts ``/uid/mblogid``, ``/status/mblogid`` and ``/detail/mblogid``.
    """
    parsed = urlparse(url or "")
    parts = [part for part in parsed.path.split("/") if part]
    if not parts:
        return None
    for index, part in enumerate(parts):
        if part in {"status", "detail"} and index + 1 < len(parts):
            return parts[index + 1]
    if len(parts) >= 2 and parts[0].isdigit():
        return parts[-1]
    return parts[-1]


_MBLOGID_ALPHABET = "0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"


def mblogid_to_numeric(mblog_id: str | None) -> str:
    """Convert a short base62 Weibo mblog id to the numeric status id.

    The web UI exposes ``/uid/<mblogid>`` URLs while the comment API expects
    the numeric status id.  Numeric ids are returned unchanged so callers can
    pass either form safely.
    """
    if not mblog_id or mblog_id.isdigit():
        return mblog_id or ""
    reversed_id = mblog_id[::-1]
    chunks = [reversed_id[i : i + 4] for i in range(0, len(reversed_id), 4)]
    pieces: list[str] = []
    for index, chunk in enumerate(chunks):
        value = 0
        try:
            for char in chunk[::-1]:
                value = value * 62 + _MBLOGID_ALPHABET.index(char)
        except ValueError:
            return ""
        piece = str(value)
        if index < len(chunks) - 1:
            piece = piece.zfill(7)
        pieces.append(piece)
    pieces.reverse()
    return "".join(pieces)


def extract_post_owner_id(url: str) -> str | None:
    """Return the author uid from a ``/uid/mblogid`` style Weibo URL."""
    parsed = urlparse(url or "")
    for part in parsed.path.split("/"):
        if part.isdigit():
            return part
    return None


def _comment_user_id(item: dict[str, Any]) -> str:
    user = item.get("user") or {}
    value = user.get("id") or user.get("idstr") or item.get("user_id") or item.get("uid")
    return str(value).strip() if value is not None else ""


def _comment_profile_url(user: dict[str, Any], user_id: str) -> str:
    value = str(user.get("profile_url") or user.get("url") or "").strip()
    if value:
        return urljoin("https://weibo.com/", value)
    if user_id.isdigit():
        return f"https://weibo.com/u/{user_id}"
    return f"https://weibo.com/{user_id}"


def parse_comment_payload(payload: dict[str, Any] | None, parent_id: str) -> list[CommenterRef]:
    """Parse commenters returned by ``buildComments`` into deduplicated refs."""
    if not isinstance(payload, dict):
        return []
    data = payload.get("data")
    if isinstance(data, dict):
        data = data.get("comments") or data.get("list") or []
    items = data if isinstance(data, list) else []
    result: dict[str, CommenterRef] = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        user_id = _comment_user_id(item)
        if not user_id:
            continue
        user = item.get("user") or {}
        screen_name = clean_text(str(user.get("screen_name") or user.get("name") or ""))
        profile_url = _comment_profile_url(user, user_id)
        result.setdefault(user_id, CommenterRef(parent_id, user_id, profile_url, screen_name))
    return list(result.values())


def comment_payload_max_id(payload: dict[str, Any] | None) -> str:
    """Return the cursor used to fetch the next ``buildComments`` page."""
    if not isinstance(payload, dict):
        return ""
    for key in ("max_id", "maxid", "maxId"):
        value = payload.get(key)
        if value not in (None, "", 0, "0"):
            return str(value)
    data = payload.get("data")
    if isinstance(data, dict):
        for key in ("max_id", "maxid", "maxId"):
            value = data.get(key)
            if value not in (None, "", 0, "0"):
                return str(value)
    return ""


async def fetch_post_detail_id(page: Any, mblog_id: str) -> str:
    """Resolve a short mblog id to the numeric id expected by buildComments."""
    if not mblog_id:
        return ""
    try:
        payload = await page.evaluate(
            """async (params) => {
                const response = await fetch(
                    `${params.endpoint}`,
                    {credentials: 'include', headers: {'x-requested-with': 'XMLHttpRequest'}}
                );
                return response.ok ? await response.json() : null;
            }""",
            {"endpoint": POST_DETAIL_ENDPOINT.format(mblog_id=mblog_id)},
        )
    except Exception:
        return ""
    data = ((payload or {}).get("data") or {}) if isinstance(payload, dict) else {}
    return str(data.get("id") or data.get("idstr") or "")


async def fetch_build_comments(
    page: Any,
    *,
    numeric_id: str,
    owner_id: str,
    max_id: str = "",
    count: int = 20,
) -> dict[str, Any] | None:
    """Fetch one page of comments through the logged-in Weibo web API."""
    if not numeric_id or not owner_id:
        return None
    params: dict[str, str] = {
        "is_reload": "1",
        "id": numeric_id,
        "is_show_bulletin": "2",
        "is_mix": "0",
        "count": str(count),
        "uid": owner_id,
    }
    if max_id:
        params["max_id"] = max_id
    try:
        payload = await page.evaluate(
            """async (params) => {
                const query = new URLSearchParams(params.query).toString();
                const response = await fetch(
                    `${params.endpoint}?${query}`,
                    {credentials: 'include', headers: {'x-requested-with': 'XMLHttpRequest'}}
                );
                return response.ok ? await response.json() : null;
            }""",
            {"endpoint": BUILD_COMMENTS_ENDPOINT, "query": params},
        )
    except Exception:
        return None
    return payload if isinstance(payload, dict) else None


def parse_api_posts(payload: dict[str, Any], user_id: str, source_parent_id: str = "") -> list[PostRecord]:
    """Parse the public profile feed returned by Weibo's page data endpoint."""
    result: list[PostRecord] = []
    for item in ((payload.get("data") or {}).get("list") or []):
        post_id = str(item.get("idstr") or item.get("mid") or item.get("id") or "")
        text = clean_text(str(item.get("text_raw") or item.get("text") or ""))
        if not post_id or not text:
            continue
        mblog_id = str(item.get("mblogid") or post_id)
        source = clean_text(str(item.get("region_name") or item.get("source") or ""))
        result.append(PostRecord(
            post_id=post_id,
            user_id=user_id,
            text=text,
            location=extract_location(source),
            published_at=str(item.get("created_at") or ""),
            is_original="retweeted_status" not in item,
            url=f"https://weibo.com/{user_id}/{mblog_id}",
            fetched_at=utc_now(),
            source_parent_id=source_parent_id,
        ))
    return result


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


def _as_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _profile_counts(info_user: dict[str, Any]) -> tuple[int | None, int | None]:
    posts = _as_int(info_user.get("statuses_count"))
    followers = _as_int(info_user.get("followers_count"))
    if followers is None and info_user.get("followers_count_str") is not None:
        followers = normalize_count(str(info_user["followers_count_str"]))
    return posts, followers


async def fetch_profile_counts(page: Any, user_id: str) -> tuple[int | None, int | None]:
    """Fetch the lightweight profile counters used for the early post-limit check."""
    try:
        payload = await page.evaluate(
            """async (uid) => {
                const response = await fetch(
                    `/ajax/profile/info?uid=${encodeURIComponent(uid)}&scene=profile`,
                    {credentials: 'include'}
                );
                return response.ok ? await response.json() : null;
            }""",
            user_id,
        )
    except Exception:
        return None, None
    info_user = ((payload or {}).get("data") or {}).get("user") or {}
    return _profile_counts(info_user)


async def fetch_profile_detail(page: Any, user_id: str) -> dict[str, Any]:
    try:
        payload = await page.evaluate(
            """async (uid) => {
                const response = await fetch(
                    `/ajax/profile/detail?uid=${encodeURIComponent(uid)}`,
                    {credentials: 'include'}
                );
                return response.ok ? await response.json() : null;
            }""",
            user_id,
        )
    except Exception:
        return {}
    return (payload or {}).get("data") or {}


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


async def parse_user_profile(
    page: Any,
    user_id: str,
    profile_url: str,
    *,
    max_posts_count: int | None = None,
    posts_count: int | None = None,
    followers_count: int | None = None,
) -> UserRecord:
    posts = posts_count
    followers = followers_count
    if posts is None and followers is None:
        posts, followers = await fetch_profile_counts(page, user_id)
    if max_posts_count is not None and max_posts_count > 0 and posts is not None and posts > max_posts_count:
        return UserRecord(
            user_id=user_id,
            profile_url=profile_url,
            posts_count=posts,
            followers_count=followers,
            profile_fetched_at=utc_now(),
            profile_status="success",
        )

    text = await _inner_text(page.locator("body"))
    try:
        markup = await page.content()
    except Exception:
        markup = ""
    registered_at = str((await fetch_profile_detail(page, user_id)).get("created_at") or "")
    if posts is None:
        post_match = re.search(r"(?:全部微博|微博)\s*[（(]?\s*([\d.]+(?:万|亿)?)", text)
        if not post_match:
            post_match = re.search(r"([\d.]+(?:万|亿)?)\s*微博", text)
        if post_match:
            posts = normalize_count(post_match.group(1))
    if followers is None:
        followers = extract_labeled_count(text, "粉丝")
    # The current Weibo profile renders these public counters in the page's
    # serialized user object rather than in visible text.
    if posts is None:
        match = re.search(r'"statuses_count"\s*:\s*(\d+)', markup)
        if match:
            posts = int(match.group(1))
    if followers is None:
        match = re.search(r'"followers_count"\s*:\s*(\d+)', markup)
        if match:
            followers = int(match.group(1))
    education_match = re.search(r"(?:大学|教育|学校)\s*[：:]?\s*([^\n|]{1,80})", text)
    if not registered_at:
        registered_at = extract_registered_at(text)
    return UserRecord(
        user_id=user_id,
        profile_url=profile_url,
        gender=extract_gender(text),
        birth_date=extract_birth_date(text),
        education=clean_text(education_match.group(1)) if education_match else "",
        registered_at=registered_at,
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
