from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory
import asyncio
import unittest

from weibo_crawler.database import StateStore
from weibo_crawler.filters import is_eligible_user, parse_date
from weibo_crawler.models import CommenterRef, PostRecord, UserRecord
from weibo_crawler.parsers import (
    comment_payload_max_id,
    extract_labeled_count,
    extract_location,
    extract_mblog_id,
    extract_post_id,
    extract_post_owner_id,
    extract_user_id,
    mblogid_to_numeric,
    parse_api_posts,
    parse_comment_payload,
    parse_user_profile,
)


class RefactorPureTests(unittest.TestCase):
    def test_ids_and_location(self) -> None:
        self.assertEqual(extract_user_id("https://weibo.com/u/12345"), "12345")
        self.assertEqual(extract_user_id("https://weibo.com/profile?uid=67890"), "67890")
        self.assertEqual(extract_post_id("https://weibo.com/status/998877"), "998877")
        self.assertEqual(extract_location("2026-01-01 位置：上海"), "上海")
        self.assertEqual(extract_location("2026-01-01 来自 iPhone"), "")

    def test_extract_labeled_count_prefers_number_before_label(self) -> None:
        self.assertEqual(extract_labeled_count("小赵赵-淼晨 308粉丝 902关注 675转评赞", "粉丝"), 308)
        self.assertEqual(extract_labeled_count("小赵赵-淼晨 308粉丝 902关注 675转评赞", "关注"), 902)
        self.assertEqual(extract_labeled_count("关注 902 粉丝 1.2万", "粉丝"), 12000)
        self.assertEqual(extract_labeled_count("粉丝 308", "粉丝"), 308)
        self.assertIsNone(extract_labeled_count("暂无粉丝", "粉丝"))

    def test_filter(self) -> None:
        user = UserRecord("u", registered_at="2020年1月2日", posts_count=50, followers_count=5000)
        self.assertEqual(parse_date("2020年1月2日"), date(2020, 1, 2))
        self.assertTrue(is_eligible_user(user, date(2026, 1, 3)))
        self.assertFalse(is_eligible_user(UserRecord("u2", registered_at="2020-01-02", posts_count=49, followers_count=1), date(2026, 1, 3)))
        self.assertFalse(is_eligible_user(UserRecord("u3", registered_at="2020-01-02", posts_count=50, followers_count=5001), date(2026, 1, 3)))

    def test_filter_max_posts_count(self) -> None:
        user = UserRecord("u", registered_at="2020年1月2日", posts_count=5000, followers_count=5000)
        self.assertTrue(is_eligible_user(user, date(2026, 1, 3), max_posts_count=5000))
        self.assertFalse(is_eligible_user(user, date(2026, 1, 3), max_posts_count=4999))
        self.assertTrue(is_eligible_user(user, date(2026, 1, 3), max_posts_count=0))
        self.assertTrue(is_eligible_user(user, date(2026, 1, 3)))

    def test_parse_user_profile_skips_when_posts_exceed_limit(self) -> None:
        class FakePage:
            async def evaluate(self, *args, **kwargs):
                return {"data": {"user": {"statuses_count": 500, "followers_count": 30}}}

        profile = asyncio.run(parse_user_profile(
            FakePage(),
            "u",
            "https://weibo.com/u/u",
            max_posts_count=100,
        ))
        self.assertEqual(profile.posts_count, 500)
        self.assertEqual(profile.followers_count, 30)
        self.assertEqual(profile.registered_at, "")

    def test_database_deduplicates_and_exports(self) -> None:
        with TemporaryDirectory() as directory:
            db = StateStore(Path(directory) / "state.sqlite3")
            ref = CommenterRef("parent", "user", "https://weibo.com/u/user", "name")
            self.assertTrue(db.upsert_commenter(ref))
            self.assertFalse(db.upsert_commenter(ref))
            user = UserRecord("user", profile_url=ref.profile_url, registered_at="2020-01-01", posts_count=50, followers_count=10, profile_status="success")
            db.upsert_user(user)
            post = PostRecord("post", "user", "正文", location="北京", is_original=True)
            self.assertTrue(db.upsert_post(post))
            self.assertFalse(db.upsert_post(post))
            users_csv = Path(directory) / "users.csv"
            posts_csv = Path(directory) / "posts.csv"
            db.export_users_basic_csv(users_csv)
            db.export_posts_content_csv(posts_csv)
            self.assertIn("用户ID", users_csv.read_text(encoding="utf-8-sig"))
            self.assertIn("是", posts_csv.read_text(encoding="utf-8-sig"))
            db.close()

    def test_parse_api_posts(self) -> None:
        payload = {
            "data": {
                "list": [
                    {"idstr": "123456", "mblogid": "AbCd", "text_raw": "正文", "created_at": "2026-10-01", "region_name": "发布于 上海"},
                    {"idstr": "123457", "text_raw": "转发", "retweeted_status": {}, "created_at": "2026-10-02"},
                ]
            }
        }
        posts = parse_api_posts(payload, "42")
        self.assertEqual(len(posts), 2)
        self.assertTrue(posts[0].is_original)
        self.assertFalse(posts[1].is_original)
        self.assertEqual(posts[0].location, "上海")

    def test_extract_post_parts_from_parent_url(self) -> None:
        url = "https://weibo.com/6004281123/RkV8KuqpO"
        self.assertEqual(extract_mblog_id(url), "RkV8KuqpO")
        self.assertEqual(extract_post_owner_id(url), "6004281123")
        self.assertEqual(extract_mblog_id("https://weibo.com/status/998877"), "998877")

    def test_mblogid_to_numeric(self) -> None:
        self.assertEqual(mblogid_to_numeric("RkV8KuqpO"), "5349862107251384")
        self.assertEqual(mblogid_to_numeric("RkVS52M0H"), "5349890210661211")
        self.assertEqual(mblogid_to_numeric("z0JH2lOMb"), "3501756485200075")
        self.assertEqual(mblogid_to_numeric("998877"), "998877")
        self.assertEqual(mblogid_to_numeric(""), "")

    def test_parse_comment_payload_and_max_id(self) -> None:
        payload = {
            "data": [
                {"user": {"id": 123, "screen_name": "张三"}},
                {"user": {"id": 456, "name": "李四", "profile_url": "/u/456"}},
            ],
            "max_id": "98765",
        }
        refs = parse_comment_payload(payload, "parent")
        self.assertEqual([ref.user_id for ref in refs], ["123", "456"])
        self.assertEqual(refs[0].profile_url, "https://weibo.com/u/123")
        self.assertEqual(refs[1].screen_name, "李四")
        self.assertEqual(refs[1].profile_url, "https://weibo.com/u/456")
        self.assertEqual(comment_payload_max_id(payload), "98765")


if __name__ == "__main__":
    unittest.main()
