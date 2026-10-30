import unittest
from unittest.mock import Mock

from identity import parse_display_name
from storage import Store
from sheets import Sheets


class RosterTests(unittest.TestCase):
    def test_join_order_is_independent_of_reused_sheet_row(self):
        store = Store(':memory:')
        self.addCleanup(store.close)
        store.create('a', 1, '거점', '2030-01-01 21:00', 30)
        store.participate('a', 1, 1, 'u1', '첫째')
        store.participate('a', 1, 2, 'u2', '둘째')
        store.participate('a', 1, 1, 'u1', '', join=False)
        store.participate('a', 1, 1, 'u1', '첫째재신청')
        self.assertEqual([m['user_id'] for m in store.members('a')], ['2', '1'])
        self.assertEqual(store.roster(1)[0][1], '둘째')
        store.participate('a', 1, 2, 'u2', '둘째수정')
        self.assertEqual([m['user_id'] for m in store.members('a')], ['2', '1'])

    def test_nickname_formats(self):
        for name in ['[포도당] 디움', '[포도당]디움', ' [ 포도당 ] 디움 ']:
            self.assertEqual(parse_display_name(name), ('포도당', '디움'))
        for name in ['디움', '[] 디움', '[포도당]', '[포도당]  ', '[포도당][디움]']:
            with self.subTest(name=name), self.assertRaises(ValueError):
                parse_display_name(name)

    def test_slots_cancel_rejoin_and_new_event(self):
        store = Store(':memory:')
        self.addCleanup(store.close)
        store.create('a', 1, '거점', '2030-01-01 21:00', 30)
        store.participate('a', 1, 1, 'user1', '디움', guild_name='포도당')
        store.participate('a', 1, 2, 'user2', '다른가문', guild_name='Probe')
        store.participate('a', 1, 1, 'user1', '', join=False)
        rows = store.roster(1)
        self.assertEqual(rows[0], ['Probe', '다른가문'])
        self.assertEqual(rows[1], ['', ''])
        store.participate('a', 1, 3, 'user3', '새가문', guild_name='포도당')
        self.assertEqual(store.roster(1)[1], ['포도당', '새가문'])
        with self.assertRaises(ValueError):
            store.participate('a', 1, 4, 'user4', '새가문', guild_name='포도당')
        store.create('b', 1, '거점', '2030-01-02 21:00', 25)
        self.assertEqual(store.roster(1), [['', ''] for _ in range(100)])
        self.assertEqual(len(store.members('a')), 2)

    def test_writes_only_roster_values_without_archive_tabs(self):
        sheets = Sheets.__new__(Sheets)
        props = [{'properties': {'title': title, 'sheetId': i,
                  'gridProperties': {'rowCount': 1000, 'columnCount': 26}}}
                 for i, title in enumerate(['거점시트', '봇_거점', '봇_참여'])]
        sheets.request = Mock(side_effect=[{'sheets': props}, {}])
        rows = [['포도당', '=디움']] + [['', ''] for _ in range(99)]
        sheets.sync(rows)
        payload = sheets.request.call_args.kwargs['json']
        self.assertEqual(payload['valueInputOption'], 'RAW')
        self.assertEqual(payload['data'][-1], {'range': "'거점시트'!K16:L115", 'values': rows})
        self.assertEqual(len(payload['data']), 1)

    def test_missing_target_fails_without_writes(self):
        sheets = Sheets.__new__(Sheets)
        sheets.request = Mock(return_value={'sheets': []})
        with self.assertRaises(ValueError):
            sheets.sync([['', ''] for _ in range(100)])
        self.assertEqual(sheets.request.call_count, 1)
