from collections import namedtuple

import pytest
from lightmes.modules.masterdata.service import MasterDataService
from lightmes.modules.masterdata.schemas import (
    ProductCreate, LineCreate, WorkStationCreate, RoutingCreate, OperationCreate,
    BomCreate, BomItemCreate,
)
from lightmes.modules.production.service import ProductionService
from lightmes.modules.production.schemas import WorkOrderCreate
from lightmes.modules.production.models import SerialUnit, OperationRecord
from lightmes.modules.production.repository import (
    SerialUnitRepository, OperationRecordRepository,
)
from lightmes.modules.trace.genealogy_service import GenealogyService
from lightmes.modules.trace.schemas import ComponentBind
from lightmes.shared.errors import BusinessRuleError, ValidationError, ConflictError, NotFoundError

_SetupCtx = namedtuple("_SetupCtx", ["line", "work_station", "op", "work_order"])


def _setup(db_session):
    md = MasterDataService(db_session)
    fin = md.create_product(ProductCreate(code="GF", name="成品", type="finished"))
    c_ser = md.create_product(
        ProductCreate(code="GCS", name="主板", type="component", track_mode="serial"))
    c_bat = md.create_product(
        ProductCreate(code="GCB", name="螺丝", type="consumable", track_mode="batch"))
    other = md.create_product(
        ProductCreate(code="GX", name="不在BOM", type="component", track_mode="serial"))
    md.create_bom(BomCreate(product_id=fin.id, items=[
        BomItemCreate(component_product_id=c_ser.id, qty=1),
        BomItemCreate(component_product_id=c_bat.id, qty=4),
    ]))
    line = md.create_line(LineCreate(code="GL", name="线"))
    w = md.create_work_station(WorkStationCreate(
        code="GW", name="作业站", line_id=line.id, seq=1))
    r = md.create_routing(RoutingCreate(code="GR", name="路线", product_id=fin.id,
        operations=[OperationCreate(seq=1, code="OP1", name="装配", default_work_station_id=w.id, allowed_work_station_ids=[w.id])]))
    wo = ProductionService(db_session).create_work_order(
        WorkOrderCreate(code="GWO", product_id=fin.id, routing_id=r.id,
                        line_id=line.id, qty=10))
    def make_su(sn):
        return SerialUnitRepository(db_session).add(
            SerialUnit(sn=sn, work_order_id=wo.id, product_id=fin.id))
    op = md.routings.operations_of(r.id)[0]
    ctx = _SetupCtx(line=line, work_station=w, op=op, work_order=wo)
    return fin, c_ser, c_bat, other, make_su, ctx


def test_bind_serial_and_batch(db_session):
    fin, c_ser, c_bat, other, make_su, _ctx = _setup(db_session)
    su = make_su("F1")
    svc = GenealogyService(db_session)
    binds = svc.bind_components(su, [
        ComponentBind(component_product_id=c_ser.id, component_sn="MB-1"),
        ComponentBind(component_product_id=c_bat.id, component_batch_no="LOT-1", qty=4),
    ], operator_id=None)
    assert len(binds) == 2
    types = {b.component_type for b in binds}
    assert types == {"serial", "batch"}


def test_bind_component_not_in_bom_rejected(db_session):
    fin, c_ser, c_bat, other, make_su, _ctx = _setup(db_session)
    su = make_su("F2")
    svc = GenealogyService(db_session)
    with pytest.raises(BusinessRuleError):
        svc.bind_components(su, [
            ComponentBind(component_product_id=other.id, component_sn="X-1")],
            operator_id=None)


def test_serial_component_requires_sn(db_session):
    fin, c_ser, c_bat, other, make_su, _ctx = _setup(db_session)
    su = make_su("F3")
    svc = GenealogyService(db_session)
    with pytest.raises(ValidationError):
        svc.bind_components(su, [
            ComponentBind(component_product_id=c_ser.id)],  # 缺 sn
            operator_id=None)


def test_batch_component_requires_batch_no(db_session):
    fin, c_ser, c_bat, other, make_su, _ctx = _setup(db_session)
    su = make_su("F4")
    svc = GenealogyService(db_session)
    with pytest.raises(ValidationError):
        svc.bind_components(su, [
            ComponentBind(component_product_id=c_bat.id)],  # 缺 batch_no
            operator_id=None)


def test_unique_component_occupancy_rejected(db_session):
    fin, c_ser, c_bat, other, make_su, _ctx = _setup(db_session)
    su1 = make_su("F5")
    su2 = make_su("F6")
    svc = GenealogyService(db_session)
    svc.bind_components(su1, [
        ComponentBind(component_product_id=c_ser.id, component_sn="MB-DUP")],
        operator_id=None)
    with pytest.raises(ConflictError):
        svc.bind_components(su2, [
            ComponentBind(component_product_id=c_ser.id, component_sn="MB-DUP")],
            operator_id=None)


def test_unbind(db_session):
    fin, c_ser, c_bat, other, make_su, _ctx = _setup(db_session)
    su = make_su("F7")
    svc = GenealogyService(db_session)
    binds = svc.bind_components(su, [
        ComponentBind(component_product_id=c_bat.id, component_batch_no="LOT-7")],
        operator_id=None)
    unbound = svc.unbind(binds[0].id, reason="返工换料", operator_id=None)
    assert unbound.status == "unbound"
    assert unbound.unbind_reason == "返工换料"
    with pytest.raises(BusinessRuleError):
        svc.unbind(binds[0].id, reason="再次", operator_id=None)  # 已 unbound


def test_unbind_unknown_rejected(db_session):
    fin, c_ser, c_bat, other, make_su, _ctx = _setup(db_session)
    svc = GenealogyService(db_session)
    with pytest.raises(NotFoundError):
        svc.unbind(999999, reason=None, operator_id=None)


def test_bind_with_operation_record_id(db_session):
    fin, c_ser, c_bat, other, make_su, ctx = _setup(db_session)
    su = make_su("F8")
    rec = OperationRecordRepository(db_session).add(OperationRecord(
        serial_unit_id=su.id, work_order_id=ctx.work_order.id,
        operation_id=ctx.op.id, work_station_id=ctx.work_station.id,
        line_id=ctx.line.id, result="pass"))
    svc = GenealogyService(db_session)
    binds = svc.bind_components(su, [
        ComponentBind(component_product_id=c_ser.id, component_sn="MB-OPREC")],
        operator_id=None, operation_record_id=rec.id)
    assert len(binds) == 1
    b = binds[0]
    assert b.operation_record_id == rec.id
