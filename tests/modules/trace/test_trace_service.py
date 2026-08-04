import pytest
from lightmes.modules.masterdata.service import MasterDataService
from lightmes.modules.masterdata.schemas import (
    ProductCreate, LineCreate, WorkStationCreate, RoutingCreate, OperationCreate,
    BomCreate, BomItemCreate,
)
from lightmes.modules.production.service import ProductionService
from lightmes.modules.production.schemas import (
    SnRuleCreate, WorkOrderCreate, OperationPassInput, ComponentInput, ParamInput,
)
from lightmes.modules.production.repository import SerialUnitRepository
from lightmes.modules.production.operation_pass_service import OperationPassService
from lightmes.modules.trace.trace_service import TraceService
from lightmes.modules.trace.genealogy_service import GenealogyService
from lightmes.modules.trace.repository import GenealogyBindRepository
from lightmes.shared.errors import NotFoundError, ValidationError


def _pass_with_components_and_params(db_session):
    md = MasterDataService(db_session)
    fin = md.create_product(ProductCreate(code="TF", name="成品", type="finished"))
    c = md.create_product(
        ProductCreate(code="TC", name="主板", type="component", track_mode="serial"))
    md.create_bom(BomCreate(product_id=fin.id, items=[
        BomItemCreate(component_product_id=c.id, qty=1)]))
    line = md.create_line(LineCreate(code="TFL", name="线"))
    w = md.create_work_station(WorkStationCreate(
        code="TSW", name="装配站", line_id=line.id, seq=1))
    r = md.create_routing(RoutingCreate(code="TR", name="路线", product_id=fin.id,
        operations=[OperationCreate(seq=1, code="OP1", name="装配", default_work_station_id=w.id)]))
    prod = ProductionService(db_session)
    rule = prod.create_sn_rule(SnRuleCreate(code="TRL", name="r", pattern="T{SEQ:3}"))
    wo = prod.create_work_order(WorkOrderCreate(
        code="TWO", product_id=fin.id, routing_id=r.id, line_id=line.id,
        qty=5, sn_rule_id=rule.id))
    prod.release_work_order(wo.id)
    res = OperationPassService(db_session).pass_operation(OperationPassInput(
        work_station_id=w.id, work_order_code="TWO",
        components=[ComponentInput(component_product_id=c.id, component_sn="MB-100")],
        params=[ParamInput(param_key="torque", param_value="1.5", unit="N·m")]))
    return res.sn


def test_genealogy_forward(db_session):
    sn = _pass_with_components_and_params(db_session)
    view = TraceService(db_session).genealogy_of(sn)
    assert view.sn == sn
    assert len(view.components) == 1
    assert view.components[0].component_ref == "MB-100"


def test_where_used_reverse(db_session):
    sn = _pass_with_components_and_params(db_session)
    parents = TraceService(db_session).where_used(component_sn="MB-100")
    assert len(parents) == 1
    assert parents[0].status == "active"


def test_history_records_and_params_and_components(db_session):
    """履历：工序级 records（operation_record）+ params + components 真实数据。

    OperationPassService 写 operation_record/operation_param 而非 station_pass，
    TraceService.history_of 必须读 operation_record/operation_param，故此断言
    恢复为非空（Task 6 曾放宽为 len(h.passes)==0）。
    """
    sn = _pass_with_components_and_params(db_session)
    h = TraceService(db_session).history_of(sn)
    assert h.sn == sn
    assert len(h.records) == 1
    rec = h.records[0]
    assert rec.result == "pass"
    assert rec.operation_id is not None
    assert rec.work_station_id is not None
    assert rec.line_id is not None
    assert rec.end_time is not None
    assert len(h.components) == 1
    assert len(h.params) == 1
    p = h.params[0]
    assert p.param_key == "torque"
    assert p.param_value == "1.5"
    assert p.unit == "N·m"
    assert p.source == "manual"


def test_params_of_param_traceability(db_session):
    """工艺参数追溯：params_of 单列返回该 SN 全部工艺参数。"""
    sn = _pass_with_components_and_params(db_session)
    params = TraceService(db_session).params_of(sn)
    assert len(params) == 1
    p = params[0]
    assert p.param_key == "torque"
    assert p.param_value == "1.5"
    assert p.unit == "N·m"


def test_params_of_unknown_sn(db_session):
    with pytest.raises(NotFoundError):
        TraceService(db_session).params_of("NOPE")


def test_genealogy_unknown_sn(db_session):
    with pytest.raises(NotFoundError):
        TraceService(db_session).genealogy_of("NOPE")


def test_history_unknown_sn(db_session):
    with pytest.raises(NotFoundError):
        TraceService(db_session).history_of("NOPE")


def test_where_used_requires_a_key(db_session):
    with pytest.raises(ValidationError):
        TraceService(db_session).where_used()


def test_where_used_sees_unbound_history(db_session):
    """召回保证：逆向追溯 must 看到已解绑（被换下的）零件历史。

    组件先绑定后解绑（如返工换料），where_used 仍应返回父件且 status=unbound。
    该用例钉死"召回必须看到被移除零件"的承诺，防止未来误加 active-only 过滤。
    """
    sn = _pass_with_components_and_params(db_session)
    su = SerialUnitRepository(db_session).get_by_sn(sn)
    bind = GenealogyBindRepository(db_session).list_active_by_parent(su.id)[0]
    GenealogyService(db_session).unbind(bind.id, reason="返工换料", operator_id=None)

    parents = TraceService(db_session).where_used(component_sn="MB-100")
    assert len(parents) == 1
    assert parents[0].parent_sn_id == su.id
    assert parents[0].status == "unbound"
