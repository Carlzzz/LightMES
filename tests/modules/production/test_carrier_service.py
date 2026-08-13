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
    OperationRecordRepository,
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
                        default_work_station_id=ws[i].id, allowed_work_station_ids=[ws[i].id]) for i in range(n_ops)]))
    prod = ProductionService(db_session)
    rule = prod.create_sn_rule(SnRuleCreate(code="SR", name="r", pattern="SN{SEQ:5}", seq_reset="never", product_id=p.id))
    wo = prod.create_work_order(WorkOrderCreate(code="WO", product_id=p.id, routing_id=r.id, line_id=line.id, qty=qty, sn_rule_id=rule.id))
    prod.release_work_order(wo.id)
    return prod, wo, ws, user, line


def test_bind_first_carrier_assigns_first_pending_without_passing(db_session):
    prod, wo, ws, user, line = _setup(db_session, qty=3)
    svc = CarrierService(db_session)
    su1 = svc.bind_first_carrier(wo.id, "PAL-1", user.id)
    su2 = svc.bind_first_carrier(wo.id, "PAL-2", user.id)
    # 顺序赋值：第一个 pending → PAL-1，第二个 → PAL-2
    assert su1.sn == "SN00001" and su2.sn == "SN00002"
    # 仅绑、不过站：status 仍 pending；carrier_code 已设；有活跃 binding
    assert su1.status == "pending" and su1.carrier_code == "PAL-1"
    assert CarrierBindingRepository(db_session).active_by_serial_unit(su1.id) is not None
    # 关键：无 OperationRecord（证明没过首工序）
    assert OperationRecordRepository(db_session).list_by_serial_unit(su1.id) == []


def test_bind_first_carrier_exhausted_blocks(db_session):
    prod, wo, ws, user, line = _setup(db_session, qty=1)
    svc = CarrierService(db_session)
    svc.bind_first_carrier(wo.id, "PAL-1", user.id)
    with pytest.raises(BusinessRuleError):  # pending 用完
        svc.bind_first_carrier(wo.id, "PAL-2", user.id)


def test_bind_first_carrier_duplicate_carrier_blocks(db_session):
    prod, wo, ws, user, line = _setup(db_session, qty=3)
    svc = CarrierService(db_session)
    svc.bind_first_carrier(wo.id, "PAL-DUP", user.id)
    with pytest.raises(BusinessRuleError):  # 载体码已绑活跃单元
        svc.bind_first_carrier(wo.id, "PAL-DUP", user.id)


def test_unbind_after_bind_first_carrier_allows_reuse(db_session):
    prod, wo, ws, user, line = _setup(db_session, qty=3)
    svc = CarrierService(db_session)
    svc.bind_first_carrier(wo.id, "PAL-R", user.id)
    su, _carrier_code = svc.unbind("PAL-R", user.id)
    assert su.carrier_code is None
    # 载体码可复用
    su2 = svc.bind_first_carrier(wo.id, "PAL-R", user.id)
    assert su2.sn == "SN00002"


def test_unbind_by_sn(db_session):
    prod, wo, ws, user, line = _setup(db_session, qty=3)
    svc = CarrierService(db_session)
    su = svc.bind_first_carrier(wo.id, "PAL-X", user.id)
    su_unbound, _carrier_code = svc.unbind(su.sn, user.id)
    assert su_unbound.carrier_code is None


def test_unbind_unknown_raises(db_session):
    prod, wo, ws, user, line = _setup(db_session, qty=1)
    svc = CarrierService(db_session)
    with pytest.raises(NotFoundError):
        svc.unbind("NOPE", user.id)


def test_selectable_for_station_filters(db_session):
    prod, wo, ws, user, line = _setup(db_session, qty=1)
    sel = WorkOrderRepository(db_session).selectable_for_station(line.id)
    assert wo.id in [w.id for w in sel]
    from lightmes.modules.masterdata.service import MasterDataService as MD
    other = MD(db_session).create_line(LineCreate(code="OTH", name="别线"))
    db_session.flush()
    assert WorkOrderRepository(db_session).selectable_for_station(other.id) == []
