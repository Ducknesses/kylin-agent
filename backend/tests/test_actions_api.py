"""Action API 集成测试"""
import pytest
from fastapi.testclient import TestClient

from app.schemas.action import FixOption
from app.services.fix_option_store import FixOptionStore


@pytest.fixture
def client():
    from app.main import app
    return TestClient(app)


@pytest.fixture(autouse=True)
def _clean_store():
    """每个测试使用独立 Store 实例"""
    from app.dependencies import action_service
    store = FixOptionStore()
    from app.services.action_service import ActionService
    action_service._store = store  # 注入独立 Store
    return store


def _save(store, sid, tid, *opts):
    store.save_options(sid, tid, list(opts))


def _opt(option_id, risk_level="low"):
    return FixOption(
        option_id=option_id, title="t", description="d",
        risk_level=risk_level, tool="sys_info",
        params={"metric": "cpu"}, requires_confirm=(risk_level == "medium"),
    )


class TestActionAPI:
    def test_low_returns_200_ready(self, client, _clean_store):
        _save(_clean_store, "s1", "t1", _opt("fix_11111111", "low"))
        r = client.post("/api/actions/execute", json={
            "session_id": "s1", "option_id": "fix_11111111",
        })
        assert r.status_code == 200
        data = r.json()
        assert data["status"] == "ready"
        assert data["requires_confirm"] is False
        assert "params" not in data

    def test_medium_returns_200_confirm_required(self, client, _clean_store):
        _save(_clean_store, "s1", "t1", _opt("fix_22222222", "medium"))
        r = client.post("/api/actions/execute", json={
            "session_id": "s1", "option_id": "fix_22222222",
        })
        assert r.status_code == 200
        assert r.json()["status"] == "confirm_required"

    def test_unknown_returns_404(self, client):
        r = client.post("/api/actions/execute", json={
            "session_id": "s1", "option_id": "fix_deadbeef",
        })
        assert r.status_code == 404

    def test_wrong_session_returns_404(self, client, _clean_store):
        _save(_clean_store, "s1", "t1", _opt("fix_33333333"))
        r = client.post("/api/actions/execute", json={
            "session_id": "s2", "option_id": "fix_33333333",
        })
        assert r.status_code == 404

    def test_blocked_returns_403(self, client, _clean_store):
        _save(_clean_store, "s1", "t1", _opt("fix_44444444", "high"))
        r = client.post("/api/actions/execute", json={
            "session_id": "s1", "option_id": "fix_44444444",
        })
        assert r.status_code == 403

    def test_conflict_returns_409(self, client, _clean_store):
        _save(_clean_store, "s1", "t1", _opt("fix_55555555"))
        _clean_store.claim_for_execution("s1", "fix_55555555")
        r = client.post("/api/actions/execute", json={
            "session_id": "s1", "option_id": "fix_55555555",
        })
        assert r.status_code == 409

    def test_expired_returns_410(self, client, _clean_store):
        from datetime import datetime, timedelta, timezone
        t0 = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        _clean_store._clock = lambda: t0
        _save(_clean_store, "s1", "t1", _opt("fix_66666666"))
        _clean_store._clock = lambda: t0 + timedelta(seconds=9999)
        r = client.post("/api/actions/execute", json={
            "session_id": "s1", "option_id": "fix_66666666",
        })
        assert r.status_code == 410

    def test_invalid_body_returns_422(self, client):
        r = client.post("/api/actions/execute", json={
            "session_id": "", "option_id": "fix_a1b2c3d4",
        })
        assert r.status_code == 422

    def test_extra_fields_returns_422(self, client):
        r = client.post("/api/actions/execute", json={
            "session_id": "s1", "option_id": "fix_a1b2c3d4",
            "tool": "sys_info",
        })
        assert r.status_code == 422

    def test_no_code_data_wrapper(self, client, _clean_store):
        _save(_clean_store, "s1", "t1", _opt("fix_77777777", "low"))
        r = client.post("/api/actions/execute", json={
            "session_id": "s1", "option_id": "fix_77777777",
        })
        data = r.json()
        assert "code" not in data
        assert "data" not in data
        assert "status" in data

    def test_no_store_state_change(self, client, _clean_store):
        _save(_clean_store, "s1", "t1", _opt("fix_88888888"))
        client.post("/api/actions/execute", json={
            "session_id": "s1", "option_id": "fix_88888888",
        })
        stored = _clean_store.get_option("s1", "fix_88888888")
        assert stored.status == "pending"
