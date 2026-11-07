import unittest
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import discord
from operations import delete_due_reports
from storage import KST, Store


class ReportCleanupTests(unittest.IsolatedAsyncioTestCase):
    async def test_only_report_messages_deleted_at_nine_and_retry_after_restart(self):
        store = Store(':memory:')
        day = '2026-11-08'
        attendance = '1:' + day
        voice = 'voice:1:' + day
        store.mark_report_sent(attendance, 11)  # Earlier release, same day.
        store.mark_report_sent(voice + ':0', 12, channel_id=456)
        store.mark_report_sent(voice + ':1', 13, channel_id=456)
        store.mark_report_sent(voice, 'complete')
        store.mark_report_sent('1:2026-11-07', 14)  # Pre-upgrade historical post.
        store.mark_report_sent('1:2026-11-09', 15, channel_id=456)
        messages = {}
        for mid, marker in [(11, '참여현황 ID: ' + attendance),
                            (12, '음성확인 ID: ' + voice + ':0'),
                            (13, '음성확인 ID: ' + voice + ':1')]:
            messages[mid] = Mock(author=Mock(id=999), embeds=[discord.Embed().set_footer(text=marker)], delete=AsyncMock())
        messages[13].delete.side_effect = discord.Forbidden(Mock(status=403, reason='Forbidden'), 'permission')
        channel = Mock(guild=Mock(id=1), fetch_message=AsyncMock(side_effect=messages.__getitem__))
        bot = SimpleNamespace(store=store, guild_id=1, auto_channel_id=456, user=Mock(id=999), get_channel=Mock(return_value=channel))
        now = datetime(2026, 11, 8, 21, tzinfo=KST)
        await delete_due_reports(bot, now - timedelta(seconds=1))
        channel.fetch_message.assert_not_awaited()
        await delete_due_reports(bot, now)
        self.assertEqual(store.data['daily_reports'][attendance], 'deleted')
        self.assertEqual(store.data['daily_reports'][voice + ':0'], 'deleted')
        self.assertEqual(store.data['daily_reports'][voice + ':1'], '13')
        messages[13].delete.side_effect = None
        await delete_due_reports(bot, now + timedelta(hours=7))
        self.assertEqual(store.data['daily_reports'][voice + ':1'], 'deleted')
        messages[11].delete.assert_awaited_once()
        self.assertEqual(store.data['daily_reports']['1:2026-11-09'], '15')
