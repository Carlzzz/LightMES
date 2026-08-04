from sqlalchemy import update
from sqlalchemy.orm import Session

from lightmes.modules.production.models import SerialUnit
from lightmes.modules.production.repository import SerialUnitRepository
from lightmes.modules.trace.events import SerialUnitReworkStarted
from lightmes.modules.trace.genealogy_service import GenealogyService
from lightmes.shared.errors import (
    NotFoundError, BusinessRuleError, ValidationError, ConflictError,
)
from lightmes.shared.events import event_bus


class ReworkService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.serial_units = SerialUnitRepository(db)
        self.genealogy = GenealogyService(db)

    def rework(
        self, sn: str, target_seq: int, unbind_bind_ids: list[int] | None = None,
        reason: str | None = None, operator_id: int | None = None,
    ) -> SerialUnit:
        su = self.serial_units.get_by_sn(sn)
        if su is None:
            raise NotFoundError(f"SN 不存在: {sn}")
        if su.status == "scrapped":
            raise BusinessRuleError(f"SN 已判废，不可返工: {sn}")
        if target_seq < 0 or target_seq >= su.current_step_seq:
            raise ValidationError(
                f"返工目标工序 {target_seq} 必须小于当前 {su.current_step_seq}")
        for bind_id in (unbind_bind_ids or []):
            bind = self.genealogy.binds.get(bind_id)
            if bind is None or bind.parent_sn_id != su.id:
                raise NotFoundError(f"谱系绑定不存在或不属于本 SN: {bind_id}")
            self.genealogy.unbind(bind_id, reason=reason, operator_id=operator_id)
        prev_version = su.version
        result = self.db.execute(
            update(SerialUnit)
            .where(SerialUnit.id == su.id, SerialUnit.version == prev_version)
            .values(status="reworking", current_step_seq=target_seq,
                    version=prev_version + 1)
        )
        if result.rowcount == 0:
            raise ConflictError("该产品正被其他操作处理，请重试")
        self.db.refresh(su)
        event_bus.publish(SerialUnitReworkStarted(
            serial_unit_id=su.id, sn=su.sn, target_seq=target_seq,
        ))
        return su

    def scrap(self, sn: str, reason: str | None = None) -> SerialUnit:
        su = self.serial_units.get_by_sn(sn)
        if su is None:
            raise NotFoundError(f"SN 不存在: {sn}")
        if su.status not in ("in_process", "reworking"):
            raise BusinessRuleError(f"仅在制/返工件可判废，当前: {su.status}")
        su.status = "scrapped"
        self.db.flush()
        return su
