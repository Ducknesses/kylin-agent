"""
ToolRegistry —— MCP 工具注册表（单一事实来源）

职责：
  - 从 JSON 配置文件加载工具定义（name、Schema、风险等级、action 覆盖）
  - 统一登记允许调用的 MCP 工具及其全部元信息
  - 参数枚举校验和 required 检查
  - 管理从 MCP 服务器动态发现的所有工具
  - 动态构建 OpenAI function calling 格式（LLM 每次调用时实时查询）
  - 不负责安全裁决（安全裁决归 SafetyGuard）
  - 不直接调用 MCPClient

工具风险等级可由用户通过 API 修改，修改后持久化到配置文件。
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Dict, List, Literal, Mapping, Optional

from app.mcp.client import MCPTool
from app.schemas.action import RiskLevel

logger = logging.getLogger(__name__)

# ── 默认配置文件路径 ──────────────────────────────────────────────────

_DEFAULT_CONFIG_PATH = os.path.join(os.path.dirname(__file__), "..", "..", "data", "tool_definitions.json")


# ── 结构化工具模型 ────────────────────────────────────────────────────


def _readonly_constraints(
    value: dict[str, int] | None,
) -> Mapping[str, int] | None:
    if value is None:
        return None
    return MappingProxyType(dict(value))


@dataclass(frozen=True)
class ToolParamSpec:
    """单个工具参数定义"""
    type: str                        # "string" | "integer" | "boolean" | "number"
    required: bool = False
    enum: tuple[str, ...] = ()
    description: str = ""
    constraints: Mapping[str, int] | None = None  # {"min": 1, "max": 500}


@dataclass(frozen=True)
class AuditPolicy:
    """工具的审计策略配置

    三种模式：
      - whitelist: 只保留 safe_fields 中的字段，其余丢弃
      - summary:   调用 summary_builder 函数生成安全摘要，不记录原始参数
      - full:      完整保留所有参数，只做敏感 key 脱敏（默认/降级模式）
    """
    mode: str = "full"                       # "whitelist" | "summary" | "full"
    safe_fields: tuple[str, ...] = ()        # whitelist 模式下的安全字段
    summary_builder: str | None = None       # summary 模式的摘要构造器名

    def to_dict(self) -> dict[str, object]:
        return {
            "mode": self.mode,
            "safe_fields": list(self.safe_fields),
            "summary_builder": self.summary_builder,
        }


@dataclass(frozen=True)
class ToolSpec:
    """一个工具的完整定义（不可变）

    保留此类以兼容现有测试和调用方（agent_harness, action_service 等）。
    内部由 UnifiedToolDefinition 构建而来。
    """
    name: str
    description: str
    params: Mapping[str, ToolParamSpec]
    default_risk: RiskLevel
    action_field: str | None = None
    action_risk_overrides: Mapping[str, RiskLevel] = field(
        default_factory=lambda: MappingProxyType({})
    )
    audit_policy: AuditPolicy = field(default_factory=AuditPolicy)

    def to_dict(self) -> dict[str, object]:
        """序列化为 JSON 兼容的 dict（用于保存到配置文件）"""
        params_dict: dict[str, object] = {}
        for pname, pdef in self.params.items():
            pd: dict[str, object] = {
                "type": pdef.type,
                "required": pdef.required,
            }
            if pdef.enum:
                pd["enum"] = list(pdef.enum)
            if pdef.description:
                pd["description"] = pdef.description
            if pdef.constraints:
                pd["constraints"] = dict(pdef.constraints)
            params_dict[pname] = pd

        return {
            "name": self.name,
            "description": self.description,
            "default_risk": self.default_risk,
            "action_field": self.action_field,
            "action_risk_overrides": dict(self.action_risk_overrides),
            "audit_policy": self.audit_policy.to_dict(),
            "params": params_dict,
        }


# ── 新增：统一工具定义模型 ────────────────────────────────────────────


@dataclass
class UnifiedToolDefinition:
    """统一的工具定义（合并 ToolSpec + MCPTool 元信息）

    这是 Phase 1 的核心数据结构，替代原有的 _registry + _tools 双存储。
    同时支持静态配置文件定义和 MCP 动态发现。
    """
    name: str
    description: str
    params: Mapping[str, ToolParamSpec]
    default_risk: RiskLevel                          # low / medium / high
    action_field: str | None = None
    action_risk_overrides: Mapping[str, RiskLevel] = field(
        default_factory=lambda: MappingProxyType({})
    )
    audit_policy: AuditPolicy = field(default_factory=AuditPolicy)
    source: Literal["static", "mcp"] = "static"      # 工具来源
    server_id: str = ""                               # MCP 服务器 ID（动态工具）
    status: Literal["available", "unavailable"] = "available"
    # MCP 原始参数定义（仅动态工具，用于 OpenAI function calling 补充）
    mcp_parameters: Dict[str, Any] = field(default_factory=dict)
    mcp_required: List[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        """序列化为 API 响应格式（含 source / server_id / status）"""
        params_dict: dict[str, object] = {}
        for pname, pdef in self.params.items():
            pd: dict[str, object] = {
                "type": pdef.type,
                "required": pdef.required,
            }
            if pdef.enum:
                pd["enum"] = list(pdef.enum)
            if pdef.description:
                pd["description"] = pdef.description
            if pdef.constraints:
                pd["constraints"] = dict(pdef.constraints)
            params_dict[pname] = pd

        return {
            "name": self.name,
            "description": self.description,
            "default_risk": self.default_risk,
            "action_field": self.action_field,
            "action_risk_overrides": dict(self.action_risk_overrides),
            "audit_policy": self.audit_policy.to_dict(),
            "params": params_dict,
            "source": self.source,
            "server_id": self.server_id,
            "status": self.status,
        }

    def to_tool_spec(self) -> ToolSpec:
        """转换为 ToolSpec（向后兼容）"""
        return ToolSpec(
            name=self.name,
            description=self.description,
            params=self.params,
            default_risk=self.default_risk,
            action_field=self.action_field,
            action_risk_overrides=self.action_risk_overrides,
            audit_policy=self.audit_policy,
        )

    @classmethod
    def from_tool_spec(
        cls,
        spec: ToolSpec,
        source: Literal["static", "mcp"] = "static",
        server_id: str = "",
        status: Literal["available", "unavailable"] = "available",
    ) -> "UnifiedToolDefinition":
        """从 ToolSpec 创建 UnifiedToolDefinition"""
        return cls(
            name=spec.name,
            description=spec.description,
            params=spec.params,
            default_risk=spec.default_risk,
            action_field=spec.action_field,
            action_risk_overrides=spec.action_risk_overrides,
            audit_policy=spec.audit_policy,
            source=source,
            server_id=server_id,
            status=status,
        )

    @classmethod
    def from_mcp_tool(
        cls,
        tool: MCPTool,
        overrides: Optional[dict[str, object]] = None,
    ) -> "UnifiedToolDefinition":
        """从 MCPTool 创建 UnifiedToolDefinition（动态工具）

        如果 overrides 中包含来自 JSON 的预定义元信息（risk/audit），则合并使用。
        否则使用默认值 low/full。
        """
        overrides = overrides or {}
        default_risk = str(overrides.get("default_risk", "low"))
        audit_policy = AuditPolicy()
        ap_raw = overrides.get("audit_policy")
        if isinstance(ap_raw, dict):
            audit_policy = AuditPolicy(
                mode=str(ap_raw.get("mode", "full")),
                safe_fields=tuple(ap_raw.get("safe_fields", []) if isinstance(ap_raw.get("safe_fields"), list) else []),
                summary_builder=ap_raw.get("summary_builder"),
            )

        # 从 MCPTool.parameters 构建 ToolParamSpec
        params: dict[str, ToolParamSpec] = {}
        for pname, pdef in tool.parameters.items():
            if not isinstance(pdef, dict):
                continue
            penum_raw = pdef.get("enum")
            penum: tuple[str, ...] = ()
            if isinstance(penum_raw, list):
                penum = tuple(str(e) for e in penum_raw)
            pconstraints_raw = pdef.get("constraints")
            pconstraints = None
            if isinstance(pconstraints_raw, dict):
                pconstraints = {str(k): int(v) for k, v in pconstraints_raw.items()}
            params[pname] = ToolParamSpec(
                type=str(pdef.get("type", "string")),
                required=pname in tool.required,
                enum=penum,
                description=str(pdef.get("description", "")),
                constraints=_readonly_constraints(pconstraints) if pconstraints else None,
            )

        return cls(
            name=tool.name,
            description=tool.description,
            params=MappingProxyType(params),
            default_risk=default_risk,  # type: ignore[arg-type]
            audit_policy=audit_policy,
            source="mcp",
            server_id=tool.server_id or "",
            status="available",
            mcp_parameters=tool.parameters,
            mcp_required=tool.required,
        )


# ── JSON 配置文件加载 ──────────────────────────────────────────────────


def _load_from_json(config_path: str) -> dict[str, ToolSpec]:
    """从 JSON 配置文件加载工具定义

    Returns:
        {tool_name: ToolSpec} 字典
    """
    if not os.path.exists(config_path):
        logger.warning("工具定义配置文件不存在: %s，返回空注册表", config_path)
        return {}

    with open(config_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    tools_data = data.get("tools", {})
    if not isinstance(tools_data, dict):
        logger.error("配置文件 tools 字段格式错误，期望 dict")
        return {}

    registry: dict[str, ToolSpec] = {}
    for tool_name, tool_data in tools_data.items():
        try:
            spec = _parse_tool_spec(tool_name, tool_data)
            registry[tool_name] = spec
        except (KeyError, TypeError, ValueError) as e:
            logger.warning("跳过无效工具定义 '%s': %s", tool_name, e)

    # 启动时完整性校验
    _validate_registry(registry)

    logger.info("从配置文件加载了 %d 个工具定义", len(registry))
    return registry


def _parse_tool_spec(name: str, data: dict[str, object]) -> ToolSpec:
    """解析单个工具定义"""
    description = str(data.get("description", ""))
    default_risk = str(data.get("default_risk", "low"))
    if default_risk not in ("low", "medium", "high"):
        raise ValueError(f"无效的 default_risk: {default_risk}")

    action_field = data.get("action_field")
    if action_field is not None:
        action_field = str(action_field)
        if not action_field:
            action_field = None

    action_overrides_raw = data.get("action_risk_overrides", {})
    action_risk_overrides: dict[str, RiskLevel] = {}
    if isinstance(action_overrides_raw, dict):
        for k, v in action_overrides_raw.items():
            v_str = str(v)
            if v_str in ("low", "medium", "high"):
                action_risk_overrides[k] = v_str  # type: ignore[assignment]
            else:
                logger.warning("工具 '%s' action_risk_overrides 中 '%s' 无效: %s", name, k, v)

    params_dict: dict[str, ToolParamSpec] = {}
    params_raw = data.get("params", {})
    if isinstance(params_raw, dict):
        for pname, pdata in params_raw.items():
            if not isinstance(pdata, dict):
                continue
            ptype = str(pdata.get("type", "string"))
            prequired = bool(pdata.get("required", False))
            penum_raw = pdata.get("enum")
            penum: tuple[str, ...] = ()
            if isinstance(penum_raw, list):
                penum = tuple(str(e) for e in penum_raw)
            pdesc = str(pdata.get("description", ""))
            pconstraints_raw = pdata.get("constraints")
            pconstraints = None
            if isinstance(pconstraints_raw, dict):
                pconstraints = {str(k): int(v) for k, v in pconstraints_raw.items()}
            params_dict[pname] = ToolParamSpec(
                type=ptype,
                required=prequired,
                enum=penum,
                description=pdesc,
                constraints=_readonly_constraints(pconstraints) if pconstraints else None,
            )

    # 解析 audit_policy
    audit_policy = AuditPolicy()  # 默认 full
    ap_raw = data.get("audit_policy")
    if isinstance(ap_raw, dict):
        mode = str(ap_raw.get("mode", "full"))
        if mode not in ("whitelist", "summary", "full"):
            logger.warning("工具 '%s' audit_policy mode 无效: %s，回退 full", name, mode)
            mode = "full"
        safe_fields_raw = ap_raw.get("safe_fields", [])
        safe_fields: tuple[str, ...] = ()
        if isinstance(safe_fields_raw, list):
            safe_fields = tuple(str(sf) for sf in safe_fields_raw)
        summary_builder = ap_raw.get("summary_builder")
        if summary_builder is not None:
            summary_builder = str(summary_builder)
            if not summary_builder:
                summary_builder = None
        audit_policy = AuditPolicy(
            mode=mode,
            safe_fields=safe_fields,
            summary_builder=summary_builder,
        )

    return ToolSpec(
        name=name,
        description=description,
        params=MappingProxyType(params_dict),
        default_risk=default_risk,  # type: ignore[arg-type]
        action_field=action_field,
        action_risk_overrides=MappingProxyType(action_risk_overrides),
        audit_policy=audit_policy,
    )


def _validate_registry(registry: dict[str, ToolSpec]) -> None:
    """启动时校验注册表完整性"""
    for name, spec in registry.items():
        # action_field 校验
        if spec.action_field and spec.action_field not in spec.params:
            raise ValueError(f"工具 '{name}' action_field '{spec.action_field}' 不在 params 中")
        # action_risk_overrides 校验
        if spec.action_field:
            action_param = spec.params[spec.action_field]
            for av in spec.action_risk_overrides:
                if av not in action_param.enum:
                    raise ValueError(f"工具 '{name}' action override '{av}' 不在 action enum 中")
        if spec.default_risk not in ("low", "medium", "high"):
            raise ValueError(f"工具 '{name}' default_risk 非法: {spec.default_risk}")


# ── cmd_exec 摘要构造器（保留，后续审计系统重构时对接）─────────────────


def build_cmd_exec_summary(params: Mapping[str, object]) -> dict[str, object]:
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


# ═══════════════════════════════════════════════════════════════════════
# ToolRegistry
# ═══════════════════════════════════════════════════════════════════════

class ToolRegistry:
    """
    MCP 工具注册表 —— 运行时查询工具元信息与参数校验

    Phase 1 重构：统一 _registry + _tools 双存储为单一 _definitions 字典。
    每个条目都是 UnifiedToolDefinition，包含 source / server_id / status。

    使用方式：
        registry = ToolRegistry()
        # 由 MCPServerManager 调用
        registry.register_from_server("kylin-main", tools)
        # LLM 调用时
        functions = registry.get_openai_functions()
        # 用户修改风险等级
        registry.update_tool_risk("cmd_exec", default_risk="low")
        registry.save_config()
    """

    def __init__(self, config_path: str | None = None):
        """
        参数:
            config_path: 工具定义 JSON 配置文件路径，默认 data/tool_definitions.json
        """
        # 统一工具定义存储 {tool_name: UnifiedToolDefinition}
        self._definitions: Dict[str, UnifiedToolDefinition] = {}
        # ToolSpec 缓存（向后兼容：保持 get_tool_spec 返回同一对象）
        self._spec_cache: Dict[str, ToolSpec] = {}
        # 配置文件路径
        self._config_path: str = config_path or _DEFAULT_CONFIG_PATH
        # {server_id: [tool_name, ...]}
        self._tools_by_server: Dict[str, List[str]] = {}

        # 从配置文件加载
        self._load_config()

    # ========================================================================
    # 配置文件管理
    # ========================================================================

    def _load_config(self) -> None:
        """从 JSON 配置文件加载工具定义到统一存储"""
        try:
            raw_specs = _load_from_json(self._config_path)
        except Exception:
            logger.exception("加载工具定义配置文件失败")
            raw_specs = {}

        new_defs: Dict[str, UnifiedToolDefinition] = {}
        for name, spec in raw_specs.items():
            # 保留已有的动态工具元信息（如果存在）
            existing = self._definitions.get(name)
            if existing is not None and existing.source == "mcp":
                # 动态工具存在同名的静态定义：用静态定义覆盖 risk/audit，但保留 mcp 来源信息
                new_defs[name] = UnifiedToolDefinition(
                    name=spec.name,
                    description=spec.description,
                    params=spec.params,
                    default_risk=spec.default_risk,
                    action_field=spec.action_field,
                    action_risk_overrides=spec.action_risk_overrides,
                    audit_policy=spec.audit_policy,
                    source=existing.source,
                    server_id=existing.server_id,
                    status=existing.status,
                    mcp_parameters=existing.mcp_parameters,
                    mcp_required=existing.mcp_required,
                )
                # 更新 spec 缓存
                self._spec_cache[name] = spec
            else:
                new_defs[name] = UnifiedToolDefinition.from_tool_spec(spec)
                self._spec_cache[name] = spec

        self._definitions = new_defs
        logger.info("从配置文件加载了 %d 个工具定义到统一注册表", len(self._definitions))

    def reload(self) -> None:
        """重新加载配置文件（热加载）"""
        self._load_config()
        logger.info("工具定义已重新加载，当前 %d 个工具", len(self._definitions))

    def save_config(self) -> None:
        """将当前注册表持久化到 JSON 配置文件

        只持久化静态工具和已配置 audit/risk 的动态工具。
        """
        try:
            tools_dict: dict[str, object] = {}
            for name, defn in self._definitions.items():
                tools_dict[name] = defn.to_dict()

            data: dict[str, object] = {
                "version": 1,
                "tools": tools_dict,
            }

            # 确保目录存在
            config_dir = os.path.dirname(self._config_path)
            if config_dir:
                os.makedirs(config_dir, exist_ok=True)

            with open(self._config_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
                f.write("\n")

            logger.info("工具定义已保存到 %s", self._config_path)
        except Exception:
            logger.exception("保存工具定义配置文件失败")

    def update_tool_risk(
        self,
        tool_name: str,
        default_risk: Optional[str] = None,
        action_risk_overrides: Optional[Dict[str, str]] = None,
    ) -> bool:
        """更新工具的风险等级（内存中），需调用 save_config 持久化

        现在同时支持静态工具和动态 MCP 工具。

        Args:
            tool_name: 工具名称
            default_risk: 新的默认风险等级 ("low" / "medium" / "high")
            action_risk_overrides: 新的 action 风险覆盖 {action: risk}

        Returns:
            True 更新成功，False 工具不存在或参数无效
        """
        defn = self._definitions.get(tool_name)
        if defn is None:
            logger.warning("更新风险失败: 工具 '%s' 不在注册表中", tool_name)
            return False

        new_default = defn.default_risk
        new_overrides = dict(defn.action_risk_overrides)

        if default_risk is not None:
            if default_risk not in ("low", "medium", "high"):
                logger.warning("无效的 default_risk: %s", default_risk)
                return False
            new_default = default_risk  # type: ignore[assignment]

        if action_risk_overrides is not None:
            for action, risk in action_risk_overrides.items():
                if risk not in ("low", "medium", "high"):
                    logger.warning("无效的 action risk '%s': %s", action, risk)
                    return False
                # 如果有 action_field，校验 action 是否在 enum 中
                if defn.action_field:
                    action_param = defn.params.get(defn.action_field)
                    if action_param and action_param.enum and action not in action_param.enum:
                        logger.warning(
                            "action '%s' 不在工具 '%s' 允许的 action enum 中: %s",
                            action, tool_name, list(action_param.enum),
                        )
                        return False
                new_overrides[action] = risk  # type: ignore[assignment]

        # 更新统一定义
        self._definitions[tool_name] = UnifiedToolDefinition(
            name=defn.name,
            description=defn.description,
            params=defn.params,
            default_risk=new_default,
            action_field=defn.action_field,
            action_risk_overrides=MappingProxyType(new_overrides),
            audit_policy=defn.audit_policy,
            source=defn.source,
            server_id=defn.server_id,
            status=defn.status,
            mcp_parameters=defn.mcp_parameters,
            mcp_required=defn.mcp_required,
        )
        logger.info(
            "工具 '%s' 风险等级已更新: default_risk=%s, overrides=%s",
            tool_name, new_default, new_overrides,
        )
        return True

    def get_all_tool_definitions(self) -> List[Dict[str, object]]:
        """获取所有工具定义的完整信息（静态 + 动态，供前端展示/修改）

        Phase 2.1: 现在返回 _definitions 中的全部工具，包含 source / server_id / status。
        """
        result: List[Dict[str, object]] = []
        for defn in self._definitions.values():
            result.append(defn.to_dict())
        return result

    def get_tool_definition(self, tool_name: str) -> Optional[Dict[str, object]]:
        """获取单个工具定义"""
        defn = self._definitions.get(tool_name)
        if defn is None:
            return None
        return defn.to_dict()

    # ========================================================================
    # 动态注册 / 注销
    # ========================================================================

    def register_from_server(self, server_id: str, tools: List[MCPTool]) -> None:
        """从 MCP 服务器注册（或刷新）工具列表

        Phase 1.2: 合并到统一 _definitions 存储。
        如果 JSON 中已有同名工具的元信息定义，合并（使用 JSON 中的 risk/audit 覆盖默认值）；
        如果没有，用默认 low/full 创建。
        """
        # 先清除该服务器的旧工具
        old_names = self._tools_by_server.pop(server_id, [])
        for name in old_names:
            if name in self._definitions and self._definitions[name].source == "mcp":
                self._definitions.pop(name, None)

        # 注册新工具
        names = []
        for tool in tools:
            # 处理工具名冲突：追加 server_id 后缀
            unique_name = tool.name
            if unique_name in self._definitions:
                unique_name = f"{tool.name}__{server_id}"
                tool = MCPTool(
                    name=unique_name,
                    description=f"[{tool.server_name or server_id}] {tool.description}",
                    parameters=tool.parameters,
                    required=tool.required,
                    server_id=server_id,
                    server_name=tool.server_name,
                )

            # 检查 JSON 中是否有同名工具的预定义元信息（历史持久化配置）
            existing_defn = self._definitions.get(unique_name)
            overrides = {}
            if existing_defn is not None and existing_defn.source == "static":
                # 静态定义优先级最高：使用 JSON 配置的 risk/audit
                overrides = {
                    "default_risk": existing_defn.default_risk,
                    "audit_policy": existing_defn.audit_policy,
                    "action_risk_overrides": dict(existing_defn.action_risk_overrides),
                    "action_field": existing_defn.action_field,
                }
            elif tool.meta:
                # 第二优先级：MCP 服务器提供的 _meta 元信息
                meta_overrides: dict[str, object] = {}
                if "suggested_risk" in tool.meta:
                    meta_overrides["default_risk"] = tool.meta["suggested_risk"]
                if "action_risk_overrides" in tool.meta:
                    meta_overrides["action_risk_overrides"] = tool.meta["action_risk_overrides"]
                if meta_overrides:
                    overrides = meta_overrides

            defn = UnifiedToolDefinition.from_mcp_tool(tool, overrides=overrides if overrides else None)
            self._definitions[unique_name] = defn
            names.append(unique_name)

        self._tools_by_server[server_id] = names
        logger.info(
            f"[ToolRegistry] 服务器 {server_id} 注册了 {len(tools)} 个工具"
        )

    def unregister_server(self, server_id: str) -> None:
        """注销服务器的所有工具

        Phase 1.2: 标记工具为 unavailable 而非删除，保留用户配置。
        """
        tool_names = self._tools_by_server.pop(server_id, [])
        for name in tool_names:
            defn = self._definitions.get(name)
            if defn is not None and defn.source == "mcp":
                # 标记为 unavailable 而非删除
                self._definitions[name] = UnifiedToolDefinition(
                    name=defn.name,
                    description=defn.description,
                    params=defn.params,
                    default_risk=defn.default_risk,
                    action_field=defn.action_field,
                    action_risk_overrides=defn.action_risk_overrides,
                    audit_policy=defn.audit_policy,
                    source=defn.source,
                    server_id=defn.server_id,
                    status="unavailable",
                    mcp_parameters=defn.mcp_parameters,
                    mcp_required=defn.mcp_required,
                )

        if tool_names:
            logger.info(
                f"[ToolRegistry] 服务器 {server_id} 注销了 {len(tool_names)} 个工具"
            )

    # ========================================================================
    # 查询
    # ========================================================================

    def exists(self, tool_name: str) -> bool:
        """判断工具是否存在"""
        return tool_name in self._definitions

    def get_tool(self, tool_name: str) -> Optional[MCPTool]:
        """根据名称获取动态 MCP 工具（重建 MCPTool）"""
        defn = self._definitions.get(tool_name)
        if defn is None:
            return None
        if defn.source == "mcp":
            return MCPTool(
                name=defn.name,
                description=defn.description,
                parameters=defn.mcp_parameters,
                required=defn.mcp_required,
                server_id=defn.server_id,
            )
        return None

    def get_tools_for_server(self, server_id: str) -> List[MCPTool]:
        """获取指定服务器的所有工具"""
        tool_names = self._tools_by_server.get(server_id, [])
        result: List[MCPTool] = []
        for name in tool_names:
            defn = self._definitions.get(name)
            if defn is not None:
                result.append(MCPTool(
                    name=defn.name,
                    description=defn.description,
                    parameters=defn.mcp_parameters,
                    required=defn.mcp_required,
                    server_id=defn.server_id,
                ))
        return result

    def list_all(self) -> List[MCPTool]:
        """列出所有已注册动态工具"""
        result: List[MCPTool] = []
        for defn in self._definitions.values():
            if defn.source == "mcp":
                result.append(MCPTool(
                    name=defn.name,
                    description=defn.description,
                    parameters=defn.mcp_parameters,
                    required=defn.mcp_required,
                    server_id=defn.server_id,
                ))
        return result

    def count(self) -> int:
        """已注册动态工具总数"""
        return sum(1 for d in self._definitions.values() if d.source == "mcp")

    def get_tool_names(self) -> List[str]:
        """获取所有工具名称列表"""
        return list(self._definitions.keys())

    def get_tool_spec(self, tool_name: str) -> Optional[ToolSpec]:
        """返回工具规格（向后兼容，缓存转换结果保证对象同一性）"""
        defn = self._definitions.get(tool_name)
        if defn is None:
            return None
        # 缓存：同一工具名总是返回同一个 ToolSpec 对象
        if tool_name not in self._spec_cache:
            self._spec_cache[tool_name] = defn.to_tool_spec()
        return self._spec_cache[tool_name]

    def get_tool_info(self, tool_name: str) -> Optional[Dict[str, object]]:
        """返回工具元信息（兼容旧版 dict 接口）"""
        defn = self._definitions.get(tool_name)
        if defn is None:
            return None

        params_info: dict[str, object] = {}
        for k, v in defn.params.items():
            params_info[k] = {
                "type": v.type,
                "required": v.required,
                "enum": list(v.enum) or None,
                "constraints": dict(v.constraints) if v.constraints else None,
            }

        return {
            "name": defn.name,
            "description": defn.description,
            "risk_level": defn.default_risk,
            "params": params_info,
        }

    def get_param_info(self, tool_name: str, param_name: str) -> Optional[Dict[str, Any]]:
        """获取工具参数的详细信息"""
        defn = self._definitions.get(tool_name)
        if defn is None:
            return None
        p = defn.params.get(param_name)
        if p is None:
            return None
        return {
            "type": p.type,
            "required": p.required,
            "enum": list(p.enum) or None,
            "constraints": dict(p.constraints) if p.constraints else None,
        }

    def get_tool_server_id(self, tool_name: str) -> str:
        """获取工具所属 MCP 服务器的 server_id

        查找顺序：
        1. 统一定义中取 server_id
        2. 回退：查所有已注册服务器中是否有匹配的工具名
        3. 返回空字符串（调用方自行处理降级）
        """
        defn = self._definitions.get(tool_name)
        if defn is not None and defn.server_id:
            return defn.server_id

        # 回退：遍历所有服务器找匹配工具
        for sid, tool_names in self._tools_by_server.items():
            for name in tool_names:
                if name == tool_name or name.startswith(f"{tool_name}__"):
                    return sid

        return ""

    # ========================================================================
    # LLM 集成
    # ========================================================================

    def get_openai_functions(self) -> List[Dict[str, Any]]:
        """
        动态构建 OpenAI function calling 格式的工具列表

        LLM 每次调用时实时查询，确保工具列表始终是最新的。
        从统一定义构建，只包含 available 状态的工具。
        """
        functions = []
        for defn in self._definitions.values():
            # 跳过不可用的动态工具
            if defn.source == "mcp" and defn.status == "unavailable":
                continue

            properties = {}
            required_list = []
            for pname, pdef in defn.params.items():
                prop = {"type": pdef.type, "description": pdef.description or pname}
                if pdef.enum:
                    prop["enum"] = list(pdef.enum)
                properties[pname] = prop
                if pdef.required:
                    required_list.append(pname)
            functions.append({
                "type": "function",
                "function": {
                    "name": defn.name,
                    "description": defn.description,
                    "parameters": {
                        "type": "object",
                        "properties": properties,
                        "required": required_list,
                    },
                },
            })
        return functions

    def build_tool_prompt_section(self) -> str:
        """动态生成 Agent system prompt 中的工具说明段

        用于注入到 diagnose_agent / fix_planner_agent 等 Agent 的 system prompt 中。
        每个工具产出格式：- tool_name: description（param1: enum描述, param2: 说明...）
        """
        lines: list[str] = []
        for defn in self._definitions.values():
            # 跳过不可用的动态工具
            if defn.source == "mcp" and defn.status == "unavailable":
                continue

            param_parts: list[str] = []
            for pname, pdef in defn.params.items():
                desc = pdef.description or pname
                if pdef.enum:
                    desc += f" ({'/'.join(pdef.enum)})"
                if pdef.required:
                    param_parts.append(f"{pname}（必填）: {desc}")
                else:
                    param_parts.append(f"{pname}（可选）: {desc}")

            if param_parts:
                lines.append(f"- {defn.name}: {defn.description}\n  {', '.join(param_parts)}")
            else:
                lines.append(f"- {defn.name}: {defn.description}")

        return "\n".join(lines)


    def validate_params(
        self, tool_name: str, params: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        校验参数枚举和约束，返回 {"valid": bool, "errors": list[str]}

        使用统一定义中的 params 校验。
        """
        defn = self._definitions.get(tool_name)
        if defn is None:
            return {"valid": False, "errors": [f"未知工具: {tool_name}"]}

        errors: list[str] = []
        for pname, pdef in defn.params.items():
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

    def resolve(
        self, tool_name: str, params: Dict[str, Any]
    ) -> Dict[str, Any]:
        """解析为标准调用格式"""
        return {
            "tool": tool_name,
            "arguments": params,
        }

    # ========================================================================
    # 风险评级
    # ========================================================================

    def get_default_risk(self, tool_name: str) -> Optional[str]:
        """获取工具的默认风险等级。

        Returns:
            "low" / "medium" / "high" 或 None（未知工具）
        """
        defn = self._definitions.get(tool_name)
        if defn is not None:
            return defn.default_risk
        return None

    def get_risk_for_action(self, tool_name: str, action: Any) -> Optional[str]:
        """对带 action 参数的工具，按 action 返回具体风险"""
        defn = self._definitions.get(tool_name)
        if defn is not None:
            if isinstance(action, str) and defn.action_field:
                return defn.action_risk_overrides.get(action, defn.default_risk)
            return defn.default_risk
        return None

    # ========================================================================
    # 审计
    # ========================================================================

    def get_tool_audit_policy(self, tool_name: str) -> Optional[Dict[str, object]]:
        """获取工具的审计策略"""
        defn = self._definitions.get(tool_name)
        if defn is None:
            return None
        return defn.audit_policy.to_dict()

    def update_tool_audit_policy(
        self,
        tool_name: str,
        mode: Optional[str] = None,
        safe_fields: Optional[List[str]] = None,
        summary_builder: Optional[str] = None,
    ) -> bool:
        """更新工具的审计策略（内存中），需调用 save_config 持久化

        现在同时支持静态工具和动态 MCP 工具。

        Args:
            tool_name: 工具名称
            mode: 审计模式 ("whitelist" / "summary" / "full")
            safe_fields: whitelist 模式下的安全字段列表
            summary_builder: summary 模式的摘要构造器名 ("cmd_exec_summary")

        Returns:
            True 更新成功，False 工具不存在或参数无效
        """
        defn = self._definitions.get(tool_name)
        if defn is None:
            logger.warning("更新审计策略失败: 工具 '%s' 不在注册表中", tool_name)
            return False

        new_mode = defn.audit_policy.mode
        new_safe_fields = defn.audit_policy.safe_fields
        new_summary_builder = defn.audit_policy.summary_builder

        if mode is not None:
            if mode not in ("whitelist", "summary", "full"):
                logger.warning("无效的 audit mode: %s", mode)
                return False
            new_mode = mode

        if safe_fields is not None:
            # 校验 safe_fields 都在 params 中
            valid_fields: list[str] = []
            for sf in safe_fields:
                if sf in defn.params:
                    valid_fields.append(sf)
                else:
                    logger.warning(
                        "工具 '%s' safe_field '%s' 不在 params 中，已跳过", tool_name, sf
                    )
            new_safe_fields = tuple(valid_fields)

        if summary_builder is not None:
            if summary_builder and summary_builder != "cmd_exec_summary":
                logger.warning("未知的 summary_builder: %s", summary_builder)
                return False
            new_summary_builder = summary_builder if summary_builder else None

        new_ap = AuditPolicy(
            mode=new_mode,
            safe_fields=new_safe_fields,
            summary_builder=new_summary_builder,
        )

        self._definitions[tool_name] = UnifiedToolDefinition(
            name=defn.name,
            description=defn.description,
            params=defn.params,
            default_risk=defn.default_risk,
            action_field=defn.action_field,
            action_risk_overrides=defn.action_risk_overrides,
            audit_policy=new_ap,
            source=defn.source,
            server_id=defn.server_id,
            status=defn.status,
            mcp_parameters=defn.mcp_parameters,
            mcp_required=defn.mcp_required,
        )
        logger.info(
            "工具 '%s' 审计策略已更新: mode=%s, safe_fields=%s, summary_builder=%s",
            tool_name, new_mode, list(new_safe_fields), new_summary_builder,
        )
        return True

    def build_audit_metadata(self, tool_name: str, params: Mapping[str, object]) -> dict[str, object]:
        """构建审计安全元数据

        按审计策略三模式处理：
          - whitelist: 只保留 safe_fields 中的字段，对结果做敏感脱敏
          - summary:   调用对应的 summary_builder 生成安全摘要，对结果做敏感脱敏
          - full:      完整保留所有字段，只做敏感 key 脱敏

        动态 MCP 工具回退到 full 模式（通用脱敏）。
        """
        from app.services.audit_service import sanitize_sensitive_data

        defn = self._definitions.get(tool_name)
        if defn is not None:
            ap = defn.audit_policy
            params_dict = dict(params)

            if ap.mode == "whitelist":
                # 只保留白名单字段
                filtered: dict[str, object] = {}
                for sf in ap.safe_fields:
                    if sf in params_dict:
                        filtered[sf] = params_dict[sf]
                return sanitize_sensitive_data(filtered)  # type: ignore[return-value]

            if ap.mode == "summary":
                if ap.summary_builder == "cmd_exec_summary":
                    return sanitize_sensitive_data(build_cmd_exec_summary(params))  # type: ignore[return-value]
                # 未知 summary_builder → 回退 full
                logger.warning("工具 '%s' 未知 summary_builder: %s，回退 full", tool_name, ap.summary_builder)
                return sanitize_sensitive_data(params_dict)  # type: ignore[return-value]

            # mode == "full"
            return sanitize_sensitive_data(params_dict)  # type: ignore[return-value]

        raise ValueError(f"找不到对应工具: {tool_name}")