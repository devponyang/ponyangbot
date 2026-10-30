import threading
import json
import os
from pathlib import Path
from uuid import uuid4
from urllib.parse import quote
from datetime import datetime

from google.auth.transport.requests import AuthorizedSession
from google.oauth2.service_account import Credentials

from identity import normalize_guild
from storage import ROSTER_SIZE


def sync_error_message(error):
    response = getattr(error, 'response', None)
    if response is None:
        return str(error) if isinstance(error, ValueError) else type(error).__name__
    code = response.status_code
    try:
        message = str(response.json().get('error', {}).get('message', '')).lower()
    except (ValueError, AttributeError):
        message = ''
    if 'protected cell' in message or 'protected range' in message:
        target = '제목 F2(병합 F2:R3)' if 'data[1]' in message else '수정 대상 범위'
        return f'HTTP {code}: {target} 보호됨 — 보호된 범위의 편집 허용 사용자에 봇 서비스 계정을 추가하세요.'
    reasons = {400: '요청 범위 또는 입력 값 확인 필요', 401: '인증 실패',
               403: '편집 권한 또는 API 사용 권한 확인 필요', 404: '시트를 찾지 못함',
               429: '요청 한도 초과 — 자동 재시도'}
    return f'HTTP {code}: {reasons.get(code, "구글 API 요청 실패 — 자동 재시도")}'


def find_role(rows, guild, family):
    """Read repeated guild/family/role blocks using their header labels."""
    if not rows:
        raise ValueError('전체 직업 시트가 비어 있습니다.')
    headers = [''.join(str(v).split()) for v in rows[0]]
    blocks = []
    guild_columns = [i for i, h in enumerate(headers) if h in ('길드', '길드명')]
    for index, start in enumerate(guild_columns):
        end = guild_columns[index + 1] if index + 1 < len(guild_columns) else len(headers)
        family_col = next((i for i in range(start, end) if headers[i] == '가문명'), None)
        role_col = next((i for i in range(start, end) if headers[i] == '역할'), None)
        if family_col is not None and role_col is not None:
            blocks.append((start, family_col, role_col))
    if not blocks:
        raise ValueError('전체 직업 시트의 길드·가문명·역할 헤더를 확인해 주세요.')
    matches = []
    for row in rows[1:]:
        for g, f, r in blocks:
            cell = lambda i: str(row[i]).strip() if i < len(row) else ''
            if normalize_guild(cell(g)) == normalize_guild(guild) and cell(f) == family:
                matches.append(cell(r))
    if not matches:
        raise ValueError('전체 직업 시트에서 해당 길드·가문명을 찾지 못했습니다.')
    if len(matches) != 1:
        raise ValueError('전체 직업 시트에 같은 길드·가문명이 중복되어 있습니다. 관리자에게 확인해 주세요.')
    return matches[0]


class Sheets:
    def __init__(self, spreadsheet_id, credentials_path):
        credentials = Credentials.from_service_account_file(
            credentials_path, scopes=['https://www.googleapis.com/auth/spreadsheets'])
        self.session = AuthorizedSession(credentials)
        self.url = f'https://sheets.googleapis.com/v4/spreadsheets/{spreadsheet_id}'
        self.lock = threading.RLock()

    def request(self, method, suffix='', **kwargs):
        with self.lock:
            response = self.session.request(method, self.url + suffix, timeout=30, **kwargs)
            response.raise_for_status()
            return response.json()

    def lookup_role(self, guild, family):
        meta = self.request('GET', params={'fields': 'sheets(properties)'})
        props = next((s['properties'] for s in meta['sheets']
                      if ''.join(s['properties']['title'].split()) == '전체직업시트'), None)
        if props is None:
            raise ValueError('전체 직업 시트 탭을 찾지 못했습니다.')
        title = props['title'].replace("'", "''")
        rows = self.request('GET', '/values/' + quote(f"'{title}'", safe=''),
                            params={'valueRenderOption': 'FORMATTED_VALUE'}).get('values', [])
        return find_role(rows, guild, family)

    def archive_legacy_tabs(self, backup_dir):
        """Delete only the two retired bot tabs, after a durable JSON backup."""
        meta = self.request('GET', params={'fields': 'sheets(properties)'})
        targets = [s['properties'] for s in meta['sheets']
                   if s['properties']['title'] in ('봇_거점', '봇_참여')]
        if not targets:
            return
        backup = {'schema_version': 1, 'tabs': []}
        for props in targets:
            title = props['title'].replace("'", "''")
            values = self.request('GET', '/values/' + quote(f"'{title}'", safe=''),
                                  params={'valueRenderOption': 'FORMULA'})
            backup['tabs'].append({'properties': props, 'values': values.get('values', [])})
        directory = Path(backup_dir)
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f'legacy-sheets-{uuid4().hex}.json'
        with path.open('x', encoding='utf-8') as output:
            json.dump(backup, output, ensure_ascii=False, indent=2)
            output.flush()
            os.fsync(output.fileno())
        # If backup creation fails, execution never reaches deletion.
        self.request('POST', ':batchUpdate', json={'requests': [
            {'deleteSheet': {'sheetId': props['sheetId']}} for props in targets]})

    def sync(self, roster, event=None):
        if roster is None:
            return
        if len(roster) != ROSTER_SIZE or any(len(row) != 2 for row in roster):
            raise ValueError('거점시트 명단은 100행 2열이어야 합니다.')
        meta = self.request('GET', params={'fields': 'sheets(properties)'})
        props = next((s['properties'] for s in meta['sheets'] if s['properties']['title'] == '거점시트'), None)
        if props is None:
            raise ValueError('거점시트 탭을 찾을 수 없습니다.')
        grid = props['gridProperties']
        if grid['rowCount'] < 115 or grid['columnCount'] < 12:
            raise ValueError('거점시트 K16:L115 입력 범위를 확인해 주세요.')
        data = [{'range': "'거점시트'!K16:L115", 'values': roster}]
        if event is not None:
            title = ''
            if not event.get('archived'):
                starts = datetime.strptime(event['starts'], '%Y-%m-%d %H:%M')
                weekday = '월화수목금토일'[starts.weekday()]
                title = f"{starts:%Y-%m-%d} {weekday}요일 {event['capacity']}인 거점전 참여명단"
            data.append({'range': "'거점시트'!F2", 'values': [[title]]})
        self.request('POST', '/values:batchUpdate', json={'valueInputOption': 'RAW', 'data': data})
