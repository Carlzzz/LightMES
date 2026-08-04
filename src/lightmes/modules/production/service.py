from sqlalchemy.orm import Session
from lightmes.modules.masterdata.models import Line, Product, Routing
from lightmes.modules.production.models import SnRule, WorkOrder
from lightmes.modules.production.repository import (
    SnRuleRepository, WorkOrderRepository,
)
from lightmes.modules.production.schemas import SnRuleCreate, WorkOrderCreate
from lightmes.modules.production.sn_generator import validate_pattern


class ProductionService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.sn_rules = SnRuleRepository(db)
        self.work_orders = WorkOrderRepository(db)

    def create_sn_rule(self, data: SnRuleCreate) -> SnRule:
        validate_pattern(data.pattern)  # 非法 pattern 抛 ValueError
        if self.sn_rules.get_by_code(data.code) is not None:
            raise ValueError(f"SN 规则编码已存在: {data.code}")
        rule = SnRule(
            code=data.code, name=data.name, pattern=data.pattern,
            seq_reset=data.seq_reset, product_id=data.product_id,
        )
        return self.sn_rules.add(rule)

    def create_work_order(self, data: WorkOrderCreate) -> WorkOrder:
        if self.work_orders.get_by_code(data.code) is not None:
            raise ValueError(f"工单号已存在: {data.code}")
        if self.db.get(Product, data.product_id) is None:
            raise ValueError(f"产品不存在: {data.product_id}")
        if self.db.get(Routing, data.routing_id) is None:
            raise ValueError(f"路线不存在: {data.routing_id}")
        if self.db.get(Line, data.line_id) is None:
            raise ValueError(f"产线不存在: {data.line_id}")
        if data.sn_rule_id is not None and self.sn_rules.get(data.sn_rule_id) is None:
            raise ValueError(f"SN 规则不存在: {data.sn_rule_id}")
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
        wo.status = "released"
        self.db.flush()
        return wo
