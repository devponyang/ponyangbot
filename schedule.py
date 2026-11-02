"""길드의 한국시간 기준 1단 거점 일정."""

from datetime import datetime, time, timedelta

from storage import KST

WEEKDAY_CAPACITY = {1: 30, 2: 25, 3: 30, 4: 25, 6: 30}


def scheduled_capacity(starts):
    """진행일의 정원. 지정되지 않은 요일은 None을 반환한다."""
    if starts.tzinfo is None:
        raise ValueError('진행일시에는 시간대가 필요합니다.')
    return WEEKDAY_CAPACITY.get(starts.astimezone(KST).weekday())


def due_event_date(now):
    """Latest 21:30 posting window; recover missed posts until that event day ends."""
    local = now.astimezone(KST)
    day = local.date() + timedelta(days=1) if local.time() >= time(21, 30) else local.date()
    return day if day.weekday() in WEEKDAY_CAPACITY else None


def event_start(day, clock):
    return datetime.combine(day, time.fromisoformat(clock), tzinfo=KST)


def cleanup_cutoff(now):
    local = now.astimezone(KST)
    cutoff = local.replace(hour=21, minute=10, second=0, microsecond=0)
    return cutoff if local >= cutoff else cutoff - timedelta(days=1)
