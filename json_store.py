"""Atomic local JSON state; SQLite is read only for one-time migration."""
import copy
import json
import os
import sqlite3
import tempfile
from uuid import uuid4
from datetime import datetime, timezone, timedelta
from pathlib import Path

KST = timezone(timedelta(hours=9))
ROLE_LIMITS = {'화염탑': 2, '신기전': 1, '코끼리': 1, '불퇴': 1, '대포': 2}
ROSTER_SIZE = 100


class Store:
    def __init__(self, path):
        requested = Path(path)
        self.path = None if str(path) == ':memory:' else requested.with_suffix('.json')
        self.data = {'schema_version': 1, 'revision': 0, 'events': [], 'participants': [], 'auto_posts': {}}
        if self.path is not None:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            if self.path.exists():
                self.data = json.loads(self.path.read_text(encoding='utf-8'))
                if self.data.get('schema_version') != 1 or not all(k in self.data for k in ('events', 'participants', 'auto_posts', 'revision')):
                    raise ValueError('JSON 저장 파일 형식이 올바르지 않습니다. 백업을 확인하세요.')
            else:
                legacy = requested if requested.suffix == '.db' else requested.with_suffix('.db')
                if legacy.exists():
                    self._migrate(legacy)
                self._save(self.data)

    def _migrate(self, path):
        db = sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True)
        db.row_factory = sqlite3.Row
        try:
            self.data['events'] = [dict(r) for r in db.execute('SELECT * FROM events ORDER BY rowid')]
            for i, row in enumerate(db.execute('SELECT * FROM participants ORDER BY rowid'), 1):
                p = dict(row)
                p.setdefault('guild_name', '')
                if p['guild_name'] == 'P':
                    p['guild_name'] = 'Probe'
                p.setdefault('display_name', '')
                p['join_order'] = p.get('join_order') or i
                p['priority_role'] = ''
                p['priority_order'] = 0
                self.data['participants'].append(p)
            self.data['auto_posts'] = {r['event_id']: dict(r) for r in db.execute('SELECT * FROM auto_posts')}
            self.data['revision'] = db.execute('SELECT value FROM revision WHERE id=1').fetchone()[0]
        finally:
            db.close()

    def _save(self, data):
        if self.path is None:
            return
        fd, temporary = tempfile.mkstemp(prefix=self.path.name + '.', suffix='.tmp', dir=self.path.parent)
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as output:
                json.dump(data, output, ensure_ascii=False, indent=2)
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, self.path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    @staticmethod
    def _queue(data, kind, text, guild_id, event_id=None, user_id=None):
        data.setdefault('notifications', []).append(dict(id=uuid4().hex, kind=kind, text=text,
            guild_id=str(guild_id), event_id=event_id, user_id=user_id,
            at=datetime.now(KST).isoformat(), done=False))

    def mark_report_sent(self, key, message_id):
        data = copy.deepcopy(self.data)
        data.setdefault('daily_reports', {})[key] = str(message_id)
        self._commit(data, bump_revision=False)

    def queue_log(self, text, guild_id):
        data = copy.deepcopy(self.data)
        self._queue(data, 'log', text, guild_id)
        self._commit(data, bump_revision=False)

    def finish_notification(self, notification_id, failure=None):
        data = copy.deepcopy(self.data)
        row = next(r for r in data.get('notifications', []) if r['id'] == notification_id)
        row['done'] = True
        if failure:
            self._queue(data, 'log', failure, row['guild_id'])
        # Keep pending work and the most recent 1000 completed deliveries.
        done = [r for r in data['notifications'] if r['done']][-1000:]
        data['notifications'] = [r for r in data['notifications'] if not r['done']] + done
        self._commit(data, bump_revision=False)

    def _commit(self, data, bump_revision=True):
        old_history = len(self.data.get('participation_history', []))
        events = {e['id']: e for e in data['events']}
        for row in data.get('participation_history', [])[old_history:]:
            if row['action'].startswith('기존 마지막'):
                continue
            event = events[row['event_id']]
            name = row['display_name'] or f"[{row['guild_name']}] {row['nickname']}"
            text = (f"{row['action']} | {name} | {row['status']}"
                    f" | 역할: {row['priority_role'] or '일반'} | 처리자: {row['actor_id']}"
                    f"\n모집: {event['starts']} {event['name']} ({event['id']})")
            self._queue(data, 'log', text, row['guild_id'])
            if row['action'] == '자동 순번 변경' and row['status'] == '참여':
                self._queue(data, 'promotion', name, row['guild_id'], row['event_id'], row['user_id'])
        old_events = {e['id']: e for e in self.data['events']}
        for event in data['events']:
            old = old_events.get(event['id'])
            if old and not old.get('archived') and event.get('archived'):
                self._queue(data, 'log', f"모집 정리 완료: {event['starts']} {event['name']}", event['guild_id'])
        for event_id, post in data['auto_posts'].items():
            if event_id not in self.data['auto_posts']:
                event = events[event_id]
                self._queue(data, 'log', f"모집 게시: {event['starts']} {event['name']} · {event['capacity']}인", event['guild_id'])
        if bump_revision:
            data['revision'] += 1
        self._save(data)
        self.data = data

    def close(self):
        pass  # Every mutation has already been persisted atomically.

    @property
    def revision(self):
        return self.data['revision']

    def weekly_tiers(self, guild_id):
        return list(self.data.get('weekly_tiers', {}).get(str(guild_id), [0, 1, 1, 1, 1, 0, 1]))

    def set_weekly_tier(self, guild_id, weekday, tier, actor_id=None):
        if weekday not in range(7) or tier not in ((0, 3, 4, 5) if weekday == 5 else (0, 1, 2)):
            raise ValueError('토요일은 미진행 또는 공성전, 다른 요일은 미진행·1단·2단만 가능합니다.')
        data = copy.deepcopy(self.data)
        tiers = self.weekly_tiers(guild_id)
        previous_tier = tiers[weekday]
        tiers[weekday] = tier
        data.setdefault('weekly_tiers', {})[str(guild_id)] = tiers
        labels = {0: '미진행', 1: '1단', 2: '2단', 3: '제한 발레노스 촌성', 4: '제한 발렌시아 성', 5: '무제한 메디아 성'}
        if previous_tier != tier:
            self._queue(data, 'log', f"일정 변경: {'월화수목금토일'[weekday]}요일 {labels[previous_tier]} → {labels[tier]} | 관리자: {actor_id or '미상'}", guild_id)
        self._commit(data)

    def all_events(self):
        return copy.deepcopy(self.data['events'])

    def create(self, event_id, guild_id, name, starts, capacity):
        if any(e['id'] == event_id for e in self.data['events']):
            raise ValueError('이미 존재하는 모집입니다.')
        data = copy.deepcopy(self.data)
        data['events'].append(dict(id=event_id, guild_id=str(guild_id), name=name, starts=starts, capacity=capacity, closed=0))
        self._commit(data)

    def event(self, event_id, guild_id):
        event = next((e for e in self.data['events'] if e['id'] == event_id and e['guild_id'] == str(guild_id)), None)
        if event is None:
            raise ValueError('모집 정보를 찾을 수 없습니다.')
        return copy.deepcopy(event)

    def latest_event(self, guild_id):
        return next((copy.deepcopy(e) for e in reversed(self.data['events']) if e['guild_id'] == str(guild_id)), None)

    @staticmethod
    def _rank(rows):
        return sorted(rows, key=lambda p: (0 if p.get('priority_role') else 1,
                      p.get('priority_order', 0) if p.get('priority_role') else p['join_order']))

    def members(self, event_id, waiting=False):
        status = '대기' if waiting else '참여'
        return copy.deepcopy(self._rank([p for p in self.data['participants'] if p['event_id'] == event_id and p['status'] == status]))

    def participate(self, event_id, guild_id, user_id, username, nickname, join=True,
                    guild_name='', display_name='', priority_role=None, actor_id=None):
        event = self.event(event_id, guild_id)
        if event['closed']:
            raise ValueError('이미 마감된 모집입니다.')
        data = copy.deepcopy(self.data)
        rows = [p for p in data['participants'] if p['event_id'] == event_id]
        active = [p for p in rows if p['status'] != '취소']
        own = next((p for p in rows if p['user_id'] == str(user_id)), None)
        before = copy.deepcopy(own)
        enrolled = own is not None and own['status'] != '취소'
        previous = {p['user_id']: p['status'] for p in active}
        if not join:
            if not enrolled:
                raise ValueError('참여 신청 내역이 없습니다.')
            own['status'] = '취소'
            own['priority_role'] = ''
        else:
            if not nickname.strip():
                raise ValueError('게임 닉네임을 입력해 주세요.')
            if guild_name and any(p['nickname'] == nickname.strip() and p['user_id'] != str(user_id) for p in active):
                raise ValueError('이미 같은 가문명으로 참여한 사람이 있습니다. 관리자에게 확인해 주세요.')
            role = priority_role if priority_role is not None else (own.get('priority_role', '') if enrolled else '')
            if enrolled and own.get('priority_role') and priority_role is None and (own['nickname'] != nickname.strip() or own['guild_name'] != guild_name):
                raise ValueError('부속 참여자의 닉네임 변경은 부속 우선 참여 버튼으로 역할을 다시 확인해 주세요.')
            if role:
                if role not in ROLE_LIMITS:
                    raise ValueError('부속 우선 참여 대상 역할이 아닙니다.')
                count = sum(p.get('priority_role') == role and p['user_id'] != str(user_id) for p in active)
                if count >= ROLE_LIMITS[role]:
                    raise ValueError(f'{role} 우선 참여 TO({ROLE_LIMITS[role]}명)가 찼습니다. 일반 참여 버튼으로 신청해 주세요.')
            if not enrolled and len(active) >= ROSTER_SIZE:
                raise ValueError('참여·예비 인원이 시트 최대 인원인 100명에 도달했습니다.')
            if role and not (enrolled and own.get('priority_role')) and event['capacity'] and sum(bool(p.get('priority_role')) for p in active) >= event['capacity']:
                raise ValueError('전체 정원이 부속 우선 참여자로 가득 찼습니다.')
            order = max((p['join_order'] for p in rows), default=0) + 1
            priority_order = own.get('priority_order', 0) if enrolled and own.get('priority_role') == role else max((p.get('priority_order', 0) for p in rows), default=0) + 1
            if own is None:
                own = {'event_id': event_id, 'user_id': str(user_id)}
                data['participants'].append(own)
            own.update(username=username, nickname=nickname.strip(), guild_name=guild_name,
                       display_name=display_name, status='참여', priority_role=role,
                       priority_order=priority_order, join_order=own['join_order'] if enrolled else order)
        own['updated'] = datetime.now(KST).isoformat(timespec='microseconds')
        ranked = self._rank([p for p in data['participants'] if p['event_id'] == event_id and p['status'] != '취소'])
        for i, p in enumerate(ranked):
            p['status'] = '참여' if not event['capacity'] or i < event['capacity'] else '대기'
        if 'participation_history' not in data:
            data['participation_history'] = self._legacy_history()
        action = ('신청 취소' if not join else
                  '용병 추가' if str(user_id).startswith('merc-') and not enrolled else
                  '재신청' if before and not enrolled else
                  '신청 변경/재확인' if enrolled else '참여 신청')
        def record(person, action, actor):
            return dict(event_id=event_id, guild_id=str(guild_id), user_id=person['user_id'],
                        display_name=person.get('display_name', ''), nickname=person['nickname'],
                        guild_name=person.get('guild_name', ''), action=action,
                        status=person['status'], priority_role=person.get('priority_role', ''),
                        at=own['updated'], actor_id=str(actor))
        data['participation_history'].append(record(own, action, actor_id or user_id))
        for person in ranked:
            if person['user_id'] != str(user_id) and previous.get(person['user_id']) != person['status']:
                data['participation_history'].append(record(person, '자동 순번 변경', actor_id or user_id))
        self._commit(data)
        return {p['user_id']: p['status'] for p in ranked if previous.get(p['user_id']) != p['status']}

    def _legacy_history(self):
        guilds = {e['id']: e['guild_id'] for e in self.data['events']}
        return [dict(event_id=p['event_id'], guild_id=guilds[p['event_id']], user_id=p['user_id'],
                     display_name=p.get('display_name', ''), nickname=p['nickname'],
                     guild_name=p.get('guild_name', ''), action='기존 마지막 변경 (전체 이력 없음)',
                     status=p['status'], priority_role=p.get('priority_role', ''),
                     at=p['updated'], actor_id='미상')
                for p in self.data['participants'] if p.get('updated') and p['event_id'] in guilds]

    def participation_history(self, guild_id, day=None, user_id=None, family=None):
        events = {e['id']: e for e in self.data['events'] if e['guild_id'] == str(guild_id)
                  and (day is None or e['starts'][:10] == day)}
        history = self.data.get('participation_history')
        if history is None:
            history = self._legacy_history()
        return [dict(copy.deepcopy(row), event_name=events[row['event_id']]['name'],
                     starts=events[row['event_id']]['starts'])
                for row in history if row['event_id'] in events
                and (user_id is None or row['user_id'] == str(user_id))
                and (family is None or row['nickname'] == family.strip())]

    def close_event(self, event_id, guild_id):
        self.event(event_id, guild_id)
        data = copy.deepcopy(self.data)
        next(e for e in data['events'] if e['id'] == event_id)['closed'] = 1
        self._commit(data)

    def roster(self, guild_id, size=ROSTER_SIZE):
        event = self.latest_event(guild_id)
        if event is None:
            return None
        if event.get('archived'):
            return [['', ''] for _ in range(size)]
        members = self.members(event['id']) + self.members(event['id'], waiting=True)
        if len(members) > size:
            raise ValueError('거점시트 입력 범위를 초과한 참여자가 있습니다.')
        return [['예비' if p['status'] == '대기' else p['guild_name'], p['nickname']] for p in members] + [['', ''] for _ in range(size - len(members))]

    def posted(self, event_id):
        return event_id in self.data['auto_posts']

    def record_welcome(self, message_id, guild_id, channel_id, member_id, roles):
        data = copy.deepcopy(self.data)
        data.setdefault('welcome_messages', {})[str(message_id)] = dict(
            guild_id=str(guild_id), channel_id=str(channel_id), member_id=str(member_id),
            roles={emoji: str(role_id) for emoji, role_id in roles.items()})
        self._commit(data)

    def welcome_message(self, message_id):
        return copy.deepcopy(self.data.get('welcome_messages', {}).get(str(message_id)))

    def archive_due(self, guild_id, cutoff):
        data = copy.deepcopy(self.data)
        changed = False
        for event in data['events']:
            if event['guild_id'] != str(guild_id) or event.get('archived'):
                continue
            starts = datetime.strptime(event['starts'], '%Y-%m-%d %H:%M').replace(tzinfo=KST)
            if starts <= cutoff:
                event.update(closed=1, archived=True)
                changed = True
        if changed:
            self._commit(data)
        return changed

    def cleanup_posts(self, guild_id):
        ids = {e['id'] for e in self.data['events'] if e['guild_id'] == str(guild_id) and e.get('archived')}
        return [copy.deepcopy(p) for key, p in self.data['auto_posts'].items() if key in ids and not p.get('deleted')]

    def mark_deleted(self, event_id):
        data = copy.deepcopy(self.data)
        data['auto_posts'][event_id]['deleted'] = True
        self._commit(data)

    def mark_posted(self, event_id, channel_id, message_id):
        data = copy.deepcopy(self.data)
        data['auto_posts'][event_id] = dict(event_id=event_id, channel_id=str(channel_id), message_id=str(message_id))
        self._commit(data)

    def snapshot(self):
        # Compatibility for local diagnostics; never written to spreadsheet tabs.
        events = [['모집ID', '서버ID', '거점명', '진행일시', '정원', '상태', '참여인원']]
        for e in self.data['events']:
            events.append([e['id'], e['guild_id'], e['name'], e['starts'], e['capacity'], '마감' if e['closed'] else '모집중', len(self.members(e['id']))])
        participants = [['모집ID', '거점명', '진행일시', '디스코드ID', '디스코드이름', '가문명', '상태', '변경일시', '길드']]
        for p in self.data['participants']:
            e = next(e for e in self.data['events'] if e['id'] == p['event_id'])
            participants.append([p['event_id'], e['name'], e['starts'], p['user_id'], p['username'], p['nickname'], p['status'], p['updated'], p['guild_name']])
        return self.revision, events, participants
