from sqlalchemy import select
from sqlalchemy.orm import Session
from lightmes.modules.masterdata.models import Line, Product, Routing
from lightmes.modules.masterdata.query_service import MasterDataQueryService
from lightmes.modules.production.models import SnRule, WorkOrder, SerialUnit
from lightmes.modules.production.repository import (
    SnRuleRepository, WorkOrderRepository, SerialUnitRepository,
)
from lightmes.modules.production.schemas import SnRuleCreate, WorkOrderCreate
from lightmes.modules.production.sn_generator import validate_pattern, SnGenerator
from lightmes.modules.production.process_snapshot import build_process_snapshot
from lightmes.modules.production.batch_service import BatchService


class ProductionService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.sn_rules = SnRuleRepository(db)
        self.work_orders = WorkOrderRepository(db)
        self.serial_units = SerialUnitRepository(db)
        self.sn_gen = SnGenerator(db)

    def create_sn_rule(self, data: SnRuleCreate) -> SnRule:
        validate_pattern(data.pattern)  # 非法 pattern 抛 ValueError
        if self.sn_rules.get_by_code(data.code) is not None:
            raise ValueError(f"SN 规则编码已存在: {data.code}")
        rule = SnRule(
            code=data.code, name=data.name, pattern=data.pattern,
            seq_reset=data.seq_reset, product_id=data.product_id,
        )
        return self.sn_rules.add(rule)

    def update_sn_rule(self, rule_id: int, *, code: str, name: str,
                       pattern: str, seq_reset: str,
                       product_id: int | None = None) -> SnRule:
        from sqlalchemy import select, func
        rule = self.sn_rules.get(rule_id)
        if rule is None:
            raise ValueError(f"SN 规则不存在: {rule_id}")
        validate_pattern(pattern)
        dup = self.sn_rules.get_by_code(code)
        if dup is not None and dup.id != rule_id:
            raise ValueError(f"SN 规则编码已存在: {code}")
        rule.code = code
        rule.name = name
        rule.pattern = pattern
        rule.seq_reset = seq_reset
        rule.product_id = product_id
        self.db.flush()
        return rule

    def delete_sn_rule(self, rule_id: int) -> None:
        from sqlalchemy import select, func
        rule = self.sn_rules.get(rule_id)
        if rule is None:
            raise ValueError(f"SN 规则不存在: {rule_id}")
        refs = self.db.execute(
            select(func.count()).select_from(WorkOrder)
            .where(WorkOrder.sn_rule_id == rule_id)
        ).scalar_one()
        if refs > 0:
            raise ValueError(f"该 SN 规则被 {refs} 个工单引用，不可删除")
        self.db.delete(rule)
        self.db.flush()

    def create_work_order(self, data: WorkOrderCreate) -> WorkOrder:
        if data.qty <= 0:
            raise ValueError(f"工单数量须大于 0: {data.qty}")
        if self.work_orders.get_by_code(data.code) is not None:
            raise ValueError(f"工单号已存在: {data.code}")
        if self.db.get(Product, data.product_id) is None:
            raise ValueError(f"产品不存在: {data.product_id}")
        routing = self.db.get(Routing, data.routing_id)
        if routing is None:
            raise ValueError(f"路线不存在: {data.routing_id}")
        if self.db.get(Line, data.line_id) is None:
            raise ValueError(f"产线不存在: {data.line_id}")
        if data.sn_rule_id is not None and self.sn_rules.get(data.sn_rule_id) is None:
            raise ValueError(f"SN 规则不存在: {data.sn_rule_id}")
        # 一致性校验：路线必须属于产品；所有工序默认作业站必须在工单产线上
        if routing.product_id != data.product_id:
            raise ValueError(f"路线 {data.routing_id} 不属于产品 {data.product_id}")
        masterdata = MasterDataQueryService(self.db)
        for op in masterdata.get_operations(data.routing_id):
            ws = masterdata.get_work_station(op.default_work_station_id)
            if ws is None or ws.line_id != data.line_id:
                raise ValueError(
                    f"工序 {op.seq} 的默认作业站不属于产线 {data.line_id}"
                )
        wo = WorkOrder(
            code=data.code, product_id=data.product_id,
            routing_id=data.routing_id, line_id=data.line_id,
            sn_rule_id=data.sn_rule_id,
            qty=data.qty, status="created",
        )
        return self.work_orders.add(wo)

    def release_work_order(self, work_order_id: int) -> WorkOrder:
        wo = self.work_orders.get(work_order_id)
        if wo is None:
            raise ValueError(f"工单不存在: {work_order_id}")
        if wo.status != "created":
            raise ValueError(f"仅 created 状态可下达, 当前: {wo.status}")
        if wo.qty <= 0:
            raise ValueError(f"工单数量须大于 0: {wo.qty}")
        if wo.sn_rule_id is None:
            raise ValueError("工单未配置 SN 规则，无法预生成 SN")
        rule = self.sn_rules.get(wo.sn_rule_id)
        if rule is None:
            raise ValueError("SN 规则不存在")
        wo.process_snapshot = build_process_snapshot(self.db, wo)
        wo.status = "released"
        batch = BatchService(self.db).create_initial_batch(wo)
        # 批量预生成 SerialUnit（pending）
        for _ in range(wo.qty):
            new_sn = self.sn_gen.next_sn(rule)
            self.serial_units.add(SerialUnit(
                sn=new_sn, work_order_id=wo.id, product_id=wo.product_id,
                status="pending", current_operation_seq=0, batch_id=batch.id))
        self.db.flush()
        return wo

    def cancel_work_order(self, work_order_id: int, reason: str, *, user_id: int,
                          force: bool = False) -> WorkOrder | tuple[WorkOrder, int]:
        """取消工单。返回 (wo, wip_count) 当存在在制品且未 force；成功返回 wo。

        规则：
        - created/released/in_process 可取消；completed/cancelled 不可
        - pending SN 置 scrapped（未投产直接作废，不计 scrap_qty —— 未投产无数量口径）
        - 在制（in_process/reworking/quarantined）SN 保留原状态，但工单取消后
          过站校验会拒绝（过站要求工单 released/in_process）→ 需先走报废/让步清理，
          force=True 表示主管确认带走在制品强行关闭
        - finished SN 保留（产量已计入）
        """
        wo = self.work_orders.get(work_order_id)
        if wo is None:
            raise ValueError(f"工单不存在: {work_order_id}")
        if wo.status in ("completed", "cancelled"):
            raise ValueError(f"工单已 {wo.status}，不可取消")
        if not reason.strip():
            raise ValueError("取消原因不可为空")
        wip = self.serial_units.count_by_wo_status(
            work_order_id, ("in_process", "reworking", "quarantined"))
        if wip > 0 and not force:
            return wo, wip
        # 未投产 pending SN 全部作废
        pending = self.db.execute(
            select(SerialUnit).where(
                SerialUnit.work_order_id == work_order_id,
                SerialUnit.status == "pending")
        ).scalars().all()
        for su in pending:
            su.status = "scrapped"
        wo.status = "cancelled"
        self.db.flush()
        return wo
