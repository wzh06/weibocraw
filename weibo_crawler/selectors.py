"""Centralized selectors so markup changes do not affect crawl orchestration."""

COMMENT_USER_SELECTORS = "div[node-type='comment_list'] a[href*='/u/'], div[node-type='comment_list'] a[href*='uid='], .list_li a[href*='/u/'], .list_li a[href*='uid=']"
COMMENT_USER_FALLBACK_SELECTORS = "a[href*='/u/'], a[href*='uid='], a[href*='/profile/']"
COMMENT_NEXT_SELECTORS = (
    "a.next, "
    "a:has-text('下一页'), a:has-text('查看更多评论'), a:has-text('查看全部评论'), "
    "a:has-text('展开更多评论'), a:has-text('更多评论'), "
    "button:has-text('查看更多评论'), button:has-text('查看全部评论'), "
    "button:has-text('展开更多评论'), button:has-text('更多评论')"
)
POST_CARD_SELECTORS = "div.card-wrap[mid], div.card-wrap[action-type='feed_list_item'], article"
PROFILE_FIELD_SELECTORS = ("body",)
NEXT_PAGE_SELECTORS = COMMENT_NEXT_SELECTORS
PROFILE_POSTS_ENDPOINT = "/ajax/statuses/mymblog?uid={user_id}&page={page_number}&feature=0"
POST_DETAIL_ENDPOINT = "/ajax/statuses/show?id={mblog_id}"
BUILD_COMMENTS_ENDPOINT = "/ajax/statuses/buildComments"
BLOCK_PAGE_INDICATORS = ("验证码", "安全验证", "访问频繁", "账号异常", "login.php")
