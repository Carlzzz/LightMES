"""Skip route tests: auth guard + supervisor role check.

Full E2E (skip -> Layer 2 shows skipped -> continue) is in Task 12.
"""
from fastapi.testclient import TestClient
from lightmes.main import app
from lightmes.database import SessionLocal
from lightmes.modules.auth.models import User, Role
from lightmes.shared.security import hash_password


def _login_supervisor(client, db, username="skipadmin"):
    """创建 supervisor 用户并登录。"""
    role = db.execute(
        __import__("sqlalchemy").select(Role).where(Role.name == "supervisor")
    ).scalar_one_or_none()
    if role is None:
        role = Role(name="supervisor", display_name="主管", is_system=True)
        db.add(role); db.flush()
    user = User(username=username, password_hash=hash_password("pass123"),
                display_name="主管", role_id=role.id)
    db.add(user); db.commit()
    db.refresh(user)
    resp = client.post("/login", data={"username": username, "password": "pass123"})
    assert resp.status_code in (200, 303)
    return user


def _login_operator(client, db, username="skipop"):
    """创建 operator 用户并登录。"""
    role = db.execute(
        __import__("sqlalchemy").select(Role).where(Role.name == "operator")
    ).scalar_one_or_none()
    if role is None:
        role = Role(name="operator", display_name="操作员", is_system=True)
        db.add(role); db.flush()
    user = User(username=username, password_hash=hash_password("pass123"),
                display_name="操作员", role_id=role.id)
    db.add(user); db.commit()
    db.refresh(user)
    client.post("/login", data={"username": username, "password": "pass123"})
    return user


def test_skip_form_requires_login(db_session):
    """未登录 -> 401。"""
    client = TestClient(app)
    resp = client.get("/production/station/skip-form",
                      params={"work_station_id": 1, "scan": "X"})
    assert resp.status_code == 401


def test_skip_form_rejects_operator(db_session):
    """operator 角色无权跳站 -> 错误片段。"""
    db = SessionLocal()
    try:
        client = TestClient(app)
        _login_operator(client, db, username="skipop_form")
    finally:
        db.close()
    resp = client.get("/production/station/skip-form",
                      params={"work_station_id": 1, "scan": "X"})
    assert resp.status_code == 200
    assert "仅主管" in resp.text or "无权" in resp.text


def test_skip_post_requires_login(db_session):
    """未登录 POST skip -> 401。"""
    client = TestClient(app)
    resp = client.post("/production/station/skip", data={
        "work_station_id": 1, "scan": "X", "reason": "测试"})
    assert resp.status_code == 401


def test_skip_post_rejects_operator(db_session):
    """operator 角色无权跳站 -> 错误片段。"""
    db = SessionLocal()
    try:
        client = TestClient(app)
        _login_operator(client, db, username="skipop_post")
    finally:
        db.close()
    resp = client.post("/production/station/skip", data={
        "work_station_id": 1, "scan": "X", "reason": "测试"})
    assert resp.status_code == 200
    assert "仅主管" in resp.text or "无权" in resp.text
