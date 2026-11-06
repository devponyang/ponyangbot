import asyncio
import io
import logging
import os
import re
from datetime import datetime, timedelta
from uuid import uuid4

import discord
from discord import app_commands
from discord.ext import commands, tasks
from dotenv import load_dotenv

from sheets import Sheets, sync_error_message
from storage import KST, ROLE_LIMITS, Store
from schedule import due_event_date, event_start, scheduled_capacity, cleanup_cutoff, mode_name, SIEGE_NAMES, require_priority_open
from identity import parse_display_name
from welcome import WelcomeService
from admin_panel import SchedulePanel

log = logging.getLogger('ponyangbot')


async def report_error(interaction, error):
    if isinstance(error, ValueError):
        message = str(error)
    elif isinstance(error, app_commands.CheckFailure):
        message = '서버 관리 권한이 필요합니다.'
    else:
        log.error('Interaction failed', exc_info=(type(error), error, error.__traceback__))
        message = '처리 중 오류가 발생했습니다. 관리자에게 알려 주세요.'
    if interaction.response.is_done():
        await interaction.followup.send(message, ephemeral=True)
    else:
        await interaction.response.send_message(message, ephemeral=True)


class MercenaryModal(discord.ui.Modal, title='용병 추가'):
    family = discord.ui.TextInput(label='게임 가문명', placeholder='추가할 용병의 가문명', max_length=50)

    def __init__(self, view):
        super().__init__()
        self.event_view = view

    async def on_submit(self, interaction):
        if not interaction.permissions.manage_guild:
            raise ValueError('용병 추가는 서버 관리 권한이 있는 관리자만 가능합니다.')
        view = self.event_view
        view.require_current_event(interaction.guild_id)
        family = self.family.value.strip()
        user_id = 'merc-' + uuid4().hex
        view.bot.store.participate(view.event_id, interaction.guild_id, user_id, '', family,
                                   guild_name='용병', display_name=f'[용병] {family}', actor_id=interaction.user.id)
        members = view.bot.store.members(view.event_id)
        position = next((i for i, p in enumerate(members, 1) if p['user_id'] == user_id), None)
        if position is None:
            waiting = view.bot.store.members(view.event_id, waiting=True)
            position = next(i for i, p in enumerate(waiting, 1) if p['user_id'] == user_id)
            status = f'예비 {position}번째'
        else:
            status = f'{position}번째 순서'
        await interaction.response.send_message(
            f'[용병] {family} 추가 완료 · {status}\n시트에는 약 5분 이내에 반영됩니다.',
            ephemeral=True, allowed_mentions=discord.AllowedMentions.none())

    async def on_error(self, interaction, error):
        await report_error(interaction, error)


class EventView(discord.ui.View):
    def __init__(self, bot, event_id):
        super().__init__(timeout=None)
        self.bot = bot
        self.event_id = event_id
        for child in self.children:
            child.custom_id = f'node:{event_id}:{child.custom_id}'

    def content(self, event):
        return event_content(event)

    async def on_error(self, interaction, error, item):
        await report_error(interaction, error)

    def require_current_event(self, guild_id):
        latest = self.bot.store.latest_event(guild_id)
        if latest is None or latest['id'] != self.event_id:
            raise ValueError('이전 모집입니다. 가장 최근 모집 글에서 신청해 주세요.')

    @discord.ui.button(label='참여', style=discord.ButtonStyle.success, custom_id='join')
    async def join(self, interaction, button):
        self.require_current_event(interaction.guild_id)
        event = self.bot.store.event(self.event_id, interaction.guild_id)
        if event['closed']:
            raise ValueError('이미 마감된 모집입니다.')
        guild_name, family = parse_display_name(interaction.user.display_name)
        await interaction.response.defer(ephemeral=True)
        self.bot.store.participate(self.event_id, interaction.guild_id,
            interaction.user.id, str(interaction.user), family, guild_name=guild_name,
            display_name=interaction.user.display_name)
        position = next((i for i, member in enumerate(self.bot.store.members(self.event_id), 1)
                        if member['user_id'] == str(interaction.user.id)), None)
        if position is None:
            position = next(i for i, p in enumerate(self.bot.store.members(self.event_id, waiting=True), 1)
                            if p['user_id'] == str(interaction.user.id))
            result = f'정원이 초과되어 예비 신청이 완료되었습니다.\n\n예비 {position}번째 순서'
        else:
            result = f'거점 신청이 완료되었습니다.\n\n{position}번째 순서'
        await interaction.followup.send(
            f'<@{interaction.user.id}>\n\n{result}',
            ephemeral=True, allowed_mentions=discord.AllowedMentions(
                everyone=False, roles=False, users=[discord.Object(id=interaction.user.id)], replied_user=False))

    @discord.ui.button(label='부속 우선 참여', style=discord.ButtonStyle.primary, custom_id='priority')
    async def priority(self, interaction, button):
        self.require_current_event(interaction.guild_id)
        if self.bot.store.event(self.event_id, interaction.guild_id)['closed']:
            raise ValueError('이미 마감된 모집입니다.')
        require_priority_open(self.bot.store.event(self.event_id, interaction.guild_id))
        guild_name, family = parse_display_name(interaction.user.display_name)
        await interaction.response.defer(ephemeral=True)
        try:
            async with self.bot.role_lookup_lock:
                role = await asyncio.to_thread(self.bot.sheets.lookup_role, guild_name, family)
        except ValueError:
            raise
        except Exception:
            raise ValueError('직업 시트를 조회하지 못했습니다. 잠시 후 다시 신청해 주세요.') from None
        if role not in ROLE_LIMITS:
            raise ValueError('부속 우선 참여는 화염탑·신기전·코끼리·불퇴·대포 역할만 가능합니다. 일반 참여 버튼을 이용해 주세요.')
        # Another event or application may have been created during the network call.
        self.require_current_event(interaction.guild_id)
        require_priority_open(self.bot.store.event(self.event_id, interaction.guild_id))
        changes = self.bot.store.participate(self.event_id, interaction.guild_id,
            interaction.user.id, str(interaction.user), family, guild_name=guild_name,
            display_name=interaction.user.display_name, priority_role=role)
        position = next(i for i, p in enumerate(self.bot.store.members(self.event_id), 1)
                        if p['user_id'] == str(interaction.user.id))
        waiting = sum(state == '대기' for state in changes.values())
        note = f'\n\n일반 참여자 {waiting}명이 예비로 전환되었습니다.' if waiting else ''
        await interaction.followup.send(
            f'<@{interaction.user.id}>\n\n{role} 부속 우선 참여 신청이 완료되었습니다.\n\n{position}번째 순서{note}',
            ephemeral=True, allowed_mentions=discord.AllowedMentions(
                everyone=False, roles=False, users=[discord.Object(id=interaction.user.id)], replied_user=False))

    @discord.ui.button(label='참여 취소', style=discord.ButtonStyle.danger, custom_id='cancel')
    async def cancel(self, interaction, button):
        self.require_current_event(interaction.guild_id)
        await interaction.response.defer(ephemeral=True)
        self.bot.store.participate(self.event_id, interaction.guild_id,
            interaction.user.id, str(interaction.user), '', join=False)
        await interaction.followup.send(
            '참여를 취소했습니다. 구글 시트에 자동 반영됩니다.', ephemeral=True)

    @discord.ui.button(label='참여 인원 현황', style=discord.ButtonStyle.secondary, custom_id='list')
    async def members(self, interaction, button):
        event = self.bot.store.event(self.event_id, interaction.guild_id)
        members = self.bot.store.members(self.event_id)
        starts = datetime.strptime(event['starts'], '%Y-%m-%d %H:%M')
        tier = re.search(r'(\d+)\s*단', event['name'])
        tier_number = tier.group(1) if tier else '1'
        kind = '공성전' if '공성전' in event['name'] else f'{tier_number}단 거점'
        heading = f'{starts.month}월{starts.day}일 {kind} 신청 현황'
        names = [m['display_name'] or (f"[{m['guild_name']}] {m['nickname']}" if m['guild_name'] else m['nickname']) for m in members]
        for i, member in enumerate(members):
            if member.get('priority_role'):
                names[i] += f" · {member['priority_role']} 우선"
        body = '\n'.join(f'{i}. {discord.utils.escape_markdown(name)}' for i, name in enumerate(names, 1))
        content = heading + '\n\n' + (body or '아직 참여자가 없습니다.')
        waiting = self.bot.store.members(self.event_id, waiting=True)
        if waiting:
            waiting_names = [p['display_name'] or f"[{p['guild_name']}] {p['nickname']}" for p in waiting]
            content += '\n\n예비 인원\n' + '\n'.join(
                f'{i}. {discord.utils.escape_markdown(name)}' for i, name in enumerate(waiting_names, 1))
        if len(content) > 1900:
            await interaction.response.send_message(heading, ephemeral=True,
                file=discord.File(io.BytesIO(content.encode('utf-8-sig')), filename='participants.txt'))
        else:
            await interaction.response.send_message(content, ephemeral=True,
                                                    allowed_mentions=discord.AllowedMentions.none())

    # Retain the former button ID so old messages cannot accidentally close a recruitment.
    @discord.ui.button(label='용병 추가', style=discord.ButtonStyle.secondary, custom_id='close')
    async def add_mercenary(self, interaction, button):
        if not interaction.permissions.manage_guild:
            raise ValueError('용병 추가는 서버 관리 권한이 있는 관리자만 가능합니다.')
        self.require_current_event(interaction.guild_id)
        if self.bot.store.event(self.event_id, interaction.guild_id)['closed']:
            raise ValueError('이미 마감된 모집입니다.')
        await interaction.response.send_modal(MercenaryModal(self))


def event_content(event):
    starts = datetime.strptime(event['starts'], '%Y-%m-%d %H:%M')
    weekday = '월화수목금토일'[starts.weekday()]
    tier_match = re.search(r"([12])\s*단", event["name"])
    tier = int(tier_match.group(1)) if tier_match else 1
    channels = "칼페온, 올루키타" if tier == 2 else "발레노스, 세렌디아"
    capacity = f"총 {event['capacity']}인" if event['capacity'] else '제한 없음'
    siege = '공성전' in event['name']
    battle = '공성전' if siege else '거점전'
    restriction = f'{tier}단 제한 거점입니다.'
    if siege:
        location = next((name for name in SIEGE_NAMES.values() if name in event['name']), event['name'])
        channels = location.removeprefix('무제한 ').removeprefix('제한 ')
        restriction = f'{location} 공성전입니다.'
    supplies = ('녹아내린 구리조각 100개 + 철 주괴 100개,지뢰류, 비약, 석재, 부속건설도구, '
                '(강화)화염탑용기름, (대)신기전화살, 정제수' if siege or tier == 2 else
                '지뢰류, 비약, 다듬어진 석재, 부속건설도구, 화염탑용기름, 신기전화살')
    content = (
        f"# **__{starts:%m월/%d일}__** {weekday}요일 {battle}\n\n"
        '```bash\n'
        f'"채널 : {channels}"\n'
        f'({restriction})\n'
        '```\n\n'
        '참여 신청 : 선착순\n\n'
        f'시작 시각 : {starts:%H:%M}\n\n'
        f'참여인원 : {capacity}\n\n'
        f'준비물 : {supplies}\n\n'
        '## :clipboard: **시트 안내사항**\n\n'
        '시트지는 5분마다 업데이트 됩니다.\n\n'
        '직업, 역할 등을 변경하고 싶으신 분은\n'
        '시트 하단 `"각 길드별 직업시트"`에서 변경해주세요.\n\n'
        '[거점 시트 바로가기](https://docs.google.com/spreadsheets/d/1RnhR-RdrDTnAjusPYVtloK5L-GFku9ZbKmsujTMWnMk/edit?gid=0#gid=0)\n\n'
        '@everyone'
    )
    if not siege:
        cutoff = starts - timedelta(hours=1)
        content += f'\n\n※ 부속 우선 참여는 시작 1시간 전({cutoff:%H:%M})부터 불가능합니다. 이후에는 일반 참여 버튼을 이용해 주세요.'
    if event['closed']:
        content += '\n\n**모집 마감**'
    return content


def recruitment_mentions():
    return discord.AllowedMentions(everyone=True, users=False, roles=False, replied_user=False)


class NodeBot(commands.Bot):
    def __init__(self, store, sheets, guild_id, auto_channel_id=None, start_time='21:00'):
        intents = discord.Intents.default()
        intents.members = True
        super().__init__(command_prefix=commands.when_mentioned, intents=intents,
                         allowed_mentions=discord.AllowedMentions.none())
        self.store = store
        self.sheets = sheets
        self.guild_id = guild_id
        self.synced_version = -1
        self.sync_error = False
        self.sync_error_detail = ''
        self.auto_channel_id = auto_channel_id
        self.start_time = start_time
        self.role_lookup_lock = asyncio.Lock()
        self.sheet_sync_lock = asyncio.Lock()
        self.legacy_tabs_archived = False
        self.welcome_service = WelcomeService(self)
        self.posts_refreshed = False

    async def on_ready(self):
        if self.posts_refreshed:
            return
        self.posts_refreshed = True
        for event in self.store.all_events():
            if event['guild_id'] != str(self.guild_id) or event.get('archived'):
                continue
            post = self.store.data['auto_posts'].get(event['id'])
            if not post or post.get('deleted'):
                continue
            try:
                channel = self.get_channel(int(post['channel_id'])) or await self.fetch_channel(int(post['channel_id']))
                if channel.guild.id != self.guild_id:
                    continue
                message = await channel.fetch_message(int(post['message_id']))
                if message.author.id == self.user.id:
                    await message.edit(content=event_content(event), view=EventView(self, event['id']),
                                       allowed_mentions=discord.AllowedMentions.none())
            except discord.HTTPException as error:
                log.warning('Recruitment refresh failed (%s)', type(error).__name__)

    async def on_member_join(self, member):
        await self.welcome_service.joined(member)

    async def on_raw_reaction_add(self, payload):
        await self.welcome_service.reacted(payload)

    async def setup_hook(self):
        for event in self.store.all_events():
            self.add_view(EventView(self, event['id']))
        guild = discord.Object(id=self.guild_id)
        self.tree.copy_global_to(guild=guild)
        await self.tree.sync(guild=guild)
        self.sync_sheet.start()
        self.auto_recruit.start()

    async def cleanup_due_events(self, now):
        changed = self.store.archive_due(self.guild_id, cleanup_cutoff(now))
        # Synchronize immediately; the same lock protects the regular 5-minute job.
        if changed:
            await self.sync_sheet()
        for post in self.store.cleanup_posts(self.guild_id):
            channel_id = int(post['channel_id'])
            try:
                channel = self.get_channel(channel_id) or await self.fetch_channel(channel_id)
                if getattr(getattr(channel, 'guild', None), 'id', None) != self.guild_id:
                    raise ValueError('모집 메시지의 서버가 설정과 다릅니다.')
                message = await channel.fetch_message(int(post['message_id']))
                if message.author.id != self.user.id:
                    raise ValueError('봇이 작성한 모집 메시지가 아닙니다.')
                await message.delete()
            except discord.NotFound:
                pass  # Already deleted; completing cleanup is safe.
            self.store.mark_deleted(post['event_id'])

    async def post_due_event(self, now):
        tiers = self.store.weekly_tiers(self.guild_id)
        day = due_event_date(now, tiers)
        if day is None:
            return
        starts = event_start(day, self.start_time, tiers[day.weekday()])
        if starts <= now:
            return
        event_id = f'auto-{self.guild_id}-{day.isoformat()}'
        if self.store.posted(event_id):
            return
        channel = self.get_channel(self.auto_channel_id) or await self.fetch_channel(self.auto_channel_id)
        if not isinstance(channel, discord.TextChannel) or channel.guild.id != self.guild_id:
            raise ValueError('자동 게시 채널은 설정한 서버의 텍스트 채널이어야 합니다.')
        marker = f'자동모집 ID: {event_id}'
        # Recover a send that succeeded just before a crash or network timeout.
        window = (starts - timedelta(days=1)).replace(hour=21, minute=30)
        async for message in channel.history(limit=None, after=window):
            matches = any(e.footer.text == marker for e in message.embeds) or any(
                getattr(button, 'custom_id', None) == f'node:{event_id}:join'
                for row in message.components for button in getattr(row, 'children', []))
            if message.author.id == self.user.id and matches:
                self.store.mark_posted(event_id, channel.id, message.id)
                return
        try:
            event = self.store.event(event_id, self.guild_id)
        except ValueError:
            self.store.create(event_id, self.guild_id, mode_name(tiers[day.weekday()]),
                              starts.strftime('%Y-%m-%d %H:%M'), scheduled_capacity(starts, tiers))
            event = self.store.event(event_id, self.guild_id)
        if event['closed']:
            return
        message = await channel.send(content=event_content(event), view=EventView(self, event_id),
                                     allowed_mentions=recruitment_mentions())
        self.store.mark_posted(event_id, channel.id, message.id)

    @tasks.loop(seconds=30)
    async def auto_recruit(self):
        now = datetime.now(KST)
        try:
            await self.cleanup_due_events(now)
        except Exception as error:
            log.warning('Recruitment cleanup failed (%s); retrying in 30 seconds', type(error).__name__)
        try:
            if self.auto_channel_id:
                await self.post_due_event(now)
        except Exception as error:
            log.warning('Auto recruitment failed (%s); retrying in 30 seconds', type(error).__name__)

    @auto_recruit.before_loop
    async def before_auto_recruit(self):
        await self.wait_until_ready()

    @tasks.loop(minutes=5)
    async def sync_sheet(self):
        async with self.sheet_sync_lock:
            await self.sync_sheet_once()

    async def sync_sheet_once(self):
        try:
            if not self.legacy_tabs_archived:
                if self.store.path is not None:
                    await asyncio.to_thread(self.sheets.archive_legacy_tabs, self.store.path.parent / 'backups')
                self.legacy_tabs_archived = True
            version = self.store.revision
            if version == self.synced_version:
                return
            roster = self.store.roster(self.guild_id)
            event = self.store.latest_event(self.guild_id)
            await asyncio.to_thread(self.sheets.sync, roster, event)
            self.synced_version = version
            self.sync_error = False
            self.sync_error_detail = ''
        except Exception as error:
            self.sync_error = True
            self.sync_error_detail = sync_error_message(error)
            log.warning('Sheet sync failed: %s; retrying in 5 minutes', self.sync_error_detail)

    async def close(self):
        self.auto_recruit.cancel()
        self.sync_sheet.cancel()
        await super().close()


def make_bot(store, sheets, guild_id, auto_channel_id=None, start_time='21:00'):
    bot = NodeBot(store, sheets, guild_id, auto_channel_id, start_time)

    @bot.tree.command(name='관리자패널', description='요일별 거점 단계와 정기 모집 설정을 변경합니다.')
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.checks.has_permissions(manage_guild=True)
    async def admin_panel(interaction: discord.Interaction):
        view = SchedulePanel(bot.store, interaction.guild_id, interaction.user.id, bot.start_time)
        await interaction.response.send_message(view.content(), view=view, ephemeral=True)

    @bot.tree.command(name='거점테스트', description='전체 멘션과 실제 시트 기록을 사용하는 모집을 즉시 게시합니다.')
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.checks.has_permissions(manage_guild=True)
    @app_commands.describe(정원='테스트 정원 (기본 25명)')
    async def test_recruitment(interaction: discord.Interaction, 정원: app_commands.Range[int, 1, 100] = 25):
        await interaction.response.defer(ephemeral=True)
        event_id = 'test-' + uuid4().hex
        starts = (datetime.now(KST) + timedelta(days=1)).strftime('%Y-%m-%d') + ' ' + bot.start_time
        bot.store.create(event_id, interaction.guild_id, '1단거점', starts, 정원)
        view = EventView(bot, event_id)
        try:
            message = await interaction.channel.send(
                content=view.content(bot.store.event(event_id, interaction.guild_id)),
                view=view, allowed_mentions=recruitment_mentions())
            bot.store.mark_posted(event_id, interaction.channel.id, message.id)
        except discord.HTTPException:
            view.stop()
            bot.store.close_event(event_id, interaction.guild_id)
            raise ValueError('모집 글을 게시하지 못했습니다. 채널 전송 권한을 확인해 주세요.')
        await interaction.followup.send(
            '이 채널에 실제 모집을 게시했습니다. @everyone 호출 및 실제 시트 기록을 사용합니다.\n'
            '다음 동기화 때(약 5분 이내) 거점시트 명단이 이 모집으로 교체됩니다.',
            ephemeral=True)

    @bot.tree.command(name='거점생성', description='참여 버튼이 있는 거점 모집을 생성합니다.')
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.checks.has_permissions(manage_guild=True)
    @app_commands.describe(거점명='거점 이름', 진행일시='한국시간: 2026-10-30 21:00', 정원='생략하면 관리자 패널의 요일 설정, 최대 100명')
    async def create(interaction: discord.Interaction, 거점명: app_commands.Range[str, 1, 80],
                     진행일시: str, 정원: app_commands.Range[int, 1, 100] | None = None):
        try:
            starts = datetime.strptime(진행일시, '%Y-%m-%d %H:%M').replace(tzinfo=KST)
        except ValueError:
            raise ValueError('진행일시는 2026-10-30 21:00 형식으로 입력해 주세요.')
        if starts <= datetime.now(KST):
            raise ValueError('진행일시는 현재보다 이후여야 합니다.')
        if 정원 is None:
            정원 = scheduled_capacity(starts, bot.store.weekly_tiers(interaction.guild_id))
            if 정원 is None:
                raise ValueError('해당 요일은 정기 거점 일정이 없습니다. 별도 모집은 정원을 지정해 주세요.')
        await interaction.response.defer(ephemeral=True)
        event_id = uuid4().hex
        bot.store.create(event_id, interaction.guild_id, 거점명, starts.strftime('%Y-%m-%d %H:%M'), 정원)
        view = EventView(bot, event_id)
        try:
            message = await interaction.channel.send(content=event_content(bot.store.event(event_id, interaction.guild_id)),
                                           view=view, allowed_mentions=recruitment_mentions())
            bot.store.mark_posted(event_id, interaction.channel.id, message.id)
        except discord.HTTPException:
            bot.store.close_event(event_id, interaction.guild_id)
            raise ValueError('모집 메시지를 게시하지 못해 모집을 마감했습니다. 채널 전송·링크 첨부 권한을 확인해 주세요.')
        await interaction.followup.send('모집을 생성했습니다.', ephemeral=True)

    @bot.tree.command(name='참여기록', description='거점·공성의 신청 및 취소 이력을 조회합니다.')
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.checks.has_permissions(manage_guild=True)
    @app_commands.describe(날짜='거점 진행일 YYYY-MM-DD (생략하면 가장 최근 모집일)',
                           이용자='특정 디스코드 이용자만 조회', 가문명='특정 가문명만 조회 (용병 포함)')
    async def history(interaction: discord.Interaction, 날짜: str | None = None,
                      이용자: discord.User | None = None, 가문명: str | None = None):
        if 날짜 is None:
            event = bot.store.latest_event(interaction.guild_id)
            if event is None:
                raise ValueError('조회할 모집이 없습니다.')
            날짜 = event['starts'][:10]
        try:
            날짜 = datetime.strptime(날짜, '%Y-%m-%d').strftime('%Y-%m-%d')
        except ValueError:
            raise ValueError('날짜는 2026-11-06 형식으로 입력해 주세요.')
        rows = bot.store.participation_history(interaction.guild_id, 날짜,
                    이용자.id if 이용자 else None, 가문명)
        heading = f'{날짜} 참여 기록 (한국시간)'
        lines = []
        for row in rows:
            stamp = datetime.fromisoformat(row['at'])
            if stamp.tzinfo is None:
                stamp = stamp.replace(tzinfo=KST)
            name = row['display_name'] or f"[{row['guild_name']}] {row['nickname']}"
            role = f" · {row['priority_role']} 우선" if row['priority_role'] else ''
            lines.append(f"{stamp.astimezone(KST):%Y-%m-%d %H:%M:%S} | {name} | {row['action']}"
                         f" | {row['status']}{role} | 처리자 ID: {row['actor_id']}"
                         f" | 모집: {row['event_name']} ({row['event_id']})")
        text = heading + '\n\n' + ('\n'.join(lines) or '해당하는 기록이 없습니다.')
        escaped = discord.utils.escape_markdown(text)
        if len(escaped) > 1800:
            await interaction.response.send_message(heading,
                file=discord.File(io.BytesIO(text.encode('utf-8-sig')), filename='participation-history.txt'),
                ephemeral=True, allowed_mentions=discord.AllowedMentions.none())
        else:
            await interaction.response.send_message(escaped,
                ephemeral=True, allowed_mentions=discord.AllowedMentions.none())

    @bot.tree.command(name='시트상태', description='구글 스프레드시트 동기화 상태를 확인합니다.')
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.checks.has_permissions(manage_guild=True)
    async def status(interaction: discord.Interaction):
        version = bot.store.revision
        state = ('연결 오류 — 자동 재시도 중' if bot.sync_error else
                 '동기화 완료' if version == bot.synced_version else '동기화 대기 중')
        if bot.sync_error_detail:
            state += '\n' + bot.sync_error_detail
        await interaction.response.send_message(state, ephemeral=True)

    @bot.tree.error
    async def command_error(interaction, error):
        await report_error(interaction, getattr(error, 'original', error))

    return bot


def main():
    load_dotenv()
    logging.basicConfig(level=logging.INFO)
    required = ['DISCORD_TOKEN', 'DISCORD_GUILD_ID', 'GOOGLE_SPREADSHEET_ID', 'GOOGLE_APPLICATION_CREDENTIALS']
    missing = [key for key in required if not os.getenv(key)]
    if missing:
        raise SystemExit('.env에 다음 값을 입력하세요: ' + ', '.join(missing))
    bot = make_bot(Store(os.getenv('DATABASE_PATH', 'data/bot.json')),
        Sheets(os.environ['GOOGLE_SPREADSHEET_ID'], os.environ['GOOGLE_APPLICATION_CREDENTIALS']),
        int(os.environ['DISCORD_GUILD_ID']),
        int(os.environ['AUTO_RECRUIT_CHANNEL_ID']) if os.getenv('AUTO_RECRUIT_CHANNEL_ID') else None,
        os.getenv('EVENT_START_TIME', '21:00'))
    bot.run(os.environ['DISCORD_TOKEN'])


if __name__ == '__main__':
    main()
