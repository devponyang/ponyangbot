"""Durable notification delivery and local JSON snapshots."""
import asyncio
from collections import Counter
import copy
import json
import logging
import os
import tempfile
from datetime import datetime, timedelta

import discord

from storage import KST
from sheets import find_role

log = logging.getLogger('ponyangbot')


ROLE_GROUPS = {
    '부속': {'빌더', '대포', '코끼리', '불퇴', '신기전', '화탑', '화염탑'},
    '꼬장조': {'꼬장', '꼬장조', '꼬장오더'},
    '본대': {'본대', '오더', '서브오더', '공군'},
}


async def send_voice_reminder(bot, now=None):
    now = (now or datetime.now(KST)).astimezone(KST)
    # A short recovery window avoids claiming to know historical voice attendance.
    if not (20, 35) <= (now.hour, now.minute) < (20, 40):
        return
    event = bot.store.latest_event(bot.guild_id)
    if not event or event.get('archived') or event['starts'][:10] != now.strftime('%Y-%m-%d'):
        return
    key = f"voice:{bot.guild_id}:{now:%Y-%m-%d}"
    if key in bot.store.data.get('daily_reports', {}):
        return
    voice = bot.get_channel(1209420952082063381)
    if not isinstance(voice, discord.VoiceChannel) or voice.guild.id != bot.guild_id:
        raise ValueError('음성 확인 채널을 찾지 못했습니다. 채널 보기 권한을 확인하세요.')
    if not voice.permissions_for(voice.guild.me).view_channel or not bot.intents.voice_states:
        raise ValueError('음성 상태 확인에 필요한 권한 또는 인텐트가 없습니다.')
    channel = bot.get_channel(bot.auto_channel_id) or await bot.fetch_channel(bot.auto_channel_id)
    if channel.guild.id != bot.guild_id:
        raise ValueError('음성 미참여 안내 채널의 서버가 다릅니다.')
    plan = bot.store.data.get('voice_reminders', {}).get(key)
    if plan is None:
        present = set(voice.voice_states)
        members = bot.store.members(event['id'])
        missing = [p['user_id'] for p in members if str(p['user_id']).isdigit() and int(p['user_id']) not in present]
        unknown = sum(not str(p['user_id']).isdigit() for p in members)
        plan = dict(missing=missing, unknown=unknown, checked_at=now.strftime('%H:%M:%S'),
                    event_id=event['id'], title=f"{event['starts'][:10]} {event['name']}")
        data = copy.deepcopy(bot.store.data)
        data.setdefault('voice_reminders', {})[key] = plan
        bot.store._commit(data, bump_revision=False)
    chunks = [plan['missing'][i:i+50] for i in range(0, len(plan['missing']), 50)] or [[]]
    seen = {}
    async for message in channel.history(limit=None, after=now.replace(hour=20, minute=35, second=0, microsecond=0)):
        if message.author.id == bot.user.id:
            for embed in message.embeds:
                seen[embed.footer.text] = message.id
    for index, recipients in enumerate(chunks):
        part_key = f'{key}:{index}'
        if part_key in bot.store.data.get('daily_reports', {}):
            continue
        marker = f'음성확인 ID: {part_key}'
        if marker in seen:
            bot.store.mark_report_sent(part_key, seen[marker])
            continue
        text = (f"{plan['title']} 음성채널 참여 확인 ({plan['checked_at']} 기준)\n"
                f"지정 음성채널 미참여: {len(plan['missing'])}명\n"
                f"계정 미연결 용병: {plan['unknown']}명 — 별도 확인 필요\n"
                f"음성채널 <#1209420952082063381>에 입장해 주세요.")
        embed = discord.Embed(description=text)
        embed.set_footer(text=marker)
        content = ' '.join(f'<@{uid}>' for uid in recipients) or '언급할 음성채널 미참여자가 없습니다.'
        message = await channel.send(content=content, embed=embed,
            allowed_mentions=discord.AllowedMentions(everyone=False, roles=False,
                users=[discord.Object(id=int(uid)) for uid in recipients], replied_user=False))
        bot.store.mark_report_sent(part_key, message.id)
    bot.store.mark_report_sent(key, 'complete')


def attendance_text(event, members, waiting_count, rows):
    guilds = Counter(p['guild_name'] or '미등록' for p in members)
    groups = Counter()
    details = Counter()
    for person in members:
        try:
            role = ''.join(find_role(rows, person['guild_name'], person['nickname']).split())
        except ValueError:
            role = '미확인'
        group = next((name for name, roles in ROLE_GROUPS.items() if role in roles), '기타/미확인')
        groups[group] += 1
        details[role or '미확인'] += 1
    lines = [f"**{event['starts'][:10]} {event['name']} 참여 현황**",
             f"총 참여자: {len(members)}명 / 정원 {event['capacity']}명", f'예비: {waiting_count}명', '', '**길드별 참여자**']
    for guild in dict.fromkeys(['포도당', 'Probe', '고라파덕', '용병'] + sorted(guilds)):
        lines.append(f'{discord.utils.escape_markdown(guild)}: {guilds[guild]}명')
    lines += ['', '**역할별 참여자**']
    for group in ('부속', '꼬장조', '본대', '기타/미확인'):
        lines.append(f'{group}: {groups[group]}명')
    lines += ['', '역할 상세: ' + ', '.join(f'{discord.utils.escape_markdown(role)} {count}명' for role, count in sorted(details.items())),
              '※ 확정 참여자 기준 · 역할은 전체 직업 시트 기준 · 예비/취소 제외']
    return '\n'.join(lines)


async def send_attendance_report(bot, now=None):
    now = (now or datetime.now(KST)).astimezone(KST)
    # Do not post yesterday's or already-cleared roster after the cleanup window.
    if not (20, 30) <= (now.hour, now.minute) < (21, 10):
        return
    event = bot.store.latest_event(bot.guild_id)
    if not event or event.get('archived') or event['starts'][:10] != now.strftime('%Y-%m-%d'):
        return
    key = f"{bot.guild_id}:{now:%Y-%m-%d}"
    if key in bot.store.data.get('daily_reports', {}):
        return
    channel_id = bot.auto_channel_id
    if not channel_id:
        raise ValueError('자동 모집 채널을 설정해 주세요.')
    channel = bot.get_channel(channel_id) or await bot.fetch_channel(channel_id)
    if channel.guild.id != bot.guild_id:
        raise ValueError('참여 현황 채널의 서버가 다릅니다.')
    marker = f'참여현황 ID: {key}'
    async for message in channel.history(limit=None, after=now.replace(hour=20, minute=30, second=0, microsecond=0)):
        if message.author.id == bot.user.id and any(embed.footer.text == marker for embed in message.embeds):
            bot.store.mark_report_sent(key, message.id)
            return
    recipient = discord.Object(id=358871896449613824)
    members = bot.store.members(event['id'])
    waiting = len(bot.store.members(event['id'], waiting=True))
    rows = await asyncio.to_thread(bot.sheets.role_rows) if members else []
    text = attendance_text(event, members, waiting, rows)
    embed = discord.Embed(description=text[:4000])
    embed.set_footer(text=marker)
    message = await channel.send(content=f'<@{recipient.id}>', embed=embed,
        allowed_mentions=discord.AllowedMentions(everyone=False, users=[recipient], roles=False, replied_user=False))
    bot.store.mark_report_sent(key, message.id)


def backup_json(store, reason, now=None):
    if store.path is None:
        return None
    now = (now or datetime.now(KST)).astimezone(KST)
    if reason == 'daily' and (now.hour, now.minute) < (20, 30):
        return None
    directory = store.path.parent / 'backups' / 'json'
    directory.mkdir(parents=True, exist_ok=True)
    if reason not in ('daily', 'before-cleanup'):
        raise ValueError('Unknown backup reason')
    suffix = now.strftime('%Y-%m-%d') if reason == 'daily' else now.strftime('%Y-%m-%d-%H%M%S-%f')
    target = directory / f'bot-{reason}-{suffix}.json'
    if reason == 'daily' and target.exists():
        # A damaged snapshot must not suppress today's backup.
        try:
            saved = json.loads(target.read_text(encoding='utf-8'))
            if saved.get('schema_version') == 1 and all(k in saved for k in ('events', 'participants', 'revision')):
                return None
        except (ValueError, OSError):
            pass
    fd, temporary = tempfile.mkstemp(prefix='.snapshot-', dir=directory)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as output:
            json.dump(store.data, output, ensure_ascii=False, indent=2)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, target)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    # Only our own dated snapshots in this directory are eligible for retention cleanup.
    for path in directory.glob('bot-*.json'):
        prefix = next((p for p in ('bot-daily-', 'bot-before-cleanup-') if path.name.startswith(p)), None)
        if prefix is None or path.is_symlink():
            continue
        try:
            day = datetime.strptime(path.name[len(prefix):len(prefix) + 10], '%Y-%m-%d').date()
        except ValueError:
            continue
        if day < (now - timedelta(days=30)).date():
            path.unlink()
    return target


async def deliver_notifications(bot):
    rows = [copy.deepcopy(r) for r in bot.store.data.get('notifications', [])
            if not r['done'] and r['guild_id'] == str(bot.guild_id)]
    log_failed = False
    batch = [r for r in rows if r['kind'] == 'promotion'][:50] + [r for r in rows if r['kind'] == 'log'][:50]
    for row in batch:
        if row['kind'] == 'log' and log_failed:
            continue
        failure = None
        try:
            if row['kind'] == 'log':
                channel = bot.get_channel(bot.operations_channel_id) or await bot.fetch_channel(bot.operations_channel_id)
                if getattr(getattr(channel, 'guild', None), 'id', None) != bot.guild_id:
                    raise ValueError('운영 기록 채널의 서버가 다릅니다.')
                timestamp = datetime.fromisoformat(row['at']).astimezone(KST).strftime('%m/%d %H:%M:%S')
                content = discord.utils.escape_markdown(f"[{timestamp}] {row['text']}")
                await channel.send(content=content[:1900], allowed_mentions=discord.AllowedMentions.none())
            else:
                event = bot.store.event(row['event_id'], bot.guild_id)
                member = next((p for p in bot.store.members(event['id']) if p['user_id'] == row['user_id']), None)
                if not member or event.get('archived') or event['closed']:
                    bot.store.finish_notification(row['id'])
                    continue
                if not str(row['user_id']).isdigit():
                    failure = f"승격 DM 불가: {row['text']} — 계정과 연결되지 않은 수동 용병입니다. 모집 {event['starts']}"
                else:
                    user = bot.get_user(int(row['user_id'])) or await bot.fetch_user(int(row['user_id']))
                    post = bot.store.data['auto_posts'].get(event['id'])
                    link = f"\nhttps://discord.com/channels/{bot.guild_id}/{post['channel_id']}/{post['message_id']}" if post else ''
                    await user.send(f"{event['starts']} {event['name']}\n예비 인원에서 참여 인원으로 승격되었습니다!\n참여 인원 현황을 확인해 주세요.{link}",
                                    allowed_mentions=discord.AllowedMentions.none())
        except (discord.Forbidden, discord.NotFound) as error:
            if row['kind'] == 'log':
                log.warning('Operations log delivery failed (%s); retrying', type(error).__name__)
                log_failed = True
                continue
            failure = f"승격 DM 실패: {row['text']} ({row['user_id']}) · {type(error).__name__} · 모집 {row['event_id']}"
        except (discord.HTTPException, OSError, ValueError) as error:
            log.warning('Notification delivery failed (%s); retrying', type(error).__name__)
            if row['kind'] == 'log':
                log_failed = True
            continue
        bot.store.finish_notification(row['id'], failure)
