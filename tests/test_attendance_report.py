import unittest
from datetime import datetime
from unittest.mock import Mock, AsyncMock
from storage import Store, KST
from bot import make_bot
from operations import attendance_text, send_attendance_report

class ReportTests(unittest.IsolatedAsyncioTestCase):
    async def test_report_timing_counts_recipient_and_once(self):
        store = Store(':memory:')
        store.create('e', 1, '2단거점', '2026-11-06 21:00', 50)
        store.participate('e', 1, 7, 'u', '가문', guild_name='포도당')
        sheets = Mock()
        sheets.role_rows.return_value = [['길드', '가문명', '역할'], ['포도당', '가문', '빌더']]
        bot = make_bot(store, sheets, 1, 456)
        self.addAsyncCleanup(bot.close)
        bot._connection.user = Mock(id=999)
        messages = []
        async def history(**kwargs):
            for msg in messages:
                yield msg
        async def send(**kwargs):
            msg = Mock(id=88, author=Mock(id=999), embeds=[kwargs['embed']])
            messages.append(msg)
            return msg
        channel = Mock(guild=Mock(id=1), history=history, send=AsyncMock(side_effect=send))
        bot.get_channel = Mock(return_value=channel)
        now = datetime(2026, 11, 6, 20, 30, tzinfo=KST)
        await send_attendance_report(bot, now.replace(minute=29))
        channel.send.assert_not_awaited()
        await send_attendance_report(bot, now)
        kwargs = channel.send.call_args.kwargs
        self.assertEqual(kwargs['content'], '<@358871896449613824>')
        self.assertFalse(kwargs['allowed_mentions'].everyone)
        self.assertFalse(kwargs['allowed_mentions'].roles)
        self.assertEqual([u.id for u in kwargs['allowed_mentions'].users], [358871896449613824])
        self.assertIn('부속: 1명', kwargs['embed'].description)
        await send_attendance_report(bot, now)
        channel.send.assert_awaited_once()
        # Recover a successful send whose local acknowledgement was lost.
        store.data['daily_reports'].clear()
        await send_attendance_report(bot, now)
        channel.send.assert_awaited_once()
        self.assertTrue(store.data['daily_reports'])
        await send_attendance_report(bot, now.replace(hour=21, minute=10))
        channel.send.assert_awaited_once()

    async def test_role_grouping_unmatched_and_guild_totals(self):
        roles = ['빌더', '대포', '코끼리', '불퇴', '신기전', '화탑', '화염탑',
                 '꼬장조', '꼬장오더', '본대', '오더', '서브오더', '공군', '알수없음']
        members = [dict(guild_name='고라파덕', nickname=str(i)) for i in range(len(roles)+1)]
        rows = [['길드', '가문명', '역할']] + [['고라파덕', str(i), role] for i, role in enumerate(roles)]
        text = attendance_text(dict(starts='2026-11-06 21:00', name='2단거점', capacity=50), members, 3, rows)
        for part in ('총 참여자: 15명', '고라파덕: 15명', '부속: 7명', '꼬장조: 2명', '본대: 4명', '기타/미확인: 2명', '예비: 3명'):
            self.assertIn(part, text)
