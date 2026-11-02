import unittest
from datetime import datetime
from unittest.mock import Mock, AsyncMock

from bot import make_bot
from schedule import cleanup_cutoff
from storage import Store, KST
from sheets import Sheets


class CleanupTests(unittest.IsolatedAsyncioTestCase):
    async def test_cleanup_at_2110_and_future_event_preserved(self):
        store = Store(':memory:')
        sheets = Mock()
        bot = make_bot(store, sheets, 123)
        self.addAsyncCleanup(bot.close)
        bot._connection.user = Mock(id=999)
        store.create('today', 123, '1단거점', '2026-10-30 21:00', 25)
        store.participate('today', 123, 1, 'u', '가문', guild_name='포도당')
        store.mark_posted('today', 456, 789)
        channel = Mock(id=456, guild=Mock(id=123))
        message = Mock(author=Mock(id=999))
        message.delete = AsyncMock()
        channel.fetch_message = AsyncMock(return_value=message)
        bot.get_channel = Mock(return_value=channel)
        await bot.cleanup_due_events(datetime(2026, 10, 30, 21, 9, 59, tzinfo=KST))
        channel.fetch_message.assert_not_called()
        sheets.sync.assert_not_called()
        await bot.cleanup_due_events(datetime(2026, 10, 30, 21, 10, tzinfo=KST))
        message.delete.assert_awaited_once()
        self.assertTrue(store.event('today', 123)['closed'])
        self.assertEqual(store.roster(123), [['', ''] for _ in range(100)])
        self.assertEqual(len(store.members('today')), 1)  # Archive retains history.
        self.assertTrue(sheets.sync.call_args.args[1]['archived'])
        await bot.cleanup_due_events(datetime(2026, 10, 30, 21, 11, tzinfo=KST))
        message.delete.assert_awaited_once()
        store.create('future', 123, '1단거점', '2026-11-01 21:00', 30)
        store.mark_posted('future', 456, 790)
        await bot.cleanup_due_events(datetime(2026, 10, 31, 22, 1, tzinfo=KST))
        self.assertFalse(store.event('future', 123)['closed'])
        message.delete.assert_awaited_once()

    async def test_failed_delete_retries(self):
        store = Store(':memory:')
        bot = make_bot(store, Mock(), 123)
        self.addAsyncCleanup(bot.close)
        store.create('e', 123, '거점', '2026-10-30 21:00', 25)
        store.mark_posted('e', 456, 789)
        bot.fetch_channel = AsyncMock(side_effect=RuntimeError('offline'))
        bot.get_channel = Mock(return_value=None)
        with self.assertRaises(RuntimeError):
            await bot.cleanup_due_events(datetime(2026, 10, 30, 22, tzinfo=KST))
        self.assertEqual(len(store.cleanup_posts(123)), 1)

    async def test_reset_clears_title_and_only_bot_inputs(self):
        sheets = Sheets.__new__(Sheets)
        sheets.request = Mock(side_effect=[{'sheets': [{'properties': {'title': '거점시트',
            'gridProperties': {'rowCount': 115, 'columnCount': 18}}}]}, {}])
        sheets.sync([['', ''] for _ in range(100)], {'archived': True})
        data = sheets.request.call_args.kwargs['json']['data']
        self.assertEqual([r['range'] for r in data], ["'거점시트'!K16:L115", "'거점시트'!F2"])
        self.assertEqual(data[1]['values'], [['']])

    async def test_cutoff_recovers_previous_day_before_2110(self):
        self.assertEqual(cleanup_cutoff(datetime(2026, 10, 31, 9, tzinfo=KST)),
                         datetime(2026, 10, 30, 21, 10, tzinfo=KST))
