"""Two-server welcome messages and explicit self-service reaction roles."""
import logging
import discord

log = logging.getLogger('ponyangbot')
PODODANG_GUILD_ID = 1128000592112009257
PODODANG_CHANNEL_ID = 1128245693652414495
ALLIANCE_CHANNEL_ID = 1209393580414603306


def emoji_key(value):
    return str(value).replace('\ufe0f', '')


def welcome_config(guild_id, alliance_id):
    if guild_id == PODODANG_GUILD_ID:
        return {'channel_id': PODODANG_CHANNEL_ID, 'title': '포도당', 'roles': [
            ('🍇', '길드원', None), ('💙', '용병', None)]}
    if guild_id == alliance_id:
        return {'channel_id': ALLIANCE_CHANNEL_ID, 'title': '연맹 디스코드', 'roles': [
            ('❤️', '포도당', 1216730106966577182),
            ('💛', 'Probe', 1553683323685969970),
            ('💙', '용병', 1209817194402291732)]}
    return None


def welcome_content(member, config):
    text = f"안녕하세요, <@{member.id}>님! **{config['title']}**에 오신 것을 환영합니다! 🎉\n\n"
    if member.guild.id != PODODANG_GUILD_ID:
        text += ('먼저 서버 닉네임을 **[길드] 닉네임** 형식으로 변경해 주세요.\n'
                 '예: `[포도당] 디움` · Probe는 `[P] 닉네임`\n\n')
    text += '아래 이모지를 클릭하여 본인에게 맞는 역할을 받아 주세요!\n\n'
    text += '\n'.join(f'{emoji} : {name}' for emoji, name, _ in config['roles'])
    if member.guild.id != PODODANG_GUILD_ID:
        text += '\n💜 : 추가 예정'
    return text


def resolve_role(guild, name, role_id):
    if role_id:
        return guild.get_role(role_id)
    roles = [role for role in guild.roles if role.name == name and not role.managed]
    return roles[0] if len(roles) == 1 else None


class WelcomeService:
    def __init__(self, bot):
        self.bot = bot

    async def joined(self, member):
        config = welcome_config(member.guild.id, self.bot.guild_id)
        if member.bot or config is None:
            return
        try:
            channel = self.bot.get_channel(config['channel_id']) or await self.bot.fetch_channel(config['channel_id'])
            if channel.guild.id != member.guild.id:
                raise ValueError('환영 채널의 서버가 일치하지 않습니다.')
            roles = {}
            for emoji, name, role_id in config['roles']:
                role = resolve_role(member.guild, name, role_id)
                if role is None:
                    log.warning('Welcome role missing/ambiguous: guild=%s role=%s', member.guild.id, name)
                    continue
                roles[emoji_key(emoji)] = role.id
            message = await channel.send(content=welcome_content(member, config),
                allowed_mentions=discord.AllowedMentions(everyone=False, roles=False,
                    users=[discord.Object(id=member.id)], replied_user=False))
            self.bot.store.record_welcome(message.id, member.guild.id, channel.id, member.id, roles)
            for emoji, _, _ in config['roles']:
                if emoji_key(emoji) in roles:
                    await message.add_reaction(emoji)
        except (discord.HTTPException, ValueError, OSError) as error:
            log.warning('Welcome failed: guild=%s error=%s (check channel, reactions and role permissions)',
                        member.guild.id, type(error).__name__)

    async def reacted(self, payload):
        record = self.bot.store.welcome_message(payload.message_id)
        if record is None or payload.guild_id is None:
            return
        if (str(payload.guild_id) != record['guild_id'] or str(payload.channel_id) != record['channel_id']
                or str(payload.user_id) != record['member_id']):
            return
        role_id = record['roles'].get(emoji_key(payload.emoji))
        if role_id is None:
            return
        config = welcome_config(payload.guild_id, self.bot.guild_id)
        if config is None or config['channel_id'] != payload.channel_id:
            return
        guild = self.bot.get_guild(payload.guild_id)
        if guild is None:
            return
        try:
            member = payload.member or guild.get_member(payload.user_id) or await guild.fetch_member(payload.user_id)
            if member.bot:
                return
            role = guild.get_role(int(role_id))
            me = guild.me
            if (role is None or role.managed or role.is_default() or me is None
                    or not me.guild_permissions.manage_roles or role >= me.top_role):
                raise ValueError('역할 관리 권한 또는 봇 역할 순서를 확인하세요.')
            if role not in member.roles:
                await member.add_roles(role, reason='환영 메시지 이모지 역할 선택')
        except (discord.HTTPException, ValueError) as error:
            log.warning('Welcome role assignment failed: guild=%s role=%s error=%s',
                        payload.guild_id, role_id, type(error).__name__)
