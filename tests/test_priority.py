import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch, AsyncMock

from identity import parse_display_name
from sheets import Sheets, find_role
from storage import Store, ROLE_LIMITS
from bot import make_bot, EventView


class PriorityTests(unittest.TestCase):
    def setUp(self):
        self.store = Store(':memory:')
        self.store.create('event', 1, '1단거점', '2030-01-01 21:00', 2)

    def join(self, uid, role=None):
        return self.store.participate('event', 1, uid, f'u{uid}', f'가문{uid}',
            guild_name='Probe', display_name=f'[P]가문{uid}', priority_role=role)

    def test_bump_waitlist_cancel_promotes_and_clears_role_quota(self):
        self.join(1)
        self.join(2)
        self.join(3, '화염탑')
        self.assertEqual([p['user_id'] for p in self.store.members('event')], ['3', '1'])
        self.assertEqual([p['user_id'] for p in self.store.members('event', waiting=True)], ['2'])
        self.assertEqual(self.store.roster(1)[:2], [['Probe', '가문3'], ['Probe', '가문1']])
        self.store.participate('event', 1, 3, '', '', join=False)
        self.assertEqual([p['user_id'] for p in self.store.members('event')], ['1', '2'])
        self.join(4, '화염탑')
        self.assertEqual(self.store.members('event')[0]['user_id'], '4')

    def test_all_role_quotas_and_idempotency(self):
        self.store.create('event2', 1, '1단거점', '2030-01-02 21:00', 30)
        for role, cap in ROLE_LIMITS.items():
            for i in range(cap):
                uid = role + str(i)
                for repeat in range(2):
                    self.store.participate('event2', 1, uid, uid, uid, guild_name='포도당', priority_role=role)
            before = self.store.snapshot()
            with self.assertRaisesRegex(ValueError, '일반 참여 버튼'):
                self.store.participate('event2', 1, role+'extra', '', role+'extra', guild_name='포도당', priority_role=role)
            self.assertEqual(before, self.store.snapshot())
        self.assertEqual(len(self.store.members('event2')), 7)

    def test_upgrade_normal_participant_and_waiter(self):
        self.join(1)
        self.join(2)
        self.join(3, '대포')
        self.join(2, '신기전')
        self.assertEqual([p['user_id'] for p in self.store.members('event')], ['3', '2'])
        self.assertEqual(self.store.members('event', waiting=True)[0]['user_id'], '1')
        # Regular participation cannot silently remove priority, nor change identity.
        self.join(2)
        self.assertEqual(self.store.members('event')[1]['priority_role'], '신기전')
        with self.assertRaises(ValueError):
            self.store.participate('event', 1, 2, '', '다른가문', guild_name='Probe')

    def test_lookup_repeated_blocks_and_probe_alias(self):
        self.assertEqual(parse_display_name('[P]디움'), ('Probe', '디움'))
        rows = [['길드', '가문명', '역 할', '', '길드', '가문명', '역 할', '', '길드', '가문명', '역 할'],
                ['포도당', '가문1', '본대', '', 'Probe', '디움', '대포', '', '접어', '가문3', '화염탑']]
        self.assertEqual(find_role(rows, 'P', '디움'), '대포')
        self.assertEqual(find_role(rows, '접어', '가문3'), '화염탑')
        with self.assertRaises(ValueError):
            find_role(rows, '포도당', '디움')
        with self.assertRaisesRegex(ValueError, '중복'):
            find_role(rows + [rows[1]], 'Probe', '디움')


class JsonTests(unittest.TestCase):
    def test_persistence_and_failed_write_rollback(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'bot.json'
            store = Store(path)
            store.create('event', 1, '1단거점', '2030-01-01 21:00', 1)
            store.participate('event', 1, 1, 'u', '가문', guild_name='Probe', priority_role='대포')
            store.mark_posted('event', 123, 456)
            reopened = Store(path)
            self.assertEqual(reopened.members('event')[0]['priority_role'], '대포')
            self.assertTrue(reopened.posted('event'))
            before = path.read_bytes()
            with patch('json_store.os.replace', side_effect=OSError('disk full')):
                with self.assertRaises(OSError):
                    store.close_event('event', 1)
            self.assertFalse(store.event('event', 1)['closed'])
            self.assertEqual(path.read_bytes(), before)
            path.write_text('{broken', encoding='utf-8')
            with self.assertRaises(json.JSONDecodeError):
                Store(path)

    def test_sqlite_migration_preserves_original_and_auto_posts(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'bot.db'
            db = sqlite3.connect(path)
            db.executescript('''
                CREATE TABLE events (id TEXT, guild_id TEXT, name TEXT, starts TEXT, capacity INTEGER, closed INTEGER);
                INSERT INTO events VALUES ('e', '1', '거점', '2030-01-01 21:00', 25, 0);
                CREATE TABLE participants (event_id TEXT, user_id TEXT, username TEXT, nickname TEXT, status TEXT, updated TEXT, guild_name TEXT);
                INSERT INTO participants VALUES ('e', '5', 'user', '디움', '참여', 'now', 'P');
                CREATE TABLE auto_posts (event_id TEXT, channel_id TEXT, message_id TEXT);
                INSERT INTO auto_posts VALUES ('e', '123', '456');
                CREATE TABLE revision (id INTEGER, value INTEGER);
                INSERT INTO revision VALUES (1, 4);
            ''')
            db.close()
            before = path.read_bytes()
            store = Store(path)
            self.assertEqual(store.roster(1)[0], ['Probe', '디움'])
            self.assertTrue(store.posted('e'))
            self.assertEqual(path.read_bytes(), before)
            self.assertTrue(path.with_suffix('.json').exists())

    def test_legacy_sheet_backup_before_delete(self):
        with tempfile.TemporaryDirectory() as directory:
            sheets = Sheets.__new__(Sheets)
            sheets.request = Mock(side_effect=[{'sheets': [
                {'properties': {'title': '봇_거점', 'sheetId': 4}},
                {'properties': {'title': '거점시트', 'sheetId': 5}}]}, {'values': [['기존기록']]}, {}])
            sheets.archive_legacy_tabs(directory)
            saved = json.loads(next(Path(directory).glob('*.json')).read_text(encoding='utf-8'))
            self.assertEqual(saved['tabs'][0]['values'], [['기존기록']])
            self.assertEqual(sheets.request.call_args.kwargs['json'], {'requests': [{'deleteSheet': {'sheetId': 4}}]})

    def test_failed_backup_never_deletes_tabs(self):
        sheets = Sheets.__new__(Sheets)
        sheets.request = Mock(side_effect=[{'sheets': [
            {'properties': {'title': '봇_참여', 'sheetId': 4}}]}, {'values': [['기록']]}])
        with patch('sheets.Path.mkdir', side_effect=OSError('disk full')):
            with self.assertRaises(OSError):
                sheets.archive_legacy_tabs('unused-backup-path')
        self.assertTrue(all(call.args[0] == 'GET' for call in sheets.request.call_args_list))


class PriorityButtonTests(unittest.IsolatedAsyncioTestCase):
    async def test_button_lookup_and_waitlist_messages(self):
        store = Store(':memory:')
        store.create('e', 1, '1단거점', '2030-01-01 21:00', 1)
        store.participate('e', 1, 9, 'u9', '일반', guild_name='포도당')
        sheets = Mock()
        sheets.lookup_role.return_value = '신기전'
        bot = make_bot(store, sheets, 1)
        view = EventView(bot, 'e')
        interaction = Mock(guild_id=1)
        interaction.user = Mock(id=1, display_name='[P]디움')
        interaction.response.defer = AsyncMock()
        interaction.response.send_message = AsyncMock()
        interaction.followup.send = AsyncMock()
        await view.priority.callback(interaction)
        sheets.lookup_role.assert_called_once_with('Probe', '디움')
        self.assertEqual(store.roster(1)[0], ['Probe', '디움'])
        self.assertIn('예비로 전환', interaction.followup.send.call_args.args[0])
        await view.members.callback(interaction)
        self.assertIn('예비 인원', interaction.response.send_message.call_args.args[0])
        # Network failures and ineligible roles cannot enroll a participant.
        interaction.user = Mock(id=2, display_name='[P]다른가문')
        sheets.lookup_role.side_effect = RuntimeError('offline')
        with self.assertRaisesRegex(ValueError, '조회하지 못'):
            await view.priority.callback(interaction)
        self.assertEqual(len(store.members('e')), 1)
        view.stop()
        await bot.close()
