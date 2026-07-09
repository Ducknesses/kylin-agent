"""ToolRegistry 单元测试

覆盖：
  - 6 个已注册工具的识别
  - 未知工具返回不存在
  - sys_info metric 枚举校验
  - service_mgr action 枚举校验（不含 enable/disable）
  - service_mgr action 风险等级映射
  - log_reader lines 上限校验（1-500）
  - net_monitor metric 枚举校验
  - file_guard action 枚举校验
  - ToolRegistry 不直接调用 MCPClient
"""

import pytest

from app.services.tool_registry import ToolRegistry


@pytest.fixture
def registry() -> ToolRegistry:
    """每个测试一个干净的 ToolRegistry 实例"""
    return ToolRegistry()


# ── 工具存在性测试 ────────────────────────────────────────────────────

class TestToolExistence:
    """工具存在性判断"""

    _EXPECTED_TOOLS = [
        "sys_info", "service_mgr", "log_reader",
        "net_monitor", "cmd_exec", "file_guard",
    ]

    @pytest.mark.parametrize("tool_name", _EXPECTED_TOOLS)
    def test_known_tool_exists(self, registry, tool_name):
        """已知工具应返回 True"""
        assert registry.exists(tool_name) is True

    def test_unknown_tool_not_exists(self, registry):
        """未知工具应返回 False"""
        assert registry.exists("nonexistent_tool") is False

    def test_empty_string_not_exists(self, registry):
        """空字符串工具名应返回 False"""
        assert registry.exists("") is False

    def test_get_tool_names_returns_all(self, registry):
        """get_tool_names 应返回全部 6 个工具"""
        names = registry.get_tool_names()
        assert len(names) == 6
        for t in self._EXPECTED_TOOLS:
            assert t in names

    def test_get_tool_info_known(self, registry):
        """已知工具应返回完整信息"""
        info = registry.get_tool_info("sys_info")
        assert info is not None
        assert "description" in info
        assert "risk_level" in info
        assert "params" in info

    def test_get_tool_info_unknown(self, registry):
        """未知工具应返回 None"""
        assert registry.get_tool_info("unknown") is None


# ── sys_info 测试 ─────────────────────────────────────────────────────

class TestSysInfo:
    """sys_info 工具测试"""

    def test_metric_valid_cpu(self, registry):
        result = registry.validate_params("sys_info", {"metric": "cpu"})
        assert result["valid"] is True
        assert result["errors"] == []

    def test_metric_valid_memory(self, registry):
        result = registry.validate_params("sys_info", {"metric": "memory"})
        assert result["valid"] is True

    def test_metric_valid_disk(self, registry):
        result = registry.validate_params("sys_info", {"metric": "disk"})
        assert result["valid"] is True

    def test_metric_valid_load(self, registry):
        result = registry.validate_params("sys_info", {"metric": "load"})
        assert result["valid"] is True

    def test_metric_valid_uptime(self, registry):
        result = registry.validate_params("sys_info", {"metric": "uptime"})
        assert result["valid"] is True

    def test_metric_valid_all(self, registry):
        result = registry.validate_params("sys_info", {"metric": "all"})
        assert result["valid"] is True

    def test_metric_valid_network(self, registry):
        result = registry.validate_params("sys_info", {"metric": "network"})
        assert result["valid"] is True

    def test_metric_invalid(self, registry):
        """非法的 metric 值应校验失败"""
        result = registry.validate_params("sys_info", {"metric": "hack"})
        assert result["valid"] is False
        assert len(result["errors"]) == 1
        assert "hack" in result["errors"][0]

    def test_missing_required_param(self, registry):
        """缺少必填参数 metric"""
        result = registry.validate_params("sys_info", {})
        assert result["valid"] is False
        assert any("metric" in e for e in result["errors"])

    def test_default_risk_low(self, registry):
        assert registry.get_default_risk("sys_info") == "low"


# ── service_mgr 测试 ──────────────────────────────────────────────────

class TestServiceMgr:
    """service_mgr 工具测试"""

    _VALID_ACTIONS = ["status", "start", "stop", "restart", "is-active", "is-enabled"]

    @pytest.mark.parametrize("action", _VALID_ACTIONS)
    def test_action_valid(self, registry, action):
        result = registry.validate_params("service_mgr", {
            "action": action,
            "service": "nginx",
        })
        assert result["valid"] is True, f"action={action} 应为合法值"

    def test_action_enable_invalid(self, registry):
        """enable 不应在 ToolRegistry 允许范围内"""
        result = registry.validate_params("service_mgr", {
            "action": "enable",
            "service": "nginx",
        })
        assert result["valid"] is False

    def test_action_disable_invalid(self, registry):
        """disable 不应在 ToolRegistry 允许范围内"""
        result = registry.validate_params("service_mgr", {
            "action": "disable",
            "service": "nginx",
        })
        assert result["valid"] is False

    def test_action_empty_invalid(self, registry):
        result = registry.validate_params("service_mgr", {
            "action": "",
            "service": "nginx",
        })
        assert result["valid"] is False

    def test_missing_service(self, registry):
        """缺少必填参数 service"""
        result = registry.validate_params("service_mgr", {"action": "status"})
        assert result["valid"] is False

    def test_missing_action(self, registry):
        """缺少必填参数 action"""
        result = registry.validate_params("service_mgr", {"service": "nginx"})
        assert result["valid"] is False

    # ── action 级别风险映射 ──

    def test_risk_status_low(self, registry):
        assert registry.get_risk_for_action("service_mgr", "status") == "low"

    def test_risk_is_active_low(self, registry):
        assert registry.get_risk_for_action("service_mgr", "is-active") == "low"

    def test_risk_is_enabled_low(self, registry):
        assert registry.get_risk_for_action("service_mgr", "is-enabled") == "low"

    def test_risk_start_medium(self, registry):
        assert registry.get_risk_for_action("service_mgr", "start") == "medium"

    def test_risk_stop_medium(self, registry):
        assert registry.get_risk_for_action("service_mgr", "stop") == "medium"

    def test_risk_restart_medium(self, registry):
        assert registry.get_risk_for_action("service_mgr", "restart") == "medium"

    def test_risk_unknown_action_returns_default(self, registry):
        """不存在的 action 返回工具默认风险等级"""
        assert registry.get_risk_for_action("service_mgr", "nonexistent") == "low"


# ── log_reader 测试 ───────────────────────────────────────────────────

class TestLogReader:
    """log_reader 工具测试"""

    def test_lines_within_range(self, registry):
        """lines 在 1-500 范围内应通过"""
        result = registry.validate_params("log_reader", {
            "source": "/var/log/messages",
            "lines": 100,
        })
        assert result["valid"] is True

    def test_lines_min_boundary(self, registry):
        """lines=1 应通过"""
        result = registry.validate_params("log_reader", {
            "source": "/var/log/messages",
            "lines": 1,
        })
        assert result["valid"] is True

    def test_lines_max_boundary(self, registry):
        """lines=500 应通过"""
        result = registry.validate_params("log_reader", {
            "source": "/var/log/messages",
            "lines": 500,
        })
        assert result["valid"] is True

    def test_lines_zero_invalid(self, registry):
        """lines=0 应失败"""
        result = registry.validate_params("log_reader", {
            "source": "/var/log/messages",
            "lines": 0,
        })
        assert result["valid"] is False

    def test_lines_negative_invalid(self, registry):
        """lines 负数应失败"""
        result = registry.validate_params("log_reader", {
            "source": "/var/log/messages",
            "lines": -1,
        })
        assert result["valid"] is False

    def test_lines_exceeds_max(self, registry):
        """lines > 500 应失败"""
        result = registry.validate_params("log_reader", {
            "source": "/var/log/messages",
            "lines": 501,
        })
        assert result["valid"] is False

    def test_default_risk_low(self, registry):
        assert registry.get_default_risk("log_reader") == "low"


# ── net_monitor 测试 ──────────────────────────────────────────────────

class TestNetMonitor:
    """net_monitor 工具测试"""

    _VALID_METRICS = ["connections", "traffic", "interfaces", "routes", "dns", "listen", "all"]

    @pytest.mark.parametrize("metric", _VALID_METRICS)
    def test_metric_valid(self, registry, metric):
        result = registry.validate_params("net_monitor", {"metric": metric})
        assert result["valid"] is True

    def test_metric_invalid(self, registry):
        result = registry.validate_params("net_monitor", {"metric": "bandwidth"})
        assert result["valid"] is False

    def test_default_risk_low(self, registry):
        assert registry.get_default_risk("net_monitor") == "low"


# ── cmd_exec 测试 ─────────────────────────────────────────────────────

class TestCmdExec:
    """cmd_exec 工具测试"""

    def test_command_present_valid(self, registry):
        """command 存在即可通过（不做命令黑名单裁决）"""
        result = registry.validate_params("cmd_exec", {
            "command": "whoami",
        })
        assert result["valid"] is True

    def test_missing_command(self, registry):
        """缺少必填参数 command"""
        result = registry.validate_params("cmd_exec", {})
        assert result["valid"] is False

    def test_with_timeout(self, registry):
        """可选参数 timeout"""
        result = registry.validate_params("cmd_exec", {
            "command": "whoami",
            "timeout": 10,
        })
        assert result["valid"] is True

    def test_with_user(self, registry):
        """可选参数 user"""
        result = registry.validate_params("cmd_exec", {
            "command": "whoami",
            "user": "nobody",
        })
        assert result["valid"] is True

    def test_default_risk_medium(self, registry):
        assert registry.get_default_risk("cmd_exec") == "medium"


# ── file_guard 测试 ───────────────────────────────────────────────────

class TestFileGuard:
    """file_guard 工具测试"""

    _VALID_ACTIONS = ["check", "read", "write"]

    @pytest.mark.parametrize("action", _VALID_ACTIONS)
    def test_action_valid(self, registry, action):
        result = registry.validate_params("file_guard", {
            "action": action,
            "path": "/tmp/test.txt",
        })
        assert result["valid"] is True

    def test_action_invalid(self, registry):
        result = registry.validate_params("file_guard", {
            "action": "delete",
            "path": "/tmp/test.txt",
        })
        assert result["valid"] is False

    def test_missing_path(self, registry):
        result = registry.validate_params("file_guard", {"action": "read"})
        assert result["valid"] is False

    def test_missing_action(self, registry):
        result = registry.validate_params("file_guard", {"path": "/tmp/test.txt"})
        assert result["valid"] is False

    def test_default_risk_medium(self, registry):
        assert registry.get_default_risk("file_guard") == "medium"


# ── 边界情况 ──────────────────────────────────────────────────────────

class TestEdgeCases:
    """边界情况测试"""

    def test_validate_unknown_tool(self, registry):
        result = registry.validate_params("unknown", {})
        assert result["valid"] is False
        assert "未知工具" in result["errors"][0]

    def test_validate_extra_params_allowed(self, registry):
        """额外参数不应导致校验失败（ToolRegistry 仅校验已定义参数）"""
        result = registry.validate_params("sys_info", {
            "metric": "cpu",
            "extra_field": "should_be_ignored",
        })
        assert result["valid"] is True

    def test_get_param_info_known(self, registry):
        info = registry.get_param_info("sys_info", "metric")
        assert info is not None
        assert info["type"] == "string"
        assert "enum" in info

    def test_get_param_info_unknown_param(self, registry):
        assert registry.get_param_info("sys_info", "nonexistent") is None

    def test_get_param_info_unknown_tool(self, registry):
        assert registry.get_param_info("unknown", "metric") is None

    def test_get_risk_unknown_tool(self, registry):
        assert registry.get_default_risk("unknown") is None

    def test_get_risk_for_action_unknown_tool(self, registry):
        assert registry.get_risk_for_action("unknown", "start") is None


# ── ToolRegistry 不直接调用 MCPClient 的保证 ──────────────────────────

class TestNoMCPClientDirectCall:
    """验证 ToolRegistry 不导入或调用 MCPClient"""

    def test_no_mcp_client_import(self):
        """ToolRegistry 模块不应导入 MCPClient"""
        import app.services.tool_registry as tr
        source = tr.__dict__
        # 检查模块级别是否引入了 mcp client
        assert "MCPClient" not in source
        assert "mcp" not in str(tr.__dict__.get("__builtins__", ""))
