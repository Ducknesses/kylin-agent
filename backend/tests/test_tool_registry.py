"""ToolRegistry 单元测试（纯动态模式）

覆盖：
  - 通过 register_from_server 注册 mock 工具
  - 7 个已注册工具的识别
  - 未知工具返回不存在
  - sys_info metric 枚举校验
  - service_mgr action 枚举校验
  - service_mgr action 风险等级映射
  - log_reader lines 上限校验（1-500）
  - net_monitor metric 枚举校验
  - file_guard action 枚举校验
  - cmd_exec 审计策略（summary 模式）
  - ToolRegistry 不直接调用 MCPClient
"""

import pytest

from app.mcp.client import MCPTool
from app.services.tool_registry import ToolRegistry


# ── Mock MCP 工具构建辅助函数 ──────────────────────────────────────────

def _make_mock_tool(
    name: str,
    description: str,
    properties: dict,
    required: list[str] | None = None,
    meta: dict | None = None,
) -> MCPTool:
    """构建 mock MCPTool（模拟 MCP 服务器 tools/list 响应格式）"""
    return MCPTool(
        name=name,
        description=description,
        parameters=properties,
        required=required or [],
        server_id="test-server",
        server_name="Test MCP Server",
        meta=meta or {},
    )


# ── 从旧 JSON 配置文件迁移的元信息 ─────────────────────────────────────

_SYS_INFO_META = {
    "suggested_risk": "low",
    "audit_policy": {"mode": "whitelist", "safe_fields": ["metric"]},
}

_SERVICE_MGR_META = {
    "suggested_risk": "low",
    "action_field": "action",
    "action_risk_overrides": {
        "status": "low", "is-active": "low", "is-enabled": "low",
        "start": "medium", "stop": "medium", "restart": "medium",
    },
    "audit_policy": {"mode": "whitelist", "safe_fields": ["action", "service"]},
}

_LOG_READER_META = {
    "suggested_risk": "low",
    "audit_policy": {"mode": "whitelist", "safe_fields": ["service", "lines"]},
}

_NET_MONITOR_META = {
    "suggested_risk": "low",
    "audit_policy": {"mode": "whitelist", "safe_fields": ["metric"]},
}

_CMD_EXEC_META = {
    "suggested_risk": "medium",
    "audit_policy": {"mode": "summary", "summary_builder": "cmd_exec_summary"},
}

_FILE_GUARD_META = {
    "suggested_risk": "medium",
    "action_field": "action",
    "action_risk_overrides": {
        "check": "low", "read": "low", "write": "medium",
    },
    "audit_policy": {"mode": "whitelist", "safe_fields": ["action", "path"]},
}

_METRICS_HISTORY_META = {
    "suggested_risk": "low",
    "audit_policy": {"mode": "whitelist", "safe_fields": ["from_ts", "to_ts", "metrics"]},
}


def _build_mock_tools() -> list[MCPTool]:
    """构建完整的 mock 工具列表（模拟 MCP 服务器 tools/list 返回）"""
    return [
        _make_mock_tool(
            "sys_info", "获取系统信息（CPU、内存、磁盘、负载等）",
            {
                "metric": {
                    "type": "string",
                    "description": "指标类型",
                    "enum": ["cpu", "memory", "disk", "load", "uptime", "all", "network"],
                },
            },
            required=[],
            meta=_SYS_INFO_META,
        ),
        _make_mock_tool(
            "service_mgr", "管理系统服务（systemctl 操作）",
            {
                "action": {
                    "type": "string",
                    "description": "操作类型",
                    "enum": ["status", "start", "stop", "restart", "is-active", "is-enabled"],
                },
                "service": {
                    "type": "string",
                    "description": "服务名称",
                },
            },
            required=["action", "service"],
            meta=_SERVICE_MGR_META,
        ),
        _make_mock_tool(
            "log_reader", "读取系统日志",
            {
                "type": {"type": "string", "description": "日志类型"},
                "source": {"type": "string", "description": "日志来源"},
                "service": {"type": "string", "description": "服务名称"},
                "lines": {
                    "type": "integer",
                    "description": "读取行数",
                    "constraints": {"min": 1, "max": 500},
                },
                "since": {"type": "string", "description": "起始时间"},
                "keyword": {"type": "string", "description": "关键词过滤"},
            },
            meta=_LOG_READER_META,
        ),
        _make_mock_tool(
            "net_monitor", "网络监控信息",
            {
                "metric": {
                    "type": "string",
                    "description": "监控指标",
                    "enum": ["connections", "traffic", "interfaces", "routes", "dns", "listen", "all"],
                },
                "port": {"type": "integer", "description": "端口号"},
            },
            meta=_NET_MONITOR_META,
        ),
        _make_mock_tool(
            "cmd_exec", "执行安全范围内的系统命令",
            {
                "command": {"type": "string", "description": "要执行的命令"},
                "timeout": {"type": "integer", "description": "超时时间（秒）"},
                "user": {"type": "string", "description": "执行用户"},
            },
            required=["command"],
            meta=_CMD_EXEC_META,
        ),
        _make_mock_tool(
            "metrics_history", "查询系统历史指标数据（CPU、内存、磁盘、网络），按时间范围返回历史读数",
            {
                "from_ts": {"type": "number", "description": "开始时间戳（Unix秒），默认5分钟前"},
                "to_ts": {"type": "number", "description": "结束时间戳（Unix秒），默认当前时间"},
                "metrics": {"type": "string", "description": "逗号分隔的指标名: cpu,memory,disk,network,all"},
                "limit": {
                    "type": "integer",
                    "description": "返回记录数上限",
                    "constraints": {"min": 1, "max": 10000},
                },
            },
            meta=_METRICS_HISTORY_META,
        ),
        _make_mock_tool(
            "file_guard", "安全地操作文件（检查、读取、写入）",
            {
                "action": {
                    "type": "string",
                    "description": "操作类型",
                    "enum": ["check", "read", "write"],
                },
                "path": {"type": "string", "description": "文件路径"},
                "content": {"type": "string", "description": "写入内容（仅 write 操作需要）"},
                "max_size": {"type": "integer", "description": "最大文件大小"},
            },
            required=["action", "path"],
            meta=_FILE_GUARD_META,
        ),
    ]


@pytest.fixture
def registry() -> ToolRegistry:
    """每个测试一个干净的 ToolRegistry 实例，注册 mock 工具"""
    reg = ToolRegistry()
    tools = _build_mock_tools()
    reg.register_from_server("test-server", tools)
    return reg


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
        """get_tool_names 应返回全部 7 个工具"""
        names = registry.get_tool_names()

        expected_names = {
            "sys_info",
            "service_mgr",
            "log_reader",
            "net_monitor",
            "cmd_exec",
            "metrics_history",
            "file_guard",
        }

        assert set(names) == expected_names
        assert len(names) == len(set(names))
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
        result = registry.validate_params("sys_info", {"metric": "hack"})
        assert result["valid"] is False
        assert any("metric" in e for e in result["errors"])

    def test_missing_required_param(self, registry):
        """sys_info metric 非 required，空参数合法"""
        result = registry.validate_params("sys_info", {})
        assert result["valid"] is True

    def test_default_risk_low(self, registry):
        assert registry.get_default_risk("sys_info") == "low"


# ── service_mgr 测试 ──────────────────────────────────────────────────

class TestServiceMgr:
    """service_mgr 工具测试"""

    _VALID_ACTIONS = ["status", "start", "stop", "restart", "is-active", "is-enabled"]

    @pytest.mark.parametrize("action", _VALID_ACTIONS)
    def test_action_valid(self, registry, action):
        result = registry.validate_params("service_mgr", {"action": action, "service": "nginx"})
        assert result["valid"] is True

    def test_action_enable_invalid(self, registry):
        result = registry.validate_params("service_mgr", {"action": "enable", "service": "nginx"})
        assert result["valid"] is False
        assert any("action" in e for e in result["errors"])

    def test_action_disable_invalid(self, registry):
        result = registry.validate_params("service_mgr", {"action": "disable", "service": "nginx"})
        assert result["valid"] is False
        assert any("action" in e for e in result["errors"])

    def test_action_empty_invalid(self, registry):
        result = registry.validate_params("service_mgr", {"action": "", "service": "nginx"})
        assert result["valid"] is False

    def test_missing_action(self, registry):
        result = registry.validate_params("service_mgr", {"service": "nginx"})
        assert result["valid"] is False
        assert any("action" in e for e in result["errors"])

    def test_missing_service(self, registry):
        result = registry.validate_params("service_mgr", {"action": "status"})
        assert result["valid"] is False
        assert any("service" in e for e in result["errors"])

    def test_missing_both(self, registry):
        result = registry.validate_params("service_mgr", {})
        assert result["valid"] is False
        assert len(result["errors"]) == 2

    def test_default_risk(self, registry):
        assert registry.get_default_risk("service_mgr") == "low"

    def test_action_risk_status(self, registry):
        assert registry.get_risk_for_action("service_mgr", "status") == "low"

    def test_action_risk_start(self, registry):
        assert registry.get_risk_for_action("service_mgr", "start") == "medium"

    def test_action_risk_stop(self, registry):
        assert registry.get_risk_for_action("service_mgr", "stop") == "medium"

    def test_action_risk_restart(self, registry):
        assert registry.get_risk_for_action("service_mgr", "restart") == "medium"

    def test_action_risk_is_active(self, registry):
        assert registry.get_risk_for_action("service_mgr", "is-active") == "low"


# ── log_reader 测试 ───────────────────────────────────────────────────

class TestLogReader:
    """log_reader 工具测试"""

    def test_valid_simple(self, registry):
        result = registry.validate_params("log_reader", {"type": "journalctl", "source": "system"})
        assert result["valid"] is True

    def test_lines_within_range(self, registry):
        result = registry.validate_params("log_reader", {"type": "file", "source": "/var/log/syslog", "lines": 300})
        assert result["valid"] is True

    def test_lines_min_boundary(self, registry):
        result = registry.validate_params("log_reader", {"type": "file", "source": "/var/log/syslog", "lines": 1})
        assert result["valid"] is True

    def test_lines_max_boundary(self, registry):
        result = registry.validate_params("log_reader", {"type": "file", "source": "/var/log/syslog", "lines": 500})
        assert result["valid"] is True

    def test_lines_below_min(self, registry):
        result = registry.validate_params("log_reader", {"type": "file", "source": "/var/log/syslog", "lines": 0})
        assert result["valid"] is False

    def test_lines_above_max(self, registry):
        result = registry.validate_params("log_reader", {"type": "file", "source": "/var/log/syslog", "lines": 501})
        assert result["valid"] is False

    def test_no_required_params_ok(self, registry):
        """所有参数都是可选的"""
        result = registry.validate_params("log_reader", {})
        assert result["valid"] is True

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
        assert any("metric" in e for e in result["errors"])

    def test_no_params_ok(self, registry):
        """所有参数都是可选的"""
        result = registry.validate_params("net_monitor", {})
        assert result["valid"] is True

    def test_default_risk_low(self, registry):
        assert registry.get_default_risk("net_monitor") == "low"


# ── cmd_exec 测试 ─────────────────────────────────────────────────────

class TestCmdExec:
    """cmd_exec 工具测试"""

    def test_valid_command(self, registry):
        result = registry.validate_params("cmd_exec", {"command": "df -h"})
        assert result["valid"] is True

    def test_valid_with_timeout(self, registry):
        result = registry.validate_params("cmd_exec", {"command": "ls", "timeout": 60})
        assert result["valid"] is True

    def test_missing_command(self, registry):
        result = registry.validate_params("cmd_exec", {})
        assert result["valid"] is False
        assert any("command" in e for e in result["errors"])

    def test_default_risk_medium(self, registry):
        assert registry.get_default_risk("cmd_exec") == "medium"

    def test_audit_policy_summary(self, registry):
        policy = registry.get_tool_audit_policy("cmd_exec")
        assert policy is not None
        assert policy["mode"] == "summary"
        assert policy["summary_builder"] == "cmd_exec_summary"

    def test_build_audit_metadata_summary(self, registry):
        """审计元数据应生成摘要而非原命令"""
        metadata = registry.build_audit_metadata(
            "cmd_exec", {"command": "rm -rf /tmp/test", "timeout": 30}
        )
        # 摘要模式不应包含原始完整命令
        assert "command" not in metadata or metadata.get("command") != "rm -rf /tmp/test"
        # 应包含 command_name 或 argument_count 等摘要字段
        assert any(k in metadata for k in ("command_name", "argument_count", "command"))


# ── metrics_history 测试 ──────────────────────────────────────────────

class TestMetricsHistory:
    """metrics_history 工具测试"""

    def test_valid_defaults(self, registry):
        result = registry.validate_params("metrics_history", {})
        assert result["valid"] is True

    def test_with_metrics_param(self, registry):
        result = registry.validate_params("metrics_history", {"metrics": "cpu,memory"})
        assert result["valid"] is True

    def test_limit_within_range(self, registry):
        result = registry.validate_params("metrics_history", {"limit": 5000})
        assert result["valid"] is True

    def test_limit_below_min(self, registry):
        result = registry.validate_params("metrics_history", {"limit": 0})
        assert result["valid"] is False

    def test_limit_above_max(self, registry):
        result = registry.validate_params("metrics_history", {"limit": 10001})
        assert result["valid"] is False

    def test_limit_boundary_min(self, registry):
        result = registry.validate_params("metrics_history", {"limit": 1})
        assert result["valid"] is True

    def test_limit_boundary_max(self, registry):
        result = registry.validate_params("metrics_history", {"limit": 10000})
        assert result["valid"] is True

    def test_default_risk_low(self, registry):
        assert registry.get_default_risk("metrics_history") == "low"


# ── file_guard 测试 ───────────────────────────────────────────────────

class TestFileGuard:
    """file_guard 工具测试"""

    _VALID_ACTIONS = ["check", "read", "write"]

    @pytest.mark.parametrize("action", _VALID_ACTIONS)
    def test_action_valid(self, registry, action):
        params = {"action": action, "path": "/tmp/test.txt"}
        if action == "write":
            params["content"] = "hello"
        result = registry.validate_params("file_guard", params)
        assert result["valid"] is True

    def test_action_invalid(self, registry):
        result = registry.validate_params("file_guard", {"action": "delete", "path": "/tmp/test.txt"})
        assert result["valid"] is False
        assert any("action" in e for e in result["errors"])

    def test_missing_action(self, registry):
        result = registry.validate_params("file_guard", {"path": "/tmp/test.txt"})
        assert result["valid"] is False
        assert any("action" in e for e in result["errors"])

    def test_missing_path(self, registry):
        result = registry.validate_params("file_guard", {"action": "read"})
        assert result["valid"] is False
        assert any("path" in e for e in result["errors"])

    def test_default_risk(self, registry):
        assert registry.get_default_risk("file_guard") == "medium"

    def test_action_risk_check(self, registry):
        assert registry.get_risk_for_action("file_guard", "check") == "low"

    def test_action_risk_read(self, registry):
        assert registry.get_risk_for_action("file_guard", "read") == "low"

    def test_action_risk_write(self, registry):
        assert registry.get_risk_for_action("file_guard", "write") == "medium"

    def test_action_risk_unknown_action(self, registry):
        """未定义的 action 回退到 default_risk"""
        assert registry.get_risk_for_action("file_guard", "delete") == "medium"


# ── resolve 测试 ──────────────────────────────────────────────────────

class TestResolve:
    """解析为标准调用格式"""

    def test_resolve_sys_info(self, registry):
        resolved = registry.resolve("sys_info", {"metric": "cpu"})
        assert resolved == {"tool": "sys_info", "arguments": {"metric": "cpu"}}

    def test_resolve_cmd_exec(self, registry):
        resolved = registry.resolve("cmd_exec", {"command": "ls", "timeout": 30})
        assert resolved == {"tool": "cmd_exec", "arguments": {"command": "ls", "timeout": 30}}

    def test_resolve_unknown(self, registry):
        """未知工具仍然返回格式，调用方自行处理"""
        resolved = registry.resolve("unknown", {"a": 1})
        assert resolved["tool"] == "unknown"
        assert resolved["arguments"] == {"a": 1}


# ── get_openai_functions / build_tool_prompt_section 测试 ─────────────

class TestOpenAIFunctions:
    """OpenAI function calling 格式生成"""

    def test_returns_functions(self, registry):
        functions = registry.get_openai_functions()
        assert isinstance(functions, list)
        assert len(functions) == 7

        names = [f["function"]["name"] for f in functions]
        assert "sys_info" in names
        assert "cmd_exec" in names
        assert "file_guard" in names

    def test_function_format(self, registry):
        functions = registry.get_openai_functions()
        cmd_exec = next(f for f in functions if f["function"]["name"] == "cmd_exec")
        assert cmd_exec["type"] == "function"
        assert "command" in cmd_exec["function"]["parameters"]["properties"]
        assert "command" in cmd_exec["function"]["parameters"]["required"]

    def test_build_tool_prompt_section(self, registry):
        prompt = registry.build_tool_prompt_section()
        assert "sys_info" in prompt
        assert "service_mgr" in prompt
        assert "cmd_exec" in prompt


# ── 动态注册 / 注销测试 ───────────────────────────────────────────────

class TestDynamicRegistration:
    """动态注册与注销"""

    def test_register_new_server_adds_tools(self, registry):
        """注册新服务器应添加工具"""
        new_tool = _make_mock_tool(
            "new_tool", "A new tool",
            {"param1": {"type": "string", "description": "param"}},
            meta={"suggested_risk": "low"},
        )
        registry.register_from_server("another-server", [new_tool])
        assert registry.exists("new_tool") is True
        assert registry.get_default_risk("new_tool") == "low"

    def test_unregister_server_marks_unavailable(self, registry):
        """注销服务器应标记工具为 unavailable"""
        registry.unregister_server("test-server")
        # 工具名仍在 get_tool_names 中（标记 unavailable 非删除）
        names = registry.get_tool_names()
        assert "sys_info" in names
        # 但 get_openai_functions 不应包含 unavailable 工具
        functions = registry.get_openai_functions()
        names_in_functions = [f["function"]["name"] for f in functions]
        assert "sys_info" not in names_in_functions

    def test_unregister_server_retains_config(self, registry):
        """注销后工具定义仍在，但 source='mcp' 且 status='unavailable'"""
        registry.unregister_server("test-server")
        defn = registry.get_tool_definition("sys_info")
        assert defn is not None
        assert defn["source"] == "mcp"
        assert defn["status"] == "unavailable"
        # risk 元信息应保留
        assert defn["default_risk"] == "low"

    def test_refresh_server_replaces_tools(self, registry):
        """刷新服务器应替换工具列表"""
        # 注册不同工具
        new_tool = _make_mock_tool(
            "replaced_tool", "Replaced",
            {"x": {"type": "string", "description": "x"}},
            meta={"suggested_risk": "high"},
        )
        registry.register_from_server("test-server", [new_tool])
        # 旧工具不再存在（被清除）
        assert registry.exists("sys_info") is False
        assert registry.exists("replaced_tool") is True
        assert registry.get_default_risk("replaced_tool") == "high"


# ── 审计策略测试 ──────────────────────────────────────────────────────

class TestAuditPolicy:
    """审计策略功能测试"""

    def test_sys_info_whitelist(self, registry):
        """sys_info 审计策略应只保留 whitelist 字段"""
        metadata = registry.build_audit_metadata(
            "sys_info", {"metric": "cpu", "extra_secret": "should_be_dropped"}
        )
        assert "metric" in metadata
        assert "extra_secret" not in metadata

    def test_file_guard_whitelist(self, registry):
        """file_guard read 操作应只保留 action/path"""
        metadata = registry.build_audit_metadata(
            "file_guard", {"action": "read", "path": "/etc/hosts", "content": "secret_data"}
        )
        assert "action" in metadata
        assert "path" in metadata
        assert "content" not in metadata

    def test_update_audit_policy(self, registry):
        """更新审计策略应在内存中生效"""
        result = registry.update_tool_audit_policy("sys_info", mode="full")
        assert result is True
        policy = registry.get_tool_audit_policy("sys_info")
        assert policy["mode"] == "full"

        # full 模式下所有字段都应保留
        metadata = registry.build_audit_metadata(
            "sys_info", {"metric": "cpu", "extra": "kept"}
        )
        assert "metric" in metadata
        assert "extra" in metadata

    def test_update_audit_policy_unknown_tool(self, registry):
        """更新未知工具的审计策略应返回 False"""
        result = registry.update_tool_audit_policy("nonexistent", mode="whitelist")
        assert result is False


# ── 风险等级更新测试 ─────────────────────────────────────────────────

class TestRiskUpdate:
    """风险等级更新功能测试"""

    def test_update_risk_default(self, registry):
        """更新默认风险等级"""
        result = registry.update_tool_risk("net_monitor", default_risk="high")
        assert result is True
        assert registry.get_default_risk("net_monitor") == "high"

    def test_update_risk_overrides(self, registry):
        """更新 action 风险覆盖"""
        result = registry.update_tool_risk(
            "file_guard",
            action_risk_overrides={"write": "high"},
        )
        assert result is True
        assert registry.get_risk_for_action("file_guard", "write") == "high"
        # check 应保持不变
        assert registry.get_risk_for_action("file_guard", "check") == "low"

    def test_update_risk_invalid_risk(self, registry):
        """无效风险等级应拒绝"""
        result = registry.update_tool_risk("sys_info", default_risk="critical")
        assert result is False

    def test_update_risk_invalid_action(self, registry):
        """无效 action 应拒绝"""
        result = registry.update_tool_risk(
            "file_guard",
            action_risk_overrides={"invalid_action": "low"},
        )
        assert result is False

    def test_update_risk_unknown_tool(self, registry):
        """未知工具应返回 False"""
        result = registry.update_tool_risk("unknown", default_risk="medium")
        assert result is False


# ── get_all_tool_definitions 测试 ────────────────────────────────────

class TestGetAllDefinitions:
    """获取所有工具定义"""

    def test_returns_all(self, registry):
        definitions = registry.get_all_tool_definitions()
        assert len(definitions) == 7
        # 验证 source 字段
        sources = {d["name"]: d["source"] for d in definitions}
        assert all(s == "mcp" for s in sources.values())
        # 验证 status 字段
        statuses = {d["name"]: d["status"] for d in definitions}
        assert all(s == "available" for s in statuses.values())

    def test_get_single_definition(self, registry):
        d = registry.get_tool_definition("cmd_exec")
        assert d is not None
        assert d["name"] == "cmd_exec"
        assert d["default_risk"] == "medium"
        assert d["source"] == "mcp"
        assert d["audit_policy"]["mode"] == "summary"


# ── get_tool_spec 向后兼容测试 ────────────────────────────────────────

class TestToolSpecCompat:
    """ToolSpec 向后兼容"""

    def test_get_tool_spec_known(self, registry):
        spec = registry.get_tool_spec("sys_info")
        assert spec is not None
        assert spec.name == "sys_info"
        assert spec.default_risk == "low"
        assert "metric" in spec.params

    def test_get_tool_spec_cache(self, registry):
        """同一工具返回同一对象"""
        spec1 = registry.get_tool_spec("sys_info")
        spec2 = registry.get_tool_spec("sys_info")
        assert spec1 is spec2

    def test_get_tool_spec_unknown(self, registry):
        assert registry.get_tool_spec("unknown") is None

    def test_get_param_info(self, registry):
        info = registry.get_param_info("sys_info", "metric")
        assert info is not None
        assert info["type"] == "string"
        assert "cpu" in info["enum"]

    def test_get_param_info_unknown_tool(self, registry):
        assert registry.get_param_info("unknown", "x") is None

    def test_get_param_info_unknown_param(self, registry):
        assert registry.get_param_info("sys_info", "nonexistent") is None


# ── get_tool_server_id 测试 ──────────────────────────────────────────

class TestToolServerId:
    """工具服务器 ID 解析"""

    def test_returns_server_id(self, registry):
        sid = registry.get_tool_server_id("sys_info")
        assert sid == "test-server"

    def test_returns_empty_for_nonexistent(self, registry):
        sid = registry.get_tool_server_id("nonexistent")
        assert sid == ""


# ── count 测试 ───────────────────────────────────────────────────────

class TestCount:
    """count 方法测试"""

    def test_count_returns_mcp_tools(self, registry):
        assert registry.count() == 7

    def test_count_after_unregister(self, registry):
        registry.unregister_server("test-server")
        # unregister 标记 unavailable 但不删除定义
        # count() 只统计 source="mcp" 的工具（无论 status）
        assert registry.count() == 7


# ── 边界情况测试 ─────────────────────────────────────────────────────

class TestEdgeCases:
    """边界情况"""

    def test_get_tool_spec_after_unregister(self, registry):
        """注销后 get_tool_spec 仍可用"""
        registry.unregister_server("test-server")
        spec = registry.get_tool_spec("sys_info")
        assert spec is not None
        assert spec.name == "sys_info"

    def test_get_tool_unknown(self, registry):
        """get_tool 对未知工具返回 None"""
        assert registry.get_tool("unknown") is None

    def test_get_tool_info_after_risk_update(self, registry):
        """风险更新后 get_tool_info 反映新值"""
        registry.update_tool_risk("sys_info", default_risk="high")
        info = registry.get_tool_info("sys_info")
        assert info is not None
        assert info["risk_level"] == "high"

    def test_empty_registry(self):
        """空注册表（无任何 MCP 连接）"""
        reg = ToolRegistry()
        assert reg.get_tool_names() == []
        assert reg.count() == 0
        assert reg.get_openai_functions() == []
        assert reg.build_tool_prompt_section() == ""

    def test_save_config_noop(self):
        """save_config 应不抛异常"""
        reg = ToolRegistry()
        reg.save_config()  # should not raise