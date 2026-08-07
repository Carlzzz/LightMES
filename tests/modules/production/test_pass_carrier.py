import pytest
from lightmes.modules.auth.models import User
from lightmes.modules.masterdata.service import MasterDataService
from lightmes.modules.masterdata.schemas import (
    ProductCreate, LineCreate, WorkStationCreate, RoutingCreate, OperationCreate,
)
from lightmes.modules.production.service import ProductionService
from lightmes.modules.production.schemas import (
    SnRuleCreate, WorkOrderCreate, OperationPassInput,
)
from lightmes.modules.production.carrier_service import CarrierService
from lightmes.modules.production.operation_pass_service import OperationPassService
from lightmes.modules.production.repository import SerialUnitRepository, CarrierBindingRepository
from lightmes.modules.production.models import CarrierBinding
from lightmes.shared.errors import BusinessRuleError


def _setup(db_session, n_ops=2, qty=3):
    md = MasterDataService(db_session)
    p = md.create_product(ProductCreate(code="P", name="件", type="finished"))
    line = md.create_line(LineCreate(code="L", name="线"))
    ws = [md.create_work_station(WorkStationCreate(
        code=f"W{i}", name=f"站{i}", line_id=line.id, seq=i+1)) for i in range(n_ops)]
    r = md.create_routing(RoutingCreate(code="RT", name="路线", product_id=p.id, operations=[
        OperationCreate(seq=i+1, code=f"OP{i+1}", name=f"工序{i+1}",
                        default_work_station_id=ws[i].id, allowed_work_station_ids=[ws[i].id]) for i in range(n_ops)]))
    prod = ProductionService(db_session)
    rule = prod.create_sn_rule(SnRuleCreate(code="SR", name="r", pattern="SN{SEQ:5}", seq_reset="never", product_id=p.id))
    wo = prod.create_work_order(WorkOrderCreate(code="WO", product_id=p.id, routing_id=r.id, line_id=line.id, qty=qty, sn_rule_id=rule.id))
    prod.release_work_order(wo.id)
    return prod, wo, ws


def test_work_order_first_item_takes_first_pending(db_session):
    prod, wo, ws = _setup(db_session)
    svc = OperationPassService(db_session)
    r = svc.pass_operation(OperationPassInput(
        work_station_id=ws[0].id, work_order_code="WO"))
    # 取第一个 pending（SN00001），不新生成
    assert r.sn == "SN00001"
    su = SerialUnitRepository(db_session).get_by_sn("SN00001")
    assert su.status == "in_process" and su.current_operation_seq == 1


def test_carrier_code_locates_active_unit(db_session):
    prod, wo, ws = _setup(db_session)
    su_repo = SerialUnitRepository(db_session)
    # 手工给第一个 pending 绑载体码 + 投产首工序（模拟已投产）
    svc = OperationPassService(db_session)
    r = svc.pass_operation(OperationPassInput(work_station_id=ws[0].id, work_order_code="WO"))
    su = su_repo.get_by_sn(r.sn)
    su.carrier_code = "PALLET-7"
    db_session.flush()
    # 后续站扫载体码过站 → 命中同一单元推进工序2
    r2 = svc.pass_operation(OperationPassInput(work_station_id=ws[1].id, sn="PALLET-7"))
    assert r2.sn == su.sn and r2.is_finished is True


def test_work_order_first_item_exhausted_blocks(db_session):
    prod, wo, ws = _setup(db_session, n_ops=1, qty=1)
    svc = OperationPassService(db_session)
    svc.pass_operation(OperationPassInput(work_station_id=ws[0].id, work_order_code="WO"))
    # 唯一 pending 已投产 → 再用工单号首件 → 无 pending → 拦截
    with pytest.raises(BusinessRuleError):
        svc.pass_operation(OperationPassInput(work_station_id=ws[0].id, work_order_code="WO"))


def test_sn_scan_still_works(db_session):
    prod, wo, ws = _setup(db_session)
    svc = OperationPassService(db_session)
    r = svc.pass_operation(OperationPassInput(work_station_id=ws[0].id, work_order_code="WO"))
    r2 = svc.pass_operation(OperationPassInput(work_station_id=ws[1].id, sn=r.sn))
    assert r2.is_finished is True


def test_finish_auto_unbinds_carrier_with_reason(db_session):
    # 单工序路线：bind_first_carrier → 手动 PASS → 完工自动解绑 + unbound_reason="finish"
    md = MasterDataService(db_session)
    user = User(username="fin", password_hash="x", display_name="Fin")
    db_session.add(user); db_session.flush()
    p = md.create_product(ProductCreate(code="P", name="件", type="finished"))
    line = md.create_line(LineCreate(code="L", name="线"))
    ws = md.create_work_station(WorkStationCreate(code="W", name="站", line_id=line.id, seq=1))
    r = md.create_routing(RoutingCreate(code="RT", name="路线", product_id=p.id, operations=[
        OperationCreate(seq=10, code="OP10", name="工序", default_work_station_id=ws.id, allowed_work_station_ids=[ws.id])]))
    prod = ProductionService(db_session)
    rule = prod.create_sn_rule(SnRuleCreate(code="SR", name="r", pattern="SN{SEQ:5}", seq_reset="never", product_id=p.id))
    wo = prod.create_work_order(WorkOrderCreate(code="WO", product_id=p.id, routing_id=r.id, line_id=line.id, qty=1, sn_rule_id=rule.id))
    prod.release_work_order(wo.id)
    svc = CarrierService(db_session)
    su = svc.bind_first_carrier(wo.id, "PAL-FIN", user.id)
    OperationPassService(db_session).pass_operation(OperationPassInput(
        work_station_id=ws.id, sn=su.sn, operator_id=user.id))
    db_session.refresh(su)
    assert su.status == "finished" and su.carrier_code is None
    binding = CarrierBindingRepository(db_session).active_by_serial_unit(su.id)
    assert binding is None  # 完工解绑后无活跃绑定
    # 取最近一条 binding 行（含已解绑）检查原因
    from sqlalchemy import select
    last = db_session.execute(select(CarrierBinding).where(
        CarrierBinding.serial_unit_id == su.id).order_by(CarrierBinding.id.desc()).limit(1)).scalar_one()
    assert last.unbound_reason == "finish"


def test_manual_unbind_records_reason(db_session):
    # bind → 手动 unbind → unbound_reason="manual"
    md = MasterDataService(db_session)
    user = User(username="man", password_hash="x", display_name="Man")
    db_session.add(user); db_session.flush()
    p = md.create_product(ProductCreate(code="PM", name="件", type="finished"))
    line = md.create_line(LineCreate(code="LM", name="线"))
    ws = md.create_work_station(WorkStationCreate(code="WM", name="站", line_id=line.id, seq=1))
    r = md.create_routing(RoutingCreate(code="RM", name="路线", product_id=p.id, operations=[
        OperationCreate(seq=10, code="OM", name="工序", default_work_station_id=ws.id, allowed_work_station_ids=[ws.id])]))
    prod = ProductionService(db_session)
    rule = prod.create_sn_rule(SnRuleCreate(code="SM", name="r", pattern="SN{SEQ:5}", seq_reset="never", product_id=p.id))
    wo = prod.create_work_order(WorkOrderCreate(code="WOM", product_id=p.id, routing_id=r.id, line_id=line.id, qty=1, sn_rule_id=rule.id))
    prod.release_work_order(wo.id)
    svc = CarrierService(db_session)
    su = svc.bind_first_carrier(wo.id, "PAL-MAN", user.id)
    svc.unbind("PAL-MAN", user.id)
    from sqlalchemy import select
    last = db_session.execute(select(CarrierBinding).where(
        CarrierBinding.serial_unit_id == su.id).order_by(CarrierBinding.id.desc()).limit(1)).scalar_one()
    assert last.unbound_reason == "manual"
