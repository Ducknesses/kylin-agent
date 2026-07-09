"""Orchestrator Agent 主流程闭环测试

覆盖：
  1. 普通指标查询（CPU → sys_info → done）
  2. 根因分析（多工具调用）
  3. 高危命令拒绝（risk_alert，不调 AgentHarness）
  4. 工具失败（ok=False → 不崩溃）
  5. 未知意图（无计划 → chunk + done）
  6. 帧格式（type/trace_id/done 最后）
  7. 边界检查（不导入 MCPClient，不调用 call_tool）
"""

import asyncio

import pytest

from app.services.agent_context import AgentContext


# ═══════════════════════════════════════════════════════════════════
# Fake 对象
# ═══════════════════════════════════════════════════════════════════

class FakeSafetyGuard:
    def analyze_user_input(self, content: str) -> dict:
        if "rm -rf" in content or "shutdown" in content:
            return {"allowed": False, "risk_level": "high", "reason": "高危命令已拦截", "requires_confirm": False}
        if "重启 nginx" in content:
            return {"allowed": True, "risk_level": "medium", "reason": "中风险操作", "requires_confirm": True}
        return {"allowed": True, "risk_level": "low", "reason": "", "requires_confirm": False}


class FakeToolRegistry:
    def exists(self, name): return True
    def validate_params(self, tool, params): return {"valid": True, "errors": []}


class FakeMCPClient:
    async def call_tool(self, tool_name, arguments=None):
        return {"ok": True, "result": {"mock": True}, "error": None}


class FakeAgentHarness:
    """模拟 AgentHarness，记录调用"""
    def __init__(self, fail_tools: list[str] | None = None):
        self.calls = []
        self.fail_tools = fail_tools or []

    async def run_tool(self, ctx, tool_name, params):
        self.calls.append((tool_name, params))
        if tool_name in self.fail_tools:
            return {"ok": False, "tool": tool_name, "params": params, "error": "mock failure", "risk_level": "low", "mcp_error": True}
        return {"ok": True, "tool": tool_name, "params": params, "result": {"mock": True}, "risk_level": "low"}


class FakeAuditService:
    def __init__(self):
        self.calls = []

    async def save_context(self, ctx, event_type="chat"):
        self.calls.append((ctx.trace_id, event_type))


# ═══════════════════════════════════════════════════════════════════
# 辅助
# ═══════════════════════════════════════════════════════════════════

async def _collect(gen):
    """收集 async generator 的所有产出"""
    items = []
    async for item in gen:
        items.append(item)
    return items


# ═══════════════════════════════════════════════════════════════════
# 测试
# ═══════════════════════════════════════════════════════════════════

class TestNormalFlow:
    """普通指标查询流程"""

    def test_cpu_query_full_flow(self):
        from app.services.orchestrator import Orchestrator

        harness = FakeAgentHarness()
        audit = FakeAuditService()
        orch = Orchestrator(
            safety_guard=FakeSafetyGuard(),
            tool_registry=FakeToolRegistry(),
            mcp_client=FakeMCPClient(),
            agent_harness=harness,
            audit_service=audit,
        )

        items = asyncio.run(_collect(orch.handle_chat("s1", "查看 CPU 使用率")))

        types = [m["type"] for m in items]
        assert "status" in types
        assert "tool_call" in types
        assert "chunk" in types
        assert "done" in types
        # done 必须是最后一个
        assert types[-1] == "done"

        # final_response 不为空
        done_msg = items[-1]
        assert done_msg.get("final_response")

        # trace_id 贯穿
        trace_id = items[0]["trace_id"]
        for m in items:
            assert m.get("trace_id") == trace_id

        # AgentHarness 被调用
        assert len(harness.calls) >= 1

        # AuditService 被调用
        assert len(audit.calls) == 1
        assert audit.calls[0][1] == "chat_done"


class TestRootCauseFlow:
    """根因分析流程（多工具调用）"""

    def test_root_cause_multi_tool(self):
        from app.services.orchestrator import Orchestrator

        harness = FakeAgentHarness()
        audit = FakeAuditService()
        orch = Orchestrator(
            safety_guard=FakeSafetyGuard(),
            tool_registry=FakeToolRegistry(),
            mcp_client=FakeMCPClient(),
            agent_harness=harness,
            audit_service=audit,
        )

        items = asyncio.run(_collect(orch.handle_chat("s2", "分析 nginx 访问慢的原因")))

        # 应有多个 tool_call
        tool_calls = [m for m in items if m["type"] == "tool_call"]
        assert len(tool_calls) >= 2  # root_cause_analysis 至少 log_reader + sys_info + service_mgr

        # AgentHarness 确实被多次调用
        assert len(harness.calls) >= 2

        # done
        assert items[-1]["type"] == "done"
        assert items[-1].get("final_response")

        # audit
        assert audit.calls[0][1] == "chat_done"


class TestHighRiskBlocked:
    """高危命令拒绝"""

    def test_rm_rf_blocked(self):
        from app.services.orchestrator import Orchestrator

        harness = FakeAgentHarness()
        audit = FakeAuditService()
        orch = Orchestrator(
            safety_guard=FakeSafetyGuard(),
            tool_registry=FakeToolRegistry(),
            mcp_client=FakeMCPClient(),
            agent_harness=harness,
            audit_service=audit,
        )

        items = asyncio.run(_collect(orch.handle_chat("s3", "rm -rf /")))

        # 应有 risk_alert
        types = [m["type"] for m in items]
        assert "risk_alert" in types

        # 不应调用 AgentHarness
        assert len(harness.calls) == 0

        # done 最后
        assert types[-1] == "done"

        # audit event_type 为 blocked
        assert audit.calls[0][1] == "blocked"


class TestToolFailure:
    """工具失败流程"""

    def test_tool_failure_not_crash(self):
        from app.services.orchestrator import Orchestrator

        harness = FakeAgentHarness(fail_tools=["sys_info"])
        orch = Orchestrator(
            safety_guard=FakeSafetyGuard(),
            tool_registry=FakeToolRegistry(),
            mcp_client=FakeMCPClient(),
            agent_harness=harness,
        )

        items = asyncio.run(_collect(orch.handle_chat("s4", "查看 CPU 使用率")))

        # 不崩溃，done 最后
        assert items[-1]["type"] == "done"
        assert items[-1].get("final_response")

        # tool_call 的 ok=False
        tool_calls = [m for m in items if m["type"] == "tool_call"]
        assert any(not tc.get("ok", True) for tc in tool_calls)


class TestUnknownIntent:
    """未知意图流程"""

    def test_unknown_intent_no_crash(self):
        from app.services.orchestrator import Orchestrator

        audit = FakeAuditService()
        orch = Orchestrator(
            safety_guard=FakeSafetyGuard(),
            tool_registry=FakeToolRegistry(),
            mcp_client=FakeMCPClient(),
            audit_service=audit,
        )

        items = asyncio.run(_collect(orch.handle_chat("s5", "今天天气怎么样")))

        assert items[-1]["type"] == "done"
        # 审计仍然写入
        assert len(audit.calls) == 1


class TestFrameFormat:
    """帧格式测试"""

    def test_all_frames_have_type(self):
        from app.services.orchestrator import Orchestrator

        orch = Orchestrator(
            safety_guard=FakeSafetyGuard(),
            tool_registry=FakeToolRegistry(),
            mcp_client=FakeMCPClient(),
            agent_harness=FakeAgentHarness(),
        )
        items = asyncio.run(_collect(orch.handle_chat("s6", "查看 CPU 使用率")))
        for m in items:
            assert "type" in m

    def test_done_is_last(self):
        from app.services.orchestrator import Orchestrator

        orch = Orchestrator(
            safety_guard=FakeSafetyGuard(),
            tool_registry=FakeToolRegistry(),
            mcp_client=FakeMCPClient(),
            agent_harness=FakeAgentHarness(),
        )
        items = asyncio.run(_collect(orch.handle_chat("s7", "查看 CPU 使用率")))
        assert items[-1]["type"] == "done"

    def test_no_reject_type(self):
        from app.services.orchestrator import Orchestrator

        orch = Orchestrator(
            safety_guard=FakeSafetyGuard(),
            tool_registry=FakeToolRegistry(),
            mcp_client=FakeMCPClient(),
            agent_harness=FakeAgentHarness(),
        )
        items = asyncio.run(_collect(orch.handle_chat("s8", "rm -rf /")))
        for m in items:
            assert m["type"] != "reject"

    def test_no_llm_reasoning_in_output(self):
        from app.services.orchestrator import Orchestrator

        orch = Orchestrator(
            safety_guard=FakeSafetyGuard(),
            tool_registry=FakeToolRegistry(),
            mcp_client=FakeMCPClient(),
            agent_harness=FakeAgentHarness(),
        )
        items = asyncio.run(_collect(orch.handle_chat("s9", "查看 CPU 使用率")))
        for m in items:
            assert "llm_reasoning" not in m
            assert "chain_of_thought" not in m


class TestBoundaryChecks:
    """边界检查"""

    def test_no_mcp_client_import(self):
        import inspect
        import app.services.orchestrator as orch_module
        src = inspect.getsource(orch_module.Orchestrator)
        assert "from app.mcp.client import" not in src
        assert "MCPClient" not in src

    def test_no_call_tool_directly(self):
        import inspect
        import app.services.orchestrator as orch_module
        src = inspect.getsource(orch_module.Orchestrator)
        assert "call_tool" not in src

    def test_no_subprocess(self):
        import inspect
        import app.services.orchestrator as orch_module
        src = inspect.getsource(orch_module.Orchestrator)
        assert "subprocess" not in src
        assert "os.system" not in src


class TestConfirmedSafety:
    """confirmed=True 不应绕过安全检查"""

    def test_confirmed_high_risk_still_blocked(self):
        """confirmed=True + rm -rf / → 仍返回 risk_alert"""
        from app.services.orchestrator import Orchestrator

        harness = FakeAgentHarness()
        orch = Orchestrator(
            safety_guard=FakeSafetyGuard(),
            tool_registry=FakeToolRegistry(),
            mcp_client=FakeMCPClient(),
            agent_harness=harness,
        )
        items = asyncio.run(_collect(orch.handle_chat("s-c1", "rm -rf /", confirmed=True)))

        types = [m["type"] for m in items]
        assert "risk_alert" in types
        assert types[-1] == "done"
        # 不应产生 tool_call
        assert "tool_call" not in types
        # AgentHarness 不应被调用
        assert len(harness.calls) == 0

    def test_confirmed_medium_proceeds(self):
        """confirmed=True + 重启 nginx → 继续执行，产生 tool_call"""
        from app.services.orchestrator import Orchestrator

        harness = FakeAgentHarness()
        orch = Orchestrator(
            safety_guard=FakeSafetyGuard(),
            tool_registry=FakeToolRegistry(),
            mcp_client=FakeMCPClient(),
            agent_harness=harness,
        )
        items = asyncio.run(_collect(orch.handle_chat("s-c2", "重启 nginx", confirmed=True)))

        types = [m["type"] for m in items]
        # 不应要求二次确认
        assert "risk_alert" not in types
        assert "tool_call" in types
        assert types[-1] == "done"
        assert len(harness.calls) >= 1

    def test_normal_low_still_passes(self):
        """普通 low → 正常通过"""
        from app.services.orchestrator import Orchestrator

        harness = FakeAgentHarness()
        orch = Orchestrator(
            safety_guard=FakeSafetyGuard(),
            tool_registry=FakeToolRegistry(),
            mcp_client=FakeMCPClient(),
            agent_harness=harness,
        )
        items = asyncio.run(_collect(orch.handle_chat("s-c3", "查看 CPU 使用率")))
        types = [m["type"] for m in items]
        assert "tool_call" in types
        assert types[-1] == "done"

    def test_high_risk_no_confirm_still_blocked(self):
        """普通 rm -rf / → 高危阻断"""
        from app.services.orchestrator import Orchestrator

        harness = FakeAgentHarness()
        orch = Orchestrator(
            safety_guard=FakeSafetyGuard(),
            tool_registry=FakeToolRegistry(),
            mcp_client=FakeMCPClient(),
            agent_harness=harness,
        )
        items = asyncio.run(_collect(orch.handle_chat("s-c4", "rm -rf /")))
        types = [m["type"] for m in items]
        assert "risk_alert" in types
        assert "tool_call" not in types
        assert len(harness.calls) == 0


# ═══════════════════════════════════════════════════════════════════
# MAJOR #4: trace_id 连续性测试
# ═══════════════════════════════════════════════════════════════════

class TestTraceIdContinuity:
    """trace_id 连续性测试"""

    def test_explicit_trace_id_propagates(self):
        """handle_chat(trace_id='t-fixed') → 所有 frame trace_id 一致"""
        from app.services.orchestrator import Orchestrator

        orch = Orchestrator(
            safety_guard=FakeSafetyGuard(),
            tool_registry=FakeToolRegistry(),
            mcp_client=FakeMCPClient(),
            agent_harness=FakeAgentHarness(),
        )
        items = asyncio.run(_collect(
            orch.handle_chat("s-t1", "查看 CPU 使用率", confirmed=True, trace_id="t-fixed")
        ))
        for m in items:
            if "trace_id" in m:
                assert m["trace_id"] == "t-fixed"

    def test_confirmed_with_trace_id_has_tool_call(self):
        """trace_id 传入不影响 tool_call 生成"""
        from app.services.orchestrator import Orchestrator

        harness = FakeAgentHarness()
        orch = Orchestrator(
            safety_guard=FakeSafetyGuard(),
            tool_registry=FakeToolRegistry(),
            mcp_client=FakeMCPClient(),
            agent_harness=harness,
        )
        items = asyncio.run(_collect(
            orch.handle_chat("s-t2", "重启 nginx", confirmed=True, trace_id="fixed-123")
        ))
        types = [m["type"] for m in items]
        assert "tool_call" in types
        for m in items:
            if "trace_id" in m:
                assert m["trace_id"] == "fixed-123"
