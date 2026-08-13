import re
from markupsafe import Markup, escape

_ISSUE_REF = re.compile(r"#(\d{1,8})(?!\d)")


def issue_linkify(text) -> Markup:
    """渲染时把 #数字 替换为 /issues/数字 链接。先 escape 防 XSS。"""
    if not text:
        return Markup("")
    escaped = str(escape(text))
    return Markup(_ISSUE_REF.sub(r'<a href="/issues/\1">#\1</a>', escaped))
