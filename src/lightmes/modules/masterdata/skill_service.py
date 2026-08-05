from sqlalchemy.orm import Session

from lightmes.modules.auth.repository import UserRepository
from lightmes.modules.masterdata.models import Skill, OperatorSkill
from lightmes.modules.masterdata.repository import (
    SkillRepository, OperatorSkillRepository,
)
from lightmes.modules.masterdata.schemas import SkillCreate


class SkillService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.skills = SkillRepository(db)
        self.operator_skills = OperatorSkillRepository(db)
        self.users = UserRepository(db)

    def create_skill(self, data: SkillCreate) -> Skill:
        if self.skills.get_by_code(data.code) is not None:
            raise ValueError(f"技能编码已存在: {data.code}")
        return self.skills.add(Skill(
            code=data.code, name=data.name,
            max_level=data.max_level, description=data.description))

    def list_skills(self) -> list[Skill]:
        return self.skills.list_all()

    def set_operator_skill(self, user_id: int, skill_id: int, level: int) -> OperatorSkill:
        if self.users.get(user_id) is None:
            raise ValueError(f"用户不存在: {user_id}")
        skill = self.skills.get(skill_id)
        if skill is None:
            raise ValueError(f"技能不存在: {skill_id}")
        if level < 1 or level > skill.max_level:
            raise ValueError(f"等级越界: {level}（1..{skill.max_level}）")
        existing = self.operator_skills.get_by_user_skill(user_id, skill_id)
        if existing is not None:
            existing.level = level
            self.db.flush()
            return existing
        return self.operator_skills.add(OperatorSkill(
            user_id=user_id, skill_id=skill_id, level=level))

    def list_operator_skills(self) -> list[OperatorSkill]:
        return self.operator_skills.list_all()

    def get_operator_level(self, user_id: int, skill_id: int) -> int | None:
        os = self.operator_skills.get_by_user_skill(user_id, skill_id)
        return os.level if os is not None else None
