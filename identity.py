import re


def parse_display_name(display_name):
    match = re.fullmatch(r'\s*\[([^\[\]\r\n]+)\]\s*([^\[\]\r\n]+?)\s*', display_name)
    if not match or not all(part.strip() for part in match.groups()):
        raise ValueError('서버 닉네임을 [길드명] 가문명 형식으로 변경해 주세요. 예: [포도당] 디움 / [P] 가문명 / [고라파덕] 가문명')
    guild, family = (part.strip() for part in match.groups())
    return normalize_guild(guild), family


def normalize_guild(guild):
    return 'Probe' if guild.strip() in ('P', 'Probe') else guild.strip()
