import tempfile
import unittest
from pathlib import Path

from storage import Store


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / 'bot.db'
        self.store = Store(self.path)
        self.store.create('event', 'guild', '거점', '2030-01-01 21:00', 1)

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def join(self, user='user', nickname='기사'):
        self.store.participate('event', 'guild', user, user, nickname)

    def test_duplicate_updates_without_consuming_capacity(self):
        self.join()
        self.join(nickname='마법사')
        self.assertEqual(len(self.store.members('event')), 1)
        self.assertEqual(self.store.members('event')[0]['nickname'], '마법사')
        self.join('second')
        self.assertEqual(self.store.members('event', waiting=True)[0]['user_id'], 'second')

    def test_cancel_frees_capacity_and_keeps_record(self):
        self.join()
        self.store.participate('event', 'guild', 'user', 'user', '', False)
        self.join('second')
        rows = self.store.snapshot()[2]
        self.assertEqual(rows[1][6], '취소')
        self.assertEqual(rows[1][5], '기사')
        self.assertEqual(rows[2][6], '참여')

    def test_close_rejects_changes(self):
        self.join()
        self.store.close_event('event', 'guild')
        with self.assertRaises(ValueError):
            self.join()
        with self.assertRaises(ValueError):
            self.store.participate('event', 'guild', 'user', 'user', '', False)

    def test_server_boundary(self):
        with self.assertRaises(ValueError):
            self.store.participate('event', 'other', 'user', 'user', '기사')

    def test_restart_preserves_data(self):
        self.join()
        self.store.close()
        self.store = Store(self.path)
        self.assertEqual(len(self.store.members('event')), 1)
        self.assertEqual(self.store.snapshot()[0], 2)

    def test_rejoin_and_empty_nickname(self):
        with self.assertRaises(ValueError):
            self.join(nickname='  ')
        self.join()
        self.store.participate('event', 'guild', 'user', 'user', '', False)
        self.join()
        self.assertEqual(len(self.store.snapshot()[2]), 2)


if __name__ == '__main__':
    unittest.main()
