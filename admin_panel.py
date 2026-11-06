import discord

from datetime import datetime, timedelta
from storage import KST
from schedule import capacity_for, mode_name, event_start, SIEGE_NAMES


class SchedulePanel(discord.ui.View):
    def __init__(self, store, guild_id, owner_id, start_time="21:00"):
        super().__init__(timeout=600)
        self.store = store
        self.guild_id = guild_id
        self.owner_id = owner_id
        self.weekday = 0
        self.start_time = start_time

    async def interaction_check(self, interaction):
        if (interaction.guild_id != self.guild_id or interaction.user.id != self.owner_id
                or not interaction.permissions.manage_guild):
            await interaction.response.send_message('서버 관리 권한이 있는 패널 사용자만 변경할 수 있습니다.', ephemeral=True)
            return False
        return True

    def next_event_text(self, now=None):
        now = now or datetime.now(KST)
        tiers = self.store.weekly_tiers(self.guild_id)
        existing = [event for event in self.store.all_events()
                    if event['guild_id'] == str(self.guild_id) and not event.get('archived')
                    and datetime.strptime(event['starts'], '%Y-%m-%d %H:%M').replace(tzinfo=KST) > now]
        candidates = [(datetime.strptime(e['starts'], '%Y-%m-%d %H:%M').replace(tzinfo=KST),
                       e['name'], e['capacity']) for e in existing]
        existing_days = {item[0].date() for item in candidates}
        for offset in range(8):
            day = now.astimezone(KST).date() + timedelta(days=offset)
            starts = event_start(day, self.start_time, tiers[day.weekday()])
            capacity = capacity_for(day.weekday(), tiers[day.weekday()])
            if starts > now and capacity and day not in existing_days:
                candidates.append((starts, mode_name(tiers[day.weekday()]), capacity))
        if not candidates:
            return '다음 일정: 설정된 일정 없음'
        starts, name, capacity = min(candidates, key=lambda item: item[0])
        return f'다음 일정: {starts:%m월 %d일} {"월화수목금토일"[starts.weekday()]}요일 · {name} · {capacity}인 ({starts:%H:%M})'

    def content(self):
        lines = [f'**{self.next_event_text()}**', '', '**요일별 관리자 설정**', '']
        for day, tier in enumerate(self.store.weekly_tiers(self.guild_id)):
            capacity = capacity_for(day, tier)
            label = f'공성전 · {SIEGE_NAMES[tier]}' if tier in SIEGE_NAMES else f'{tier}단'
            setting = f'{label} · {capacity}인' if capacity else '미진행'
            lines.append(f'{"월화수목금토일"[day]}요일: {setting}')
        lines += ['', f'선택한 요일: **{"월화수목금토일"[self.weekday]}요일**',
                  '요일 선택 → 단계 선택 시 즉시 저장됩니다. 수정하기 전까지 매주 반복됩니다.',
                  '변경 사항은 다음에 새로 게시하는 모집부터 적용됩니다.',
                  f'21:10 정리 · 21:30 다음 날 모집 게시 · 거점 {self.start_time} · 공성 20:00 시작',
                  '토요일 공성전은 100인입니다. 패널이 만료되면 /관리자패널로 다시 열어 주세요.']
        return '\n'.join(lines)

    @discord.ui.select(placeholder='변경할 요일 선택', options=[
        discord.SelectOption(label=f'{day}요일', value=str(i))
        for i, day in enumerate('월화수목금토일')])
    async def select_day(self, interaction, select):
        self.weekday = int(select.values[0])
        options = [discord.SelectOption(label='미진행', value='0')]
        if self.weekday == 5:
            options += [discord.SelectOption(label=name, value=str(mode)) for mode, name in SIEGE_NAMES.items()]
        else:
            options += [discord.SelectOption(label=f'{mode}단 거점', value=str(mode)) for mode in (1, 2)]
        self.select_tier.options = options
        await interaction.response.edit_message(content=self.content(), view=self)

    @discord.ui.select(placeholder='선택한 요일의 단계 설정 (즉시 저장)', options=[
        discord.SelectOption(label='미진행', value='0'),
        discord.SelectOption(label='1단 거점', value='1'),
        discord.SelectOption(label='2단 거점', value='2')])
    async def select_tier(self, interaction, select):
        try:
            self.store.set_weekly_tier(self.guild_id, self.weekday, int(select.values[0]), actor_id=interaction.user.id)
        except ValueError as error:
            await interaction.response.send_message(str(error), ephemeral=True)
            return
        await interaction.response.edit_message(content=self.content(), view=self)
