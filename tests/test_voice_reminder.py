import unittest
from datetime import datetime
from unittest.mock import Mock, AsyncMock
import discord
from storage import Store, KST
from bot import make_bot
from operations import send_voice_reminder

class VoiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_only_absent_confirmed_members_and_no_repeat(self):
        store = Store(':memory:')
        store.create('e', 1, '2단거점', '2026-11-06 21:00', 4)
        for uid in (11, 22, 33, 'merc-1', 44):
            store.participate('e', 1, uid, '', str(uid))
        store.participate('e', 1, 33, '', '', join=False)
        bot = make_bot(store, Mock(), 1, 456)
        self.addAsyncCleanup(bot.close)
        bot._connection.user = Mock(id=999)
        voice = Mock(spec=discord.VoiceChannel, guild=Mock(id=1), voice_states={11: Mock()})
        voice.permissions_for.return_value.view_channel = True
        messages = []
        async def history(**kwargs):
            for msg in messages:
                yield msg
        async def send(**kwargs):
            msg = Mock(id=88, author=Mock(id=999), embeds=[kwargs['embed']])
            messages.append(msg)
            return msg
        channel = Mock(guild=Mock(id=1), history=history, send=AsyncMock(side_effect=send))
        bot.get_channel = Mock(side_effect=lambda cid: voice if cid == 1209420952082063381 else channel)
        now = datetime(2026, 11, 6, 20, 35, tzinfo=KST)
        await send_voice_reminder(bot, now.replace(minute=34))
        channel.send.assert_not_awaited()
        await send_voice_reminder(bot, now)
        kwargs = channel.send.call_args.kwargs
        self.assertEqual(kwargs['content'], '<@22> <@44>')
        self.assertIn('계정 미연결 용병: 1명', kwargs['embed'].description)
        self.assertFalse(kwargs['allowed_mentions'].everyone)
        self.assertFalse(kwargs['allowed_mentions'].roles)
        self.assertEqual([u.id for u in kwargs['allowed_mentions'].users], [22, 44])
        await send_voice_reminder(bot, now)
        channel.send.assert_awaited_once()
        store.data['daily_reports'].clear()
        await send_voice_reminder(bot, now)
        channel.send.assert_awaited_once()
        await send_voice_reminder(bot, now.replace(minute=40))
        channel.send.assert_awaited_once()
