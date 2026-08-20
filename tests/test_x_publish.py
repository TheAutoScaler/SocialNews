import datetime as dt
import importlib.util
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "src" / "x_publish.py"
SPEC = importlib.util.spec_from_file_location("x_publish", SCRIPT)
x_publish = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(x_publish)
UTC = dt.timezone.utc


class XPublishTests(unittest.TestCase):
    def test_queue_freezes_text_and_rejects_duplicate_id(self):
        queue = {"version": 1, "posts": []}
        now = dt.datetime(2026, 8, 20, 12, tzinfo=UTC)
        post = x_publish.queue_post(queue, "2026-W34", "  Important week.  ", now, now)
        self.assertEqual(post["text"], "Important week.")
        self.assertEqual(post["content_hash"], x_publish.content_hash("Important week."))
        with self.assertRaisesRegex(ValueError, "already exists"):
            x_publish.queue_post(queue, "2026-W34", "Different", now, now)

    def test_dry_run_does_not_publish_or_change_status(self):
        queue = {"version": 1, "posts": []}
        due = dt.datetime(2026, 8, 17, 17, tzinfo=UTC)
        x_publish.queue_post(queue, "weekly", "Draft", due, due)
        messages = x_publish.publish_due(queue, due, live=False, publisher=lambda text: self.fail())
        self.assertEqual(messages, ["DRY RUN: weekly is due (5 characters)"])
        self.assertEqual(queue["posts"][0]["status"], "approved")

    def test_live_publish_records_id_and_is_idempotent(self):
        queue = {"version": 1, "posts": []}
        due = dt.datetime(2026, 8, 17, 17, tzinfo=UTC)
        x_publish.queue_post(queue, "weekly", "Draft", due, due)
        calls = []

        def publisher(text):
            calls.append(text)
            return {"id": "123", "text": text}

        messages = x_publish.publish_due(queue, due, live=True, publisher=publisher)
        self.assertEqual(messages, ["PUBLISHED: weekly: https://x.com/i/web/status/123"])
        self.assertEqual(calls, ["Draft"])
        self.assertEqual(x_publish.publish_due(queue, due, live=True, publisher=publisher), [])
        self.assertEqual(calls, ["Draft"])

    def test_changed_approved_text_is_rejected(self):
        queue = {"version": 1, "posts": []}
        due = dt.datetime(2026, 8, 17, 17, tzinfo=UTC)
        post = x_publish.queue_post(queue, "weekly", "Approved", due, due)
        post["text"] = "Tampered"
        with self.assertRaisesRegex(ValueError, "changed after approval"):
            x_publish.publish_due(queue, due, live=True, publisher=lambda text: {"id": "1"})

    def test_oauth1_header_is_deterministic(self):
        credentials = {
            "consumer_key": "key",
            "consumer_secret": "secret",
            "access_token": "token",
            "access_token_secret": "token-secret",
        }
        header = x_publish.oauth1_header(
            "POST", x_publish.X_CREATE_POST_URL, credentials, nonce="nonce", timestamp="123"
        )
        self.assertTrue(header.startswith("OAuth "))
        self.assertIn('oauth_consumer_key="key"', header)
        self.assertIn('oauth_signature=', header)


if __name__ == "__main__":
    unittest.main()
