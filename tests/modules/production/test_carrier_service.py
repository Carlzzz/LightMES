import pytest
from lightmes.modules.masterdata.service import MasterDataService
from lightmes.modules.masterdata.schemas import (
    ProductCreate, LineCreate, WorkStationCreate, RoutingCreate, OperationCreate,
)
from lightmes.modules.production.service import ProductionService
from lightmes.modules.production.schemas import SnRuleCreate, WorkOrderCreate
from lightmes.modules.production.carrier_service import CarrierService
from lightmes.modules.production.repository import (
    SerialUnitRepository, CarrierBindingRepository, WorkOrderRepository,
)
from lightmes.modules.production.models import CarrierBinding
from lightmes.modules.auth.models import User
from lightmes.shared.errors import BusinessRuleError, NotFoundError


def _setup(db_session, qty=3, n_ops=2):
    md = MasterDataService(db_session)
    user = User(username="cop", password_hash="x", display_name="工")
    db_session.add(user); db_session.flush()
    p = md.create_product(ProductCreate(code="P", name="件", type="finished"))
    line = md.create_line(LineCreate(code="L", name="线"))
    ws = [md.create_work_station(WorkStationCreate(
        code=f"W{i}", name=f"站{i}", line_id=line.id, seq=i+1)) for i in range(n_ops)]
    r = md.create_routing(RoutingCreate(code="RT", name="路线", product_id=p.id, operations=[
        OperationCreate(seq=i+1, code=f"OP{i+1}", name=f"工序{i+1}",
                        default_work_station_id=ws[i].id) for i in range(n_ops)]))
    prod = ProductionService(db_session)
    rule = prod.create_sn_rule(SnRuleCreate(code="SR", name="r", pattern="SN{SEQ:5}", seq_reset="never", product_id=p.id))
    wo = prod.create_work_order(WorkOrderCreate(code="WO", product_id=p.id, routing_id=r.id, line_id=line.id, qty=qty, sn_rule_id=rule.id))
    prod.release_work_order(wo.id)
    return prod, wo, ws, user, line


def test_bind_and_pass_assigns_first_pending_in_order(db_session):
    prod, wo, ws, user, line = _setup(db_session, qty=3)
    svc = CarrierService(db_session)
    r1 = svc.bind_and_pass_first(wo.id, "PAL-1", ws[0].id, user.id)
    r2 = svc.bind_and_pass_first(wo.id, "PAL-2", ws[0].id, user.id)
    assert r1.sn == "SN00001" and r2.sn == "SN00002"  # 顺序赋值
    su1 = SerialUnitRepository(db_session).get_by_sn("SN00001")
    assert su1.carrier_code == "PAL-1" and su1.status == "in_process"
    assert CarrierBindingRepository(db_session).active_by_serial_unit(su1.id) is not None


def test_bind_exhausted_blocks(db_session):
    prod, wo, ws, user, line = _setup(db_session, qty=1)
    svc = CarrierService(db_session)
    svc.bind_and_pass_first(wo.id, "PAL-1", ws[0].id, user.id)
    with pytest.raises(BusinessRuleError):
        svc.bind_and_pass_first(wo.id, "PAL-2", ws[0].id, user.id)


def test_bind_duplicate_carrier_blocks(db_session):
    prod, wo, ws, user, line = _setup(db_session, qty=3)
    svc = CarrierService(db_session)
    svc.bind_and_pass_first(wo.id, "PAL-DUP", ws[0].id, user.id)
    with pytest.raises(BusinessRuleError):  # 载体码已绑活跃单元
        svc.bind_and_pass_first(wo.id, "PAL-DUP", ws[0].id, user.id)


def test_unbind_clears_and_allows_reuse(db_session):
    prod, wo, ws, user, line = _setup(db_session, qty=3)
    svc = CarrierService(db_session)
    svc.bind_and_pass_first(wo.id, "PAL-R", ws[0].id, user.id)
    su = svc.unbind("PAL-R", user.id)
    assert su.carrier_code is None
    binding = CarrierBindingRepository(db_session).active_by_serial_unit(su.id)
    assert binding is None  # 已无活跃绑定
    # 载体码可复用：绑到下一个 pending
    r2 = svc.bind_and_pass_first(wo.id, "PAL-R", ws[0].id, user.id)
    assert r2.sn == "SN00002"


def test_unbind_by_sn(db_session):
    prod, wo, ws, user, line = _setup(db_session, qty=3)
    svc = CarrierService(db_session)
    r = svc.bind_and_pass_first(wo.id, "PAL-X", ws[0].id, user.id)
    su = svc.unbind(r.sn, user.id)  # 用 SN 解绑
    assert su.carrier_code is None


def test_unbind_unknown_raises(db_session):
    prod, wo, ws, user, line = _setup(db_session, qty=1)
    svc = CarrierService(db_session)
    with pytest.raises(NotFoundError):
        svc.unbind("NOPE", user.id)


def _latest_binding(db_session, serial_unit_id):
    return db_session.query(CarrierBinding).filter_by(
        serial_unit_id=serial_unit_id).order_by(CarrierBinding.id.desc()).first()


def test_finish_auto_unbinds_carrier(db_session):
    # 单工序路由：bind_and_pass_first 即完工
    prod, wo, ws, user, line = _setup(db_session, qty=3, n_ops=1)
    svc = CarrierService(db_session)
    r = svc.bind_and_pass_first(wo.id, "PAL-F", ws[0].id, user.id)
    assert r.is_finished is True
    su = SerialUnitRepository(db_session).get_by_sn(r.sn)
    assert su.carrier_code is None  # 完工自动解绑
    binding = _latest_binding(db_session, su.id)
    assert binding.unbound_at is not None
    assert binding.unbound_reason == "finish"


def test_finish_auto_unbind_allows_carrier_reuse(db_session):
    # I-1 回归：完工自动解绑后，同一载体码可绑到下一 pending 且不抛异常
    prod, wo, ws, user, line = _setup(db_session, qty=3, n_ops=1)
    svc = CarrierService(db_session)
    r1 = svc.bind_and_pass_first(wo.id, "PAL-RU", ws[0].id, user.id)
    assert r1.is_finished is True
    r2 = svc.bind_and_pass_first(wo.id, "PAL-RU", ws[0].id, user.id)
    assert r2.sn == "SN00002"
    assert r2.is_finished is True


def test_manual_unbind_records_reason(db_session):
    prod, wo, ws, user, line = _setup(db_session, qty=3)
    svc = CarrierService(db_session)
    svc.bind_and_pass_first(wo.id, "PAL-M", ws[0].id, user.id)
    su = svc.unbind("PAL-M", user.id)
    assert su.carrier_code is None
    binding = _latest_binding(db_session, su.id)
    assert binding.unbound_at is not None
    assert binding.unbound_reason == "manual"


def test_selectable_for_station_filters(db_session):
    prod, wo, ws, user, line = _setup(db_session, qty=1)
    # wo 已 released → 可选
    sel = WorkOrderRepository(db_session).selectable_for_station(line.id)
    assert wo.id in [w.id for w in sel]
    # 异产线不含
    from lightmes.modules.masterdata.service import MasterDataService as MD
    other = MD(db_session).create_line(LineCreate(code="OTH", name="别线"))
    db_session.flush()
    assert WorkOrderRepository(db_session).selectable_for_station(other.id) == []
