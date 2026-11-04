"""길드의 한국시간 기준 1단 거점 일정."""

from datetime import datetime, time, timedelta

from storage import KST

WEEKDAY_CAPACITY = {1: 30, 2: 25, 3: 30, 4: 25, 6: 30}


DEFAULT_WEEKLY_TIERS = [0, 1, 1, 1, 1, 0, 1]


SIEGE_NAMES = {3: '제한 발레노스 촌성', 4: '제한 발렌시아 성', 5: '무제한 메디아 성'}


def mode_name(mode):
    return f'공성전 · {SIEGE_NAMES[mode]}' if mode in SIEGE_NAMES else f'{mode}단거점'


def capacity_for(weekday, tier):
    if tier == 0:
        return None
    if weekday == 5:
        return 100 if tier in SIEGE_NAMES else None
    if tier in SIEGE_NAMES:
        return None
    if tier == 2:
        return 50 if weekday in (4, 6) else 40
    return 25 if weekday in (0, 2, 4) else 30


def scheduled_capacity(starts, tiers=None):
    """진행일의 정원. 지정되지 않은 요일은 None을 반환한다."""
    if starts.tzinfo is None:
        raise ValueError('진행일시에는 시간대가 필요합니다.')
    weekday = starts.astimezone(KST).weekday()
    return capacity_for(weekday, (tiers or DEFAULT_WEEKLY_TIERS)[weekday])


def due_event_date(now, tiers=None):
    """Latest 21:30 posting window; recover missed posts until that event day ends."""
    local = now.astimezone(KST)
    day = local.date() + timedelta(days=1) if local.time() >= time(21, 30) else local.date()
    return day if capacity_for(day.weekday(), (tiers or DEFAULT_WEEKLY_TIERS)[day.weekday()]) else None


def event_start(day, clock, mode=1):
    return datetime.combine(day, time.fromisoformat('20:00' if mode in SIEGE_NAMES else clock), tzinfo=KST)


def require_priority_open(event, now=None):
    if '공성전' in event['name']:
        return
    starts = datetime.strptime(event['starts'], '%Y-%m-%d %H:%M').replace(tzinfo=KST)
    cutoff = starts - timedelta(hours=1)
    if (now or datetime.now(KST)) >= cutoff:
        raise ValueError(f'부속 우선 참여는 시작 1시간 전({cutoff:%H:%M})부터 신청할 수 없습니다. 일반 참여 버튼을 이용해 주세요.')


def cleanup_cutoff(now):
    local = now.astimezone(KST)
    cutoff = local.replace(hour=21, minute=10, second=0, microsecond=0)
    return cutoff if local >= cutoff else cutoff - timedelta(days=1)
