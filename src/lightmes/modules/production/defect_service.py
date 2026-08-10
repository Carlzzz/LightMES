from datetime import datetime
from sqlalchemy.orm import Session

from lightmes.modules.masterdata.query_service import MasterDataQueryService
from lightmes.modules.production.events import DefectLogged, DefectHandled
from lightmes.modules.production.models import DefectType, DefectRecord
from lightmes.modules.production.repository import SerialUnitRepository
from lightmes.modules.trace.rework_service import ReworkService
from lightmes.shared.errors import BusinessRuleError, NotFoundError
from lightmes.shared.events import event_bus


class DefectService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.query = MasterDataQueryService(db)
        self.rework = ReworkService(db)
        self.serial_units = SerialUnitRepository(db)

    def log_defect(self, defect_type_id: int, sn: str, discovered_by: int,
                   operation_id: int | None = None, work_station_id: int | None = None,
                   position: str | None = None, remark: str | None = None,
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
