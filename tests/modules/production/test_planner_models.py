from datetime import datetime
from lightmes.modules.production.models import Shift, ScheduleChangeLog, WorkOrder


def test_shift_model_basic_fields(db_session):
    s = Shift(code="S1", name="早班", start_time="06:00", end_time="14:00",
              days_of_week=[1,2,3,4,5], line_id=None, is_active=True, sort_order=1)
    db_session.add(s); db_session.flush()
    assert s.id is not None
    assert s.code == "S1"
    assert s.days_of_week == [1,2,3,4,5]


def test_schedule_change_log_model_basic_fields(db_session):
    # Brief test as-written referenced work_order_id=1 without creating it,
    # which violates the FK constraint. Build a real WorkOrder first so the
    # ScheduleChangeLog FK is satisfied.
    from lightmes.modules.masterdata.service import MasterDataService
    from lightmes.modules.masterdata.schemas import (
        ProductCreate, LineCreate, WorkStationCreate, RoutingCreate, OperationCreate,
    )
    from lightmes.modules.production.service import ProductionService
    from lightmes.modules.production.schemas import SnRuleCreate, WorkOrderCreate
    md = MasterDataService(db_session)
    p = md.create_product(ProductCreate(code="SCLP", name="壳", type="finished"))
    line = md.create_line(LineCreate(code="SCLL", name="线"))
    w = md.create_work_station(WorkStationCreate(
        code="SCLW", name="站", line_id=line.id, seq=1))
    r = md.create_routing(RoutingCreate(
        code="SCLR", name="路线", product_id=p.id,
        operations=[OperationCreate(seq=1, code="SOP1", name="装配",
                                    default_work_station_id=w.id, allowed_work_station_ids=[w.id])]))
    svc = ProductionService(db_session)
    rule = svc.create_sn_rule(SnRuleCreate(code="SCLR1", name="r", pattern="SC{SEQ:4}"))
    wo = svc.create_work_order(WorkOrderCreate(
        code="SCLWO", product_id=p.id, routing_id=r.id, line_id=line.id,
        qty=10, sn_rule_id=rule.id))
    db_session.flush()

    log = ScheduleChangeLog(
        work_order_id=wo.id, user_id=None, action="schedule",
        before=None, after={"line_id": line.id, "planned_start": "2026-08-11T08:00", "planned_end": "2026-08-11T16:00"})
    db_session.add(log); db_session.flush()
    assert log.id is not None
    assert log.action == "schedule"
    assert log.undone_at is None


def test_work_order_has_priority_default_5(db_session):
    """新建 WorkOrder，priority 默认 5。"""
    from lightmes.modules.masterdata.service import MasterDataService
    from lightmes.modules.masterdata.schemas import (
        ProductCreate, LineCreate, WorkStationCreate, RoutingCreate, OperationCreate,
    )
    from lightmes.modules.production.service import ProductionService
    from lightmes.modules.production.schemas import (
        SnRuleCreate, WorkOrderCreate,
    )
    md = MasterDataService(db_session)
    p = md.create_product(ProductCreate(code="PMP", name="壳", type="finished"))
    line = md.create_line(LineCreate(code="MPL", name="线"))
    w = md.create_work_station(WorkStationCreate(
        code="MPW", name="站", line_id=line.id, seq=1))
    r = md.create_routing(RoutingCreate(
        code="MPR", name="路线", product_id=p.id,
        operations=[OperationCreate(seq=1, code="OP1", name="装配",
                                    default_work_station_id=w.id, allowed_work_station_ids=[w.id])]))
    svc = ProductionService(db_session)
    rule = svc.create_sn_rule(SnRuleCreate(code="MPR1", name="r", pattern="MP{SEQ:4}"))
    wo = svc.create_work_order(WorkOrderCreate(
        code="MPWO", product_id=p.id, routing_id=r.id, line_id=line.id,
        qty=10, sn_rule_id=rule.id))
    db_session.flush()
    db_session.refresh(wo)
    assert wo.priority == 5
