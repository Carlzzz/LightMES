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
    {"code": "TEST_DATA_FAIL", "name": "测试数据不合格",
     "category": "功能", "severity": "critical",
     "description": "系统自动创建：工序测试数据判定 failed"},
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
        self._sync_linked_issue(record, handled_by, "rework")
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
        self._sync_linked_issue(record, handled_by, "scrap")
        return record

    def handle_concession(self, record_id: int, handled_by: int,
                          remark: str | None = None) -> DefectRecord:
        record = self._get_pending(record_id)
        su = self.serial_units.get(record.serial_unit_id)
        if su.status == "finished":
            raise BusinessRuleError(
                f"完工件不可让步回退到在制（当前 finished）：请走问题单处置")
        su.status = "in_process"  # 一律回 in_process
        record.handling_status = "concession"
        record.handled_by = handled_by
        record.handled_at = datetime.now()
        record.handling_remark = remark
        self.db.flush()
        event_bus.publish(DefectHandled(
            defect_record_id=record.id, serial_unit_id=su.id, sn=su.sn,
            decision="concession"))
        self._sync_linked_issue(record, handled_by, "use_as_is")
        return record

    def _sync_linked_issue(self, record: DefectRecord, user_id: int,
                           disposition: str) -> None:
        """缺陷处置后同步关联 issue：open/acknowledged → resolved，避免双体系漂移。"""
        from lightmes.modules.issue.models import Issue
        issue = self.db.execute(
            select(Issue).where(
                Issue.defect_id == record.id,
                Issue.status.in_(("open", "acknowledged")),
            ).order_by(Issue.id.desc()).limit(1)
        ).scalars().first()
        if issue is None:
            return
        if issue.status == "open":
            issue.acknowledged_by_id = user_id
            issue.acknowledged_at = datetime.now()
        issue.status = "resolved"
        issue.root_cause = issue.root_cause or f"缺陷处置同步（{record.handling_status}）"
        issue.containment_action = issue.containment_action or "缺陷已处置，系统自动同步"
        issue.disposition = disposition
        issue.resolved_by_id = user_id
        issue.resolved_at = datetime.now()
        issue.resolution_notes = "缺陷处置联动自动 resolve"
        self.db.flush()

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

    def log_defect_from_test_data(self, td_record, sn: str, discovered_by: int,
                                  remark: str | None = None) -> DefectRecord:
        """测试数据判定 failed 时调用：隔离 SN + 建 TEST_DATA_FAIL 缺陷。

        td_record（TestDataRecord）必须已持久化。已 quarantined 的 SN 直接
        返回 None（上一缺陷未处理，不重复建单）。
        """
        if td_record.id is None:
            raise ValidationError("td_record 必须已持久化（flush/commit 后再调用）")
        su = self.serial_units.get_by_sn(sn)
        if su is None:
            raise NotFoundError(f"SN 不存在: {sn}")
        if su.status == "quarantined":
            return None
        dt = self._get_or_create_system_defect_type(
            code="TEST_DATA_FAIL", name="测试数据不合格",
            severity="critical", category="功能",
            description="系统自动创建：工序测试数据判定 failed")
        return self.log_defect(
            defect_type_id=dt.id, sn=sn, discovered_by=discovered_by,
            operation_id=td_record.operation_id,
            work_station_id=td_record.work_station_id,
            position=None, remark=remark)
