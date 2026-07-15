"""RBAC 动态权限接入测试

覆盖：
  1. _resolve_role() 三角色映射测试
  2. SafetyGuard.analyze_tool_call() 权限矩阵测试
  3. AgentHarness.run_tool() RBAC 集成测试（viewer/operator/admin）
  4. 高危命令始终被拒绝（不受角色影响）
"""
import asyncio

import pytest

from app.core.auth import AuthContext, AuthLevel
from app.services.agent_context import AgentContext
from app.services.agent_harness import AgentHarness
from app.services.safety_guard import SafetyGuard


# ═══════════════════════════════════════════════════════════════════
# _resolve_role() 单元测试
# ═══════════════════════════════════════════════════════════════════

class TestResolveRole:
    """_resolve_role 角色映射测试"""

    def _resolve_role(self, auth: AuthContext) -> str:
        """复制 chat.py 中的 _resolve_role 逻辑"""
        if auth.level == AuthLevel.ADMIN:
            return "admin"
        if auth.level == AuthLevel.OP:
            return "operator"
        return "viewer"

    def test_admin_maps_to_admin(self):
        """agent-admin → admin"""
        auth = AuthContext.from_token("tk", AuthLevel.ADMIN)
        assert self._resolve_role(auth) == "admin"

    def test_op_maps_to_operator(self):
        """agent-op → operator"""
        auth = AuthContext.from_token("tk", AuthLevel.OP)
        assert self._resolve_role(auth) == "operator"

    def test_read_maps_to_viewer(self):
        """agent-read → viewer（最小权限）"""
        auth = AuthContext.from_token("tk", AuthLevel.READ)
        assert self._resolve_role(auth) == "viewer"

    def test_anonymous_maps_to_viewer(self):
        """agent-anonymous → viewer"""
        auth = AuthContext.anonymous()
        assert self._resolve_role(auth) == "viewer"


# ═══════════════════════════════════════════════════════════════════
# SafetyGuard.analyze_tool_call() 权限矩阵测试
# ═══════════════════════════════════════════════════════════════════

class TestPermissionMatrix:
    """权限矩阵：3 角色 × 3 风险等级"""

    @pytest.fixture(autouse=True)
    def setup(self):
        self.safety_guard = SafetyGuard()

    # ── agent-read (viewer) 权限矩阵 ────────────────────────────────

    def test_read_low_allowed(self):
        """agent-read: low 风险允许"""
        # sys_info metric=cpu 是 low 风险
        result = self.safety_guard.analyze_tool_call(
            "sys_info", {"metric": "cpu"}, role="viewer",
        )
        assert result["allowed"] is True
        assert result["requires_confirm"] is False
        assert result["risk_level"] == "low"

    def test_read_medium_denied(self):
        """agent-read: medium 风险拒绝"""
        # service_mgr restart nginx 是 medium 风险
        result = self.safety_guard.analyze_tool_call(
            "service_mgr", {"action": "restart", "service": "nginx"}, role="viewer",
        )
        assert result["allowed"] is False
        # viewer 不在 _ROLE_CAN_MEDIUM 中，medium 被拒绝
        assert "无权" in result.get("reason", "")

    def test_read_high_denied(self):
        """agent-read: high 风险拒绝"""
        # 敏感路径 file_guard 是 high 风险
        result = self.safety_guard.analyze_tool_call(
            "file_guard", {"action": "read", "path": "/etc/passwd"}, role="viewer",
        )
        assert result["allowed"] is False
        assert result["risk_level"] == "high"

    # ── agent-op (operator) 权限矩阵 ────────────────────────────────

    def test_op_low_allowed(self):
        """agent-op: low 风险允许"""
        result = self.safety_guard.analyze_tool_call(
            "sys_info", {"metric": "cpu"}, role="operator",
        )
        assert result["allowed"] is True
        assert result["requires_confirm"] is False
        assert result["risk_level"] == "low"

    def test_op_medium_confirm(self):
        """agent-op: medium 风险进入 confirm 流程"""
        result = self.safety_guard.analyze_tool_call(
            "service_mgr", {"action": "restart", "service": "nginx"}, role="operator",
        )
        assert result["allowed"] is True
        assert result["requires_confirm"] is True
        assert result["risk_level"] == "medium"

    def test_op_high_denied(self):
        """agent-op: high 风险拒绝"""
        result = self.safety_guard.analyze_tool_call(
            "file_guard", {"action": "read", "path": "/etc/passwd"}, role="operator",
        )
        assert result["allowed"] is False
        assert result["risk_level"] == "high"

    # ── agent-admin (admin) 权限矩阵 ────────────────────────────────

    def test_admin_low_allowed(self):
        """agent-admin: low 风险允许"""
        result = self.safety_guard.analyze_tool_call(
            "sys_info", {"metric": "cpu"}, role="admin",
        )
        assert result["allowed"] is True
        assert result["requires_confirm"] is False
        assert result["risk_level"] == "low"

    def test_admin_medium_confirm(self):
        """agent-admin: medium 风险进入 confirm 流程"""
        result = self.safety_guard.analyze_tool_call(
            "service_mgr", {"action": "restart", "service": "nginx"}, role="admin",
        )
        assert result["allowed"] is True
        assert result["requires_confirm"] is True
        assert result["risk_level"] == "medium"

    def test_admin_high_denied(self):
        """agent-admin: high 风险仍拒绝（高危不受角色影响）"""
        result = self.safety_guard.analyze_tool_call(
            "file_guard", {"action": "read", "path": "/etc/passwd"}, role="admin",
        )
        assert result["allowed"] is False
        assert result["risk_level"] == "high"

    # ── 边界：未知角色回退 viewer ──────────────────────────────────

    def test_unknown_role_treated_as_viewer(self):
        """未知角色 → 按 viewer 处理（medium 拒绝）"""
        result = self.safety_guard.analyze_tool_call(
            "service_mgr", {"action": "restart", "service": "nginx"}, role="superadmin",
        )
        # SafetyGuard 将未知角色规范化为 viewer → medium 拒绝
        assert result["allowed"] is False
        assert "无权" in result.get("reason", "")

    def test_empty_role_treated_as_viewer(self):
        """空角色 → 按 viewer 处理"""
        result = self.safety_guard.analyze_tool_call(
            "service_mgr", {"action": "restart", "service": "nginx"}, role="",
        )
        assert result["allowed"] is False


# ═══════════════════════════════════════════════════════════════════
# AgentHarness.run_tool() RBAC 集成测试（Fake 对象）
# ═══════════════════════════════════════════════════════════════════

class FakeToolRegistry:
    """模拟 ToolRegistry"""

    _TOOLS = {"sys_info", "service_mgr", "file_guard"}

    def exists(self, tool_name: str) -> bool:
        return tool_name in self._TOOLS

    def validate_params(self, tool_name: str, params: dict) -> dict:
        return {"valid": True, "errors": []}


class FakeMCPClient:
    """模拟 MCPClient —— 记录调用次数"""

    def __init__(self):
        self.calls = []

    async def call_tool(self, tool_name: str, arguments: dict) -> dict:
        self.calls.append({"tool": tool_name, "arguments": arguments})
        return {"ok": True, "result": {"status": "ok"}}


class TestAgentHarnessRBAC:
    """AgentHarness.run_tool() 在高危时不调用 MCPClient"""

    @pytest.fixture(autouse=True)
    def setup(self):
        self.safety_guard = SafetyGuard()
        self.tool_registry = FakeToolRegistry()
        self.mcp_client = FakeMCPClient()
        self.harness = AgentHarness(
            safety_guard=self.safety_guard,
            tool_registry=self.tool_registry,
            mcp_client=self.mcp_client,
        )

    def _ctx(self, role: str) -> AgentContext:
        return AgentContext(session_id="test-session", user_input="test", role=role)

    def test_viewer_low_calls_mcp(self):
        """viewer + low 风险 → MCPClient 被调用"""
        ctx = self._ctx("viewer")
        result = asyncio.run(
            self.harness.run_tool(ctx, "sys_info", {"metric": "cpu"})
        )
        assert result["ok"] is True
        assert len(self.mcp_client.calls) == 1

    def test_viewer_medium_does_not_call_mcp(self):
        """viewer + medium 风险 → MCPClient 不被调用（被 RBAC 拦截）"""
        ctx = self._ctx("viewer")
        result = asyncio.run(
            self.harness.run_tool(ctx, "service_mgr", {"action": "restart", "service": "nginx"})
        )
        assert result["ok"] is False
        assert result.get("blocked") is True
        # MCPClient 未被调用
        assert len(self.mcp_client.calls) == 0

    def test_operator_medium_requires_confirm(self):
        """operator + medium 风险 → requires_confirm（不调用 MCP）"""
        ctx = self._ctx("operator")
        result = asyncio.run(
            self.harness.run_tool(ctx, "service_mgr", {"action": "restart", "service": "nginx"})
        )
        # 中危确认：ok=False, requires_confirm=True
        assert result["ok"] is False
        assert result.get("requires_confirm") is True
        # MCPClient 未被调用（等待确认）
        assert len(self.mcp_client.calls) == 0

    def test_viewer_high_does_not_call_mcp(self):
        """viewer + high 风险 → MCPClient 不被调用"""
        ctx = self._ctx("viewer")
        result = asyncio.run(
            self.harness.run_tool(ctx, "file_guard", {"action": "read", "path": "/etc/passwd"})
        )
        assert result["ok"] is False
        assert result.get("blocked") is True
        assert len(self.mcp_client.calls) == 0

    def test_admin_high_does_not_call_mcp(self):
        """admin + high 风险 → MCPClient 仍不被调用（高危不受角色影响）"""
        ctx = self._ctx("admin")
        result = asyncio.run(
            self.harness.run_tool(ctx, "file_guard", {"action": "read", "path": "/etc/passwd"})
        )
        assert result["ok"] is False
        assert result.get("blocked") is True
        assert len(self.mcp_client.calls) == 0
