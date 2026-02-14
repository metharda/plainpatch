import json
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).parent.parent))

from app import app


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def temp_schema_dir(tmp_path):
    global schema_dir
    import app as app_module

    original = app_module.schema_dir
    app_module.schema_dir = tmp_path

    test_schema = {"type": "object", "properties": {"name": {"type": "string"}}}
    (tmp_path / "test.schema.json").write_text(json.dumps(test_schema))
    (tmp_path / "invalid.schema.json").write_text("not valid json")

    yield tmp_path

    app_module.schema_dir = original


class TestHealth:
    def test_health_returns_ok(self, client):
        resp = client.get("/health")
        assert resp.status_code == 200
        assert resp.json() == {"status": "healthy"}


class TestGetSchema:
    def test_valid_app_returns_schema(self, client, temp_schema_dir):
        resp = client.get("/test")
        assert resp.status_code == 200
        assert resp.json()["type"] == "object"

    def test_nonexistent_app_returns_404(self, client, temp_schema_dir):
        resp = client.get("/unknown")
        assert resp.status_code == 404

    def test_invalid_app_name_returns_404(self, client, temp_schema_dir):
        resp = client.get("/test%00hack")
        assert resp.status_code == 404

    def test_path_traversal_blocked(self, client, temp_schema_dir):
        resp = client.get("/../etc/passwd")
        assert resp.status_code in [404, 400, 307]

    def test_empty_app_name_handled(self, client):
        resp = client.get("/")
        assert resp.status_code in [404, 405]

    def test_corrupted_json_returns_500(self, client, temp_schema_dir):
        resp = client.get("/invalid")
        assert resp.status_code == 500
