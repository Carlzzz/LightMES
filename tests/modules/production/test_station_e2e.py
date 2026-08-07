import pytest
from fastapi.testclient import TestClient
from lightmes.main import app
from lightmes.database import get_db
from lightmes.modules.auth.service import AuthService
from lightmes.modules.auth.schemas import UserCreate
from lightmes.modules.masterdata.service import MasterDataService
from lightmes.modules.masterdata.skill_service import SkillService
from lightmes.modules.masterdata.schemas import (
    ProductCreate, LineCreate, WorkStationCreate, RoutingCreate, OperationCreate, SkillCreate,
)
from lightmes.modules.production.service import ProductionService
from lightmes.modules.production.schemas import SnRuleCreate, WorkOrderCreate


@pytest.fixture()
def client(db_session):
    app.dependency_overrides[get_db] = lambda: db_session
    yield TestClient(app)
    app.dependency_overrides.clear()


def _login(client, db_session, uname="e2e"):
    AuthService(db_session).create_user(UserCreate(username=uname, password="pw12345", display_name="E2E工"))
    db_session.flush()
    client.post("/login", data={"username": uname, "password": "pw12345"})


def _two_station(db_session, required_skill=False, op_level=None):
    md = MasterDataService(db_session); sk = SkillService(db_session)
    line = md.create_line(LineCreate(code="L", name="线"))
    ws1 = md.create_work_station(WorkStationCreate(code="W1", name="站1", line_id=line.id, seq=1))
    ws2 = md.create_work_station(WorkStationCreate(code="W2", name="站2", line_id=line.id, seq=2))
    p = md.create_product(ProductCreate(code="P", name="成品", type="finished"))
    skill = sk.create_skill(SkillCreate(code="ASSY", name="装配", max_level=3))
    ops = [
        OperationCreate(seq=10, code="OP10", name="工序10", default_work_station_id=ws1.id, allowed_work_station_ids=[ws1.id]),
        OperationCreate(seq=20, code="OP20", name="工序20", default_work_station_id=ws2.id, allowed_work_station_ids=[ws2.id]),
    ]
    routing = md.create_routing(RoutingCreate(code="RT", name="路线", product_id=p.id, operations=ops))
    if required_skill:
        op0 = md.routings.operations_of(routing.id)[0]
        op0.required_skill_id = skill.id; op0.required_level = op_level
        db_session.flush()
    prod = ProductionService(db_session)
    rule = prod.create_sn_rule(SnRuleCreate(code="SR", name="r", pattern="SN{SEQ:5}", seq_reset="never", product_id=p.id))
    wo = prod.create_work_order(WorkOrderCreate(code="WO", product_id=p.id, routing_id=routing.id, line_id=line.id, qty=5, sn_rule_id=rule.id))
    prod.release_work_order(wo.id); db_session.flush()
    return ws1, ws2, skill


def test_e2e_scan_load_pass_reset(client, db_session):
    ws1, ws2, skill = _two_station(db_session)
    _login(client, db_session)
    # 首件加载
    r1 = client.post("/production/station/load", data={"work_station_id": str(ws1.id), "scan": "WO"})
    assert r1.status_code == 200 and "工序10" in r1.text
    # 过首站 → 成功 + 出现"扫下一单元"
    r2 = client.post("/production/station/pass", data={"work_station_id": str(ws1.id), "scan": "WO"})
    assert r2.status_code == 200 and "已过" in r2.text and "下一单元" in r2.text


def test_e2e_off_station_blocked(client, db_session):
    ws1, ws2, skill = _two_station(db_session)
    _login(client, db_session)
    client.post("/production/station/pass", data={"work_station_id": str(ws1.id), "scan": "WO"})
    # 首件已在 ws1 过了工序10，SN=SN00001；用 ws1 再过 → 应到工序20@ws2，防跳站拦
    r = client.post("/production/station/pass", data={"work_station_id": str(ws1.id), "scan": "SN00001"})
    # 断言防跳站消息本身，避免 SN 格式漂移时退化成 NotFoundError 也渲染 ✗ 的假通过
    assert r.status_code == 200 and "✗" in r.text and "作业站不符" in r.text


def test_e2e_skill_insufficient_blocked(client, db_session):
    ws1, ws2, skill = _two_station(db_session, required_skill=True, op_level=3)
    _login(client, db_session)  # 登录用户无技能档案
    r = client.post("/production/station/pass", data={"work_station_id": str(ws1.id), "scan": "WO"})
    assert r.status_code == 200 and "✗" in r.text and "技能" in r.text
    # 失败分支的"返回"链接须带上 work_station_id（router pass 错误渲染修正）
    assert f"/production/station?work_station_id={ws1.id}" in r.text


def test_e2e_operator_id_cannot_be_spoofed(client, db_session):
    ws1, ws2, skill = _two_station(db_session, required_skill=True, op_level=1)
    # 登录用户有技能，但表单传假 operator_id 也应被 current_user 覆盖 → 用真身校验
    _login(client, db_session)
    from lightmes.modules.auth.repository import UserRepository
    uid = UserRepository(db_session).get_by_username("e2e").id
    SkillService(db_session).set_operator_skill(uid, skill.id, 2)
    db_session.flush()
    r = client.post("/production/station/pass",
                    data={"work_station_id": str(ws1.id), "scan": "WO", "operator_id": "99999"})
    assert r.status_code == 200 and "已过" in r.text  # 用真身(有技能)过站成功
