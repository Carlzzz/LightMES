import uuid

import pytest
from sqlalchemy.exc import IntegrityError
from lightmes.modules.production.models import SerialUnit, CarrierBinding


def _build_wo(db_session):
    """创建最小依赖链 product -> line -> station -> routing -> work_order，
    使 SerialUnit 的 FK 约束可满足（共享 dev 库中不存在 id=1 的 product/work_order）。
    """
    from lightmes.modules.masterdata.service import MasterDataService
    from lightmes.modules.masterdata.schemas import (
        ProductCreate, LineCreate, WorkStationCreate, RoutingCreate, OperationCreate,
    )
    from lightmes.modules.production.service import ProductionService
    from lightmes.modules.production.schemas import WorkOrderCreate

    tag = uuid.uuid4().hex[:8]
    md = MasterDataService(db_session)
    p = md.create_product(ProductCreate(code=f"P{tag}", name="壳", type="finished"))
    line = md.create_line(LineCreate(code=f"L{tag}", name="线"))
    w = md.create_work_station(WorkStationCreate(
        code=f"W{tag}", name="作业站", line_id=line.id, seq=1))
    r = md.create_routing(RoutingCreate(code=f"R{tag}", name="路线", product_id=p.id,
        operations=[OperationCreate(seq=1, code=f"OP{tag}", name="装配",
                                    default_work_station_id=w.id, allowed_work_station_ids=[w.id])]))
    wo = ProductionService(db_session).create_work_order(
        WorkOrderCreate(code=f"WO{tag}", product_id=p.id, routing_id=r.id,
                        line_id=line.id, qty=5))
    return wo


def _su(db_session, sn, carrier=None, status="pending"):
    wo = _build_wo(db_session)
    su = SerialUnit(sn=sn, work_order_id=wo.id, product_id=wo.product_id,
                    status=status, carrier_code=carrier)
    db_session.add(su); db_session.flush(); return su


def test_serial_unit_carrier_defaults_none(db_session):
    su = _su(db_session, "SNX1")
    assert su.carrier_code is None


def test_active_carrier_unique(db_session):
    _su(db_session, "SNX2", carrier="PALLET-1")
    # 第二个同 carrier 的 SerialUnit 在 _su 内部 flush 时即触发唯一冲突
    with pytest.raises(IntegrityError):
        _su(db_session, "SNX3", carrier="PALLET-1")


def test_carrier_null_not_conflicting(db_session):
    # 多个 carrier_code=None 不冲突（部分唯一索引仅约束非空）
    _su(db_session, "SNX4", carrier=None)
    _su(db_session, "SNX5", carrier=None)
    db_session.flush()  # 无异常即通过


def test_carrier_binding_row(db_session):
    su = _su(db_session, "SNX6", carrier="PALLET-9")
    b = CarrierBinding(serial_unit_id=su.id, carrier_code="PALLET-9")
    db_session.add(b); db_session.flush()
    assert b.id is not None and b.unbound_at is None and b.bound_at is not None
