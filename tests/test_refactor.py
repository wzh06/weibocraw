from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from weibo_crawler.database import StateStore
from weibo_crawler.filters import is_eligible_user, parse_date
from weibo_crawler.models import CommenterRef, PostRecord, UserRecord
from weibo_crawler.parsers import extract_post_id, extract_user_id, extract_location


class RefactorPureTests(unittest.TestCase):
    def test_ids_and_location(self) -> None:
        self.assertEqual(extract_user_id("https://weibo.com/u/12345"), "12345")
        self.assertEqual(extract_user_id("https://weibo.com/profile?uid=67890"), "67890")
        self.assertEqual(extract_post_id("https://weibo.com/status/998877"), "998877")
        self.assertEqual(extract_location("2026-01-01 位置：上海"), "上海")
        self.assertEqual(extract_location("2026-01-01 来自 iPhone"), "")

    def test_filter(self) -> None:
        user = UserRecord("u", registered_at="2020年1月2日", posts_count=50, followers_count=5000)
        self.assertEqual(parse_date("2020年1月2日"), date(2020, 1, 2))
        self.assertTrue(is_eligible_user(user, date(2026, 1, 3)))
        self.assertFalse(is_eligible_user(UserRecord("u2", registered_at="2020-01-02", posts_count=49, followers_count=1), date(2026, 1, 3)))
        self.assertFalse(is_eligible_user(UserRecord("u3", registered_at="2020-01-02", posts_count=50, followers_count=5001), date(2026, 1, 3)))

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
            self.assertIn("user_id", users_csv.read_text(encoding="utf-8-sig"))
            self.assertIn("是", posts_csv.read_text(encoding="utf-8-sig"))
            db.close()


if __name__ == "__main__":
    unittest.main()
