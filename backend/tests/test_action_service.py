"""ActionService 预检测试"""
import pytest

from app.schemas.action import FixOption
from app.services.fix_option_store import FixOptionStore


def _opt(option_id="fix_a1b2c3d4", risk_level="low"):
    return FixOption(
        option_id=option_id, title="t", description="d",
        risk_level=risk_level, tool="sys_info",
        params={"metric": "cpu"}, requires_confirm=(risk_level == "medium"),
    )


@pytest.fixture
def service():
    from app.services.action_service import ActionService
    store = FixOptionStore()
    return ActionService(fix_option_store=store), store


class TestPrecheck:
    def test_pending_low_returns_ready(self, service):
        svc, store = service
        store.save_options("s1", "t1", [_opt("fix_11111111", "low")])
        r = svc.precheck("s1", "fix_11111111")
        assert r.result == "ready"
        assert r.requires_confirm is False

    def test_pending_medium_returns_confirm_required(self, service):
        svc, store = service
        store.save_options("s1", "t1", [_opt("fix_22222222", "medium")])
        r = svc.precheck("s1", "fix_22222222")
        assert r.result == "confirm_required"
        assert r.requires_confirm is True

    def test_high_returns_blocked(self, service):
        svc, store = service
        store.save_options("s1", "t1", [_opt("fix_33333333", "high")])
        r = svc.precheck("s1", "fix_33333333")
        assert r.result == "blocked"

    def test_unknown_option_returns_not_found(self, service):
        svc, _ = service
        r = svc.precheck("s1", "fix_deadbeef")
        assert r.result == "not_found"

    def test_wrong_session_returns_not_found(self, service):
        svc, store = service
        store.save_options("s1", "t1", [_opt("fix_44444444")])
        r = svc.precheck("s2", "fix_44444444")
        assert r.result == "not_found"

    def test_expired_returns_expired(self, service):
        from datetime import datetime, timedelta, timezone
        svc, store = service
        t0 = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        store._clock = lambda: t0
        store.save_options("s1", "t1", [_opt("fix_55555555")])
        store._clock = lambda: t0 + timedelta(seconds=9999)
        r = svc.precheck("s1", "fix_55555555")
        assert r.result == "expired"

    def test_executing_returns_conflict(self, service):
        svc, store = service
        store.save_options("s1", "t1", [_opt("fix_66666666")])
        store.claim_for_execution("s1", "fix_66666666")
        r = svc.precheck("s1", "fix_66666666")
        assert r.result == "conflict"

    def test_executed_returns_conflict(self, service):
        svc, store = service
        store.save_options("s1", "t1", [_opt("fix_77777777")])
        store.claim_for_execution("s1", "fix_77777777")
        store.mark_executed("s1", "fix_77777777")
        r = svc.precheck("s1", "fix_77777777")
        assert r.result == "conflict"

    def test_blocked_returns_blocked(self, service):
        svc, store = service
        store.save_options("s1", "t1", [_opt("fix_88888888")])
        store.mark_blocked("s1", "fix_88888888")
        r = svc.precheck("s1", "fix_88888888")
        assert r.result == "blocked"

    def test_failed_returns_conflict(self, service):
        svc, store = service
        store.save_options("s1", "t1", [_opt("fix_99999999")])
        store.claim_for_execution("s1", "fix_99999999")
        store.mark_failed("s1", "fix_99999999")
        r = svc.precheck("s1", "fix_99999999")
        assert r.result == "conflict"

    def test_confirm_required_status_returns_confirm_required(self, service):
        svc, store = service
        store.save_options("s1", "t1", [_opt("fix_aaaaaaaa")])
        store.mark_confirm_required("s1", "fix_aaaaaaaa")
        r = svc.precheck("s1", "fix_aaaaaaaa")
        assert r.result == "confirm_required"

    def test_does_not_claim(self, service):
        svc, store = service
        store.save_options("s1", "t1", [_opt("fix_bbbbbbbb")])
        svc.precheck("s1", "fix_bbbbbbbb")
        stored = store.get_option("s1", "fix_bbbbbbbb")
        assert stored.status == "pending"

    def test_does_not_change_store_state(self, service):
        svc, store = service
        store.save_options("s1", "t1", [_opt("fix_cccccccc")])
        svc.precheck("s1", "fix_cccccccc")
        svc.precheck("s1", "fix_cccccccc")
        svc.precheck("s1", "fix_cccccccc")
        stored = store.get_option("s1", "fix_cccccccc")
        assert stored.status == "pending"

    def test_trace_id_from_store_not_request(self, service):
        svc, store = service
        store.save_options("s1", "trace-abc", [_opt("fix_dddddddd")])
        r = svc.precheck("s1", "fix_dddddddd")
        assert r.trace_id == "trace-abc"

    def test_no_params_in_result(self, service):
        svc, store = service
        store.save_options("s1", "t1", [_opt("fix_eeeeeeee")])
        r = svc.precheck("s1", "fix_eeeeeeee")
        assert not hasattr(r, "params")
