import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, MagicMock, AsyncMock

from storage import Store
from welcome import WelcomeService, welcome_config, welcome_content, PODODANG_GUILD_ID


class WelcomeTests(unittest.IsolatedAsyncioTestCase):
    def make_guild(self, guild_id):
        roles = []
        config = welcome_config(guild_id, 1209393579848507465)
        for i, (_, name, role_id) in enumerate(config['roles'], 1):
            role = MagicMock(id=role_id or i, name=name, managed=False)
            role.name = name
            role.is_default.return_value = False
            role.__ge__.return_value = False
            roles.append(role)
        guild = Mock(id=guild_id, roles=roles)
        guild.get_role.side_effect = lambda rid: next((r for r in roles if r.id == rid), None)
        guild.me.guild_permissions.manage_roles = True
        return guild

    async def test_welcome_routes_mentions_and_reaction_options(self):
        for guild_id in (PODODANG_GUILD_ID, 1209393579848507465):
            with self.subTest(guild_id=guild_id):
                store = Store(':memory:')
                guild = self.make_guild(guild_id)
                config = welcome_config(guild_id, 1209393579848507465)
                channel = Mock(id=config['channel_id'], guild=guild)
                message = Mock(id=88)
                message.add_reaction = AsyncMock()
                channel.send = AsyncMock(return_value=message)
                bot = Mock(store=store, guild_id=1209393579848507465)
                bot.get_channel.return_value = channel
                service = WelcomeService(bot)
                member = Mock(id=77, guild=guild, bot=False)
                await service.joined(member)
                sent = channel.send.call_args.kwargs
                self.assertIn('<@77>', sent['content'])
                self.assertFalse(sent['allowed_mentions'].everyone)
                self.assertEqual(sent['allowed_mentions'].users[0].id, 77)
                self.assertEqual(message.add_reaction.await_count, len(config['roles']))
                self.assertNotIn('💜', store.welcome_message(88)['roles'])

    async def test_roles_only_granted_to_welcomed_user_and_persist(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'bot.json'
            store = Store(path)
            guild = self.make_guild(PODODANG_GUILD_ID)
            store.record_welcome(88, guild.id, 1128245693652414495, 77, {'🍇': 1})
            bot = Mock(store=Store(path), guild_id=1209393579848507465)
            bot.get_guild.return_value = guild
            service = WelcomeService(bot)
            member = Mock(id=77, bot=False, roles=[])
            member.add_roles = AsyncMock()
            payload = SimpleNamespace(message_id=88, guild_id=guild.id,
                channel_id=1128245693652414495, user_id=77, emoji='🍇', member=member)
            await service.reacted(payload)
            member.add_roles.assert_awaited_once_with(guild.roles[0], reason='환영 메시지 이모지 역할 선택')
            payload.user_id = 78
            await service.reacted(payload)
            payload.user_id = 77
            payload.message_id = 99
            await service.reacted(payload)
            self.assertEqual(member.add_roles.await_count, 1)

    async def test_role_hierarchy_prevents_grant(self):
        store = Store(':memory:')
        guild = self.make_guild(PODODANG_GUILD_ID)
        guild.roles[0].__ge__.return_value = True
        store.record_welcome(88, guild.id, 1128245693652414495, 77, {'🍇': 1})
        bot = Mock(store=store, guild_id=1209393579848507465)
        bot.get_guild.return_value = guild
        member = Mock(bot=False, roles=[])
        member.add_roles = AsyncMock()
        await WelcomeService(bot).reacted(SimpleNamespace(message_id=88, guild_id=guild.id,
            channel_id=1128245693652414495, user_id=77, emoji='🍇', member=member))
        member.add_roles.assert_not_awaited()

    async def test_other_servers_and_bots_ignored(self):
        bot = Mock(store=Store(':memory:'), guild_id=1209393579848507465)
        bot.fetch_channel = AsyncMock()
        service = WelcomeService(bot)
        await service.joined(Mock(guild=Mock(id=42), bot=False))
        await service.joined(Mock(guild=Mock(id=PODODANG_GUILD_ID), bot=True))
        bot.fetch_channel.assert_not_awaited()
        bot.get_channel.assert_not_called()
