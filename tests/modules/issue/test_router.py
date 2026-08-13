import pytest
from fastapi.testclient import TestClient

from lightmes.database import get_db
from lightmes.main import app
from lightmes.modules.auth.schemas import UserCreate
from lightmes.modules.auth.service import AuthService


@pytest.fixture()
def client(db_session):
    app.dependency_overrides[get_db] = lambda: db_session
    yield TestClient(app)
    app.dependency_overrides.clear()


@pytest.fixture
def admin_user(db_session):
    """创建一个 admin 用户（role_obj.name == 'admin'），返回 User。"""
    auth = AuthService(db_session)
    admin_role = auth.role_repo.get_by_name("admin")
    if admin_role is None:
        auth.initialize_default_roles()
        admin_role = auth.role_repo.get_by_name("admin")
    user = auth.create_user(UserCreate(
        username="_issue_admin", password="pw12345",
        display_name="Issue Admin", role_id=admin_role.id,
    ))
    db_session.flush()
    return user


@pytest.fixture
def privileged_client(client, db_session, admin_user):
    """登录后的 admin TestClient。"""
    r = client.post("/login", data={"username": "_issue_admin", "password": "pw12345"})
    assert r.status_code == 204, f"login failed: {r.status_code} {r.text}"
    return client


def test_issue_list_requires_login(client):
    r = client.get("/issues", follow_redirects=False)
    assert r.status_code == 302
    assert r.headers["location"] == "/login"


def test_issue_list_visible_to_admin(privileged_client):
    r = privileged_client.get("/issues")
    assert r.status_code == 200
    assert "Issue 看板" in r.text


def test_issue_detail_404(privileged_client):
    r = privileged_client.get("/issues/99999")
    assert r.status_code == 404


def test_issue_close_rejected_with_unverified_capa(
        privileged_client, db_session, admin_user):
    """close 时 CAPA 未全 verified 返回 422。"""
    from lightmes.modules.issue.models import Issue, IssueAction, IssueType
    it = IssueType(code="T_close", name="T", severity="minor")
    db_session.add(it); db_session.flush()
    issue = Issue(issue_type_id=it.id, title="t", severity="minor",
                  status="resolved", reported_by_id=admin_user.id)
    db_session.add(issue); db_session.flush()
    db_session.add(IssueAction(issue_id=issue.id, type="corrective",
                               title="a", status="open"))
    db_session.commit()
    r = privileged_client.post(f"/issues/{issue.id}/close")
    assert r.status_code == 422
