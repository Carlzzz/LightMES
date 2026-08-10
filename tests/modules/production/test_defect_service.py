import pytest
from lightmes.modules.masterdata.service import MasterDataService
from lightmes.modules.masterdata.schemas import (
    ProductCreate, LineCreate, WorkStationCreate, RoutingCreate, OperationCreate,
)
from lightmes.modules.production.service import ProductionService
from lightmes.modules.production.schemas import SnRuleCreate, WorkOrderCreate, OperationPassInput
from lightmes.modules.production.operation_pass_service import OperationPassService
from lightmes.modules.production.models import DefectType
from lightmes.modules.production.repository import SerialUnitRepository
from lightmes.modules.production.defect_service import DefectService
from lightmes.modules.auth.models import User
from lightmes.shared.errors import BusinessRuleError


def _setup(db, n_ops=2):
    md = MasterDataService(db)
    user = User(username="dsv", password_hash="x", display_name="op")
    db.add(user); db.flush()
    line = md.create_line(LineCreate(code="DSL", name="线"))
    ws1 = md.create_work_station(WorkStationCreate(code="DSW1", name="站1", line_id=line.id, seq=1))
    ws2 = md.create_work_station(WorkStationCreate(code="DSW2", name="站2", line_id=line.id, seq=2))
    p = md.create_product(ProductCreate(code="DSP", name="件", type="finished"))
    ops = [
        OperationCreate(seq=i+1, code=f"OP{i+1}", name=f"工序{i+1}",
                       default_work_station_id=ws1.id, allowed_work_station_ids=[ws1.id, ws2.id])
        for i in range(n_ops)
    ]
    routing = md.create_routing(RoutingCreate(code="DSRT", name="路线", product_id=p.id, operations=ops))
    prod = ProductionService(db)
    rule = prod.create_sn_rule(SnRuleCreate(code="DSSR", name="r", pattern="SN{SEQ:5}"))
    wo = prod.create_work_order(WorkOrderCreate(
        code="DSWO", product_id=p.id, routing_id=routing.id, line_id=line.id,
        qty=1, sn_rule_id=rule.id))
    prod.release_work_order(wo.id)
    OperationPassService(db).pass_operation(OperationPassInput(
        work_station_id=ws1.id, work_order_code="DSWO", operator_id=user.id))
    su = SerialUnitRepository(db).list_by_work_order(wo.id)[0]
    dt = DefectType(code="SCRATCH", name="划伤", category="外观", severity="major")
    db.add(dt); db.flush()
    return db, (ws1, ws2), user, wo, su, dt


def test_log_defect_quarantines_sn(db_session):
    db, (ws1, ws2), user, wo, su, dt = _setup(db_session)
    rec = DefectService(db).log_defect(
        defect_type_id=dt.id, sn=su.sn, discovered_by=user.id,
        position="左上角", remark="测试")
    db.refresh(su)
    assert su.status == "quarantined"
    assert rec.defect_type_code == "SCRATCH"
    assert rec.severity == "major"
    assert rec.handling_status == "pending"
    assert rec.position == "左上角"


def test_log_defect_scrapped_sn_rejected(db_session):
    db, (ws1, ws2), user, wo, su, dt = _setup(db_session)
    from lightmes.modules.trace.rework_service import ReworkService
    ReworkService(db).scrap(su.sn, reason="先报废")
    with pytest.raises(BusinessRuleError, match="已判废"):
        DefectService(db).log_defect(
            defect_type_id=dt.id, sn=su.sn, discovered_by=user.id)


def test_log_defect_quarantined_sn_rejected(db_session):
    db, (ws1, ws2), user, wo, su, dt = _setup(db_session)
    DefectService(db).log_defect(defect_type_id=dt.id, sn=su.sn, discovered_by=user.id)
    with pytest.raises(BusinessRuleError, match="已隔离"):
        DefectService(db).log_defect(defect_type_id=dt.id, sn=su.sn, discovered_by=user.id)


def test_handle_rework_calls_rework_service(db_session):
    db, (ws1, ws2), user, wo, su, dt = _setup(db_session)
    rec = DefectService(db).log_defect(defect_type_id=dt.id, sn=su.sn, discovered_by=user.id)
    rec = DefectService(db).handle_rework(
        record_id=rec.id, handled_by=user.id,
        target_seq=0, expected_repass_station_id=ws2.id, remark="返工")
    db.refresh(su)
    assert su.status == "reworking"
    assert rec.handling_status == "rework"
    assert rec.handled_by == user.id


def test_handle_scrap_calls_scrap(db_session):
    db, (ws1, ws2), user, wo, su, dt = _setup(db_session)
    rec = DefectService(db).log_defect(defect_type_id=dt.id, sn=su.sn, discovered_by=user.id)
    rec = DefectService(db).handle_scrap(record_id=rec.id, handled_by=user.id, remark="报废")
    db.refresh(su)
    assert su.status == "scrapped"
    assert rec.handling_status == "scrap"


def test_handle_concession_back_to_in_process(db_session):
    db, (ws1, ws2), user, wo, su, dt = _setup(db_session)
    rec = DefectService(db).log_defect(defect_type_id=dt.id, sn=su.sn, discovered_by=user.id)
    rec = DefectService(db).handle_concession(record_id=rec.id, handled_by=user.id, remark="让步")
    db.refresh(su)
    assert su.status == "in_process"
    assert rec.handling_status == "concession"


def test_handle_already_handled_rejected(db_session):
    db, (ws1, ws2), user, wo, su, dt = _setup(db_session)
    rec = DefectService(db).log_defect(defect_type_id=dt.id, sn=su.sn, discovered_by=user.id)
    DefectService(db).handle_concession(record_id=rec.id, handled_by=user.id)
    with pytest.raises(BusinessRuleError, match="已处理"):
        DefectService(db).handle_scrap(record_id=rec.id, handled_by=user.id)
