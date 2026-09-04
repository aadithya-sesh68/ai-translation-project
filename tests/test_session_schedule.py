"""Tests for reusable OraTranslate event-session codes."""

from __future__ import annotations

import unittest

from session_schedule import SessionCodeCatalog, normalize_session_code


class SessionScheduleTest(unittest.TestCase):
    def test_all_event_codes_are_available_when_no_session_is_active(self) -> None:
        payload = SessionCodeCatalog().snapshot()

        self.assertEqual(
            ["DAY1-AM", "DAY1-PM", "DAY2-AM", "DAY2-PM"],
            [slot["code"] for slot in payload["slots"]],
        )
        self.assertEqual(
            ["available", "available", "available", "available"],
            [slot["status"] for slot in payload["slots"]],
        )
        self.assertIsNone(payload["active_code"])
        self.assertIsNone(payload["active_state"])

    def test_only_the_current_code_receives_the_live_state(self) -> None:
        payload = SessionCodeCatalog().snapshot(
            active_code="day1 pm",
            active_state="live",
        )

        self.assertEqual("DAY1-PM", payload["active_code"])
        self.assertEqual("live", payload["active_state"])
        self.assertEqual(
            ["available", "live", "available", "available"],
            [slot["status"] for slot in payload["slots"]],
        )

    def test_normalization_accepts_readable_code_variants(self) -> None:
        self.assertEqual("DAY2-PM", normalize_session_code("day2-pm"))
        self.assertEqual("DAY2-PM", normalize_session_code("DAY2 PM"))


if __name__ == "__main__":
    unittest.main()
