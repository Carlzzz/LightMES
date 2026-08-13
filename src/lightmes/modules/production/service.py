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
