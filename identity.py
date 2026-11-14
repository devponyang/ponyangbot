import re


RETIRED_GUILDS = {'p', 'probe', '프로브', '고라파덕'}


def parse_display_name(display_name):
    match = re.fullmatch(r'\s*\[([^\[\]\r\n]+)\]\s*([^\[\]\r\n]+?)\s*', display_name)
    if not match or not all(part.strip() for part in match.groups()):
        raise ValueError('서버 닉네임을 [길드명] 가문명 형식으로 변경해 주세요. 예: [포도당] 디움 / [싸움은나빵] 가문명')
    guild, family = (part.strip() for part in match.groups())
    if guild.casefold() in RETIRED_GUILDS:
        raise ValueError('이전 연맹 길드 태그입니다. 현재 소속에 맞게 서버 닉네임을 [포도당], [싸움은나빵] 또는 [용병] 가문명으로 변경해 주세요.')
    return guild, family


def normalize_guild(guild):
    return 'Probe' if guild.strip() in ('P', 'Probe') else guild.strip()
