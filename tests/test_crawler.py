from pathlib import Path
import unittest

from crawler import Settings, clean_text, extract_location, in_configured_dates, normalize_count


def settings() -> Settings:
    return Settings(
        keywords=["测试"],
        start_date="2026-01-01",
        end_date="2026-12-31",
        target_count=1,
        output_csv=Path("x.csv"),
        state_db=Path("x.sqlite3"),
        profile_dir=Path("profile"),
        report_dir=Path("reports"),
        min_delay_seconds=0,
        max_delay_seconds=0,
        page_timeout_ms=1000,
        headless=True,
    )


class CrawlerHelperTests(unittest.TestCase):
    def test_normalize_count(self) -> None:
        self.assertEqual(normalize_count("转发 1.2万"), 12000)
        self.assertEqual(normalize_count("赞 34"), 34)
        self.assertIsNone(normalize_count("暂无"))

    def test_text_cleanup(self) -> None:
        self.assertEqual(clean_text(" a\n  b\t c "), "a b c")

    def test_explicit_location_parsing(self) -> None:
        self.assertEqual(extract_location("今天 12:00 发布于 北京"), "是")
        self.assertEqual(extract_location("2026-09-30 位置：上海"), "是")
        self.assertEqual(extract_location("今天 12:00 来自 iPhone"), "否")
        self.assertEqual(extract_location("今天 12:00"), "否")
        self.assertEqual(extract_location("今天 12:00 [杭州]"), "是")

    def test_date_filter_keeps_relative_dates(self) -> None:
        cfg = settings()
        self.assertTrue(in_configured_dates("2026-09-30 12:00", cfg))
        self.assertFalse(in_configured_dates("2025-12-31 12:00", cfg))
        self.assertTrue(in_configured_dates("今天 12:00", cfg))
