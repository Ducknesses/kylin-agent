"""ToolRegistry —— MCP 工具注册表（单一事实来源）

职责：
  - 统一登记允许调用的 MCP 工具及其全部静态元信息
  - 工具名称、参数 Schema、默认风险、action 风险覆盖
  - 审计安全字段和特殊摘要策略
  - 参数枚举校验和 required 检查
  - 不负责安全裁决（安全裁决归 SafetyGuard）
  - 不直接调用 MCPClient

与 mcp/tools.py 的关系：
  mcp/tools.py 的 TOOL_DEFINITIONS 是面向 LLM function calling 的 JSON Schema；
  ToolRegistry 是面向 Agent 编排的运行时注册表。
"""
import logging
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Mapping

from app.schemas.action import RiskLevel

logger = logging.getLogger(__name__)

# ── 结构化工具模型 ────────────────────────────────────────────────────

'''辅助函数'''
def _readonly_constraints(
    value: dict[str, int] | None,
) -> Mapping[str, int] | None:
    if value is None:
        return None

    return MappingProxyType(dict(value))

@dataclass(frozen=True)
class ToolParamSpec:
    """单个工具参数定义"""
    type: str                        # "string" | "integer" | "boolean"
    required: bool = False
    enum: tuple[str, ...] = ()
    description: str = ""
    constraints: Mapping[str, int] | None = None  # {"min": 1, "max": 500}


@dataclass(frozen=True)
class AuditPolicy:
    """审计字段白名单 + 可选摘要构造器"""
    safe_fields: tuple[str, ...] = ()
    summary_builder: str | None = None  # "cmd_exec_summary" 等


@dataclass(frozen=True)
class ToolSpec:
    """一个工具的完整静态定义（深度不可变）"""
    name: str
    description: str
    params: Mapping[str, ToolParamSpec]
    default_risk: RiskLevel
    action_field: str | None = None
    action_risk_overrides: Mapping[str, RiskLevel] = field(
        default_factory=lambda: MappingProxyType({})
    )
    audit_policy: AuditPolicy = field(default_factory=AuditPolicy)


# ── 工具定义 ──────────────────────────────────────────────────────────

_REGISTRY: dict[str, ToolSpec] = {}

def _register(spec: ToolSpec) -> None:
    if spec.name in _REGISTRY:
        raise ValueError(f"工具名重复: {spec.name}")
    _REGISTRY[spec.name] = spec


# sys_info
_register(ToolSpec(
    name="sys_info",
    description="获取系统信息（CPU、内存、磁盘、负载等）",
    params=MappingProxyType({
        "metric": ToolParamSpec(type="string", required=True,
                                 enum=("cpu", "memory", "disk", "load", "uptime", "all", "network")),
    }),
    default_risk="low",
    audit_policy=AuditPolicy(safe_fields=("metric",)),
))

# service_mgr
_register(ToolSpec(
    name="service_mgr",
    description="管理系统服务（systemctl 操作）",
    params=MappingProxyType({
        "action": ToolParamSpec(type="string", required=True,
                                 enum=("status", "start", "stop", "restart", "is-active", "is-enabled")),
        "service": ToolParamSpec(type="string", required=True),
    }),
    default_risk="low",
    action_field="action",
    action_risk_overrides=MappingProxyType({
        "status": "low", "is-active": "low", "is-enabled": "low",
        "start": "medium", "stop": "medium", "restart": "medium",
    }),
    audit_policy=AuditPolicy(safe_fields=("action", "service")),
))

# log_reader
_register(ToolSpec(
    name="log_reader",
    description="读取系统日志",
    params=MappingProxyType({
        "type": ToolParamSpec(type="string"),
        "source": ToolParamSpec(type="string"),
        "service": ToolParamSpec(type="string"),
        "lines": ToolParamSpec(type="integer", constraints=_readonly_constraints({"min": 1,"max": 500,})),
        "since": ToolParamSpec(type="string"),
        "keyword": ToolParamSpec(type="string"),
    }),
    default_risk="low",
    audit_policy=AuditPolicy(safe_fields=("service", "lines")),
))

# net_monitor
_register(ToolSpec(
    name="net_monitor",
    description="网络监控信息",
    params=MappingProxyType({
        "metric": ToolParamSpec(type="string",
                                 enum=("connections", "traffic", "interfaces", "routes", "dns", "listen", "all")),
        "port": ToolParamSpec(type="integer"),
    }),
    default_risk="low",
    audit_policy=AuditPolicy(safe_fields=("metric",)),
))

# cmd_exec
_register(ToolSpec(
    name="cmd_exec",
    description="执行安全范围内的系统命令",
    params=MappingProxyType({
        "command": ToolParamSpec(type="string", required=True),
        "timeout": ToolParamSpec(type="integer"),
        "user": ToolParamSpec(type="string"),
    }),
    default_risk="medium",
    audit_policy=AuditPolicy(safe_fields=(), summary_builder="cmd_exec_summary"),
))

# metrics_history
_register(ToolSpec(
    name="metrics_history",
    description="查询系统历史指标数据（CPU、内存、磁盘、网络），按时间范围返回历史读数。用于分析系统负载趋势、排查历史性能问题。",
    params=MappingProxyType({
        "from_ts": ToolParamSpec(type="number", description="开始时间戳（Unix秒），默认5分钟前"),
        "to_ts": ToolParamSpec(type="number", description="结束时间戳（Unix秒），默认当前时间"),
        "metrics": ToolParamSpec(type="string", description="逗号分隔的指标名: cpu,memory,disk,network,all"),
        "limit": ToolParamSpec(type="integer", constraints=_readonly_constraints({"min": 1, "max": 10000})),
    }),
    default_risk="low",
    audit_policy=AuditPolicy(safe_fields=("from_ts", "to_ts", "metrics")),
))

# file_guard
_register(ToolSpec(
    name="file_guard",
    description="安全地操作文件（检查、读取、写入）",
    params=MappingProxyType({
        "action": ToolParamSpec(type="string", required=True,
                                 enum=("check", "read", "write")),
        "path": ToolParamSpec(type="string", required=True),
        "content": ToolParamSpec(type="string"),
        "max_size": ToolParamSpec(type="integer"),
    }),
    default_risk="medium",
    action_field="action",
    action_risk_overrides=MappingProxyType({"check": "low", "read": "low", "write": "medium"}),
    audit_policy=AuditPolicy(safe_fields=("action", "path")),
))


# ── 启动时完整性校验 ──────────────────────────────────────────────────

def _cmd_exec_summary(params: Mapping[str, object]) -> dict[str, object]:
    """cmd_exec 安全摘要——不记录完整命令"""
    from app.services.audit_service import sanitize_sensitive_data
    cmd = str(params.get("command", ""))
    if not cmd:
        return {"command": "[empty]"}
    parts = cmd.split()
    return sanitize_sensitive_data({  # type: ignore[return-value]
        "command_name": parts[0][:50] if parts else "",
        "argument_count": len(parts) - 1 if len(parts) > 1 else 0,
        "contains_pipe": "|" in cmd,
        "contains_redirect": ">" in cmd,
        "contains_shell_chain": any(s in cmd for s in ("&&", "||", ";")),
    })


_SUMMARY_BUILDERS = {
    "cmd_exec_summary": _cmd_exec_summary,
}
_KNOWN_KEYS = set(_REGISTRY.keys())
try:
    for name, spec in _REGISTRY.items():
        # audit_policy
        if not spec.audit_policy.safe_fields and spec.audit_policy.summary_builder is None:
            raise ValueError(f"工具 '{name}' 缺少 AuditPolicy")
        for sf in spec.audit_policy.safe_fields:
            if sf not in spec.params:
                raise ValueError(f"工具 '{name}' audit safe_field '{sf}' 不在 params 中")
        if spec.audit_policy.summary_builder and spec.audit_policy.summary_builder not in _SUMMARY_BUILDERS:
            raise ValueError(f"工具 '{name}' summary_builder '{spec.audit_policy.summary_builder}' 未注册")
        # action_field
        if spec.action_field and spec.action_field not in spec.params:
            raise ValueError(f"工具 '{name}' action_field '{spec.action_field}' 不在 params 中")
        # action_risk_overrides
        if spec.action_field:
            action_param = spec.params[spec.action_field]
            for av in spec.action_risk_overrides:
                if av not in action_param.enum:
                    raise ValueError(f"工具 '{name}' action override '{av}' 不在 action enum 中")
        if spec.default_risk not in ("low", "medium", "high"):
            raise ValueError(f"工具 '{name}' default_risk 非法: {spec.default_risk}")
except ValueError as e:
    logger.critical("ToolRegistry 完整性校验失败: %s", e)
    raise


# ── 特殊摘要构造器 ────────────────────────────────────────────────────



# ═══════════════════════════════════════════════════════════════════════
# ToolRegistry
# ═══════════════════════════════════════════════════════════════════════

class ToolRegistry:
    """MCP 工具注册表 —— 运行时查询工具元信息与参数校验"""

    def exists(self, tool_name: str) -> bool:
        return tool_name in _REGISTRY

    def get_tool_names(self) -> list[str]:
        return list(_REGISTRY.keys())

    def get_tool_spec(self, tool_name: str) -> ToolSpec | None:
        """返回工具规格（不可变副本）"""
        return _REGISTRY.get(tool_name)

    def get_tool_info(self, tool_name: str) -> dict[str, object] | None:
        """返回工具元信息（兼容旧版 dict 接口）"""
        spec = _REGISTRY.get(tool_name)
        if spec is None:
            return None
        return {
            "name": spec.name,
            "description": spec.description,
            "risk_level": spec.default_risk,
            "params": {k: {"type": v.type, "required": v.required, "enum": list(v.enum) or None, "constraints": dict(v.constraints) if v.constraints else None}
                       for k, v in spec.params.items()},
        }

    def get_default_risk(self, tool_name: str) -> str | None:
        spec = _REGISTRY.get(tool_name)
        return spec.default_risk if spec else None

    def get_risk_for_action(self, tool_name: str, action: Any) -> str | None:
        """对带 action 参数的工具，按 action 返回具体风险"""
        spec = _REGISTRY.get(tool_name)
        if spec is None:
            return None
        if isinstance(action, str) and spec.action_field:
            return spec.action_risk_overrides.get(action, spec.default_risk)
        return spec.default_risk

    def get_param_info(self, tool_name: str, param_name: str) -> dict[str, Any] | None:
        spec = _REGISTRY.get(tool_name)
        if spec is None:
            return None
        p = spec.params.get(param_name)
        if p is None:
            return None
        return {"type": p.type, "required": p.required, "enum": list(p.enum) or None, "constraints": dict(p.constraints) if p.constraints else None}

    def validate_params(self, tool_name: str, params: dict) -> dict[str, Any]:
        """校验参数枚举和约束，返回 {"valid": bool, "errors": list[str]}"""
        spec = _REGISTRY.get(tool_name)
        if spec is None:
            return {"valid": False, "errors": [f"未知工具: {tool_name}"]}

        errors: list[str] = []
        for pname, pdef in spec.params.items():
            has_value = pname in params and params[pname] is not None
            if pdef.required and not has_value:
                errors.append(f"缺少必填参数: {pname}")
                continue
            if not has_value:
                continue
            value = params[pname]
            if pdef.enum and value not in pdef.enum:
                errors.append(f"参数 {pname} 值 '{value}' 不在允许范围内: {list(pdef.enum)}")
            if pdef.constraints and isinstance(value, (int, float)):
                c = pdef.constraints
                if "min" in c and value < c["min"]:
                    errors.append(f"参数 {pname} 值 {value} 小于最小值 {c['min']}")
                if "max" in c and value > c["max"]:
                    errors.append(f"参数 {pname} 值 {value} 大于最大值 {c['max']}")
        return {"valid": len(errors) == 0, "errors": errors}

    def get_audit_policy(self, tool_name: str) -> AuditPolicy | None:
        spec = _REGISTRY.get(tool_name)
        return spec.audit_policy if spec else None

    def build_audit_metadata(self, tool_name: str, params: Mapping[str, object]) -> dict[str, object]:
        """构建审计安全元数据——不记录完整 params"""
        from app.services.audit_service import sanitize_sensitive_data

        spec = _REGISTRY.get(tool_name)
        if spec is None:
            logger.warning("工具 %s 未注册，审计元数据为空", tool_name)
            return {}

        meta: dict[str, object] = {}
        # 安全字段
        for sf in spec.audit_policy.safe_fields:
            if sf in params:
                meta[sf] = params[sf]
        # 特殊摘要
        if spec.audit_policy.summary_builder:
            builder = _SUMMARY_BUILDERS.get(spec.audit_policy.summary_builder)
            if builder:
                meta.update(builder(params))

        return sanitize_sensitive_data(meta)  # type: ignore[return-value]
