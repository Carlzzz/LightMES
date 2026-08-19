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

    def update_skill(self, skill_id: int, *, code: str, name: str,
                     max_level: int, description: str | None = None) -> Skill:
        skill = self.skills.get(skill_id)
        if skill is None:
            raise ValueError(f"技能不存在: {skill_id}")
        dup = self.skills.get_by_code(code)
        if dup is not None and dup.id != skill_id:
            raise ValueError(f"技能编码已存在: {code}")
        # 等级下调时校验既有档案不越界
        from sqlalchemy import select, func
        over = self.db.execute(
            select(func.count()).select_from(OperatorSkill).where(
                OperatorSkill.skill_id == skill_id,
                OperatorSkill.level > max_level)
        ).scalar_one()
        if over > 0:
            raise ValueError(f"有 {over} 条人员档案等级超过新上限 {max_level}，请先调整")
        skill.code = code
        skill.name = name
        skill.max_level = max_level
        skill.description = description
        self.db.flush()
        return skill

    def delete_skill(self, skill_id: int) -> None:
        from sqlalchemy import select, func
        from lightmes.modules.masterdata.models import Operation
        skill = self.skills.get(skill_id)
        if skill is None:
            raise ValueError(f"技能不存在: {skill_id}")
        op_refs = self.db.execute(
            select(func.count()).select_from(Operation).where(
                Operation.required_skill_id == skill_id)
        ).scalar_one()
        if op_refs > 0:
            raise ValueError(f"该技能被 {op_refs} 道工序引用，不可删除")
        used = self.db.execute(
            select(func.count()).select_from(OperatorSkill).where(
                OperatorSkill.skill_id == skill_id)
        ).scalar_one()
        if used > 0:
            raise ValueError(f"有 {used} 条人员档案使用该技能，请先删除档案")
        self.db.delete(skill)
        self.db.flush()

    def delete_operator_skill(self, os_id: int) -> None:
        os = self.db.get(OperatorSkill, os_id)
        if os is None:
            raise ValueError(f"人员技能档案不存在: {os_id}")
        self.db.delete(os)
        self.db.flush()

    def get_operator_level(self, user_id: int, skill_id: int) -> int | None:
        os = self.operator_skills.get_by_user_skill(user_id, skill_id)
        return os.level if os is not None else None
