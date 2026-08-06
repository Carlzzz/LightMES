from sqlalchemy import exists, func, select
from sqlalchemy.orm import Session
from lightmes.modules.production.models import (
    OperationParam,
    OperationRecord,
    SerialUnit,
    SnRule,
    WorkOrder,
    CarrierBinding,
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

    def list_all(self) -> list[SnRule]:
        return list(self.db.execute(select(SnRule)).scalars().all())


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

    def selectable_for_station(self, line_id: int) -> list[WorkOrder]:
        return list(self.db.execute(
            select(WorkOrder).where(
                WorkOrder.line_id == line_id,
                WorkOrder.status.in_(("released", "in_process")))
            .order_by(WorkOrder.id)
        ).scalars().all())


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

    def count_pending_by_work_order(self, work_order_id: int) -> int:
        return self.db.execute(
            select(func.count()).select_from(SerialUnit).where(
                SerialUnit.work_order_id == work_order_id,
                SerialUnit.status == "pending")
        ).scalar_one()

    def get_active_by_carrier(self, carrier_code: str) -> SerialUnit | None:
        return self.db.execute(
            select(SerialUnit).where(
                SerialUnit.carrier_code == carrier_code,
                SerialUnit.status.notin_(("finished", "scrapped")))
        ).scalar_one_or_none()

    def first_pending_by_work_order(self, work_order_id: int) -> SerialUnit | None:
        """取下一个可绑定的 pending SN：从未被载体码绑定过。

        首站绑载体码只允许绑定"未被绑定过的" pending 单元——
        已绑（含解绑后）的 pending 单元须走手动 PASS 过站，不能重复绑定。
        """
        return self.db.execute(
            select(SerialUnit)
            .where(
                SerialUnit.work_order_id == work_order_id,
                SerialUnit.status == "pending",
                ~exists(
                    select(1).where(
                        CarrierBinding.serial_unit_id == SerialUnit.id)
                ),
            )
            .order_by(SerialUnit.id).limit(1)
        ).scalar_one_or_none()


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


class CarrierBindingRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def add(self, b: "CarrierBinding") -> "CarrierBinding":
        self.db.add(b); self.db.flush(); return b

    def active_by_serial_unit(self, serial_unit_id: int) -> "CarrierBinding | None":
        return self.db.execute(
            select(CarrierBinding).where(
                CarrierBinding.serial_unit_id == serial_unit_id,
                CarrierBinding.unbound_at.is_(None))
            .order_by(CarrierBinding.id.desc()).limit(1)
        ).scalar_one_or_none()
