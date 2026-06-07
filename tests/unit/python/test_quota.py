"""test_quota.py - provider usage-limit / rate-limit detection.

Covers both claude and codex phrasings + reset-time parsing. No network.
"""

from __future__ import annotations

import unittest
from datetime import datetime, timezone

from specseed_runtime.executing import quota


class QuotaSignalTest(unittest.TestCase):
    def test_codex_usage_limit(self) -> None:
        info = quota.quota_signal("You've hit your usage limit. Try again at 8:27 PM.")
        self.assertIsNotNone(info)

    def test_rate_limit_phrase(self) -> None:
        self.assertIsNotNone(quota.quota_signal("Error: rate limited, slow down"))

    def test_429(self) -> None:
        self.assertIsNotNone(quota.quota_signal("HTTP 429 Too Many Requests"))

    def test_ordinary_error_is_none(self) -> None:
        self.assertIsNone(quota.quota_signal("agent exited with code 1\nTraceback ..."))

    def test_empty_is_none(self) -> None:
        self.assertIsNone(quota.quota_signal(""))
        self.assertIsNone(quota.quota_signal(None))

    def test_429_not_matched_inside_larger_number(self) -> None:
        # bare 429 only, not part of a build id like 14290
        self.assertIsNone(quota.quota_signal("build 14290 finished"))


class ResetParseTest(unittest.TestCase):
    def test_try_again_in_minutes(self) -> None:
        info = quota.quota_signal("usage limit; try again in 5 minutes")
        self.assertEqual(info.park_seconds, 5 * 60)

    def test_retry_after_seconds_floored(self) -> None:
        info = quota.quota_signal("rate limit. retry after 10s")
        self.assertEqual(info.park_seconds, quota.MIN_PARK_S)  # floored to MIN

    def test_try_again_at_clock_future(self) -> None:
        now = datetime(2026, 6, 7, 18, 0, 0, tzinfo=timezone.utc)
        info = quota.quota_signal("usage limit. try again at 18:30", now=now)
        self.assertIsNotNone(info.reset_at)
        self.assertEqual(info.park_seconds, 30 * 60)

    def test_try_again_at_rolls_to_tomorrow(self) -> None:
        now = datetime(2026, 6, 7, 19, 0, 0, tzinfo=timezone.utc)
        info = quota.quota_signal("usage limit. try again at 8:27 PM", now=now)
        # 8:27 PM = 20:27 same day, still future -> ~87 min
        self.assertGreater(info.park_seconds, 60 * 60)

    def test_no_reset_uses_default(self) -> None:
        info = quota.quota_signal("usage limit reached")
        self.assertEqual(info.park_seconds, quota.DEFAULT_PARK_S)


if __name__ == "__main__":
    unittest.main()
