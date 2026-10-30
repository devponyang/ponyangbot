"""Run the configured sync once and print a bounded Google API diagnostic."""
import json
import os
from dotenv import load_dotenv
from requests import HTTPError
from storage import Store
from sheets import Sheets


def main():
    load_dotenv()
    store = Store(os.getenv('DATABASE_PATH', 'data/bot.json'))
    sheets = Sheets(os.environ['GOOGLE_SPREADSHEET_ID'], os.environ['GOOGLE_APPLICATION_CREDENTIALS'])
    guild_id = os.environ['DISCORD_GUILD_ID']
    stage = 'archive'
    try:
        sheets.archive_legacy_tabs(store.path.parent / 'backups')
        stage = 'roster'
        sheets.sync(store.roster(guild_id), store.latest_event(guild_id))
        print('Sheet sync OK')
    except HTTPError as error:
        payload = error.response.json().get('error', {})
        print(json.dumps({'stage': stage, 'code': error.response.status_code,
                          'status': payload.get('status'), 'message': str(payload.get('message', ''))[:1200]}, ensure_ascii=True))
        raise SystemExit(1)


if __name__ == '__main__':
    main()
