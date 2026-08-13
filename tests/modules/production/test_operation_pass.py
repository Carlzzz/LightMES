import pytest
from sqlalchemy import select
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
from lightmes.modules.production.material_lot_service import MaterialLotService
from lightmes.modules.production.models import BatchMaterialConsumption, MaterialLot
from lightmes.shared.errors import NotFoundError, BusinessRuleError


def _line(db_session, n_ops=2, *, release=True):
    md = MasterDataService(db_session)
    p = md.create_product(ProductCreate(code="PPX", name="壳", type="finished"))
    line = md.create_line(LineCreate(code="PLX", name="线"))
    ws = [md.create_work_station(WorkStationCreate(
        code=f"PWX{i}", name=f"站{i}", line_id=line.id, seq=i+1)) for i in range(n_ops)]
    r = md.create_routing(RoutingCreate(code="PRX", name="路线", product_id=p.id, operations=[
        OperationCreate(seq=i+1, code=f"OP{i+1}", name=f"工序{i+1}",
                        default_work_station_id=ws[i].id, allowed_work_station_ids=[ws[i].id]) for i in range(n_ops)]))
    prod = ProductionService(db_session)
    rule = prod.create_sn_rule(SnRuleCreate(code="PRLX", name="r", pattern="X{SEQ:4}"))
    wo = prod.create_work_order(WorkOrderCreate(
        code="PXWO", product_id=p.id, routing_id=r.id, line_id=line.id, qty=10, sn_rule_id=rule.id))
    if release:
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


def _line_with_op_bom(db_session, n_ops=3):
    """构造带 consume_at_operation_seq 的 BOM 测试环境。

    c_op2 声明在 op2 装配，c_op3 声明在 op3 装配。
    """
    from lightmes.modules.masterdata.schemas import BomCreate, BomItemCreate
    p, line, ws, wo = _line(db_session, n_ops=n_ops, release=False)
    md = MasterDataService(db_session)
    c_op2 = md.create_product(ProductCreate(code="COP2", name="op2件",
                                            type="component", track_mode="serial"))
    c_op3 = md.create_product(ProductCreate(code="COP3", name="op3件",
                                            type="component", track_mode="serial"))
    md.create_bom(BomCreate(product_id=p.id, items=[
        BomItemCreate(component_product_id=c_op2.id, qty=1,
                      consume_at_operation_seq=2),
        BomItemCreate(component_product_id=c_op3.id, qty=1,
                      consume_at_operation_seq=3),
    ]))
    ProductionService(db_session).release_work_order(wo.id)
    return p, line, ws, wo, c_op2, c_op3


def test_pass_blocks_when_required_part_for_op_not_scanned(db_session):
    """op2 应装 c_op2 但未扫 → 即时校验拦截。"""
    from lightmes.modules.production.schemas import OperationPassInput
    p, line, ws, wo, c_op2, c_op3 = _line_with_op_bom(db_session, n_ops=3)
    svc = OperationPassService(db_session)
    # 首检不涉及，op1 无应装件，过 op1
    r1 = svc.pass_operation(OperationPassInput(work_station_id=ws[0].id,
                                                work_order_code="PXWO"))
    # op2 应装 c_op2 但未扫
    with pytest.raises(BusinessRuleError) as exc:
        svc.pass_operation(OperationPassInput(
            work_station_id=ws[1].id, sn=r1.sn))
    assert "op2件" in str(exc.value)


def test_pass_ok_when_required_part_scanned_this_op(db_session):
    """op2 应装 c_op2，扫了 → 通过。"""
    from lightmes.modules.production.schemas import (
        OperationPassInput, ComponentInput,
    )
    p, line, ws, wo, c_op2, c_op3 = _line_with_op_bom(db_session, n_ops=3)
    svc = OperationPassService(db_session)
    r1 = svc.pass_operation(OperationPassInput(work_station_id=ws[0].id,
                                                work_order_code="PXWO"))
    r2 = svc.pass_operation(OperationPassInput(
        work_station_id=ws[1].id, sn=r1.sn,
        components=[ComponentInput(
            component_product_id=c_op2.id, component_sn="SN-OP2-1",
            component_batch_no=None, qty=1)]))
    assert r2.passed_op.seq == 2


def test_pass_blocks_when_scanning_part_for_future_op(db_session):
    """op2 扫了 op3 的件 → 扫错件拦截（在 bind_components）。"""
    from lightmes.modules.production.schemas import (
        OperationPassInput, ComponentInput,
    )
    p, line, ws, wo, c_op2, c_op3 = _line_with_op_bom(db_session, n_ops=3)
    svc = OperationPassService(db_session)
    r1 = svc.pass_operation(OperationPassInput(work_station_id=ws[0].id,
                                                work_order_code="PXWO"))
    with pytest.raises(BusinessRuleError) as exc:
        svc.pass_operation(OperationPassInput(
            work_station_id=ws[1].id, sn=r1.sn,
            components=[ComponentInput(
                component_product_id=c_op3.id, component_sn="SN-OP3-early",
                component_batch_no=None, qty=1)]))
    assert "工序 3" in str(exc.value)


def test_final_op_cumulative_check_still_blocks_missing(db_session):
    """NULL-seq BOM（老数据）漏检到最终工序 → 最终累积兜底拦截。

    注：consume_at_operation_seq == NULL 的 BOM 行不参与 5d-① 即时校验，
    只在 5d-③ 最终工序累积校验里强制（兼容老数据）。
    """
    from lightmes.modules.production.schemas import OperationPassInput
    from lightmes.modules.masterdata.schemas import BomCreate, BomItemCreate
    p2, line2, ws2, wo2 = _line(db_session, n_ops=2, release=False)
    c_null = MasterDataService(db_session).create_product(
        ProductCreate(code="CNULL", name="老件", type="component", track_mode="serial"))
    MasterDataService(db_session).create_bom(BomCreate(product_id=p2.id, items=[
        BomItemCreate(component_product_id=c_null.id, qty=1)]))  # NULL seq
    ProductionService(db_session).release_work_order(wo2.id)
    svc = OperationPassService(db_session)
    r_a = svc.pass_operation(OperationPassInput(work_station_id=ws2[0].id,
                                                 work_order_code=wo2.code))
    with pytest.raises(BusinessRuleError) as exc:
        svc.pass_operation(OperationPassInput(work_station_id=ws2[1].id, sn=r_a.sn))
    assert "老件" in str(exc.value)


def test_station_service_filters_components_to_current_op(db_session):
    """station_view 只显示 consume_at_operation_seq IS NULL OR == 当前 op 的件。"""
    from lightmes.modules.masterdata.schemas import BomCreate, BomItemCreate
    from lightmes.modules.production.station_service import StationService

    p, line, ws, wo = _line(db_session, n_ops=3, release=False)
    md = MasterDataService(db_session)
    c_op2 = md.create_product(ProductCreate(code="COP2", name="OP2 Comp",
                                             type="component", track_mode="serial"))
    c_op3 = md.create_product(ProductCreate(code="COP3", name="OP3 Comp",
                                             type="component", track_mode="serial"))
    c_null = md.create_product(ProductCreate(code="CNUL", name="Legacy Comp",
                                             type="component", track_mode="serial"))
    md.create_bom(BomCreate(product_id=p.id, items=[
        BomItemCreate(component_product_id=c_op2.id, qty=1,
                      consume_at_operation_seq=2),
        BomItemCreate(component_product_id=c_op3.id, qty=1,
                      consume_at_operation_seq=3),
        BomItemCreate(component_product_id=c_null.id, qty=1),
    ]))
    ProductionService(db_session).release_work_order(wo.id)

    svc = OperationPassService(db_session)
    r1 = svc.pass_operation(OperationPassInput(work_station_id=ws[0].id,
                                                work_order_code="PXWO"))

    # 进入 op2，应看到 c_op2 + c_null，不应看到 c_op3
    view = StationService(db_session).load(
        scan=r1.sn, work_station_id=ws[1].id, operator_id=None)
    comp_ids = {c.component_product_id for c in view.components}
    assert c_op2.id in comp_ids
    assert c_null.id in comp_ids
    assert c_op3.id not in comp_ids


def test_pass_consumes_batch_component_lot(db_session):
    """批次件扫描后扣减 MaterialLot 并生成消耗记录。"""
    from lightmes.modules.production.schemas import ComponentInput, OperationPassInput
    from lightmes.modules.masterdata.schemas import BomCreate, BomItemCreate
    p, line, ws, wo = _line(db_session, n_ops=1, release=False)
    md = MasterDataService(db_session)
    comp = md.create_product(ProductCreate(
        code="BATCHCOMP", name="批次件", type="component", track_mode="batch"))
    md.create_bom(BomCreate(product_id=p.id, items=[
        BomItemCreate(component_product_id=comp.id, qty=2)]))
    ProductionService(db_session).release_work_order(wo.id)
    lot = MaterialLotService(db_session).receive(
        code="BATCH-LOT", product_id=comp.id, quantity=5)
    MaterialLotService(db_session).release(lot.code)

    svc = OperationPassService(db_session)
    svc.pass_operation(OperationPassInput(
        work_station_id=ws[0].id,
        work_order_code=wo.code,
        components=[ComponentInput(
            component_product_id=comp.id,
            component_batch_no="BATCH-LOT",
            qty=2,
        )],
    ))

    lot_after = db_session.get(MaterialLot, lot.id)
    assert float(lot_after.available_quantity) == 3
    consumption = db_session.execute(
        select(BatchMaterialConsumption).where(
            BatchMaterialConsumption.material_lot_id == lot.id
        )
    ).scalar_one()
    assert float(consumption.quantity) == 2

