import unittest
from unittest.mock import Mock, AsyncMock
from datetime import datetime
import discord

from bot import EventView, make_bot, event_content
from storage import KST, Store


class BotTests(unittest.IsolatedAsyncioTestCase):
    async def test_test_command_uses_live_roster_and_mentions(self):
        self.store.create('live', 123, '거점', '2030-01-01 21:00', 30)
        interaction = Mock(guild_id=123)
        interaction.response.defer = AsyncMock()
        interaction.followup.send = AsyncMock()
        interaction.channel.send = AsyncMock()
        await self.bot.tree.get_command('거점테스트').callback(interaction, 1)
        posted = interaction.channel.send.call_args.kwargs
        view = posted['view']
        interaction.user = Mock(id=77, display_name='[포도당]디움')
        await view.join.callback(interaction)
        self.assertEqual(view.bot.store.roster(123)[0], ['포도당', '디움'])
        self.assertEqual(self.store.latest_event(123)['id'], view.event_id)
        self.assertIn('@everyone', posted['content'])
        self.assertTrue(posted['allowed_mentions'].everyone)
        self.assertTrue(view.is_persistent())
        await self.bot.sync_sheet()
        self.assertEqual(self.sheets.sync.call_args.args[0][0], ['포도당', '디움'])
        await view.cancel.callback(interaction)
        self.assertEqual(view.bot.store.members(view.event_id), [])
        interaction.permissions.manage_guild = True
        self.store.close_event(view.event_id, 123)
        with self.assertRaises(ValueError):
            await view.join.callback(interaction)
        view.stop()

    async def test_auto_post_once_and_recover_sent_message(self):
        self.bot.auto_channel_id = 456
        self.bot._connection.user = Mock(id=999)
        channel = Mock(spec=discord.TextChannel)
        channel.id = 456
        channel.guild = Mock(id=123)
        sent = []

        async def history(**kwargs):
            self.assertEqual(kwargs["after"], datetime(2026, 10, 26, 21, 30, tzinfo=KST))
            for message in sent:
                yield message

        async def send(**kwargs):
            message = Mock(id=789, author=Mock(id=999), embeds=[],
                           components=[Mock(children=kwargs['view'].children)])
            sent.append(message)
            return message

        channel.history = history
        channel.send = AsyncMock(side_effect=send)
        self.bot.get_channel = Mock(return_value=channel)
        now = datetime(2026, 10, 26, 21, 30, tzinfo=KST)
        await self.bot.post_due_event(now)
        await self.bot.post_due_event(now)
        self.assertEqual(channel.send.call_count, 1)
        posted = channel.send.call_args.kwargs
        self.assertIn('# **__10월/27일__** 화요일 거점전', posted['content'])
        self.assertIn('참여인원 : 총 30인', posted['content'])
        self.assertTrue(posted['allowed_mentions'].everyone)
        event_id = 'auto-123-2026-10-27'
        self.assertEqual(self.store.event(event_id, 123)['capacity'], 30)
        # Simulate losing the acknowledgement after Discord accepted the post.
        self.store.data['auto_posts'].clear()
        await self.bot.post_due_event(now)
        self.assertEqual(channel.send.call_count, 1)
        self.assertTrue(self.store.posted(event_id))

    async def test_no_post_for_saturday_or_started_event(self):
        self.bot.get_channel = Mock()
        await self.bot.post_due_event(datetime(2026, 10, 30, 22, tzinfo=KST))
        await self.bot.post_due_event(datetime(2026, 10, 27, 21, 29, tzinfo=KST))
        self.bot.get_channel.assert_not_called()

    async def test_join_uses_server_nickname_without_modal(self):
        self.store.create('a', 123, '1단거점', '2030-01-01 21:00', 30)
        interaction = Mock(guild_id=123)
        interaction.user = Mock(id=77, display_name='[포도당]디움')
        interaction.response.defer = AsyncMock()
        interaction.followup.send = AsyncMock()
        view = EventView(self.bot, 'a')
        await view.join.callback(interaction)
        self.assertEqual(self.store.roster(123)[0], ['포도당', '디움'])
        self.assertEqual(interaction.followup.send.call_args.args[0],
                         '<@77>\n\n거점 신청이 완료되었습니다.\n\n1번째 순서')
        self.assertEqual(interaction.followup.send.call_args.kwargs['allowed_mentions'].users[0].id, 77)
        interaction.response.send_modal.assert_not_called()
        self.store.create('b', 123, '1단거점', '2030-01-02 21:00', 25)
        with self.assertRaises(ValueError):
            await view.join.callback(interaction)

    async def test_roster_uses_event_date_tier_and_server_names(self):
        self.store.create('a', 123, '2단거점', '2030-01-02 21:00', 30)
        self.store.participate('a', 123, 1, '_dium.', '디움', guild_name='포도당', display_name='[포도당] 디움')
        self.store.participate('a', 123, 2, 'account', '가문', guild_name='Probe', display_name='[Probe]가문')
        interaction = Mock(guild_id=123)
        interaction.response.send_message = AsyncMock()
        view = EventView(self.bot, 'a')
        self.assertEqual(view.members.label, '참여 인원 현황')
        await view.members.callback(interaction)
        content = interaction.response.send_message.call_args.args[0]
        self.assertEqual(content, '1월2일 2단 거점 신청 현황\n\n1. [포도당] 디움\n2. [Probe]가문')

    async def asyncSetUp(self):
        self.store = Store(':memory:')
        self.sheets = Mock()
        self.bot = make_bot(self.store, self.sheets, 123)

    async def asyncTearDown(self):
        await self.bot.close()
        self.store.close()

    async def test_persistent_button_ids_are_stable_and_unique(self):
        first = EventView(self.bot, 'event')
        restored = EventView(self.bot, 'event')
        other = EventView(self.bot, 'other')
        self.assertTrue(first.is_persistent())
        ids = lambda view: [item.custom_id for item in view.children]
        self.assertEqual(ids(first), ids(restored))
        self.assertFalse(set(ids(first)) & set(ids(other)))
        self.assertEqual(len(set(ids(first))), 5)

    async def test_sync_failure_retries_without_marking_saved(self):
        self.sheets.sync.side_effect = RuntimeError('offline')
        await self.bot.sync_sheet()
        self.assertTrue(self.bot.sync_error)
        self.assertEqual(self.bot.synced_version, -1)
        self.sheets.sync.side_effect = None
        await self.bot.sync_sheet()
        self.assertFalse(self.bot.sync_error)
        self.assertEqual(self.bot.synced_version, 0)
        await self.bot.sync_sheet()
        self.assertEqual(self.sheets.sync.call_count, 2)

    async def test_commands_require_server_management(self):
        for command in self.bot.tree.get_commands():
            self.assertTrue(command.default_permissions.manage_guild)
            self.assertTrue(command.checks)

    async def test_mercenary_button_replaces_close(self):
        self.store.create('event', 123, '1단거점', '2030-01-01 21:00', 25)
        interaction = Mock(guild_id=123)
        interaction.permissions.manage_guild = True
        interaction.response.send_modal = AsyncMock()
        view = EventView(self.bot, 'event')
        await view.add_mercenary.callback(interaction)
        interaction.response.send_modal.assert_awaited_once()
        self.assertNotIn('모집 마감', [item.label for item in view.children])
        self.assertIn('용병 추가', [item.label for item in view.children])
        self.assertFalse(self.store.event('event', 123)['closed'])
        interaction.permissions.manage_guild = False
        with self.assertRaises(ValueError):
            await view.add_mercenary.callback(interaction)
        view.stop()
