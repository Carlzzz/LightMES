from datetime import datetime

from sqlalchemy import update
from sqlalchemy.orm import Session

from lightmes.modules.production.models import Batch, SerialUnit, WorkOrder
from lightmes.modules.production.repository import BatchRepository
from lightmes.shared.errors import BusinessRuleError, NotFoundError


class BatchService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.batches = BatchRepository(db)

    def list_batches(self, work_order_id: int | None = None) -> list[Batch]:
        if work_order_id is not None:
            return self.batches.list_by_work_order(work_order_id)
        return self.batches.list_all()

    def create_initial_batch(self, work_order: WorkOrder) -> Batch:
        if self.batches.next_number(work_order.id) != 1:
            raise BusinessRuleError("工单已存在批次")
        batch = Batch(
            work_order_id=work_order.id,
            batch_number=1,
            status="pending",
            target_qty=work_order.qty,
            produced_qty=0,
        )
        return self.batches.add(batch)

    def start_batch(self, batch_id: int) -> Batch:
        batch = self.batches.get(batch_id)
        if batch is None:
            raise NotFoundError(f"批次不存在: {batch_id}")
        if batch.status == "pending":
            batch.status = "in_process"
            batch.started_at = batch.started_at or datetime.now()
            self.db.flush()
        return batch

    def cancel_batch(self, batch_id: int) -> Batch:
        batch = self.batches.get(batch_id)
        if batch is None:
            raise NotFoundError(f"批次不存在: {batch_id}")
        if batch.status not in ("pending", "in_process"):
            raise BusinessRuleError(f"仅 pending/in_process 批次可取消: {batch.status}")
        batch.status = "cancelled"
        self.db.flush()
        return batch

    def complete_batch(self, batch_id: int, produced_qty: int) -> Batch:
        if produced_qty < 0:
            raise BusinessRuleError("完成数量不能为负数")
        batch = self.batches.get(batch_id)
        if batch is None:
            raise NotFoundError(f"批次不存在: {batch_id}")
        if batch.status == "cancelled":
            raise BusinessRuleError("已取消批次不可完成")
        batch.produced_qty = produced_qty
        batch.status = "done"
        batch.completed_at = batch.completed_at or datetime.now()
        self.db.flush()
        return batch

    def record_finished_unit(self, batch_id: int | None) -> None:
        if batch_id is None:
            return
        batch = self.batches.get(batch_id)
        if batch is None:
            return
        batch.produced_qty += 1
        if batch.produced_qty >= batch.target_qty:
            batch.status = "done"
            batch.completed_at = batch.completed_at or datetime.now()
        self.db.flush()
