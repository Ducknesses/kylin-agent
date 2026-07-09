"""ToolRegistry —— MCP 工具注册表

职责：
  - 统一登记允许调用的 MCP 工具及其元信息
  - 判断工具是否存在
  - 返回工具元信息（描述、参数 schema、默认风险等级）
  - 校验工具参数的枚举范围
  - 不负责安全裁决（安全裁决归 SafetyGuard）
  - 不直接调用 MCPClient

与 mcp/tools.py 的关系：
  mcp/tools.py 的 TOOL_DEFINITIONS 是面向 LLM function calling 的 JSON Schema；
  ToolRegistry 是面向 Agent 编排的运行时注册表，额外包含风险等级和参数校验逻辑。
  两者暂时独立维护，后续可统一。
"""

import logging
from typing import Any

logger = logging.getLogger(__name__)


# ── 工具注册数据 ──────────────────────────────────────────────────────
# 每个工具条目包含：
#   description: 工具描述
#   risk_level: 默认风险等级（low / medium / high）
#   params: 参数定义 {参数名: {"type": ..., "required": bool, "enum": [...] | None, "constraints": dict | None}}

_TOOLS: dict[str, dict[str, Any]] = {
    # ── sys_info：系统信息查询 ──
    "sys_info": {
        "description": "获取系统信息（CPU、内存、磁盘、负载等）",
        "risk_level": "low",
        "params": {
            "metric": {
                "type": "string",
                "required": True,
                # 枚举允许值：比 mcp/tools.py 多了 network 和 uptime（mcp-server 已有）
                "enum": ["cpu", "memory", "disk", "load", "uptime", "all", "network"],
            },
        },
    },

    # ── service_mgr：服务管理 ──
    "service_mgr": {
        "description": "管理系统服务（systemctl 操作）",
        "risk_level": "low",  # 默认 low，具体按 action 动态判定
        "params": {
            "action": {
                "type": "string",
                "required": True,
                # 注意：不包含 enable/disable，与 mcp/tools.py 的 TOOL_DEFINITIONS 不同
                # enable/disable 由 SafetyGuard 在安全层统一拒绝
                "enum": ["status", "start", "stop", "restart", "is-active", "is-enabled"],
            },
            "service": {
                "type": "string",
                "required": True,
                # 服务名只做基本字符串校验，真正安全规则交给 SafetyGuard
            },
        },
        # 按 action 的风险映射
        "_action_risk": {
            "status": "low",
            "is-active": "low",
            "is-enabled": "low",
            "start": "medium",
            "stop": "medium",
            "restart": "medium",
        },
    },

    # ── log_reader：日志读取 ──
    "log_reader": {
        "description": "读取系统日志",
        "risk_level": "low",
        "params": {
            "type": {
                "type": "string",
                "required": False,
            },
            "source": {
                "type": "string",
                "required": False,
            },
            "service": {
                "type": "string",
                "required": False,
            },
            "lines": {
                "type": "integer",
                "required": False,
                # 行数限制 1-500，与 SafetyGuard._check_log_reader 保持一致
                "constraints": {"min": 1, "max": 500},
            },
            "since": {
                "type": "string",
                "required": False,
            },
            "keyword": {
                "type": "string",
                "required": False,
            },
        },
    },

    # ── net_monitor：网络监控 ──
    "net_monitor": {
        "description": "网络监控信息",
        "risk_level": "low",
        "params": {
            "metric": {
                "type": "string",
                "required": False,
                "enum": ["connections", "traffic", "interfaces", "routes", "dns", "listen", "all"],
            },
            "port": {
                "type": "integer",
                "required": False,
            },
        },
    },

    # ── cmd_exec：命令执行 ──
    "cmd_exec": {
        "description": "执行安全范围内的系统命令",
        "risk_level": "medium",
        "params": {
            "command": {
                "type": "string",
                "required": True,
                # ToolRegistry 只做存在性检查，不做命令黑名单裁决
            },
            "timeout": {
                "type": "integer",
                "required": False,
            },
            "user": {
                "type": "string",
                "required": False,
            },
        },
    },

    # ── file_guard：文件操作 ──
    "file_guard": {
        "description": "安全地操作文件（检查、读取、写入）",
        "risk_level": "medium",
        "params": {
            "action": {
                "type": "string",
                "required": True,
                "enum": ["check", "read", "write"],
            },
            "path": {
                "type": "string",
                "required": True,
                # ToolRegistry 只做参数结构校验，不做敏感路径裁决
            },
            "content": {
                "type": "string",
                "required": False,
            },
            "max_size": {
                "type": "integer",
                "required": False,
            },
        },
    },
}


class ToolRegistry:
    """MCP 工具注册表 —— 运行时查询工具元信息与参数校验

    使用方式：
        registry = ToolRegistry()
        if registry.exists("sys_info"):
            info = registry.get_tool_info("sys_info")
    """

    def exists(self, tool_name: str) -> bool:
        """判断工具是否已注册"""
        return tool_name in _TOOLS

    def get_tool_names(self) -> list[str]:
        """返回所有已注册工具名称"""
        return list(_TOOLS.keys())

    def get_tool_info(self, tool_name: str) -> dict[str, Any] | None:
        """返回工具的完整元信息，不存在时返回 None"""
        return _TOOLS.get(tool_name)

    def get_default_risk(self, tool_name: str) -> str | None:
        """返回工具的默认风险等级

        对于 service_mgr，会根据 action 参数动态返回风险等级。
        """
        tool = _TOOLS.get(tool_name)
        if tool is None:
            return None
        return tool.get("risk_level", "low")

    def get_risk_for_action(self, tool_name: str, action: str) -> str | None:
        """对于有 action 参数的工具，按 action 返回具体风险等级

        目前仅 service_mgr 支持 action 级别的风险映射。
        """
        tool = _TOOLS.get(tool_name)
        if tool is None:
            return None
        action_risk_map = tool.get("_action_risk", {})
        return action_risk_map.get(action, tool.get("risk_level", "low"))

    def get_param_info(self, tool_name: str, param_name: str) -> dict[str, Any] | None:
        """返回工具某个参数的元信息"""
        tool = _TOOLS.get(tool_name)
        if tool is None:
            return None
        return tool.get("params", {}).get(param_name)

    def validate_params(self, tool_name: str, params: dict) -> dict[str, Any]:
        """校验工具参数的枚举范围和基本约束

        返回:
            {"valid": bool, "errors": list[str]}

        注意：此方法只做结构/枚举校验，不做安全裁决。
        安全裁决（命令黑名单、敏感路径等）仍由 SafetyGuard 负责。
        """
        tool = _TOOLS.get(tool_name)
        if tool is None:
            return {"valid": False, "errors": [f"未知工具: {tool_name}"]}

        errors: list[str] = []
        param_defs: dict = tool.get("params", {})

        for pname, pdef in param_defs.items():
            required = pdef.get("required", False)
            has_value = pname in params and params[pname] is not None

            # 必填参数检查
            if required and not has_value:
                errors.append(f"缺少必填参数: {pname}")
                continue

            if not has_value:
                continue

            value = params[pname]

            # 枚举值校验
            allowed = pdef.get("enum")
            if allowed is not None and value not in allowed:
                errors.append(
                    f"参数 {pname} 值 '{value}' 不在允许范围内: {allowed}"
                )

            # 数值约束校验
            constraints = pdef.get("constraints")
            if constraints and isinstance(value, (int, float)):
                if "min" in constraints and value < constraints["min"]:
                    errors.append(
                        f"参数 {pname} 值 {value} 小于最小值 {constraints['min']}"
                    )
                if "max" in constraints and value > constraints["max"]:
                    errors.append(
                        f"参数 {pname} 值 {value} 大于最大值 {constraints['max']}"
                    )

        return {"valid": len(errors) == 0, "errors": errors}
