from datetime import datetime
from sqlalchemy import select
from sqlalchemy.orm import Session

from lightmes.modules.masterdata.query_service import MasterDataQueryService
from lightmes.modules.production.events import DefectLogged, DefectHandled
from lightmes.modules.production.models import DefectType, DefectRecord, FirstInspectionRecord
from lightmes.modules.production.repository import SerialUnitRepository
from lightmes.modules.trace.rework_service import ReworkService
from lightmes.shared.errors import BusinessRuleError, NotFoundError, ValidationError
from lightmes.shared.events import event_bus


SYSTEM_DEFECT_TYPES = [
    {"code": "FIRST_INSPECTION_FAIL", "name": "首检不合格",
     "category": "质量", "severity": "critical",
     "description": "系统自动创建：首检不合格"},
]


class DefectService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.query = MasterDataQueryService(db)
        self.rework = ReworkService(db)
        self.serial_units = SerialUnitRepository(db)

    def _get_or_create_system_defect_type(self, code: str, name: str,
                                           severity: str, category: str,
                                           description: str | None = None) -> DefectType:
        """获取或创建系统缺陷类型（幂等）。不自动重激活——重激活逻辑在 ensure_system_defect_types。"""
        dt = self.db.execute(
            select(DefectType).where(DefectType.code == code)
        ).scalar_one_or_none()
        if dt is None:
            dt = DefectType(code=code, name=name, category=category,
                            severity=severity, description=description, is_active=True)
            self.db.add(dt); self.db.flush()
        return dt

    def ensure_system_defect_types(self) -> None:
        """启动时调用（admin 级）：幂等创建 + 强制激活系统缺陷类型。"""
        for spec in SYSTEM_DEFECT_TYPES:
            dt = self._get_or_create_system_defect_type(**spec)
            dt.is_active = True  # 启动时确保激活（覆盖管理员误停用）
        self.db.flush()

    def log_defect(self, defect_type_id: int, sn: str, discovered_by: int,
                   operation_id: int | None = None, work_station_id: int | None = None,
                   position: str | None = None, remark: str | None = None,
                   create_issue: bool = False,
                   ) -> DefectRecord:
        dt = self.db.get(DefectType, defect_type_id)
        if dt is None or not dt.is_active:
            raise NotFoundError(f"缺陷类型不存在或已停用: {defect_type_id}")
        su = self.serial_units.get_by_sn(sn)
        if su is None:
            raise NotFoundError(f"SN 不存在: {sn}")
        if su.status == "scrapped":
            raise BusinessRuleError(f"SN 已判废，不可登记缺陷: {sn}")
        if su.status == "quarantined":
            raise BusinessRuleError(f"SN 已隔离，请先处理既有缺陷: {sn}")
        su.status = "quarantined"
        record = DefectRecord(
            defect_type_id=dt.id,
            defect_type_code=dt.code, defect_type_name=dt.name,
            severity=dt.severity,
            serial_unit_id=su.id, work_order_id=su.work_order_id,
            operation_id=operation_id, work_station_id=work_station_id,
            position=position, discovered_by=discovered_by,
            handling_status="pending", remark=remark)
        self.db.add(record)
        self.db.flush()
        event_bus.publish(DefectLogged(
            defect_record_id=record.id, serial_unit_id=su.id, sn=su.sn,
            defect_type_code=dt.code, severity=dt.severity))
        if create_issue:
            from lightmes.modules.issue.service import IssueService
            IssueService(self.db).create_from_defect(
                record, reported_by_id=discovered_by)
        return record

    def _get_pending(self, record_id: int) -> DefectRecord:
        record = self.db.get(DefectRecord, record_id)
        if record is None:
            raise NotFoundError(f"缺陷记录不存在: {record_id}")
        if record.handling_status != "pending":
            raise BusinessRuleError(f"缺陷已处理: {record.handling_status}")
        return record

    def handle_rework(self, record_id: int, handled_by: int,
                      target_seq: int, expected_repass_station_id: int,
                      remark: str | None = None) -> DefectRecord:
        record = self._get_pending(record_id)
        su = self.serial_units.get(record.serial_unit_id)
        self.rework.rework(
            sn=su.sn, target_seq=target_seq,
            expected_repass_station_id=expected_repass_station_id,
            operator_id=handled_by)
        record.handling_status = "rework"
        record.handled_by = handled_by
        record.handled_at = datetime.now()
        record.handling_remark = remark
        self.db.flush()
        event_bus.publish(DefectHandled(
            defect_record_id=record.id, serial_unit_id=su.id, sn=su.sn,
            decision="rework"))
        return record

    def handle_scrap(self, record_id: int, handled_by: int,
                     remark: str | None = None) -> DefectRecord:
        record = self._get_pending(record_id)
        su = self.serial_units.get(record.serial_unit_id)
        self.rework.scrap(su.sn, reason=remark)
        record.handling_status = "scrap"
        record.handled_by = handled_by
        record.handled_at = datetime.now()
        record.handling_remark = remark
        self.db.flush()
        event_bus.publish(DefectHandled(
            defect_record_id=record.id, serial_unit_id=su.id, sn=su.sn,
            decision="scrap"))
        return record

    def handle_concession(self, record_id: int, handled_by: int,
                          remark: str | None = None) -> DefectRecord:
        record = self._get_pending(record_id)
        su = self.serial_units.get(record.serial_unit_id)
        su.status = "in_process"  # 一律回 in_process
        record.handling_status = "concession"
        record.handled_by = handled_by
        record.handled_at = datetime.now()
        record.handling_remark = remark
        self.db.flush()
        event_bus.publish(DefectHandled(
            defect_record_id=record.id, serial_unit_id=su.id, sn=su.sn,
            decision="concession"))
        return record

    def log_defect_from_inspection(self, fi_record: FirstInspectionRecord, sn: str,
                                    discovered_by: int,
                                    remark: str | None = None) -> DefectRecord:
        """首检失败时调用：用系统 FIRST_INSPECTION_FAIL 类型 + 既有 log_defect。

        fi_record 必须是已持久化的（id 非 None），以保证 operation_id/work_station_id 可信。
        """
        if fi_record.id is None:
            raise ValidationError("fi_record 必须已持久化（flush/commit 后再调用）")
        dt = self._get_or_create_system_defect_type(
            code="FIRST_INSPECTION_FAIL", name="首检不合格",
            severity="critical", category="质量",
            description="系统自动创建：首检不合格")
        return self.log_defect(
            defect_type_id=dt.id, sn=sn, discovered_by=discovered_by,
            operation_id=fi_record.operation_id,
            work_station_id=fi_record.work_station_id,
            position=None, remark=remark)
