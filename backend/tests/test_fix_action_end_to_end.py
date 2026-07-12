"""Day 6 完整端到端测试：Orchestrator→FixPlanner→Store→Action API

使用真实 Orchestrator/FixPlannerAgent/FixOptionStore/ConfirmationStore/ActionService
以及真实 SafetyGuard/ToolRegistry。仅替换 AgentHarness MCP 边界和 Audit 持久化边界。
"""

import asyncio
from dataclasses import dataclass
from typing import Any

import pytest
from fastapi.testclient import TestClient


@dataclass
class ProdDeps:
    store: Any
    conf: Any
    harness: Any
    safety_guard: Any
    audit: Any
    orchestrator: Any
    action_service: Any
    client: Any


class FakeMCPClient:
    async def call_tool(self, tool_name, arguments=None):
        return {"ok": True, "result": {"cpu_percent": 23.5, "memory": {"percent": 90}, "disk": [{"mountpoint": "/", "percent": 60}]}, "error": None}


class FakeAudit:
    def __init__(self): self.calls = []
    async def save_context(self, ctx, event_type="chat"): self.calls.append(event_type)


class FakeHarnessWrapper:
    def __init__(self, tool_registry, safety_guard):
        from app.services.agent_harness import AgentHarness
        self.harness = AgentHarness(safety_guard=safety_guard, tool_registry=tool_registry, mcp_client=FakeMCPClient())
        self.calls = []
        self._original = self.harness.run_tool

    async def run_tool(self, ctx, tool_name, params):
        self.calls.append((tool_name, params))
        return await self._original(ctx, tool_name, params)


@pytest.fixture
def deps():
    from app.services.fix_option_store import FixOptionStore
    from app.services.confirmation_store import ConfirmationStore
    from app.services.tool_registry import ToolRegistry
    from app.services.safety_guard import SafetyGuard
    from app.services.fix_planner_agent import FixPlannerAgent
    from app.services.action_service import ActionService
    from app.services.orchestrator import Orchestrator

    store = FixOptionStore()
    conf = ConfirmationStore()
    tr = ToolRegistry()
    sg = SafetyGuard()
    fp = FixPlannerAgent(tool_registry=tr)
    hw = FakeHarnessWrapper(tr, sg)
    audit = FakeAudit()

    orch = Orchestrator(safety_guard=sg, tool_registry=tr, mcp_client=None,
                        agent_harness=hw, fix_planner=fp, fix_option_store=store,
                        audit_service=None, intent_agent=None, diagnose_agent=None, reporter_agent=None)

    svc = ActionService(fix_option_store=store, safety_guard=sg, agent_harness=hw,
                        audit_service=audit, confirmation_store=conf)

    from app.main import app
    from app.dependencies import action_service as _gsvc
    _orig = (_gsvc._store, _gsvc._confirmation, _gsvc._harness, _gsvc._safety_guard, _gsvc._audit)
    _gsvc._store = store; _gsvc._confirmation = conf; _gsvc._harness = hw; _gsvc._safety_guard = sg; _gsvc._audit = audit
    client = TestClient(app)

    yield ProdDeps(**dict(zip(["store","conf","harness","safety_guard","audit","orchestrator","action_service","client"],
                               [store, conf, hw, sg, audit, orch, svc, client])))
    _gsvc._store, _gsvc._confirmation, _gsvc._harness, _gsvc._safety_guard, _gsvc._audit = _orig


def _run_orch(orch, session_id, user_input):
    async def _c():
        items = []
        async for item in orch.handle_chat(session_id, user_input): items.append(item)
        return items
    return asyncio.run(_c())


# ═══════════════════════════════════════════════════════════════════════
# 端到端测试
# ═══════════════════════════════════════════════════════════════════════

_O = {
    "low": ("fix_111111a1", "sys_info", {"metric": "memory"}, False),
    "medium": ("fix_222222a2", "sys_info", {"metric": "cpu"}, True),
    "high": ("fix_333333a3", "sys_info", {"metric": "cpu"}, False),
}


def _opt(k):
    oid, tool, params, rc = _O[k]
    from app.schemas.action import FixOption
    risk = k if k in ("low", "high") else "medium"
    return FixOption(option_id=oid, title="t", description="d", risk_level=risk, tool=tool, params=params, requires_confirm=rc)


class TestOrchToAction:
    def test_generated_option_in_store(self, deps):
        from app.services.fix_planner_agent import FixPlannerAgent
        opt = _opt("low")

        class FP(FixPlannerAgent):
            def plan(self, *a, **kw): return [opt]

        deps.orchestrator.fix_planner = FP()
        items = _run_orch(deps.orchestrator, "s-ot1", "查看内存")
        fx = [m for m in items if m["type"] == "fix_options"]
        assert len(fx) == 1
        oid = fx[0]["options"][0]["option_id"]
        stored = deps.store.get_option("s-ot1", oid)
        assert stored is not None
        assert stored.session_id == "s-ot1"

    def test_orch_to_api_roundtrip(self, deps):
        from app.services.fix_planner_agent import FixPlannerAgent
        opt = _opt("low")

        class FP(FixPlannerAgent):
            def plan(self, *a, **kw): return [opt]

        deps.orchestrator.fix_planner = FP()
        items = _run_orch(deps.orchestrator, "s-rt1", "查看内存")
        fx = [m for m in items if m["type"] == "fix_options"]
        oid = fx[0]["options"][0]["option_id"]
        r = deps.client.post("/api/actions/execute", json={"session_id": "s-rt1", "option_id": oid})
        assert r.status_code == 200
        assert r.json()["status"] == "executed"


class TestLowEndToEnd:
    def test_low_execute_real_sg(self, deps):
        deps.store.save_options("s-le2", "t1", [_opt("low")])
        r = deps.client.post("/api/actions/execute", json={"session_id": "s-le2", "option_id": _O["low"][0]})
        assert r.status_code == 200
        assert r.json()["status"] == "executed"
        assert deps.store.get_option("s-le2", _O["low"][0]).status == "executed"
        assert len(deps.harness.calls) == 1

    def test_low_duplicate_409(self, deps):
        deps.store.save_options("s-ld2", "t1", [_opt("low")])
        deps.client.post("/api/actions/execute", json={"session_id": "s-ld2", "option_id": _O["low"][0]})
        r = deps.client.post("/api/actions/execute", json={"session_id": "s-ld2", "option_id": _O["low"][0]})
        assert r.status_code == 409

    def test_low_no_params_leaked(self, deps):
        deps.store.save_options("s-ln2", "t1", [_opt("low")])
        r = deps.client.post("/api/actions/execute", json={"session_id": "s-ln2", "option_id": _O["low"][0]})
        assert "params" not in r.json()
        assert "tool" not in r.json()


class TestMediumEndToEnd:
    def test_medium_create_confirm(self, deps):
        deps.store.save_options("s-me2", "t1", [_opt("medium")])
        r = deps.client.post("/api/actions/execute", json={"session_id": "s-me2", "option_id": _O["medium"][0]})
        assert r.status_code == 200
        d = r.json()
        assert d["status"] == "confirm_required"
        assert d.get("confirm_id", "").startswith("cfm_")

    def test_medium_approve_real_sg(self, deps):
        deps.store.save_options("s-ma2", "t1", [_opt("medium")])
        r = deps.client.post("/api/actions/execute", json={"session_id": "s-ma2", "option_id": _O["medium"][0]})
        cid = r.json()["confirm_id"]
        r2 = deps.client.post("/api/actions/confirm", json={"session_id": "s-ma2", "confirm_id": cid, "decision": "approve"})
        assert r2.status_code == 200
        assert r2.json()["status"] == "executed"
        assert deps.store.get_option("s-ma2", _O["medium"][0]).status == "executed"

    def test_medium_reject(self, deps):
        deps.store.save_options("s-mr2", "t1", [_opt("medium")])
        r = deps.client.post("/api/actions/execute", json={"session_id": "s-mr2", "option_id": _O["medium"][0]})
        cid = r.json()["confirm_id"]
        r2 = deps.client.post("/api/actions/confirm", json={"session_id": "s-mr2", "confirm_id": cid, "decision": "reject"})
        assert r2.status_code == 200
        assert r2.json()["status"] == "rejected"
        assert deps.store.get_option("s-mr2", _O["medium"][0]).status == "blocked"


class TestHighEndToEnd:
    def test_high_blocked(self, deps):
        deps.store.save_options("s-he2", "t1", [_opt("high")])
        r = deps.client.post("/api/actions/execute", json={"session_id": "s-he2", "option_id": _O["high"][0]})
        assert r.status_code == 403

    def test_high_wrong_session_404(self, deps):
        deps.store.save_options("s-hw2", "t1", [_opt("high")])
        r = deps.client.post("/api/actions/execute", json={"session_id": "s-other", "option_id": _O["high"][0]})
        assert r.status_code == 404


class TestHarnessFailure:
    def test_low_harness_failure_502(self, deps):
        deps.store.save_options("s-hf2", "t1", [_opt("low")])
        orig = deps.harness.run_tool

        async def fail(ctx, tool, params):
            deps.harness.calls.append((tool, params))
            return {"ok": False, "error": "mock upstream failure"}

        deps.harness.run_tool = fail
        r = deps.client.post("/api/actions/execute", json={"session_id": "s-hf2", "option_id": _O["low"][0]})
        assert r.status_code == 502
        assert deps.store.get_option("s-hf2", _O["low"][0]).status == "failed"
        deps.harness.run_tool = orig
