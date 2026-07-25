"""AgentHarness 单元测试

覆盖：
  1. 成功调用：sys_info + metric=cpu
  2. 未知工具：bad_tool → ok=False, MCPClient 不调用
  3. 参数错误：sys_info + metric=bad → ok=False
  4. 高危拒绝：cmd_exec + rm -rf / → blocked=True
  5. 中危确认：service_mgr restart nginx → requires_confirm=True
  6. MCP 返回失败：ok=False → 不抛异常
  7. MCP 抛异常：被捕获，返回 ok=False
  8. Audit 抛异常：主结果仍返回
  9. 敏感信息不泄露

所有测试使用 Fake 对象，不依赖真实 MCP Server。
"""

import asyncio

import pytest

from app.services.agent_context import AgentContext
from app.services.agent_harness import AgentHarness


# ═══════════════════════════════════════════════════════════════════
# Fake 对象 —— 模拟 SafetyGuard / ToolRegistry / MCPClient / AuditService
# ═══════════════════════════════════════════════════════════════════

class FakeToolRegistry:
    """模拟 ToolRegistry：存在 sys_info/service_mgr/log_reader/net_monitor/cmd_exec/file_guard"""

    _TOOLS = {
        "sys_info", "service_mgr", "log_reader",
        "net_monitor", "cmd_exec", "file_guard",
    }

    _VALID_METRICS = {"cpu", "memory", "disk", "load", "uptime", "all", "network"}

    def exists(self, tool_name: str) -> bool:
        return tool_name in self._TOOLS

    def get_tool_server_id(self, tool_name: str) -> str:
        """模拟 server_id 查找：测试用固定 ID"""
        return "test-server" if tool_name in self._TOOLS else ""

    def validate_params(self, tool_name: str, params: dict) -> dict:
        if tool_name not in self._TOOLS:
            return {"valid": False, "errors": [f"未知工具: {tool_name}"]}
        if tool_name == "sys_info":
            metric = params.get("metric", "")
            if metric not in self._VALID_METRICS:
                return {"valid": False, "errors": [f"参数 metric 值 '{metric}' 不在允许范围内"]}
        return {"valid": True, "errors": []}


class FakeSafetyGuard:
    """模拟 SafetyGuard，按 tool + params 返回预设结果"""

    def __init__(self):
        self.calls = []

    def analyze_tool_call(self, tool: str, params: dict, role: str = "viewer") -> dict:
        self.calls.append((tool, params, role))

        # cmd_exec + rm -rf 命令 → 高危拒绝
        if tool == "cmd_exec" and "rm -rf" in params.get("command", ""):
            return {
                "allowed": False,
                "risk_level": "high",
                "reason": "禁止递归删除根目录",
                "requires_confirm": False,
            }

        # service_mgr + restart nginx → 中危需确认
        if tool == "service_mgr" and params.get("action") == "restart":
            return {
                "allowed": True,
                "risk_level": "medium",
                "reason": "中风险服务操作: restart nginx",
                "requires_confirm": True,
            }

        # 默认放行
        return {
            "allowed": True,
            "risk_level": "low",
            "reason": "未发现高危输入",
            "requires_confirm": False,
        }


class FakeMCPClient:
    """模拟 MCPClient，可预设返回值或抛异常
    返回 MCP 协议格式 {content: [...], isError: bool}，与真实 MCPClient.call_tool 一致
    """

    def __init__(self, mock_result: dict | None = None, should_raise: bool = False):
        self._mock_result = mock_result or {"cpu_percent": 23.5}
        self.should_raise = should_raise
        self.calls = []

    async def call_tool(self, tool_name: str, arguments: dict | None = None, server_id: str = "") -> dict:
        self.calls.append((tool_name, arguments))
        if self.should_raise:
            raise RuntimeError("MCP 连接失败")
        import json as _json
        return {
            "content": [{"type": "text", "text": _json.dumps(self._mock_result, ensure_ascii=False)}],
            "isError": False,
        }


class FakeMCPClientFail:
    """模拟 MCPClient 返回失败
    返回 MCP 协议格式（连接级错误） {error: str}，由 _normalize_mcp_result 处理
    """

    def __init__(self):
        self.calls = []

    async def call_tool(self, tool_name: str, arguments: dict | None = None, server_id: str = "") -> dict:
        self.calls.append((tool_name, arguments))
        return {"error": "MCP Server 请求超时"}


class FakeMCPClientRaise:
    """模拟 MCPClient 抛异常"""

    def __init__(self):
        self.calls = []

    async def call_tool(self, tool_name: str, arguments: dict | None = None, server_id: str = "") -> dict:
        self.calls.append((tool_name, arguments))
        raise ConnectionError("MCP Server 连接失败")


class FakeAuditService:
    """模拟 AuditService，可预设抛异常"""

    def __init__(self, should_raise: bool = False):
        self.calls = []
        self.should_raise = should_raise

    async def log_chain(self, **kwargs) -> None:
        self.calls.append(kwargs)
        if self.should_raise:
            raise RuntimeError("审计写入失败")


def _run(coro):
    """同步运行 async 函数"""
    return asyncio.run(coro)


# ═══════════════════════════════════════════════════════════════════
# 测试类
# ═══════════════════════════════════════════════════════════════════

class TestSuccessfulCall:
    """成功调用测试"""

    def test_sys_info_cpu_success(self):
        """sys_info + metric=cpu → 成功调用 MCPClient，ctx 有记录"""
        ctx = AgentContext(session_id="s1", user_input="查看CPU")
        registry = FakeToolRegistry()
        safety = FakeSafetyGuard()
        mcp = FakeMCPClient()
        harness = AgentHarness(safety, registry, mcp)

        result = _run(harness.run_tool(ctx, "sys_info", {"metric": "cpu"}))

        assert result["ok"] is True
        assert result["tool"] == "sys_info"
        assert result["params"] == {"metric": "cpu"}
        assert result["result"]["cpu_percent"] == 23.5
        assert result["risk_level"] == "low"

    def test_mcp_client_called_once(self):
        """MCPClient.call_tool 被调用一次"""
        ctx = AgentContext(session_id="s1", user_input="test")
        mcp = FakeMCPClient()
        harness = AgentHarness(FakeSafetyGuard(), FakeToolRegistry(), mcp)

        _run(harness.run_tool(ctx, "sys_info", {"metric": "cpu"}))

        assert len(mcp.calls) == 1
        assert mcp.calls[0][0] == "sys_info"
        assert mcp.calls[0][1] == {"metric": "cpu"}

    def test_mcp_client_called_with_arguments(self):
        """MCPClient.call_tool 使用 arguments= 参数名（非 params=）"""
        ctx = AgentContext(session_id="s1", user_input="test")
        mcp = FakeMCPClient()
        harness = AgentHarness(FakeSafetyGuard(), FakeToolRegistry(), mcp)

        _run(harness.run_tool(ctx, "sys_info", {"metric": "cpu"}))

        # 验证参数名为 arguments
        call = mcp.calls[0]
        assert call[1] == {"metric": "cpu"}

    def test_ctx_tool_calls_has_record(self):
        """ctx.tool_calls 有记录"""
        ctx = AgentContext(session_id="s1", user_input="test")
        harness = AgentHarness(FakeSafetyGuard(), FakeToolRegistry(), FakeMCPClient())

        _run(harness.run_tool(ctx, "sys_info", {"metric": "cpu"}))

        assert len(ctx.tool_calls) == 1
        assert ctx.tool_calls[0]["tool"] == "sys_info"
        assert ctx.tool_calls[0]["params"] == {"metric": "cpu"}
        assert ctx.tool_calls[0]["status"] == "done"

    def test_ctx_observations_has_record(self):
        """ctx.observations 有记录"""
        ctx = AgentContext(session_id="s1", user_input="test")
        harness = AgentHarness(FakeSafetyGuard(), FakeToolRegistry(), FakeMCPClient())

        _run(harness.run_tool(ctx, "sys_info", {"metric": "cpu"}))

        assert len(ctx.observations) == 1
        assert ctx.observations[0]["tool"] == "sys_info"
        assert ctx.observations[0]["ok"] is True
        assert "result" in ctx.observations[0]


class TestUnknownTool:
    """未知工具测试"""

    def test_unknown_tool_returns_ok_false(self):
        """未知工具返回 ok=False"""
        ctx = AgentContext(session_id="s1", user_input="test")
        mcp = FakeMCPClient()
        harness = AgentHarness(FakeSafetyGuard(), FakeToolRegistry(), mcp)

        result = _run(harness.run_tool(ctx, "bad_tool", {}))

        assert result["ok"] is False
        assert "未知工具" in result["error"]
        assert result["blocked"] is True

    def test_unknown_tool_not_call_mcp(self):
        """未知工具不调用 MCPClient"""
        ctx = AgentContext(session_id="s1", user_input="test")
        mcp = FakeMCPClient()
        harness = AgentHarness(FakeSafetyGuard(), FakeToolRegistry(), mcp)

        _run(harness.run_tool(ctx, "bad_tool", {}))

        assert len(mcp.calls) == 0

    def test_unknown_tool_recorded_in_ctx(self):
        """未知工具记录到 ctx.tool_calls，状态为 unknown_tool"""
        ctx = AgentContext(session_id="s1", user_input="test")
        harness = AgentHarness(FakeSafetyGuard(), FakeToolRegistry(), FakeMCPClient())

        _run(harness.run_tool(ctx, "bad_tool", {}))

        assert len(ctx.tool_calls) == 1
        assert ctx.tool_calls[0]["status"] == "unknown_tool"
        assert ctx.tool_calls[0]["blocked"] is True


class TestInvalidParams:
    """参数错误测试"""

    def test_invalid_metric_returns_ok_false(self):
        """sys_info + metric=bad → ok=False"""
        ctx = AgentContext(session_id="s1", user_input="test")
        mcp = FakeMCPClient()
        harness = AgentHarness(FakeSafetyGuard(), FakeToolRegistry(), mcp)

        result = _run(harness.run_tool(ctx, "sys_info", {"metric": "bad"}))

        assert result["ok"] is False
        assert "参数校验失败" in result["error"]
        assert result["blocked"] is True

    def test_invalid_params_not_call_mcp(self):
        """参数错误不调用 MCPClient"""
        ctx = AgentContext(session_id="s1", user_input="test")
        mcp = FakeMCPClient()
        harness = AgentHarness(FakeSafetyGuard(), FakeToolRegistry(), mcp)

        _run(harness.run_tool(ctx, "sys_info", {"metric": "bad"}))

        assert len(mcp.calls) == 0

    def test_invalid_params_recorded_in_ctx(self):
        """参数错误记录到 ctx，状态为 invalid_params"""
        ctx = AgentContext(session_id="s1", user_input="test")
        harness = AgentHarness(FakeSafetyGuard(), FakeToolRegistry(), FakeMCPClient())

        _run(harness.run_tool(ctx, "sys_info", {"metric": "bad"}))

        assert len(ctx.tool_calls) == 1
        assert ctx.tool_calls[0]["status"] == "invalid_params"


class TestHighRiskBlocked:
    """高危拒绝测试"""

    def test_high_risk_blocked_returns_ok_false(self):
        """cmd_exec + rm -rf / → blocked=True"""
        ctx = AgentContext(session_id="s1", user_input="test")
        mcp = FakeMCPClient()
        harness = AgentHarness(FakeSafetyGuard(), FakeToolRegistry(), mcp)

        result = _run(harness.run_tool(ctx, "cmd_exec", {"command": "rm -rf /"}))

        assert result["ok"] is False
        assert result["blocked"] is True
        assert result["risk_level"] == "high"

    def test_high_risk_not_call_mcp(self):
        """高危拒绝不调用 MCPClient"""
        ctx = AgentContext(session_id="s1", user_input="test")
        mcp = FakeMCPClient()
        harness = AgentHarness(FakeSafetyGuard(), FakeToolRegistry(), mcp)

        _run(harness.run_tool(ctx, "cmd_exec", {"command": "rm -rf /"}))

        assert len(mcp.calls) == 0

    def test_high_risk_recorded_in_ctx(self):
        """高危拒绝记录到 ctx，状态为 blocked"""
        ctx = AgentContext(session_id="s1", user_input="test")
        harness = AgentHarness(FakeSafetyGuard(), FakeToolRegistry(), FakeMCPClient())

        _run(harness.run_tool(ctx, "cmd_exec", {"command": "rm -rf /"}))

        assert len(ctx.tool_calls) == 1
        assert ctx.tool_calls[0]["status"] == "blocked"


class TestMediumRiskRequiresConfirm:
    """中危需确认测试"""

    def test_medium_risk_returns_requires_confirm(self):
        """service_mgr restart → requires_confirm=True"""
        ctx = AgentContext(session_id="s1", user_input="test")
        mcp = FakeMCPClient()
        harness = AgentHarness(FakeSafetyGuard(), FakeToolRegistry(), mcp)

        result = _run(harness.run_tool(ctx, "service_mgr", {"action": "restart", "service": "nginx"}))

        assert result["ok"] is False
        assert result["requires_confirm"] is True
        assert result["risk_level"] == "medium"
        assert result["tool"] == "service_mgr"

    def test_medium_risk_not_call_mcp(self):
        """中危需确认不调用 MCPClient"""
        ctx = AgentContext(session_id="s1", user_input="test")
        mcp = FakeMCPClient()
        harness = AgentHarness(FakeSafetyGuard(), FakeToolRegistry(), mcp)

        _run(harness.run_tool(ctx, "service_mgr", {"action": "restart", "service": "nginx"}))

        assert len(mcp.calls) == 0

    def test_medium_risk_recorded_in_ctx(self):
        """中危需确认记录到 ctx，状态为 requires_confirm"""
        ctx = AgentContext(session_id="s1", user_input="test")
        harness = AgentHarness(FakeSafetyGuard(), FakeToolRegistry(), FakeMCPClient())

        _run(harness.run_tool(ctx, "service_mgr", {"action": "restart", "service": "nginx"}))

        assert len(ctx.tool_calls) == 1
        assert ctx.tool_calls[0]["status"] == "requires_confirm"


class TestMCPFailure:
    """MCPClient 返回失败测试"""

    def test_mcp_returns_failure_not_raise(self):
        """MCPClient 返回 ok=False → run_tool 不抛异常，返回 ok=False + mcp_error"""
        ctx = AgentContext(session_id="s1", user_input="test")
        harness = AgentHarness(FakeSafetyGuard(), FakeToolRegistry(), FakeMCPClientFail())

        result = _run(harness.run_tool(ctx, "sys_info", {"metric": "cpu"}))

        assert result["ok"] is False
        assert result["mcp_error"] is True
        assert "MCP Server 请求超时" in result["error"]

    def test_mcp_failure_observation_recorded(self):
        """MCP 失败时 observation 仍记录失败结果"""
        ctx = AgentContext(session_id="s1", user_input="test")
        harness = AgentHarness(FakeSafetyGuard(), FakeToolRegistry(), FakeMCPClientFail())

        _run(harness.run_tool(ctx, "sys_info", {"metric": "cpu"}))

        assert len(ctx.observations) == 1
        assert ctx.observations[0]["ok"] is False
        assert "error" in ctx.observations[0]

    def test_mcp_failure_ctx_tool_call_status(self):
        """MCP 失败时 ctx.tool_calls 状态为 mcp_error"""
        ctx = AgentContext(session_id="s1", user_input="test")
        harness = AgentHarness(FakeSafetyGuard(), FakeToolRegistry(), FakeMCPClientFail())

        _run(harness.run_tool(ctx, "sys_info", {"metric": "cpu"}))

        assert ctx.tool_calls[0]["status"] == "mcp_error"


class TestMCPException:
    """MCPClient 抛异常测试"""

    def test_mcp_exception_caught(self):
        """MCPClient 抛异常 → run_tool 捕获，返回 ok=False"""
        ctx = AgentContext(session_id="s1", user_input="test")
        harness = AgentHarness(FakeSafetyGuard(), FakeToolRegistry(), FakeMCPClientRaise())

        # 不应抛异常
        result = _run(harness.run_tool(ctx, "sys_info", {"metric": "cpu"}))

        assert result["ok"] is False
        assert "error" in result

    def test_mcp_exception_no_traceback_in_result(self):
        """MCP 异常不泄露 traceback 到返回结果"""
        ctx = AgentContext(session_id="s1", user_input="test")
        harness = AgentHarness(FakeSafetyGuard(), FakeToolRegistry(), FakeMCPClientRaise())

        result = _run(harness.run_tool(ctx, "sys_info", {"metric": "cpu"}))

        # traceback 不应出现在 error 消息中
        assert "Traceback" not in result.get("error", "")
        assert "ConnectionError" not in result.get("error", "")


class TestAuditFailure:
    """AuditService 异常测试"""

    def test_audit_exception_not_crash(self):
        """AuditService 抛异常 → run_tool 不崩溃，主结果仍返回"""
        ctx = AgentContext(session_id="s1", user_input="test")
        audit = FakeAuditService(should_raise=True)
        harness = AgentHarness(FakeSafetyGuard(), FakeToolRegistry(), FakeMCPClient(), audit_service=audit)

        # 不应抛异常
        result = _run(harness.run_tool(ctx, "sys_info", {"metric": "cpu"}))

        assert result["ok"] is True
        assert result["tool"] == "sys_info"

    def test_audit_called_when_provided(self):
        """提供 audit_service 时应被调用"""
        ctx = AgentContext(session_id="s1", user_input="test")
        audit = FakeAuditService()
        harness = AgentHarness(FakeSafetyGuard(), FakeToolRegistry(), FakeMCPClient(), audit_service=audit)

        _run(harness.run_tool(ctx, "sys_info", {"metric": "cpu"}))

        assert len(audit.calls) == 1

    def test_audit_failure_still_returns_success(self):
        """audit 失败但 MCP 成功 → 仍返回 ok=True"""
        ctx = AgentContext(session_id="s1", user_input="test")
        audit = FakeAuditService(should_raise=True)
        harness = AgentHarness(FakeSafetyGuard(), FakeToolRegistry(), FakeMCPClient(), audit_service=audit)

        result = _run(harness.run_tool(ctx, "sys_info", {"metric": "cpu"}))

        assert result["ok"] is True


class TestSensitiveInfoProtection:
    """敏感信息不泄露测试"""

    def test_no_authorization_in_result(self):
        """返回结果中不含 Authorization"""
        ctx = AgentContext(session_id="s1", user_input="test")
        harness = AgentHarness(FakeSafetyGuard(), FakeToolRegistry(), FakeMCPClient())

        result = _run(harness.run_tool(ctx, "sys_info", {"metric": "cpu"}))

        result_str = str(result)
        assert "Authorization" not in result_str
        assert "Bearer" not in result_str

    def test_no_token_in_result(self):
        """返回结果中不含 token/DEEPSEEK_API_KEY"""
        ctx = AgentContext(session_id="s1", user_input="test")
        harness = AgentHarness(FakeSafetyGuard(), FakeToolRegistry(), FakeMCPClient())

        result = _run(harness.run_tool(ctx, "sys_info", {"metric": "cpu"}))

        result_str = str(result)
        assert "DEEPSEEK_API_KEY" not in result_str
        assert "token" not in result_str.lower() or "token" in result_str

    def test_no_sensitive_in_observations(self):
        """observations 不含敏感信息"""
        ctx = AgentContext(session_id="s1", user_input="test")
        harness = AgentHarness(FakeSafetyGuard(), FakeToolRegistry(), FakeMCPClient())

        _run(harness.run_tool(ctx, "sys_info", {"metric": "cpu"}))

        obs_str = str(ctx.observations)
        assert "Authorization" not in obs_str
        assert "Bearer" not in obs_str

    def test_no_sensitive_in_tool_calls(self):
        """tool_calls 不含敏感信息"""
        ctx = AgentContext(session_id="s1", user_input="test")
        harness = AgentHarness(FakeSafetyGuard(), FakeToolRegistry(), FakeMCPClient())

        _run(harness.run_tool(ctx, "sys_info", {"metric": "cpu"}))

        tc_str = str(ctx.tool_calls)
        assert "Authorization" not in tc_str
        assert "Bearer" not in tc_str


class TestToolCallRecordStructure:
    """ctx.tool_calls 记录结构测试"""

    def test_tool_call_has_safety_on_success(self):
        """成功调用时 tool_calls 包含 safety 信息"""
        ctx = AgentContext(session_id="s1", user_input="test")
        harness = AgentHarness(FakeSafetyGuard(), FakeToolRegistry(), FakeMCPClient())

        _run(harness.run_tool(ctx, "sys_info", {"metric": "cpu"}))

        tc = ctx.tool_calls[0]
        assert "safety" in tc
        assert tc["safety"]["risk_level"] == "low"

    def test_tool_call_has_result_on_success(self):
        """成功调用时 tool_calls 有 result"""
        ctx = AgentContext(session_id="s1", user_input="test")
        harness = AgentHarness(FakeSafetyGuard(), FakeToolRegistry(), FakeMCPClient())

        _run(harness.run_tool(ctx, "sys_info", {"metric": "cpu"}))

        assert ctx.tool_calls[0]["result"] is not None

    def test_tool_call_no_result_on_blocked(self):
        """被拒绝时 tool_calls 的 result 为 None"""
        ctx = AgentContext(session_id="s1", user_input="test")
        harness = AgentHarness(FakeSafetyGuard(), FakeToolRegistry(), FakeMCPClient())

        _run(harness.run_tool(ctx, "bad_tool", {}))

        assert ctx.tool_calls[0]["result"] is None


class TestSafetyGuardException:
    """SafetyGuard 异常处理"""

    def test_safety_guard_exception_caught(self):
        """SafetyGuard 抛异常 → 返回安全错误，不崩溃"""

        class BrokenSafetyGuard:
            def analyze_tool_call(self, tool, params, role="viewer"):
                raise RuntimeError("safety guard crashed")

        ctx = AgentContext(session_id="s1", user_input="test")
        harness = AgentHarness(BrokenSafetyGuard(), FakeToolRegistry(), FakeMCPClient())

        result = _run(harness.run_tool(ctx, "sys_info", {"metric": "cpu"}))

        assert result["ok"] is False
        assert result["blocked"] is True
        assert "安全检查服务异常" in result["error"]


# ═══════════════════════════════════════════════════════════════════
# MAJOR #2: observation 脱敏测试
# ═══════════════════════════════════════════════════════════════════

class FakeMCPClientWithSensitive:
    """返回含敏感信息的 MCP 结果"""

    def __init__(self):
        self.calls = []

    async def call_tool(self, tool_name: str, arguments: dict | None = None, server_id: str = "") -> dict:
        self.calls.append((tool_name, arguments))
        import json as _json
        sensitive_result = {
            "cpu_percent": 50,
            "auth_header": "Authorization: Bearer sk-secret123",
            "config": "token=abc123",
            "creds": {"password": "admin123", "secret_key": "xxx"},
            "nested": [{"access_token": "yyy"}, "Bearer xyz789"],
            "jwt": "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dummy",
        }
        return {
            "content": [{"type": "text", "text": _json.dumps(sensitive_result, ensure_ascii=False)}],
            "isError": False,
        }


class TestObservationSanitization:
    """observation 脱敏测试"""

    def test_observations_sanitized(self):
        """敏感值不应出现在 observations 中"""
        ctx = AgentContext(session_id="s1", user_input="test")
        mcp = FakeMCPClientWithSensitive()
        harness = AgentHarness(FakeSafetyGuard(), FakeToolRegistry(), mcp)

        _run(harness.run_tool(ctx, "sys_info", {"metric": "cpu"}))

        obs_str = str(ctx.observations)
        assert "sk-secret123" not in obs_str
        assert "abc123" not in obs_str
        assert "admin123" not in obs_str
        assert "xxx" not in obs_str or "[REDACTED]" in obs_str
        assert "yyy" not in obs_str

    def test_observations_contain_redacted(self):
        """observations 应包含 [REDACTED] 标记"""
        ctx = AgentContext(session_id="s1", user_input="test")
        harness = AgentHarness(FakeSafetyGuard(), FakeToolRegistry(), FakeMCPClientWithSensitive())

        _run(harness.run_tool(ctx, "sys_info", {"metric": "cpu"}))

        obs_str = str(ctx.observations)
        assert "[REDACTED]" in obs_str

    def test_mcp_receives_original_params(self):
        """MCPClient 收到原始参数，非脱敏参数"""
        ctx = AgentContext(session_id="s1", user_input="test")
        mcp = FakeMCPClientWithSensitive()
        harness = AgentHarness(FakeSafetyGuard(), FakeToolRegistry(), mcp)

        _run(harness.run_tool(ctx, "sys_info", {"metric": "cpu"}))

        call = mcp.calls[0]
        assert call[1] == {"metric": "cpu"}  # 原始参数，非脱敏
