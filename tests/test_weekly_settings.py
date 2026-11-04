import tempfile
import unittest
from pathlib import Path
from datetime import datetime
from unittest.mock import Mock, AsyncMock

from storage import Store, KST
from schedule import scheduled_capacity, due_event_date
import discord
from bot import event_content, make_bot
from admin_panel import SchedulePanel


class WeeklySettingsTests(unittest.TestCase):
    def test_persists_and_isolates_guilds(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'bot.json'
            store = Store(path)
            store.set_weekly_tier(123, 0, 2)
            restored = Store(path)
            self.assertEqual(restored.weekly_tiers(123)[0], 2)
            self.assertEqual(restored.weekly_tiers(456)[0], 0)
            restored.set_weekly_tier(123, 0, 0)
            self.assertEqual(Store(path).weekly_tiers(123)[0], 0)

    def test_capacity_and_enabled_monday(self):
        tiers = [2, 2, 2, 2, 2, 0, 2]
        for day, expected in enumerate([40, 40, 40, 40, 50, None, 50], 2):
            self.assertEqual(scheduled_capacity(datetime(2026, 11, day, 21, tzinfo=KST), tiers), expected)
        self.assertEqual(str(due_event_date(datetime(2026, 11, 1, 21, 30, tzinfo=KST), tiers)), '2026-11-02')
        self.assertIsNone(due_event_date(datetime(2026, 11, 1, 21, 30, tzinfo=KST)))

    def test_saturday_rejected_and_existing_event_preserved(self):
        store = Store(':memory:')
        store.create('e', 123, '1단거점', '2026-11-03 21:00', 30)
        store.set_weekly_tier(123, 1, 2)
        self.assertEqual(store.event('e', 123)['capacity'], 30)
        with self.assertRaises(ValueError):
            store.set_weekly_tier(123, 5, 2)

    def test_tier_two_notice(self):
        content = event_content(dict(name='2단거점', starts='2026-11-06 21:00', capacity=50, closed=0))
        self.assertIn('칼페온, 올루키타', content)
        self.assertIn('(2단 제한 거점입니다.)', content)
        self.assertIn('총 50인', content)
        self.assertNotIn('발레노스', content)


class PanelTests(unittest.IsolatedAsyncioTestCase):
    async def test_permission_check_and_save(self):
        store = Store(':memory:')
        view = SchedulePanel(store, 123, 7)
        interaction = Mock(guild_id=123, user=Mock(id=7), permissions=Mock(manage_guild=True))
        interaction.response.send_message = AsyncMock()
        interaction.response.edit_message = AsyncMock()
        self.assertTrue(await view.interaction_check(interaction))
        view.select_day._values = ['4']
        await view.select_day.callback(interaction)
        view.select_tier._values = ['2']
        await view.select_tier.callback(interaction)
        self.assertEqual(store.weekly_tiers(123)[4], 2)
        self.assertIn('금요일: 2단 · 50인', view.content())
        interaction.permissions.manage_guild = False
        self.assertFalse(await view.interaction_check(interaction))
        interaction.permissions.manage_guild = True
        interaction.user.id = 8
        self.assertFalse(await view.interaction_check(interaction))
        view.stop()

    async def test_automatic_post_uses_saved_tier_and_capacity(self):
        store = Store(':memory:')
        store.set_weekly_tier(123, 4, 2)
        bot = make_bot(store, Mock(), 123, 456)
        self.addAsyncCleanup(bot.close)
        bot._connection.user = Mock(id=999)
        channel = Mock(spec=discord.TextChannel, id=456, guild=Mock(id=123))
        async def history(**kwargs):
            for item in []:
                yield item
        channel.history = history
        channel.send = AsyncMock(return_value=Mock(id=789))
        bot.get_channel = Mock(return_value=channel)
        await bot.post_due_event(datetime(2026, 11, 5, 21, 30, tzinfo=KST))
        event = store.latest_event(123)
        self.assertEqual((event['name'], event['capacity']), ('2단거점', 50))
        self.assertIn('칼페온, 올루키타', channel.send.call_args.kwargs['content'])
        store.set_weekly_tier(123, 4, 1)
        await bot.post_due_event(datetime(2026, 11, 5, 22, tzinfo=KST))
        channel.send.assert_awaited_once()
        self.assertEqual(store.latest_event(123)['capacity'], 50)

    async def test_siege_options_next_event_and_saved_schedule(self):
        from schedule import mode_name, capacity_for
        store = Store(':memory:')
        view = SchedulePanel(store, 123, 7)
        interaction = Mock()
        interaction.response.edit_message = AsyncMock()
        view.select_day._values = ['5']
        await view.select_day.callback(interaction)
        self.assertEqual([o.value for o in view.select_tier.options], ['0', '3', '4', '5'])
        for mode in (3, 4, 5):
            view.select_tier._values = [str(mode)]
            await view.select_tier.callback(interaction)
            self.assertEqual(store.weekly_tiers(123)[5], mode)
            self.assertEqual(capacity_for(5, mode), 100)
            content = event_content(dict(name=mode_name(mode), starts='2026-11-07 21:00', capacity=100, closed=0))
            self.assertIn('토요일 공성전', content)
            self.assertIn('총 100인', content)
            self.assertIn('정제수', content)
            self.assertNotIn('1단 제한', content)
        upcoming = view.next_event_text(datetime(2026, 11, 6, 21, 30, tzinfo=KST))
        self.assertIn('토요일', upcoming)
        self.assertIn('무제한 메디아 성', upcoming)
        store.create('existing', 123, '2단거점', '2026-11-06 21:00', 50)
        store.set_weekly_tier(123, 4, 1)
        self.assertIn('2단거점 · 50인', view.next_event_text(datetime(2026, 11, 6, 20, tzinfo=KST)))
        for day in range(7):
            store.set_weekly_tier(123, day, 0)
        self.assertIn('설정된 일정 없음', view.next_event_text(datetime(2026, 11, 6, 22, tzinfo=KST)))
        with self.assertRaises(ValueError):
            store.set_weekly_tier(123, 1, 3)
        view.stop()
