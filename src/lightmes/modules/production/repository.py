from sqlalchemy import select
from sqlalchemy.orm import Session
from lightmes.modules.production.models import (
    OperationParam,
    OperationRecord,
    SerialUnit,
    SnRule,
    WorkOrder,
)


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


class WorkOrderRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def add(self, wo: WorkOrder) -> WorkOrder:
        self.db.add(wo)
        self.db.flush()
        return wo

    def get(self, id: int) -> WorkOrder | None:
        return self.db.get(WorkOrder, id)

    def get_by_code(self, code: str) -> WorkOrder | None:
        return self.db.execute(
            select(WorkOrder).where(WorkOrder.code == code)
        ).scalar_one_or_none()


class SerialUnitRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def add(self, su: SerialUnit) -> SerialUnit:
        self.db.add(su)
        self.db.flush()
        return su

    def get(self, id: int) -> SerialUnit | None:
        return self.db.get(SerialUnit, id)

    def get_by_sn(self, sn: str) -> SerialUnit | None:
        return self.db.execute(
            select(SerialUnit).where(SerialUnit.sn == sn)
        ).scalar_one_or_none()

    def list_by_work_order(self, work_order_id: int) -> list[SerialUnit]:
        return list(self.db.execute(
            select(SerialUnit).where(SerialUnit.work_order_id == work_order_id)
        ).scalars().all())


class OperationRecordRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def add(self, rec: OperationRecord) -> OperationRecord:
        self.db.add(rec)
        self.db.flush()
        return rec

    def list_by_serial_unit(self, serial_unit_id: int) -> list[OperationRecord]:
        return list(self.db.execute(
            select(OperationRecord)
            .where(OperationRecord.serial_unit_id == serial_unit_id)
            .order_by(OperationRecord.end_time)
        ).scalars().all())


class OperationParamRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def add(self, param: OperationParam) -> OperationParam:
        self.db.add(param)
        self.db.flush()
        return param

    def list_by_record(self, record_id: int) -> list[OperationParam]:
        return list(self.db.execute(
            select(OperationParam).where(
                OperationParam.operation_record_id == record_id)
        ).scalars().all())

    def list_by_serial_unit(self, serial_unit_id: int) -> list[OperationParam]:
        return list(self.db.execute(
            select(OperationParam)
            .join(OperationRecord,
                  OperationParam.operation_record_id == OperationRecord.id)
            .where(OperationRecord.serial_unit_id == serial_unit_id)
            .order_by(OperationParam.recorded_at)
        ).scalars().all())
