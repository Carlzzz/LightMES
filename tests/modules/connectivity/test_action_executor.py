import pytest
from unittest.mock import patch, MagicMock
from lightmes.modules.connectivity.action_executor import ActionExecutor
from lightmes.modules.connectivity.models import TopicMapping


def _mapping(action_type, params=None, field_path=None, condition=None, priority=100):
    return TopicMapping(
        id=1, machine_topic_id=1, action_type=action_type,
        action_params=params or {}, field_path=field_path,
        condition_expr=condition, priority=priority, is_active=True)


def test_log_event(db_session):
    ex = ActionExecutor(db_session)
    result = ex.execute_single(_mapping("log_event"), {"x": 1})
    assert result["status"] == "ok"


def test_condition_not_met_skipped(db_session):
    ex = ActionExecutor(db_session)
    m = _mapping("log_event", condition="value > 100")
    result = ex.execute_single(m, {})
    assert result["status"] == "skipped"


def test_unknown_action_error(db_session):
    ex = ActionExecutor(db_session)
    result = ex.execute_single(_mapping("bogus_action"), {})
    assert result["status"] == "error"


def test_update_wo_qty_increment(db_session):
    """Increment WO produced_qty by resolved field value."""
    from lightmes.modules.masterdata.service import MasterDataService
    from lightmes.modules.masterdata.schemas import (
        ProductCreate, LineCreate, WorkStationCreate, RoutingCreate, OperationCreate)
    from lightmes.modules.production.service import ProductionService
    from lightmes.modules.production.schemas import SnRuleCreate, WorkOrderCreate
    md = MasterDataService(db_session)
    p = md.create_product(ProductCreate(code="AEWP", name="壳", type="finished"))
    line = md.create_line(LineCreate(code="AEWL", name="线"))
    w = md.create_work_station(WorkStationCreate(code="AEWW", name="站", line_id=line.id, seq=1))
    r = md.create_routing(RoutingCreate(code="AEWR", name="路线", product_id=p.id,
        operations=[OperationCreate(seq=1, code="OP1", name="装配",
            default_work_station_id=w.id, allowed_work_station_ids=[w.id])]))
    rule = ProductionService(db_session).create_sn_rule(SnRuleCreate(code="AEWRR", name="r", pattern="AEW{SEQ:4}"))
    wo = ProductionService(db_session).create_work_order(WorkOrderCreate(
        code="AEWWO", product_id=p.id, routing_id=r.id, line_id=line.id, qty=100, sn_rule_id=rule.id))
    db_session.flush()

    ex = ActionExecutor(db_session)
    m = _mapping("update_work_order_produced_qty",
                 params={"work_order_code_path": "$.order_no", "qty_increment": True},
                 field_path="$.qty")
    result = ex.execute_single(m, {"order_no": "AEWWO", "qty": 5})
    assert result["status"] == "ok"
    db_session.expire_all()
    wo = db_session.get(type(wo), wo.id)
    assert wo.produced_qty == 5


def test_set_wo_status(db_session):
    from lightmes.modules.masterdata.service import MasterDataService
    from lightmes.modules.masterdata.schemas import (
        ProductCreate, LineCreate, WorkStationCreate, RoutingCreate, OperationCreate)
    from lightmes.modules.production.service import ProductionService
    from lightmes.modules.production.schemas import SnRuleCreate, WorkOrderCreate
    md = MasterDataService(db_session)
    p = md.create_product(ProductCreate(code="AESW", name="壳", type="finished"))
    line = md.create_line(LineCreate(code="AESL", name="线"))
    w = md.create_work_station(WorkStationCreate(code="AESW2", name="站", line_id=line.id, seq=1))
    r = md.create_routing(RoutingCreate(code="AESR", name="路线", product_id=p.id,
        operations=[OperationCreate(seq=1, code="OP1", name="装配",
            default_work_station_id=w.id, allowed_work_station_ids=[w.id])]))
    rule = ProductionService(db_session).create_sn_rule(SnRuleCreate(code="AESRR", name="r", pattern="AES{SEQ:4}"))
    wo = ProductionService(db_session).create_work_order(WorkOrderCreate(
        code="AESWO", product_id=p.id, routing_id=r.id, line_id=line.id, qty=10, sn_rule_id=rule.id))
    db_session.flush()
    ex = ActionExecutor(db_session)
    m = _mapping("set_work_order_status",
                 params={"work_order_code_path": "$.order_no", "status": "released"})
    result = ex.execute_single(m, {"order_no": "AESWO"})
    assert result["status"] == "ok"
    db_session.expire_all()
    assert db_session.get(type(wo), wo.id).status == "released"


def test_update_sn_status(db_session):
    from lightmes.modules.production.models import SerialUnit
    from lightmes.modules.masterdata.service import MasterDataService
    from lightmes.modules.masterdata.schemas import (
        ProductCreate, LineCreate, WorkStationCreate, RoutingCreate, OperationCreate)
    from lightmes.modules.production.service import ProductionService
    from lightmes.modules.production.schemas import SnRuleCreate, WorkOrderCreate
    md = MasterDataService(db_session)
    p = md.create_product(ProductCreate(code="AEUP", name="壳", type="finished"))
    line = md.create_line(LineCreate(code="AEUL", name="线"))
    w = md.create_work_station(WorkStationCreate(code="AEUW", name="站", line_id=line.id, seq=1))
    r = md.create_routing(RoutingCreate(code="AEUR", name="路线", product_id=p.id,
        operations=[OperationCreate(seq=1, code="OP1", name="装配",
            default_work_station_id=w.id, allowed_work_station_ids=[w.id])]))
    rule = ProductionService(db_session).create_sn_rule(SnRuleCreate(code="AEURR", name="r", pattern="AEU{SEQ:4}"))
    wo = ProductionService(db_session).create_work_order(WorkOrderCreate(
        code="AEUWO", product_id=p.id, routing_id=r.id, line_id=line.id, qty=10, sn_rule_id=rule.id))
    su = SerialUnit(sn="AEUSN1", work_order_id=wo.id, product_id=p.id, status="in_process")
    db_session.add(su); db_session.flush()
    ex = ActionExecutor(db_session)
    m = _mapping("update_serial_unit_status",
                 params={"sn_path": "$.sn", "status": "scrapped"})
    result = ex.execute_single(m, {"sn": "AEUSN1"})
    assert result["status"] == "ok"
    db_session.expire_all()
    assert db_session.get(SerialUnit, su.id).status == "scrapped"


def test_create_defect(db_session):
    from lightmes.modules.production.models import SerialUnit, DefectType, DefectRecord
    from lightmes.modules.masterdata.service import MasterDataService
    from lightmes.modules.masterdata.schemas import (
        ProductCreate, LineCreate, WorkStationCreate, RoutingCreate, OperationCreate)
    from lightmes.modules.production.service import ProductionService
    from lightmes.modules.production.schemas import SnRuleCreate, WorkOrderCreate
    md = MasterDataService(db_session)
    p = md.create_product(ProductCreate(code="AEDP", name="壳", type="finished"))
    line = md.create_line(LineCreate(code="AEDL", name="线"))
    w = md.create_work_station(WorkStationCreate(code="AEDW", name="站", line_id=line.id, seq=1))
    r = md.create_routing(RoutingCreate(code="AEDR", name="路线", product_id=p.id,
        operations=[OperationCreate(seq=1, code="OP1", name="装配",
            default_work_station_id=w.id, allowed_work_station_ids=[w.id])]))
    rule = ProductionService(db_session).create_sn_rule(SnRuleCreate(code="AEDRR", name="r", pattern="AED{SEQ:4}"))
    wo = ProductionService(db_session).create_work_order(WorkOrderCreate(
        code="AEDWO", product_id=p.id, routing_id=r.id, line_id=line.id, qty=10, sn_rule_id=rule.id))
    su = SerialUnit(sn="AEDSN1", work_order_id=wo.id, product_id=p.id, status="in_process")
    db_session.add(su)
    dt = DefectType(code="AUTO_TEST", name="自动测试缺陷", category="质量",
                    severity="minor", is_active=True)
    db_session.add(dt); db_session.flush()
    ex = ActionExecutor(db_session)
    m = _mapping("create_defect",
                 params={"sn_path": "$.sn", "defect_type_code": "AUTO_TEST"})
    result = ex.execute_single(m, {"sn": "AEDSN1"})
    assert result["status"] == "ok"
    db_session.expire_all()
    defects = db_session.query(DefectRecord).filter(DefectRecord.serial_unit_id == su.id).all()
    assert len(defects) == 1


def test_webhook_forward_mocked(db_session):
    """webhook_forward uses httpx sync client; mock the module's httpx reference."""
    ex = ActionExecutor(db_session)
    m = _mapping("webhook_forward",
                 params={"url": "https://example.com/hook", "method": "POST"})
    with patch("lightmes.modules.connectivity.action_executor.httpx") as mock_httpx:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = "OK"
        mock_httpx.request.return_value = mock_resp
        result = ex.execute_single(m, {"event": "cycle_complete"})
    assert result["status"] == "ok"
    mock_httpx.request.assert_called_once()


def test_execute_all_multiple(db_session):
    """Multiple mappings execute in priority order, independent failures."""
    ex = ActionExecutor(db_session)
    mappings = [
        _mapping("log_event", priority=100),
        _mapping("bogus_action", priority=200),
    ]
    results = ex.execute_all(mappings, {"x": 1})
    assert len(results) == 2
    assert results[0]["status"] == "ok"       # log_event (priority 100 first)
    assert results[1]["status"] == "error"    # bogus_action


def test_webhook_url_validation_blocks_internal():
    """SSRF: webhook to internal IP → ValueError."""
    from lightmes.modules.connectivity.action_executor import _validate_webhook_url
    with pytest.raises(ValueError, match="内网"):
        _validate_webhook_url("http://127.0.0.1:8000/admin")
    with pytest.raises(ValueError, match="内网"):
        _validate_webhook_url("http://169.254.169.254/latest/meta-data/")
    # Non-http schemes rejected
    with pytest.raises(ValueError, match="http/https"):
        _validate_webhook_url("file:///etc/passwd")
    # Public URL should not raise (example.com resolves to public IPs)
    _validate_webhook_url("https://example.com/webhook")
