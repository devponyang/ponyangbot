import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import Mock, AsyncMock, patch
import discord
from storage import Store, KST
from bot import make_bot
from operations import backup_json, deliver_notifications

class BackupTests(unittest.TestCase):
    def test_daily_restore_retention_and_precleanup(self):
        with tempfile.TemporaryDirectory() as folder:
            store = Store(Path(folder) / 'bot.json')
            store.create('e', 1, '거점', '2026-11-06 21:00', 25)
            now = datetime(2026, 11, 6, 20, 30, tzinfo=KST)
            self.assertIsNone(backup_json(store, 'daily', now.replace(hour=20, minute=29)))
            path = backup_json(store, 'daily', now)
            self.assertEqual(Store(path).data, store.data)
            self.assertIsNone(backup_json(store, 'daily', now))
            old = path.parent / 'bot-daily-2026-09-01.json'
            old.write_text('{}')
            other = path.parent / 'legacy-other.json'
            other.write_text('{}')
            before = backup_json(store, 'before-cleanup', now)
            self.assertTrue(before.exists())
            self.assertFalse(old.exists())
            self.assertTrue(other.exists())
            self.assertEqual(json.loads(before.read_text(encoding='utf-8'))['events'][0]['id'], 'e')

class OperationsTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.store = Store(':memory:')
        self.store.create('e', 1, '거점', '2030-01-01 21:00', 1)
        self.bot = make_bot(self.store, Mock(), 1)
        self.addAsyncCleanup(self.bot.close)
        self.channel = Mock(guild=Mock(id=1), send=AsyncMock())
        self.bot.get_channel = Mock(return_value=self.channel)
        self.user = Mock(send=AsyncMock())
        self.bot.get_user = Mock(return_value=self.user)

    def promote(self):
        self.store.participate('e', 1, 11, 'first', '첫째')
        self.store.participate('e', 1, 22, 'second', '둘째')
        self.store.participate('e', 1, 11, 'first', '', join=False)

    async def test_promotion_and_logs_once_without_pings(self):
        self.promote()
        version = self.store.revision
        await deliver_notifications(self.bot)
        self.user.send.assert_awaited_once()
        self.assertIn('승격', self.user.send.call_args.args[0])
        self.assertFalse(self.channel.send.call_args.kwargs['allowed_mentions'].everyone)
        count = self.channel.send.await_count
        await deliver_notifications(self.bot)
        self.assertEqual(self.channel.send.await_count, count)
        self.user.send.assert_awaited_once()
        self.assertEqual(self.store.revision, version)

    async def test_dm_forbidden_logged_and_no_retry(self):
        self.promote()
        self.user.send.side_effect = discord.Forbidden(Mock(status=403, reason='Forbidden'), 'blocked')
        await deliver_notifications(self.bot)
        await deliver_notifications(self.bot)
        self.user.send.assert_awaited_once()
        self.assertTrue(any('승격 DM 실패' in c.kwargs['content'] for c in self.channel.send.call_args_list))

    async def test_failed_logs_retry_while_dm_succeeds(self):
        self.promote()
        self.channel.send.side_effect = discord.Forbidden(Mock(status=403, reason='Forbidden'), 'blocked')
        await deliver_notifications(self.bot)
        self.user.send.assert_awaited_once()
        self.assertTrue(any(not r['done'] for r in self.store.data['notifications']))
        self.channel.send.side_effect = None
        await deliver_notifications(self.bot)
        self.assertTrue(all(r['done'] for r in self.store.data['notifications']))

    async def test_stale_promotion_not_sent(self):
        self.promote()
        self.store.participate('e', 1, 22, 'second', '', join=False)
        await deliver_notifications(self.bot)
        self.user.send.assert_not_awaited()

    async def test_backup_failure_blocks_cleanup(self):
        with patch('bot.backup_json', side_effect=OSError('disk')):
            with self.assertRaises(OSError):
                await self.bot.cleanup_due_events(datetime(2030, 1, 1, 21, 10, tzinfo=KST))
        self.assertFalse(self.store.event('e', 1).get('archived'))

    async def test_pending_delivery_survives_restart_and_failed_save(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'bot.json'
            store = Store(path)
            store.create('e', 1, '거점', '2030-01-01 21:00', 1)
            store.participate('e', 1, 11, 'u', '가문')
            snapshot = store.data.copy()
            with patch.object(store, '_save', side_effect=OSError('disk')):
                with self.assertRaises(OSError):
                    store.participate('e', 1, 11, 'u', '', join=False)
            reloaded = Store(path)
            self.assertEqual(reloaded.data, snapshot)
            self.assertEqual(len(reloaded.data['notifications']), 1)
            self.assertFalse(reloaded.data['notifications'][0]['done'])
