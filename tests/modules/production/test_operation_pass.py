import pytest
from lightmes.modules.masterdata.service import MasterDataService
from lightmes.modules.masterdata.schemas import (
    ProductCreate, LineCreate, WorkStationCreate, RoutingCreate, OperationCreate,
)
from lightmes.modules.production.service import ProductionService
from lightmes.modules.production.schemas import (
    SnRuleCreate, WorkOrderCreate, OperationPassInput, ParamInput,
)
from lightmes.modules.production.operation_pass_service import OperationPassService
from lightmes.modules.production.repository import (
    SerialUnitRepository, OperationParamRepository,
)
from lightmes.shared.errors import NotFoundError, BusinessRuleError


def _line(db_session, n_ops=2):
    md = MasterDataService(db_session)
    p = md.create_product(ProductCreate(code="PPX", name="壳", type="finished"))
    line = md.create_line(LineCreate(code="PLX", name="线"))
    ws = [md.create_work_station(WorkStationCreate(
        code=f"PWX{i}", name=f"站{i}", line_id=line.id, seq=i+1)) for i in range(n_ops)]
    r = md.create_routing(RoutingCreate(code="PRX", name="路线", product_id=p.id, operations=[
        OperationCreate(seq=i+1, code=f"OP{i+1}", name=f"工序{i+1}",
                        default_work_station_id=ws[i].id) for i in range(n_ops)]))
    prod = ProductionService(db_session)
    rule = prod.create_sn_rule(SnRuleCreate(code="PRLX", name="r", pattern="X{SEQ:4}"))
    wo = prod.create_work_order(WorkOrderCreate(
        code="PXWO", product_id=p.id, routing_id=r.id, line_id=line.id, qty=10, sn_rule_id=rule.id))
    prod.release_work_order(wo.id)
    return p, line, ws, wo


def test_first_pass_generates_sn_and_binds_and_params(db_session):
    p, line, ws, wo = _line(db_session, n_ops=2)
    svc = OperationPassService(db_session)
    res = svc.pass_operation(OperationPassInput(
        work_station_id=ws[0].id, work_order_code="PXWO",
        params=[ParamInput(param_key="温度", param_value="60", unit="℃")]))
    assert res.sn == "X0001"
    assert res.passed_op.seq == 1
    assert res.next_op.seq == 2
    assert res.param_count == 1
    params = OperationParamRepository(db_session).list_by_serial_unit(
        SerialUnitRepository(db_session).get_by_sn(res.sn).id)
    assert params[0].param_key == "温度"


def test_wrong_work_station_rejected(db_session):
    p, line, ws, wo = _line(db_session, n_ops=2)
    svc = OperationPassService(db_session)
    # 首件却扫到第二作业站 → 防跳站
    with pytest.raises(BusinessRuleError):
        svc.pass_operation(OperationPassInput(work_station_id=ws[1].id, work_order_code="PXWO"))


def test_work_station_of_other_line_rejected(db_session):
    p, line, ws, wo = _line(db_session, n_ops=1)
    md = MasterDataService(db_session)
    other_line = md.create_line(LineCreate(code="OTHERL", name="别的线"))
    other_ws = md.create_work_station(WorkStationCreate(
        code="OTHERW", name="站", line_id=other_line.id, seq=1))
    svc = OperationPassService(db_session)
    # 用不属于工单产线的作业站过站 → 拒绝
    with pytest.raises(BusinessRuleError):
        svc.pass_operation(OperationPassInput(
            work_station_id=other_ws.id, work_order_code="PXWO"))


def test_full_route_finishes(db_session):
    p, line, ws, wo = _line(db_session, n_ops=2)
    svc = OperationPassService(db_session)
    r1 = svc.pass_operation(OperationPassInput(work_station_id=ws[0].id, work_order_code="PXWO"))
    r2 = svc.pass_operation(OperationPassInput(work_station_id=ws[1].id, sn=r1.sn))
    assert r2.is_finished is True
    assert r2.next_op is None


def test_unknown_work_order_rejected(db_session):
    p, line, ws, wo = _line(db_session, n_ops=1)
    svc = OperationPassService(db_session)
    with pytest.raises(NotFoundError):
        svc.pass_operation(OperationPassInput(work_station_id=ws[0].id, work_order_code="NOPE"))
