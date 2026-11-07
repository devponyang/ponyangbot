import unittest
from datetime import datetime
from storage import KST, Store


class TodayUpgradeTests(unittest.TestCase):
    def test_sunday_roster_and_weekly_settings_survive_upgrade(self):
        store = Store(':memory:')
        store.create('e', 1, '1단거점', '2026-11-08 21:00', 30)
        for i in range(31):
            store.participate('e', 1, i, 'user', str(i))
        old_order = [p['user_id'] for p in store.members('e') + store.members('e', waiting=True)]
        now = datetime(2026, 11, 8, 17, tzinfo=KST)
        event = store.upgrade_today_event('e', 1, 99, now)
        self.assertEqual((event['name'], event['capacity']), ('2단거점', 50))
        self.assertEqual([p['user_id'] for p in store.members('e')], old_order)
        self.assertEqual(store.weekly_tiers(1)[6], 1)
        self.assertEqual(store.participation_history(1, user_id=30)[-1]['action'], '자동 순번 변경')
        revision = store.revision
        store.upgrade_today_event('e', 1, 99, now)
        self.assertEqual(store.revision, revision)
        with self.assertRaises(ValueError):
            store.upgrade_today_event('e', 1, 99, now.replace(day=9))
