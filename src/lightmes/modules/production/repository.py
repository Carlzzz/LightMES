from sqlalchemy import select
from sqlalchemy.orm import Session
from lightmes.modules.production.models import SnRule


class SnRuleRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def add(self, rule: SnRule) -> SnRule:
        self.db.add(rule)
        self.db.flush()
        return rule

    def get(self, id: int) -> SnRule | None:
        return self.db.get(SnRule, id)

    def get_by_code(self, code: str) -> SnRule | None:
        return self.db.execute(
            select(SnRule).where(SnRule.code == code)
        ).scalar_one_or_none()
