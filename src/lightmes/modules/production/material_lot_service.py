from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from lightmes.modules.masterdata.query_service import MasterDataQueryService
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
        self.query = MasterDataQueryService(db)

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
        product = self.query.get_product(product_id)
        if product is None:
            raise NotFoundError(f"产品不存在: {product_id}")
        if product.track_mode != "batch":
            raise BusinessRuleError(f"产品未启用批次跟踪: {product_id}")
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
        if quantity <= 0:
            raise BusinessRuleError("消耗数量必须大于 0")
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

    def _consumed_quantity(self, material_lot_id: int) -> float:
        total = self.db.execute(
            select(func.coalesce(func.sum(BatchMaterialConsumption.quantity), 0))
            .where(BatchMaterialConsumption.material_lot_id == material_lot_id)
        ).scalar_one()
        return float(total)

    def _returned_quantity(self, material_lot_id: int) -> float:
        total = self.db.execute(
            select(func.coalesce(func.sum(StockMovement.quantity), 0))
            .where(
                StockMovement.material_lot_id == material_lot_id,
                StockMovement.movement_type == "return",
            )
        ).scalar_one()
        return float(total)

    def return_consumed(
        self,
        *,
        material_lot_id: int,
        quantity: float,
        reason: str,
    ) -> None:
        if quantity <= 0:
            raise BusinessRuleError("回补数量必须大于 0")
        lot = self.db.get(MaterialLot, material_lot_id)
        if lot is None:
            raise NotFoundError(f"物料批次不存在: {material_lot_id}")

        consumed = self._consumed_quantity(material_lot_id)
        already_returned = self._returned_quantity(material_lot_id)
        if already_returned + quantity > consumed:
            raise BusinessRuleError(
                f"回补数量超过已消耗数量: 已消耗 {consumed}, "
                f"已回补 {already_returned}, 本次回补 {quantity}"
            )

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
