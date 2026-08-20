import datetime as dt
import tempfile
import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import socialnews


class ConfigTests(unittest.TestCase):
    def test_empty_queries_are_valid(self):
        socialnews.validate_config({
            "queries": [], "max_results_per_source": 20,
            "lookback_days": 7, "request_timeout_seconds": 10, "seen_retention_days": 180,
        })

    def test_unknown_platform_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "unsupported platforms"):
            socialnews.validate_config({
                "queries": [{"name": "topic", "query": "words", "platforms": ["tiktok"]}],
                "max_results_per_source": 20, "request_timeout_seconds": 10,
                "lookback_days": 7, "seen_retention_days": 180,
            })

    def test_lookback_is_required(self):
        with self.assertRaisesRegex(ValueError, "lookback_days"):
            socialnews.validate_config({
                "queries": [], "max_results_per_source": 20,
                "request_timeout_seconds": 10, "seen_retention_days": 180,
            })


class SearchTests(unittest.TestCase):
    def test_bing_results_are_restricted_to_platform_hostname(self):
        rss = b"""<rss><channel>
        <item><title>Wrong</title><link>https://example.com/post</link></item>
        <item><title>Right</title><link>https://x.com/user/status/1</link></item>
        </channel></rss>"""
        original = socialnews.fetch_bytes
        socialnews.fetch_bytes = lambda *args, **kwargs: rss
        try:
            items = socialnews.search_bing_rss(
                "T", "words", "x", limit=10, timeout=1, user_agent="test"
            )
        finally:
            socialnews.fetch_bytes = original
        self.assertEqual(["https://x.com/user/status/1"], [item.url for item in items])


class UrlTests(unittest.TestCase):
    def test_canonicalization_removes_tracking_and_fragment(self):
        actual = socialnews.canonicalize_url(
            "https://www.Example.com/post/?utm_source=x&b=2&a=1#comments"
        )
        self.assertEqual("https://example.com/post?a=1&b=2", actual)


class RssTests(unittest.TestCase):
    def test_parse_rss(self):
        data = b"""<?xml version='1.0'?><rss><channel><item>
        <title>Example &amp; result</title><link>https://x.com/user/status/1</link>
        <description><![CDATA[<b>Useful</b> text]]></description>
        <pubDate>Wed, 19 Aug 2026 12:00:00 GMT</pubDate>
        </item></channel></rss>"""
        items = socialnews.parse_rss(data, topic="Test", platform="x", source="fixture")
        self.assertEqual(1, len(items))
        self.assertEqual("Example & result", items[0].title)
        self.assertEqual("Useful text", items[0].summary)
        self.assertEqual("2026-08-19T12:00:00+00:00", items[0].published_at)


class StateTests(unittest.TestCase):
    def test_duplicate_urls_are_only_new_once(self):
        now = dt.datetime(2026, 8, 20, tzinfo=dt.timezone.utc)
        item = socialnews.Item(
            topic="T", platform="reddit", title="One", url="https://reddit.com/r/a/1"
        )
        first, state = socialnews.update_state(
            {"version": 1, "items": {}}, [item, item], now=now, retention_days=180
        )
        second, _ = socialnews.update_state(state, [item], now=now, retention_days=180)
        self.assertEqual([item], first)
        self.assertEqual([], second)

    def test_old_state_is_pruned(self):
        now = dt.datetime(2026, 8, 20, tzinfo=dt.timezone.utc)
        state = {"version": 1, "items": {"old": {
            "first_seen": "2020-01-01T00:00:00+00:00", "url": "https://example.com/old"
        }}}
        _, updated = socialnews.update_state(state, [], now=now, retention_days=30)
        self.assertEqual({}, updated["items"])


class ReportTests(unittest.TestCase):
    def test_empty_configuration_report_has_guidance(self):
        report = socialnews.render_report(
            now=dt.datetime(2026, 8, 20, tzinfo=dt.timezone.utc), configured_queries=0,
            collected_count=0, new_items=[], health=[],
        )
        self.assertIn("No searches are configured", report)
        self.assertIn("generated_at", report)

    def test_write_outputs_writes_latest_and_dated_report(self):
        now = dt.datetime(2026, 8, 20, tzinfo=dt.timezone.utc)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            socialnews.write_outputs(
                state_path=root / "data" / "seen.json", output_dir=root / "reports",
                state={"version": 1, "items": {}}, report="# report\n", now=now,
            )
            self.assertEqual("# report\n", (root / "reports" / "latest.md").read_text())
            self.assertTrue((root / "reports" / "2026-08-20.md").exists())


if __name__ == "__main__":
    unittest.main()
