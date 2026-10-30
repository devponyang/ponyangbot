import unittest
from datetime import datetime, timezone

from schedule import scheduled_capacity, due_event_date
from storage import KST


class ScheduleTests(unittest.TestCase):
    def test_posting_window_and_saturday_skip(self):
        for stamp, expected in [
            ('2026-10-25T22:00:00', None),
            ('2026-10-26T09:00:00', None),
            ('2026-10-26T21:59:00', None),
            ('2026-10-26T22:00:00', '2026-10-27'),
            ('2026-10-27T00:01:00', '2026-10-27'),
            ('2026-10-30T22:00:00', None),
            ('2026-10-31T21:59:00', None),
            ('2026-10-31T22:00:00', '2026-11-01'),
        ]:
            with self.subTest(stamp=stamp):
                day = due_event_date(datetime.fromisoformat(stamp).replace(tzinfo=KST))
                self.assertEqual(day.isoformat() if day else None, expected)

    def test_weekly_capacity(self):
        for day, expected in enumerate([None, 30, 25, 30, 25, None, 30], 26):
            # 2026-10-26 is Monday; November 1 is Sunday.
            starts = datetime(2026, 10, day, 21, tzinfo=KST) if day <= 31 else datetime(2026, 11, 1, 21, tzinfo=KST)
            with self.subTest(starts=starts):
                self.assertEqual(scheduled_capacity(starts), expected)

    def test_uses_korean_date(self):
        self.assertEqual(scheduled_capacity(datetime(2026, 10, 26, 16, tzinfo=timezone.utc)), 30)

    def test_requires_timezone(self):
        with self.assertRaises(ValueError):
            scheduled_capacity(datetime(2026, 10, 26))
