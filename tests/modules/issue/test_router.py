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
    assert r.headers["location"] == "/login?next=%2Fissues"


def test_issue_list_visible_to_admin(privileged_client):
    r = privileged_client.get("/issues")
    assert r.status_code == 200
    assert "Issue 看板" in r.text


def test_issue_detail_404(privileged_client):
    r = privileged_client.get("/issues/99999")
    assert r.status_code == 404


def test_issue_detail_renders_referenced_objects(
        privileged_client, db_session, admin_user):
    from lightmes.modules.issue.models import Issue, IssueType
    it = IssueType(code="T_detail", name="T", severity="minor")
    db_session.add(it); db_session.flush()
    issue = Issue(issue_type_id=it.id, title="detail", severity="minor",
                  reported_by_id=admin_user.id)
    db_session.add(issue); db_session.commit()
    r = privileged_client.get(f"/issues/{issue.id}")
    assert r.status_code == 200
    assert "detail" in r.text


def test_issue_close_rejected_with_unverified_capa(
        privileged_client, db_session, admin_user):
    """close 时 CAPA 未全 verified 会带回错误提示重定向。"""
    from lightmes.modules.issue.models import Issue, IssueAction, IssueType
    it = IssueType(code="T_close", name="T", severity="minor")
    db_session.add(it); db_session.flush()
    issue = Issue(issue_type_id=it.id, title="t", severity="minor",
                  status="resolved", reported_by_id=admin_user.id)
    db_session.add(issue); db_session.flush()
    db_session.add(IssueAction(issue_id=issue.id, type="corrective",
                               title="a", status="open"))
    db_session.commit()
    r = privileged_client.post(
        f"/issues/{issue.id}/close", follow_redirects=False)
    assert r.status_code == 303
    assert "error=" in r.headers["location"]


def test_add_capa_creates_action(privileged_client, db_session, admin_user):
    from lightmes.modules.issue.models import Issue, IssueType
    it = IssueType(code="T", name="T", severity="minor")
    db_session.add(it); db_session.flush()
    issue = Issue(issue_type_id=it.id, title="t", severity="minor",
                  reported_by_id=admin_user.id)
    db_session.add(issue); db_session.commit()
    r = privileged_client.post(f"/issues/{issue.id}/actions",
                               data={"type": "corrective", "title": "act"},
                               follow_redirects=False)
    assert r.status_code == 303
    from lightmes.modules.issue.models import IssueAction
    actions = db_session.query(IssueAction).all()
    assert len(actions) == 1
    assert actions[0].title == "act"


def test_capa_lifecycle_via_http(privileged_client, db_session, admin_user):
    from lightmes.modules.issue.models import Issue, IssueAction, IssueType
    it = IssueType(code="T2", name="T2", severity="minor")
    db_session.add(it); db_session.flush()
    issue = Issue(issue_type_id=it.id, title="t", severity="minor",
                  reported_by_id=admin_user.id)
    db_session.add(issue); db_session.commit()
    privileged_client.post(f"/issues/{issue.id}/actions",
                           data={"type": "corrective", "title": "a"},
                           follow_redirects=False)
    a = db_session.query(IssueAction).first()
    assert privileged_client.post(
        f"/issues/actions/{a.id}/start", follow_redirects=False).status_code == 303
    assert privileged_client.post(
        f"/issues/actions/{a.id}/complete", follow_redirects=False).status_code == 303
    assert privileged_client.post(
        f"/issues/actions/{a.id}/verify", follow_redirects=False).status_code == 303
    db_session.refresh(a)
    assert a.status == "verified"


def test_types_page_admin_only(privileged_client):
    assert privileged_client.get("/issues/types").status_code == 200
    # 未登录：清除 cookie 后再请求
    privileged_client.cookies.clear()
    r = privileged_client.get("/issues/types", follow_redirects=False)
    assert r.status_code in (302, 401, 403)
