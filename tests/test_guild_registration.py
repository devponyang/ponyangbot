import unittest
from unittest.mock import Mock, AsyncMock
from urllib.parse import unquote
from bot import make_bot, EventView
from storage import Store
from sheets import Sheets


class GuildRegistrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_checks_own_guild_tab_before_saving(self):
        store = Store(':memory:')
        store.create('e', 1, '1단거점', '2035-01-01 21:00', 30)
        sheets = Sheets.__new__(Sheets)
        def read(method, path, **kwargs):
            rows = [['가문명']]
            if unquote(path) == "/values/'포도당 직업시트'!C:C":
                rows.append(['디움'])
            return {'values': rows}
        sheets.lookup_job_and_role = Mock(return_value=('위치', '본대'))
        sheets.request = Mock(side_effect=read)
        bot = make_bot(store, sheets, 1)
        self.addAsyncCleanup(bot.close)
        view = EventView(bot, 'e')
        self.addCleanup(view.stop)
        interaction = Mock(guild_id=1, user=Mock(id=7, roles=[]))
        interaction.response.defer = AsyncMock()
        interaction.followup.send = AsyncMock()
        for button in (view.join, view.priority):
            for name in ('디움', '[싸움은나빵] 디움', '[포도당] 디움2', '[용병] 디움'):
                interaction.user.display_name = name
                with self.assertRaises(ValueError):
                    await button.callback(interaction)
                self.assertEqual(store.members('e'), [])
        interaction.user.display_name = '[포도당] 디움'
        await view.join.callback(interaction)
        self.assertEqual(store.roster(1)[0], ['포도당', '디움'])
