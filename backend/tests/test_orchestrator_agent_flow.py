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

    def test_tool_call_has_result_and_tool_call_id(self):
        """tool_call 帧必须携带 result 与 tool_call_id，供前端渲染"""
        from app.services.orchestrator import Orchestrator

        orch = Orchestrator(
            safety_guard=FakeSafetyGuard(),
            tool_registry=FakeToolRegistry(),
            mcp_client=FakeMCPClient(),
            agent_harness=FakeAgentHarness(),
        )
        items = asyncio.run(_collect(orch.handle_chat("s-tool-result", "查看 CPU 使用率")))
        tool_calls = [m for m in items if m["type"] == "tool_call"]
        assert len(tool_calls) >= 1
        for tc in tool_calls:
            assert tc.get("tool_call_id"), "tool_call_id 不应为空"
            assert tc.get("result") is not None, "成功工具调用应携带 result"
            assert tc.get("ok") is True

    def test_tool_call_failure_has_error(self):
        """工具调用失败时 tool_call 帧应携带 error"""
        from app.services.orchestrator import Orchestrator

        orch = Orchestrator(
            safety_guard=FakeSafetyGuard(),
            tool_registry=FakeToolRegistry(),
            mcp_client=FakeMCPClient(),
            agent_harness=FakeAgentHarness(fail_tools=["sys_info"]),
        )
        items = asyncio.run(_collect(orch.handle_chat("s-tool-err", "查看 CPU 使用率")))
        tool_calls = [m for m in items if m["type"] == "tool_call"]
        assert any(tc.get("ok") is False and tc.get("error") for tc in tool_calls)


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


# ═══════════════════════════════════════════════════════════════════
# Day 6-4: FixOption 集成测试
# ═══════════════════════════════════════════════════════════════════


class FakeFixOptionStore:
    """记录保存调用的假 Store"""
    def __init__(self):
        self.saved = []  # (session_id, trace_id, options)

    def save_options(self, session_id, trace_id, options):
        self.saved.append((session_id, trace_id, options))
        return [o.option_id for o in options]

    def get_option(self, session_id, option_id):
        return None


class FakeFixPlanner:
    """返回预设 FixOption 列表的假 Planner"""
    def __init__(self, options=None):
        self.options = options or []
        self.plan_calls = []

    def plan(self, intent, observations, report, target_service=None):
        self.plan_calls.append((intent, observations, report, target_service))
        return list(self.options)

    async def plan_with_llm(self, intent, observations, report, target_service=None):
        return self.plan(intent, observations, report, target_service)


class TestFixOptionFlow:
    """FixPlanner → Store → fix_options 帧 完整链路"""

    def test_service_issue_generates_fix_options_frame(self):
        """服务异常 observation 生成 restart FixOption 并出现在帧中"""
        from app.services.orchestrator import Orchestrator
        from app.schemas.action import FixOption

        opt = FixOption(
            option_id="fix_072f5706",
            title="重启 nginx",
            description="测试",
            risk_level="medium",
            tool="service_mgr",
            params={"action": "restart", "service": "nginx"},
            requires_confirm=True,
        )
        fix_planner = FakeFixPlanner(options=[opt])
        store = FakeFixOptionStore()

        orch = Orchestrator(
            safety_guard=FakeSafetyGuard(),
            tool_registry=FakeToolRegistry(),
            mcp_client=FakeMCPClient(),
            agent_harness=FakeAgentHarness(),
            fix_planner=fix_planner,
            fix_option_store=store,
        )

        items = asyncio.run(_collect(orch.handle_chat("s-f1", "查看 CPU 使用率")))

        # fix_options 帧
        fix_frames = [m for m in items if m["type"] == "fix_options"]
        assert len(fix_frames) == 1
        assert len(fix_frames[0]["options"]) == 1
        assert fix_frames[0]["options"][0]["option_id"] == "fix_072f5706"

    def test_fix_options_after_all_chunks_before_done(self):
        """fix_options 在所有 chunk 之后，done 之前"""
        from app.services.orchestrator import Orchestrator
        from app.schemas.action import FixOption

        opt = FixOption(
            option_id="fix_4cb68057", title="t", description="d",
            risk_level="low", tool="sys_info",
            params={"metric": "memory"}, requires_confirm=False,
        )
        orch = Orchestrator(
            safety_guard=FakeSafetyGuard(),
            tool_registry=FakeToolRegistry(),
            mcp_client=FakeMCPClient(),
            agent_harness=FakeAgentHarness(),
            fix_planner=FakeFixPlanner(options=[opt]),
            fix_option_store=FakeFixOptionStore(),
        )

        items = asyncio.run(_collect(orch.handle_chat("s-f2", "查看 CPU")))
        types = [m["type"] for m in items]
        # 全部 chunk 索引 < fix_options 索引 < done 索引
        chunk_indices = [i for i, t in enumerate(types) if t == "chunk"]
        fix_idx = types.index("fix_options")
        done_idx = types.index("done")
        assert len(chunk_indices) >= 1
        for ci in chunk_indices:
            assert ci < fix_idx < done_idx

    def test_fix_options_only_once(self):
        """fix_options 帧只出现一次"""
        from app.services.orchestrator import Orchestrator
        from app.schemas.action import FixOption

        opt = FixOption(
            option_id="fix_cd45bf59", title="t", description="d",
            risk_level="low", tool="sys_info",
            params={"metric": "cpu"}, requires_confirm=False,
        )
        orch = Orchestrator(
            safety_guard=FakeSafetyGuard(),
            tool_registry=FakeToolRegistry(),
            mcp_client=FakeMCPClient(),
            agent_harness=FakeAgentHarness(),
            fix_planner=FakeFixPlanner(options=[opt]),
            fix_option_store=FakeFixOptionStore(),
        )

        items = asyncio.run(_collect(orch.handle_chat("s-f3", "查看 CPU")))
        fix_frames = [m for m in items if m["type"] == "fix_options"]
        assert len(fix_frames) == 1

    def test_no_options_no_fix_options_frame(self):
        """FixPlanner 返回空列表时不发送 fix_options"""
        from app.services.orchestrator import Orchestrator

        orch = Orchestrator(
            safety_guard=FakeSafetyGuard(),
            tool_registry=FakeToolRegistry(),
            mcp_client=FakeMCPClient(),
            agent_harness=FakeAgentHarness(),
            fix_planner=FakeFixPlanner(options=[]),
            fix_option_store=FakeFixOptionStore(),
        )

        items = asyncio.run(_collect(orch.handle_chat("s-f4", "查看 CPU")))
        fix_frames = [m for m in items if m["type"] == "fix_options"]
        assert len(fix_frames) == 0

    def test_high_risk_blocked_no_fix_options(self):
        """高危输入被阻断时不生成 fix_options"""
        from app.services.orchestrator import Orchestrator
        from app.schemas.action import FixOption

        opt = FixOption(
            option_id="fix_bfd72ad2", title="t", description="d",
            risk_level="low", tool="sys_info",
            params={"metric": "cpu"}, requires_confirm=False,
        )
        orch = Orchestrator(
            safety_guard=FakeSafetyGuard(),
            tool_registry=FakeToolRegistry(),
            mcp_client=FakeMCPClient(),
            agent_harness=FakeAgentHarness(),
            fix_planner=FakeFixPlanner(options=[opt]),
            fix_option_store=FakeFixOptionStore(),
        )

        items = asyncio.run(_collect(orch.handle_chat("s-f5", "rm -rf /")))
        types = [m["type"] for m in items]
        assert "fix_options" not in types

    def test_save_options_called_with_correct_ids(self):
        """Store 保存时 session_id 和 trace_id 正确"""
        from app.services.orchestrator import Orchestrator
        from app.schemas.action import FixOption

        opt = FixOption(
            option_id="fix_2ea7b76e", title="t", description="d",
            risk_level="low", tool="sys_info",
            params={"metric": "cpu"}, requires_confirm=False,
        )
        store = FakeFixOptionStore()

        orch = Orchestrator(
            safety_guard=FakeSafetyGuard(),
            tool_registry=FakeToolRegistry(),
            mcp_client=FakeMCPClient(),
            agent_harness=FakeAgentHarness(),
            fix_planner=FakeFixPlanner(options=[opt]),
            fix_option_store=store,
        )

        items = asyncio.run(_collect(
            orch.handle_chat("s-f6", "查看 CPU", trace_id="trace-xyz")
        ))
        assert len(store.saved) == 1
        sid, tid, opts = store.saved[0]
        assert sid == "s-f6"
        assert tid == "trace-xyz"
        assert opts[0].option_id == "fix_2ea7b76e"

    def test_fix_options_not_return_store_internals(self):
        """fix_options 帧不暴露 Store 内部字段"""
        from app.services.orchestrator import Orchestrator
        from app.schemas.action import FixOption

        opt = FixOption(
            option_id="fix_c8c0f093", title="t", description="d",
            risk_level="low", tool="sys_info",
            params={"metric": "cpu"}, requires_confirm=False,
        )
        orch = Orchestrator(
            safety_guard=FakeSafetyGuard(),
            tool_registry=FakeToolRegistry(),
            mcp_client=FakeMCPClient(),
            agent_harness=FakeAgentHarness(),
            fix_planner=FakeFixPlanner(options=[opt]),
            fix_option_store=FakeFixOptionStore(),
        )

        items = asyncio.run(_collect(orch.handle_chat("s-f7", "查看 CPU")))
        fix_frames = [m for m in items if m["type"] == "fix_options"]
        opt_data = fix_frames[0]["options"][0]
        forbidden = {"status", "created_at", "expires_at", "executed_at"}
        assert forbidden.isdisjoint(opt_data.keys())

    def test_fix_planner_receives_intent_and_observations(self):
        """FixPlanner.plan() 收到正确的 intent 和 observations"""
        from app.services.orchestrator import Orchestrator
        from app.schemas.action import FixOption

        opt = FixOption(
            option_id="fix_429ad523", title="t", description="d",
            risk_level="low", tool="sys_info",
            params={"metric": "cpu"}, requires_confirm=False,
        )
        fix_planner = FakeFixPlanner(options=[opt])

        orch = Orchestrator(
            safety_guard=FakeSafetyGuard(),
            tool_registry=FakeToolRegistry(),
            mcp_client=FakeMCPClient(),
            agent_harness=FakeAgentHarness(),
            fix_planner=fix_planner,
            fix_option_store=FakeFixOptionStore(),
        )

        asyncio.run(_collect(orch.handle_chat("s-f8", "查看 CPU 使用率")))
        assert len(fix_planner.plan_calls) == 1
        intent, obs, report, target = fix_planner.plan_calls[0]
        assert intent in ("cpu_query", "unknown")  # 真实 IntentAgent 输出
        assert isinstance(obs, list)

    def test_store_save_failure_yields_error_then_done(self):
        """Store 保存失败 → error + done，不发送 fix_options"""
        from app.services.orchestrator import Orchestrator
        from app.schemas.action import FixOption

        opt = FixOption(
            option_id="fix_0948af32", title="t", description="d",
            risk_level="low", tool="sys_info",
            params={"metric": "cpu"}, requires_confirm=False,
        )

        audit = FakeAuditService()

        class FailingStore:
            def save_options(self, session_id, trace_id, options):
                raise RuntimeError("DB down")

        orch = Orchestrator(
            safety_guard=FakeSafetyGuard(),
            tool_registry=FakeToolRegistry(),
            mcp_client=FakeMCPClient(),
            agent_harness=FakeAgentHarness(),
            fix_planner=FakeFixPlanner(options=[opt]),
            fix_option_store=FailingStore(),
            audit_service=audit,
        )

        items = asyncio.run(_collect(orch.handle_chat("s-f9", "查看 CPU")))
        types = [m["type"] for m in items]
        assert "fix_options" not in types
        assert "error" in types
        assert types[-1] == "done"
        error_idx = types.index("error")
        done_idx = types.index("done")
        assert error_idx < done_idx
        # 审计应记录 error
        assert any(c[1] == "error" for c in audit.calls)

    def test_existing_flow_no_regression(self):
        """原有 status/chunk/tool_call/done 帧不受影响"""
        from app.services.orchestrator import Orchestrator
        from app.schemas.action import FixOption

        opt = FixOption(
            option_id="fix_33546053", title="t", description="d",
            risk_level="low", tool="sys_info",
            params={"metric": "cpu"}, requires_confirm=False,
        )
        orch = Orchestrator(
            safety_guard=FakeSafetyGuard(),
            tool_registry=FakeToolRegistry(),
            mcp_client=FakeMCPClient(),
            agent_harness=FakeAgentHarness(),
            fix_planner=FakeFixPlanner(options=[opt]),
            fix_option_store=FakeFixOptionStore(),
        )

        items = asyncio.run(_collect(orch.handle_chat("s-f10", "查看 CPU 使用率")))
        types = [m["type"] for m in items]
        for t in ("status", "tool_call", "chunk", "done"):
            assert t in types
        assert types[-1] == "done"


# ═══════════════════════════════════════════════════════════════════
# Day 6-4a: 真实 FixOptionStore 集成测试
# ═══════════════════════════════════════════════════════════════════


class TestRealStoreIntegration:
    """使用真实 FixOptionStore 的端到端测试"""

    def test_real_store_can_query_option_after_handle_chat(self):
        from app.services.fix_option_store import FixOptionStore
        from app.services.orchestrator import Orchestrator
        from app.schemas.action import FixOption

        opt = FixOption(
            option_id="fix_b3aa6665", title="真实Store", description="d",
            risk_level="low", tool="sys_info",
            params={"metric": "cpu"}, requires_confirm=False,
        )
        store = FixOptionStore()
        orch = Orchestrator(
            safety_guard=FakeSafetyGuard(),
            tool_registry=FakeToolRegistry(),
            mcp_client=FakeMCPClient(),
            agent_harness=FakeAgentHarness(),
            fix_planner=FakeFixPlanner(options=[opt]),
            fix_option_store=store,
        )
        items = asyncio.run(_collect(
            orch.handle_chat("s-real1", "查看 CPU", trace_id="trace-real1")
        ))
        fix_frames = [m for m in items if m["type"] == "fix_options"]
        frame_opt_id = fix_frames[0]["options"][0]["option_id"]

        stored = store.get_option("s-real1", frame_opt_id)
        assert stored is not None
        assert stored.option.option_id == "fix_b3aa6665"
        assert stored.session_id == "s-real1"
        assert stored.trace_id == "trace-real1"

    def test_same_store_reused_across_two_sessions(self):
        from app.services.fix_option_store import FixOptionStore
        from app.services.orchestrator import Orchestrator
        from app.schemas.action import FixOption

        store = FixOptionStore()
        opt1 = FixOption(
            option_id="fix_8bb6a0f1", title="s1", description="d",
            risk_level="low", tool="sys_info",
            params={"metric": "cpu"}, requires_confirm=False,
        )
        opt2 = FixOption(
            option_id="fix_24bab887", title="s2", description="d",
            risk_level="low", tool="sys_info",
            params={"metric": "memory"}, requires_confirm=False,
        )
        orch = Orchestrator(
            safety_guard=FakeSafetyGuard(),
            tool_registry=FakeToolRegistry(),
            mcp_client=FakeMCPClient(),
            agent_harness=FakeAgentHarness(),
            fix_planner=FakeFixPlanner(options=[opt1]),
            fix_option_store=store,
        )
        asyncio.run(_collect(orch.handle_chat("s-A", "查看 CPU")))
        orch.fix_planner = FakeFixPlanner(options=[opt2])
        asyncio.run(_collect(orch.handle_chat("s-B", "查看 CPU")))

        assert store.get_option("s-A", "fix_8bb6a0f1") is not None
        assert store.get_option("s-B", "fix_24bab887") is not None
        assert store.get_option("s-A", "fix_24bab887") is None
        assert store.get_option("s-B", "fix_8bb6a0f1") is None


# ═══════════════════════════════════════════════════════════════════
# Day 6-6a: 真实 FixPlanner + FakeLLM + Store 集成测试
# ═══════════════════════════════════════════════════════════════════


class FakeLLMResponse:
    def __init__(self, ok=True, content="", error=None):
        self.ok = ok
        self.content = content
        self.error = error


class FakeLLMClientForOrch:
    def __init__(self, response=None):
        self.response = response or FakeLLMResponse()
        self.calls = []

    async def chat_simple(self, system_prompt="", user_prompt="", **kw):
        self.calls.append({"system": system_prompt, "user": user_prompt})
        return self.response


def _valid_llm_json():
    import json
    return json.dumps({"options": [{
        "title": "检查内存", "description": "查看内存详情",
        "tool": "sys_info", "params": {"metric": "memory"}, "rollback": None,
    }]})


class TestRealFixPlannerLLMIntegration:
    """真实 FixPlannerAgent + FakeLLMClient + 真实 FixOptionStore"""

    def test_llm_enabled_true_real_chain(self, monkeypatch):
        """LLM_ENABLED=true 真实链路：LLM→FixOption→Store→fix_options帧"""
        from app.services.orchestrator import Orchestrator
        from app.services.fix_planner_agent import FixPlannerAgent
        from app.services.fix_option_store import FixOptionStore
        from app.services.tool_registry import ToolRegistry
        from config import settings

        monkeypatch.setattr(settings, "LLM_ENABLED", True)

        fake_llm = FakeLLMClientForOrch(FakeLLMResponse(ok=True, content=_valid_llm_json()))
        registry = ToolRegistry()
        planner = FixPlannerAgent(tool_registry=registry, llm_client=fake_llm)
        store = FixOptionStore()

        orch = Orchestrator(
            safety_guard=FakeSafetyGuard(),
            tool_registry=registry,
            mcp_client=FakeMCPClient(),
            agent_harness=FakeAgentHarness(),
            fix_planner=planner,
            fix_option_store=store,
        )

        items = asyncio.run(_collect(
            orch.handle_chat("s-llm1", "查看内存", trace_id="trace-llm1")
        ))
        # LLM 被调用一次
        assert len(fake_llm.calls) == 1
        # 存在 fix_options
        fix_frames = [m for m in items if m["type"] == "fix_options"]
        assert len(fix_frames) == 1
        fid = fix_frames[0]["options"][0]["option_id"]
        # Store 回查
        stored = store.get_option("s-llm1", fid)
        assert stored is not None
        assert stored.session_id == "s-llm1"
        assert stored.trace_id == "trace-llm1"
        # 后端风险重算：sys_info → low
        assert stored.option.risk_level == "low"
        # 帧顺序
        types = [m["type"] for m in items]
        chunk_idx = [i for i, t in enumerate(types) if t == "chunk"][0]
        fix_idx = types.index("fix_options")
        done_idx = types.index("done")
        assert chunk_idx < fix_idx < done_idx

    def test_llm_enabled_false_no_llm_call(self, monkeypatch):
        """LLM_ENABLED=false 不调用 LLM"""
        from app.services.orchestrator import Orchestrator
        from app.services.fix_planner_agent import FixPlannerAgent
        from app.services.fix_option_store import FixOptionStore
        from app.services.tool_registry import ToolRegistry
        from config import settings

        monkeypatch.setattr(settings, "LLM_ENABLED", False)

        fake_llm = FakeLLMClientForOrch()
        planner = FixPlannerAgent(tool_registry=ToolRegistry(), llm_client=fake_llm)
        store = FixOptionStore()

        orch = Orchestrator(
            safety_guard=FakeSafetyGuard(),
            tool_registry=ToolRegistry(),
            mcp_client=FakeMCPClient(),
            agent_harness=FakeAgentHarness(),
            fix_planner=planner,
            fix_option_store=store,
        )

        asyncio.run(_collect(orch.handle_chat("s-llm2", "查看内存")))
        assert len(fake_llm.calls) == 0

    def test_llm_failure_fallback_to_rules(self, monkeypatch):
        """LLM 失败 → 回退规则版"""
        from app.services.orchestrator import Orchestrator
        from app.services.fix_planner_agent import FixPlannerAgent
        from app.services.fix_option_store import FixOptionStore
        from app.services.tool_registry import ToolRegistry
        from config import settings

        monkeypatch.setattr(settings, "LLM_ENABLED", True)

        fake_llm = FakeLLMClientForOrch(FakeLLMResponse(ok=False, error="timeout"))
        planner = FixPlannerAgent(tool_registry=ToolRegistry(), llm_client=fake_llm)
        store = FixOptionStore()

        orch = Orchestrator(
            safety_guard=FakeSafetyGuard(),
            tool_registry=ToolRegistry(),
            mcp_client=FakeMCPClient(),
            agent_harness=FakeAgentHarness(),
            fix_planner=planner,
            fix_option_store=store,
        )

        items = asyncio.run(_collect(orch.handle_chat("s-llm3", "查看内存")))
        assert len(fake_llm.calls) == 1
        fix_frames = [m for m in items if m["type"] == "fix_options"]
        assert len(fix_frames) <= 1
        assert "error" not in [m["type"] for m in items]

    def test_high_risk_no_llm_call(self, monkeypatch):
        """高危输入 → 不调用 LLM"""
        from app.services.orchestrator import Orchestrator
        from app.services.fix_planner_agent import FixPlannerAgent
        from app.services.fix_option_store import FixOptionStore
        from app.services.tool_registry import ToolRegistry
        from config import settings

        monkeypatch.setattr(settings, "LLM_ENABLED", True)

        fake_llm = FakeLLMClientForOrch()
        planner = FixPlannerAgent(tool_registry=ToolRegistry(), llm_client=fake_llm)
        store = FixOptionStore()

        orch = Orchestrator(
            safety_guard=FakeSafetyGuard(),
            tool_registry=ToolRegistry(),
            mcp_client=FakeMCPClient(),
            agent_harness=FakeAgentHarness(),
            fix_planner=planner,
            fix_option_store=store,
        )

        items = asyncio.run(_collect(orch.handle_chat("s-hr", "rm -rf /")))
        assert len(fake_llm.calls) == 0
        assert "fix_options" not in [m["type"] for m in items]


# ═══════════════════════════════════════════════════════════════════
# Day 6-6b: 共享依赖装配验证
# ═══════════════════════════════════════════════════════════════════


class TestDependencyAssembly:
    """验证 dependencies.py 共享实例一致性"""

    def test_planner_uses_deps_tool_registry(self):
        from app import dependencies
        assert dependencies.fix_planner.tool_registry is dependencies.tool_registry

    def test_planner_uses_deps_llm_client(self):
        from app import dependencies
        assert dependencies.fix_planner.llm_client is dependencies.llm_client

    def test_orchestrator_uses_deps_tool_registry(self):
        from app import dependencies
        from app.api.chat import _orchestrator
        assert _orchestrator.tool_registry is dependencies.tool_registry

    def test_orchestrator_uses_deps_fix_planner(self):
        from app import dependencies
        from app.api.chat import _orchestrator
        assert _orchestrator.fix_planner is dependencies.fix_planner

    def test_orchestrator_uses_deps_fix_option_store(self):
        from app import dependencies
        from app.api.chat import _orchestrator
        assert _orchestrator.fix_option_store is dependencies.fix_option_store
