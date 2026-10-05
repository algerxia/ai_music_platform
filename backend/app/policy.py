import re

BLOCKED = [
    re.compile(r"克隆.{0,8}(声音|声线|音色)"),
    re.compile(r"(cover|翻唱).{0,12}(周杰伦|邓紫棋|jay\s*chou)", re.I),
    re.compile(r"voice\s*clon", re.I),
    re.compile(r"模仿.{0,6}(在世|明星|歌手).{0,6}声音"),
]


def review_generation_text(*parts: str) -> str | None:
    text = "\n".join(p or "" for p in parts)
    for pattern in BLOCKED:
        if pattern.search(text):
            return "请求疑似未经授权的声音克隆或特定艺人模仿，已拒绝。请改用原创描述。"
    return None
