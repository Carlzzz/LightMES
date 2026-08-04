from sqlalchemy.orm import Session
from lightmes.modules.production.repository import SerialUnitRepository
from lightmes.modules.production.schemas import WipItem


class WipService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.serial_units = SerialUnitRepository(db)

    def wip_by_work_order(self, work_order_id: int) -> list[WipItem]:
        units = self.serial_units.list_by_work_order(work_order_id)
        return [WipItem.model_validate(u) for u in units if u.status == "in_process"]
