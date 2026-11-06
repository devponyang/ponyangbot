import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, AsyncMock, patch
from storage import Store
from bot import make_bot

class HistoryTests(unittest.TestCase):
    def test_history_persists_and_failed_mutation_does_not_log(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'bot.json'
            store = Store(path)
            store.create('a', 1, '거점', '2030-01-01 21:00', 1)
            store.participate('a', 1, 7, 'u', '가문', guild_name='포도당')
            store.participate('a', 1, 7, 'u', '', join=False)
            store.participate('a', 1, 7, 'u', '가문', guild_name='포도당')
            rows = Store(path).participation_history(1)
            self.assertEqual([r['action'] for r in rows], ['참여 신청', '신청 취소', '재신청'])
            self.assertEqual(rows[1]['nickname'], '가문')
            with patch.object(store, '_save', side_effect=OSError('disk')):
                with self.assertRaises(OSError):
                    store.participate('a', 1, 7, 'u', '', join=False)
            self.assertEqual(store.participation_history(1), rows)
            self.assertEqual(store.participation_history(2), [])
            self.assertEqual(store.participation_history(1, '2030-01-02'), [])
            self.assertEqual(store.participation_history(1, user_id=8), [])
            self.assertEqual(len(store.participation_history(1, family='가문')), 3)

    def test_legacy_timestamp_preserved_before_cancel(self):
        store = Store(':memory:')
        store.create('a', 1, '거점', '2030-01-01 21:00', 1)
        store.participate('a', 1, 7, 'u', '가문')
        del store.data['participation_history']
        stamp = store.data['participants'][0]['updated']
        store.participate('a', 1, 7, 'u', '', join=False)
        rows = store.participation_history(1)
        self.assertIn('전체 이력 없음', rows[0]['action'])
        self.assertEqual(rows[0]['at'], stamp)
        self.assertEqual(rows[1]['action'], '신청 취소')

    def test_waitlist_promotion_and_mercenary_actor(self):
        store = Store(':memory:')
        store.create('a', 1, '거점', '2030-01-01 21:00', 1)
        store.participate('a', 1, 7, 'u', '가문')
        store.participate('a', 1, 'merc-1', '', '용병', actor_id=99)
        store.participate('a', 1, 7, 'u', '', join=False)
        rows = store.participation_history(1, user_id='merc-1')
        self.assertEqual(rows[0]['action'], '용병 추가')
        self.assertEqual(rows[0]['actor_id'], '99')
        self.assertEqual(rows[0]['status'], '대기')
        self.assertEqual(rows[1]['action'], '자동 순번 변경')
        self.assertEqual(rows[1]['status'], '참여')

class HistoryCommandTests(unittest.IsolatedAsyncioTestCase):
    async def test_command_private_filtered_and_file_output(self):
        store = Store(':memory:')
        store.create('a', 1, '거점', '2030-01-01 21:00', 30)
        bot = make_bot(store, Mock(), 1)
        self.addAsyncCleanup(bot.close)
        command = bot.tree.get_command('참여기록')
        self.assertTrue(command.default_permissions.manage_guild)
        self.assertTrue(command.checks)
        interaction = Mock(guild_id=1)
        interaction.response.send_message = AsyncMock()
        store.participate('a', 1, 7, 'u', '가문')
        await command.callback(interaction, None, None, '가문')
        args = interaction.response.send_message.call_args
        self.assertIn('참여 신청', args.args[0])
        self.assertTrue(args.kwargs['ephemeral'])
        self.assertFalse(args.kwargs['allowed_mentions'].everyone)
        for _ in range(100):
            store.participate('a', 1, 7, 'u', '가문')
        await command.callback(interaction, '2030-01-01', None, None)
        self.assertIn('file', interaction.response.send_message.call_args.kwargs)
        with self.assertRaises(ValueError):
            await command.callback(interaction, '틀린날짜', None, None)
