from sqlalchemy import select
from sqlalchemy.orm import Session
from lightmes.modules.production.models import (
    SerialUnit,
    SnRule,
    StationPass,
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

    def list_in_process_by_station(self, station_id: int) -> list[SerialUnit]:
        return list(self.db.execute(
            select(SerialUnit).where(
                SerialUnit.current_station_id == station_id,
                SerialUnit.status == "in_process",
            )
        ).scalars().all())


class StationPassRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def add(self, sp: StationPass) -> StationPass:
        self.db.add(sp)
        self.db.flush()
        return sp

    def exists_pass(self, serial_unit_id: int, routing_step_id: int) -> bool:
        row = self.db.execute(
            select(StationPass.id).where(
                StationPass.serial_unit_id == serial_unit_id,
                StationPass.routing_step_id == routing_step_id,
                StationPass.result == "pass",
            )
        ).first()
        return row is not None

    def list_by_serial_unit(self, serial_unit_id: int) -> list[StationPass]:
        return list(self.db.execute(
            select(StationPass)
            .where(StationPass.serial_unit_id == serial_unit_id)
            .order_by(StationPass.pass_time)
        ).scalars().all())
