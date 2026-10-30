import unittest
from unittest.mock import Mock

from storage import Store
from sheets import Sheets


class ReserveTests(unittest.TestCase):
    def test_25_and_30_capacity_reserves_and_promotion(self):
        for capacity in (25, 30):
            with self.subTest(capacity=capacity):
                store = Store(':memory:')
                store.create('e', 1, '1단거점', '2026-10-28 21:00', capacity)
                for i in range(capacity + 2):
                    store.participate('e', 1, i, str(i), f'가문{i}', guild_name='Probe')
                self.assertEqual(store.roster(1)[capacity], ['예비', f'가문{capacity}'])
                store.participate('e', 1, 1, '', '', join=False)
                rows = store.roster(1)
                self.assertEqual(rows[1], ['Probe', '가문2'])
                self.assertEqual(rows[capacity - 1], ['Probe', f'가문{capacity}'])
                self.assertEqual(rows[capacity], ['예비', f'가문{capacity + 1}'])
                self.assertEqual(rows[capacity + 1], ['', ''])

    def test_cancel_middle_compacts_and_new_event_clears_reserves(self):
        store = Store(':memory:')
        store.create('e', 1, '1단거점', '2026-10-28 21:00', 2)
        for i, name in enumerate(['디움', '달야탐주', '엔클레이브']):
            store.participate('e', 1, i, str(i), name, guild_name='포도당')
        store.participate('e', 1, 1, '', '', join=False)
        self.assertEqual(store.roster(1)[:3], [['포도당', '디움'], ['포도당', '엔클레이브'], ['', '']])
        store.create('next', 1, '1단거점', '2026-10-29 21:00', 30)
        self.assertEqual(store.roster(1), [['', ''] for _ in range(100)])

    def test_sheet_title_matches_event_not_current_date(self):
        sheets = Sheets.__new__(Sheets)
        sheets.request = Mock(side_effect=[{'sheets': [{'properties': {'title': '거점시트',
            'gridProperties': {'rowCount': 115, 'columnCount': 18}}}]}, {}])
        rows = [['', ''] for _ in range(100)]
        sheets.sync(rows, {'starts': '2026-10-28 21:00', 'capacity': 25})
        payload = sheets.request.call_args.kwargs['json']
        self.assertEqual(payload['data'], [
            {'range': "'거점시트'!K16:L115", 'values': rows},
            {'range': "'거점시트'!F2", 'values': [['2026-10-28 수요일 25인 거점전 참여명단']]}])
        self.assertEqual(payload['valueInputOption'], 'RAW')

    def test_sheet_limit_does_not_lose_accepted_members(self):
        store = Store(':memory:')
        store.create('e', 1, '거점', '2030-01-01 21:00', 30)
        for i in range(100):
            store.participate('e', 1, i, str(i), str(i), guild_name='Probe')
        with self.assertRaisesRegex(ValueError, '100명'):
            store.participate('e', 1, 100, '', '추가', guild_name='Probe')
        self.assertEqual(len(store.members('e', waiting=True)), 70)
