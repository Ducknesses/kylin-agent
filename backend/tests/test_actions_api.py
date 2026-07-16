"""Action API 集成测试"""
import pytest
from fastapi.testclient import TestClient

from app.schemas.action import FixOption, RiskLevel
from app.services.fix_option_store import FixOptionStore


@pytest.fixture
def client():
    from app.main import app
    return TestClient(app)


@pytest.fixture(autouse=True)
def _clean_store():
    from app.dependencies import action_service
    store = FixOptionStore()
    from app.services.action_service import ActionService

    class FakeHarness:
        def __init__(self): self.calls = []
        async def run_tool(self, ctx, tool, params):
            self.calls.append((tool, params))
            return {"ok": True, "result": {"ok": True}}

    class FakeSG:
        def analyze_tool_call(self, tool, params, role="viewer"):
            return {"allowed": True, "risk_level": "low", "reason": "", "requires_confirm": False}

    class FakeAudit:
        def __init__(self): self.calls = []
        async def save_context(self, ctx, event_type="chat"):
            self.calls.append((ctx.trace_id, event_type))

    harness = FakeHarness()
    action_service._store = store
    action_service._harness = harness
    action_service._safety_guard = FakeSG()
    action_service._audit = FakeAudit()
    return store, harness


def _opt(option_id, risk_level: RiskLevel = "low"):
    return FixOption(
        option_id=option_id, title="t", description="d",
        risk_level=risk_level, tool="sys_info",
        params={"metric": "cpu"}, requires_confirm=(risk_level == "medium"),
    )


class TestActionAPIExecute:
    def test_low_executes_returns_200_executed(self, client, _clean_store):
        store, _ = _clean_store
        store.save_options("s1", "t1", [_opt("fix_11111111", "low")])
        r = client.post("/api/actions/execute", json={
            "session_id": "s1", "option_id": "fix_11111111",
        })
        assert r.status_code == 200
        assert r.json()["status"] == "executed"

    def test_medium_returns_200_confirm_required(self, client, _clean_store):
        store, _ = _clean_store
        store.save_options("s1", "t1", [_opt("fix_22222222", "medium")])
        r = client.post("/api/actions/execute", json={
            "session_id": "s1", "option_id": "fix_22222222",
        })
        assert r.status_code == 200
        assert r.json()["status"] == "confirm_required"

    def test_high_returns_403(self, client, _clean_store):
        store, _ = _clean_store
        store.save_options("s1", "t1", [_opt("fix_33333333", "high")])
        r = client.post("/api/actions/execute", json={
            "session_id": "s1", "option_id": "fix_33333333",
        })
        assert r.status_code == 403

    def test_unknown_returns_404(self, client):
        r = client.post("/api/actions/execute", json={
            "session_id": "s1", "option_id": "fix_deadbeef",
        })
        assert r.status_code == 404

    def test_duplicate_returns_409(self, client, _clean_store):
        store, _ = _clean_store
        store.save_options("s1", "t1", [_opt("fix_44444444", "low")])
        client.post("/api/actions/execute", json={"session_id": "s1", "option_id": "fix_44444444"})
        r = client.post("/api/actions/execute", json={"session_id": "s1", "option_id": "fix_44444444"})
        assert r.status_code == 409

    def test_no_code_data_wrapper(self, client, _clean_store):
        store, _ = _clean_store
        store.save_options("s1", "t1", [_opt("fix_55555555", "low")])
        r = client.post("/api/actions/execute", json={"session_id": "s1", "option_id": "fix_55555555"})
        data = r.json()
        assert "code" not in data
        assert "data" not in data

    def test_store_state_updated(self, client, _clean_store):
        store, _ = _clean_store
        store.save_options("s1", "t1", [_opt("fix_66666666", "low")])
        client.post("/api/actions/execute", json={"session_id": "s1", "option_id": "fix_66666666"})
        assert store.get_option("s1", "fix_66666666").status == "executed"


class TestConfirmAPI:
    def test_expired_approve_returns_410(self, client, _clean_store):
        from datetime import datetime, timedelta, timezone
        from app.dependencies import action_service
        store, _ = _clean_store
        store.save_options("s1", "t1", [_opt("fix_32f32f89", "medium")])
        action_service._store = store
        t0 = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        conf_store = action_service._confirmation
        conf_store._clock = lambda: t0
        conf, _ = conf_store.create_or_get("s1", "fix_32f32f89", "t1")
        cid = conf.confirm_id
        conf_store._clock = lambda: t0 + timedelta(seconds=600)
        r = client.post("/api/actions/confirm", json={"session_id": "s1", "confirm_id": cid, "decision": "approve"})
        assert r.status_code == 410

    def test_expired_reject_returns_410(self, client, _clean_store):
        from datetime import datetime, timedelta, timezone
        from app.dependencies import action_service
        store, _ = _clean_store
        store.save_options("s1", "t1", [_opt("fix_89c9e74f", "medium")])
        action_service._store = store
        t0 = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        conf_store = action_service._confirmation
        conf_store._clock = lambda: t0
        conf, _ = conf_store.create_or_get("s1", "fix_89c9e74f", "t1")
        cid = conf.confirm_id
        conf_store._clock = lambda: t0 + timedelta(seconds=600)
        r = client.post("/api/actions/confirm", json={"session_id": "s1", "confirm_id": cid, "decision": "reject"})
        assert r.status_code == 410
