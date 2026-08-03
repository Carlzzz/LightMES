import re
from datetime import datetime
from sqlalchemy import select
from sqlalchemy.orm import Session
from lightmes.modules.production.models import SnRule

_TOKEN = re.compile(r"\{([A-Za-z]+)(?::([^}]*))?\}")
_KNOWN_DATE = {"YYYY", "YY", "MM", "DD"}


def validate_pattern(pattern: str) -> None:
    for m in _TOKEN.finditer(pattern):
        name, width = m.group(1), m.group(2)
        if name == "SEQ":
            if width is None or not width.isdigit():
                raise ValueError("占位符 {SEQ} 必须带数字位数, 如 {SEQ:5}")
        elif name not in _KNOWN_DATE:
            raise ValueError(f"未知占位符: {{{name}}}")
    if not any(m.group(1) == "SEQ" for m in _TOKEN.finditer(pattern)):
        raise ValueError("pattern 必须包含 {SEQ:n} 以保证唯一")


def period_key(seq_reset: str, now: datetime) -> str:
    if seq_reset == "never":
        return "*"
    if seq_reset == "daily":
        return now.strftime("%Y-%m-%d")
    if seq_reset == "monthly":
        return now.strftime("%Y-%m")
    raise ValueError(f"未知 seq_reset: {seq_reset}")


def render(pattern: str, seq: int, now: datetime) -> str:
    def repl(m: re.Match) -> str:
        name, width = m.group(1), m.group(2)
        if name == "YYYY":
            return now.strftime("%Y")
        if name == "YY":
            return now.strftime("%y")
        if name == "MM":
            return now.strftime("%m")
        if name == "DD":
            return now.strftime("%d")
        if name == "SEQ":
            if width and width.isdigit():
                return str(seq).zfill(int(width))
        return m.group(0)

    return _TOKEN.sub(repl, pattern)


class SnGenerator:
    def __init__(self, db: Session) -> None:
        self.db = db

    def next_sn(self, rule: SnRule, now: datetime | None = None) -> str:
        now = now or datetime.now()
        # 对该 rule 行加锁，保证并发下流水唯一
        locked = self.db.execute(
            select(SnRule)
            .where(SnRule.id == rule.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        ).scalar_one()
        current_key = period_key(locked.seq_reset, now)
        if locked.seq_period_key != current_key:
            locked.seq_period_key = current_key
            locked.current_seq = 1
        else:
            locked.current_seq += 1
        self.db.flush()
        return render(locked.pattern, locked.current_seq, now)
