from lightmes.modules.masterdata.service import MasterDataService
from lightmes.modules.masterdata.schemas import (
    ProductCreate, LineCreate, WorkStationCreate, RoutingCreate, OperationCreate,
)
from lightmes.modules.production.service import ProductionService
from lightmes.modules.production.schemas import SnRuleCreate, WorkOrderCreate
from lightmes.modules.production.wip_service import WipService


def test_wip_excludes_pending(db_session):
    md = MasterDataService(db_session)
    line = md.create_line(LineCreate(code="L", name="线"))
    ws = md.create_work_station(WorkStationCreate(code="W1", name="站", line_id=line.id, seq=1))
    p = md.create_product(ProductCreate(code="P", name="件", type="finished"))
    routing = md.create_routing(RoutingCreate(code="RT", name="路线", product_id=p.id,
        operations=[OperationCreate(seq=10, code="OP10", name="工序", default_work_station_id=ws.id)]))
    prod = ProductionService(db_session)
    rule = prod.create_sn_rule(SnRuleCreate(code="SR", name="r", pattern="SN{SEQ:5}", seq_reset="never", product_id=p.id))
    wo = prod.create_work_order(WorkOrderCreate(code="WO", product_id=p.id, routing_id=routing.id, line_id=line.id, qty=3, sn_rule_id=rule.id))
    prod.release_work_order(wo.id)
    # 全部 pending → WIP 为空（WipService 只显示 in_process）
    assert WipService(db_session).wip_by_work_order(wo.id) == []
