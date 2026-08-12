import pytest
from fastapi.testclient import TestClient
from lightmes.main import app
from lightmes.database import get_db


@pytest.fixture()
def client(db_session):
    app.dependency_overrides[get_db] = lambda: db_session
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_openapi_json_accessible(client, db_session):
    resp = client.get("/openapi.json")
    assert resp.status_code == 200
    spec = resp.json()
    assert spec["info"]["title"] == "LightMES"
    assert "version" in spec["info"]


def test_openapi_has_tags(client, db_session):
    resp = client.get("/openapi.json")
    spec = resp.json()
    tag_names = {t["name"] for t in spec.get("tags", [])}
    assert "Work Orders" in tag_names
    assert "Serial Units" in tag_names
    assert "Defects" in tag_names
    assert "API Keys" in tag_names


def test_openapi_has_v1_paths(client, db_session):
    resp = client.get("/openapi.json")
    spec = resp.json()
    paths = spec.get("paths", {})
    assert "/api/v1/work-orders" in paths
    assert "/api/v1/serial-units" in paths
    assert "/api/v1/defects" in paths
    assert "/api/v1/api-keys" in paths
