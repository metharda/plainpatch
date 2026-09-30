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
def temp_values_dir(tmp_path):
    import app as app_module

    original = app_module.values_dir
    app_module.values_dir = tmp_path

    test_values = {"name": "test", "memory": 1024}
    (tmp_path / "test.value.json").write_text(json.dumps(test_values))
    (tmp_path / "invalid.value.json").write_text("not valid json")

    yield tmp_path

    app_module.values_dir = original


class TestHealth:
    def test_health_returns_ok(self, client):
        resp = client.get("/health")
        assert resp.status_code == 200
        assert resp.json() == {"status": "healthy"}


class TestGetValues:
    def test_valid_app_returns_values(self, client, temp_values_dir):
        resp = client.get("/test")
        assert resp.status_code == 200
        assert resp.json()["name"] == "test"

    def test_nonexistent_app_returns_404(self, client, temp_values_dir):
        resp = client.get("/unknown")
        assert resp.status_code == 404

    def test_invalid_app_name_returns_404(self, client, temp_values_dir):
        resp = client.get("/test%00hack")
        assert resp.status_code == 404

    def test_path_traversal_blocked(self, client, temp_values_dir):
        resp = client.get("/../etc/passwd")
        assert resp.status_code in [404, 400, 307]

    def test_empty_app_name_handled(self, client):
        resp = client.get("/")
        assert resp.status_code in [404, 405]

    def test_corrupted_json_returns_500(self, client, temp_values_dir):
        resp = client.get("/invalid")
        assert resp.status_code == 500


class TestHistory:
    def put(self, client, memory, message="change"):
        return client.put("/test", json={"values": {"name": "test", "memory": memory}, "author": "me", "message": message})

    def test_put_writes_values_and_records_patch(self, client, temp_values_dir):
        resp = self.put(client, 2048)
        assert resp.status_code == 200
        assert resp.json()["id"] == 1
        assert resp.json()["patch"] == [{"op": "replace", "path": "/memory", "value": 2048}]
        assert client.get("/test").json()["memory"] == 2048

    def test_put_without_changes_records_nothing(self, client, temp_values_dir):
        assert self.put(client, 1024).json()["id"] is None
        assert client.get("/test/history").json() == []

    def test_put_unknown_app_returns_404(self, client, temp_values_dir):
        assert client.put("/unknown", json={"values": {}}).status_code == 404

    def test_history_is_newest_first_without_snapshots(self, client, temp_values_dir):
        self.put(client, 2048, "first")
        self.put(client, 4096, "second")
        history = client.get("/test/history").json()
        assert [e["message"] for e in history] == ["second", "first"]
        assert "before" not in history[0]

    def test_undo_walks_back_through_edits(self, client, temp_values_dir):
        self.put(client, 2048)
        self.put(client, 4096)

        assert client.post("/test/undo").json()["reverts"] == 2
        assert client.get("/test").json()["memory"] == 2048
        assert client.post("/test/undo").json()["reverts"] == 1
        assert client.get("/test").json()["memory"] == 1024
        assert client.post("/test/undo").status_code == 409

    def test_undo_after_new_edit_targets_new_edit(self, client, temp_values_dir):
        self.put(client, 2048)
        client.post("/test/undo")
        self.put(client, 512)
        assert client.post("/test/undo").json()["reverts"] == 3
        assert client.get("/test").json()["memory"] == 1024

    def test_no_temp_file_left_behind(self, client, temp_values_dir):
        self.put(client, 2048)
        assert not list(temp_values_dir.glob("*.tmp"))

    def test_corrupted_history_line_is_skipped(self, client, temp_values_dir):
        self.put(client, 2048)
        history = temp_values_dir / ".history" / "test.jsonl"
        history.write_text(history.read_text() + '{"id": 2, "timest')  # crash mid-append, no newline

        resp = self.put(client, 4096)
        assert resp.status_code == 200
        assert resp.json()["id"] == 2
        assert [e["id"] for e in client.get("/test/history").json()] == [2, 1]

    def test_undo_of_already_restored_change_still_advances(self, client, temp_values_dir):
        self.put(client, 2048)
        self.put(client, 4096)
        (temp_values_dir / "test.value.json").write_text('{"name": "test", "memory": 2048}')  # hand-reverted

        assert client.post("/test/undo").json()["reverts"] == 2
        assert client.post("/test/undo").json()["reverts"] == 1
        assert client.get("/test").json()["memory"] == 1024

    def test_failed_write_leaves_no_history_entry(self, temp_values_dir, monkeypatch):
        import app as app_module

        def boom(path, data):
            raise OSError("disk full")

        monkeypatch.setattr(app_module, "write_atomic", boom)
        client = TestClient(app, raise_server_exceptions=False)
        assert self.put(client, 2048).status_code == 500
        assert client.get("/test/history").json() == []
        assert client.get("/test").json()["memory"] == 1024

    @pytest.mark.parametrize("limit", [-1, 0, 501])
    def test_history_limit_bounds(self, client, temp_values_dir, limit):
        assert client.get(f"/test/history?limit={limit}").status_code == 422

    def test_undo_unknown_app_returns_404(self, client, temp_values_dir):
        assert client.post("/unknown/undo").status_code == 404
