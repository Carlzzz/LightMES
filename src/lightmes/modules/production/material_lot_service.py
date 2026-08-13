from datetime import datetime

from sqlalchemy.orm import Session

from lightmes.modules.production.models import (
    BatchMaterialConsumption,
    MaterialLot,
    StockMovement,
)
from lightmes.modules.production.repository import MaterialLotRepository
from lightmes.shared.errors import BusinessRuleError, NotFoundError


class MaterialLotService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.lots = MaterialLotRepository(db)

    def receive(
        self,
        *,
        code: str,
        product_id: int,
        quantity: float,
        supplier_lot: str | None = None,
    ) -> MaterialLot:
        if quantity <= 0:
            raise BusinessRuleError("物料批次数量必须大于 0")
        if self.lots.get_by_code(code) is not None:
            raise BusinessRuleError(f"物料批次已存在: {code}")
        lot = MaterialLot(
            code=code,
            product_id=product_id,
            quantity=quantity,
            available_quantity=quantity,
            status="received",
            supplier_lot=supplier_lot,
            received_at=datetime.now(),
        )
        self.lots.add(lot)
        self.db.add(
            StockMovement(
                material_lot_id=lot.id,
                movement_type="receive",
                quantity=quantity,
                source_type="material_lot",
                source_id=lot.id,
            )
        )
        self.db.flush()
        return lot

    def release(self, code: str) -> MaterialLot:
        lot = self.lots.get_by_code(code)
        if lot is None:
            raise NotFoundError(f"物料批次不存在: {code}")
        if lot.status not in ("received", "quarantined"):
            raise BusinessRuleError("仅 received/quarantined 批次可放行")
        lot.status = "released"
        self.db.flush()
        return lot

    def consume(
        self,
        *,
        batch_id: int,
        operation_record_id: int,
        product_id: int,
        lot_code: str,
        quantity: float,
    ) -> BatchMaterialConsumption:
        lot = self.lots.get_by_code(lot_code)
        if lot is None:
            raise NotFoundError(f"物料批次不存在: {lot_code}")
        if lot.product_id != product_id:
            raise BusinessRuleError(f"物料批次 {lot_code} 不属于该产品")
        if lot.status != "released":
            raise BusinessRuleError(f"物料批次 {lot_code} 未放行")
        if float(lot.available_quantity) < quantity:
            raise BusinessRuleError(
                f"物料批次 {lot_code} 可用数量不足: "
                f"需要 {quantity}, 可用 {lot.available_quantity}"
            )
        lot.available_quantity = float(lot.available_quantity) - quantity
        if float(lot.available_quantity) <= 0:
            lot.status = "consumed"
        record = BatchMaterialConsumption(
            batch_id=batch_id,
            material_lot_id=lot.id,
            operation_record_id=operation_record_id,
            quantity=quantity,
        )
        self.db.add(record)
        self.db.flush()
        self.db.add(
            StockMovement(
                material_lot_id=lot.id,
                movement_type="consume",
                quantity=-quantity,
                source_type="batch_material_consumption",
                source_id=record.id,
            )
        )
        self.db.flush()
        return record

    def return_consumed(
        self,
        *,
        material_lot_id: int,
        quantity: float,
        reason: str,
    ) -> None:
        lot = self.db.get(MaterialLot, material_lot_id)
        if lot is None:
            raise NotFoundError(f"鐗╂枡鎵规涓嶅瓨鍦? {material_lot_id}")

        lot.available_quantity = float(lot.available_quantity) + quantity
        lot.quantity = float(lot.quantity) + quantity
        if lot.status == "consumed":
            lot.status = "released"

        self.db.add(
            StockMovement(
                material_lot_id=lot.id,
                movement_type="return",
                quantity=quantity,
                source_type="manual",
                notes=reason,
            )
        )
        self.db.flush()
