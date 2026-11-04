import unittest
from datetime import datetime
from unittest.mock import Mock, AsyncMock, patch

from storage import Store, KST
from schedule import event_start, require_priority_open
from bot import make_bot, EventView, MercenaryModal, event_content


class CutoffTests(unittest.TestCase):
    def test_exact_boundary_and_siege_exemption(self):
        event = dict(name='2단거점', starts='2026-11-06 21:00')
        require_priority_open(event, datetime(2026, 11, 6, 19, 59, 59, tzinfo=KST))
        with self.assertRaisesRegex(ValueError, '20:00'):
            require_priority_open(event, datetime(2026, 11, 6, 20, tzinfo=KST))
        event['name'] = '공성전'
        require_priority_open(event, datetime(2026, 11, 6, 21, tzinfo=KST))
        self.assertEqual(event_start(datetime(2026, 11, 7).date(), '21:00', 3).hour, 20)


class MercenaryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.store = Store(':memory:')
        self.bot = make_bot(self.store, Mock(), 123)
        self.addAsyncCleanup(self.bot.close)
        self.store.create('e', 123, '1단거점', '2030-01-01 21:00', 1)
        self.view = EventView(self.bot, 'e')
        self.addCleanup(self.view.stop)
        self.interaction = Mock(guild_id=123, permissions=Mock(manage_guild=True))
        self.interaction.response.send_message = AsyncMock()
        self.interaction.response.defer = AsyncMock()

    async def test_manual_add_waitlist_duplicates_and_permissions(self):
        async def add(name):
            modal = MercenaryModal(self.view)
            modal.family._value = name
            await modal.on_submit(self.interaction)
            modal.stop()
        await add('용병하나')
        await add('용병둘')
        self.assertEqual(self.store.roster(123)[:2], [['용병', '용병하나'], ['예비', '용병둘']])
        with self.assertRaises(ValueError):
            await add('용병하나')
        self.interaction.permissions.manage_guild = False
        with self.assertRaises(ValueError):
            await add('무권한')
        self.assertEqual(len(self.store.members('e')), 1)

    async def test_priority_rechecks_after_lookup(self):
        self.interaction.user = Mock(display_name='[포도당] 디움')
        self.bot.sheets.lookup_role.return_value = '화염탑'
        with patch('bot.require_priority_open', side_effect=[None, ValueError('20:00 마감')]):
            with self.assertRaisesRegex(ValueError, '20:00'):
                await self.view.priority.callback(self.interaction)
        self.assertEqual(self.store.members('e'), [])

    async def test_existing_post_refresh_does_not_ping(self):
        self.store.mark_posted('e', 456, 789)
        self.bot._connection.user = Mock(id=999)
        message = Mock(author=Mock(id=999), edit=AsyncMock())
        channel = Mock(guild=Mock(id=123), fetch_message=AsyncMock(return_value=message))
        self.bot.get_channel = Mock(return_value=channel)
        await self.bot.on_ready()
        edited = message.edit.call_args.kwargs
        self.assertFalse(edited['allowed_mentions'].everyone)
        self.assertIn('용병 추가', [item.label for item in edited['view'].children])
        self.assertIn('20:00', edited['content'])
