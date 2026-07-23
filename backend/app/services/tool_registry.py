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
from typing import Any, Dict, List, Mapping, Optional

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
class ToolSpec:
    """一个工具的完整定义（不可变）"""
    name: str
    description: str
    params: Mapping[str, ToolParamSpec]
    default_risk: RiskLevel
    action_field: str | None = None
    action_risk_overrides: Mapping[str, RiskLevel] = field(
        default_factory=lambda: MappingProxyType({})
    )

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
            "params": params_dict,
        }


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

    return ToolSpec(
        name=name,
        description=description,
        params=MappingProxyType(params_dict),
        default_risk=default_risk,  # type: ignore[arg-type]
        action_field=action_field,
        action_risk_overrides=MappingProxyType(action_risk_overrides),
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

    同时管理：
      - 配置文件工具定义（self._registry）：从 JSON 文件加载，用户可修改风险等级
      - 动态 MCP 工具（self._tools）：从 MCP 服务器动态发现的工具

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
        # 工具定义注册表 {tool_name: ToolSpec} —— 从 JSON 加载
        self._registry: Dict[str, ToolSpec] = {}
        # 配置文件路径
        self._config_path: str = config_path or _DEFAULT_CONFIG_PATH
        # {tool_name: MCPTool} —— 动态 MCP 工具
        self._tools: Dict[str, MCPTool] = {}
        # {server_id: [tool_name, ...]}
        self._tools_by_server: Dict[str, List[str]] = {}
        # 静态工具名（在 _registry 中但不在 _tools 中）→ 提供该工具的 server_id
        self._static_tool_to_server: Dict[str, str] = {}

        # 从配置文件加载
        self._load_config()

    # ========================================================================
    # 配置文件管理
    # ========================================================================

    def _load_config(self) -> None:
        """从 JSON 配置文件加载工具定义"""
        try:
            self._registry = _load_from_json(self._config_path)
        except Exception:
            logger.exception("加载工具定义配置文件失败")
            self._registry = {}

    def reload(self) -> None:
        """重新加载配置文件（热加载）"""
        self._load_config()
        logger.info("工具定义已重新加载，当前 %d 个工具", len(self._registry))

    def save_config(self) -> None:
        """将当前注册表持久化到 JSON 配置文件"""
        try:
            tools_dict: dict[str, object] = {}
            for name, spec in self._registry.items():
                tools_dict[name] = spec.to_dict()

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

        Args:
            tool_name: 工具名称
            default_risk: 新的默认风险等级 ("low" / "medium" / "high")
            action_risk_overrides: 新的 action 风险覆盖 {action: risk}

        Returns:
            True 更新成功，False 工具不存在或参数无效
        """
        spec = self._registry.get(tool_name)
        if spec is None:
            logger.warning("更新风险失败: 工具 '%s' 不在注册表中", tool_name)
            return False

        new_default = spec.default_risk
        new_overrides = dict(spec.action_risk_overrides)

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
                if spec.action_field:
                    action_param = spec.params.get(spec.action_field)
                    if action_param and action_param.enum and action not in action_param.enum:
                        logger.warning(
                            "action '%s' 不在工具 '%s' 允许的 action enum 中: %s",
                            action, tool_name, list(action_param.enum),
                        )
                        return False
                new_overrides[action] = risk  # type: ignore[assignment]

        # 创建新的不可变 ToolSpec
        new_spec = ToolSpec(
            name=spec.name,
            description=spec.description,
            params=spec.params,
            default_risk=new_default,
            action_field=spec.action_field,
            action_risk_overrides=MappingProxyType(new_overrides),
        )
        self._registry[tool_name] = new_spec
        logger.info(
            "工具 '%s' 风险等级已更新: default_risk=%s, overrides=%s",
            tool_name, new_default, new_overrides,
        )
        return True

    def get_all_tool_definitions(self) -> List[Dict[str, object]]:
        """获取所有工具定义的完整信息（供前端展示/修改）"""
        result: List[Dict[str, object]] = []
        for spec in self._registry.values():
            result.append(spec.to_dict())
        return result

    def get_tool_definition(self, tool_name: str) -> Optional[Dict[str, object]]:
        """获取单个工具定义"""
        spec = self._registry.get(tool_name)
        if spec is None:
            return None
        return spec.to_dict()

    # ========================================================================
    # 动态注册 / 注销
    # ========================================================================

    def register_from_server(self, server_id: str, tools: List[MCPTool]) -> None:
        """从 MCP 服务器注册（或刷新）工具列表"""
        # 先清除该服务器的旧工具
        old_names = self._tools_by_server.pop(server_id, [])
        for name in old_names:
            self._tools.pop(name, None)

        # 注册新工具
        names = []
        for tool in tools:
            # 处理工具名冲突：追加 server_id 后缀
            unique_name = tool.name
            if unique_name in self._tools or unique_name in self._registry:
                # 已有同名工具（注册表或动态工具），追加后缀
                unique_name = f"{tool.name}__{server_id}"
                tool = MCPTool(
                    name=unique_name,
                    description=f"[{tool.server_name or server_id}] {tool.description}",
                    parameters=tool.parameters,
                    required=tool.required,
                    server_id=server_id,
                    server_name=tool.server_name,
                )
                # 记录注册表工具名 → server_id 映射
                base_name = unique_name.rsplit("__", 1)[0]
                if base_name in self._registry:
                    self._static_tool_to_server[base_name] = server_id

            self._tools[unique_name] = tool
            names.append(unique_name)

        self._tools_by_server[server_id] = names
        logger.info(
            f"[ToolRegistry] 服务器 {server_id} 注册了 {len(tools)} 个工具"
        )

    def unregister_server(self, server_id: str) -> None:
        """注销服务器的所有工具"""
        tool_names = self._tools_by_server.pop(server_id, [])
        for name in tool_names:
            self._tools.pop(name, None)
            base_name = name.rsplit("__", 1)[0]
            for k in list(self._tools.keys()):
                if k.startswith(f"{base_name}__") and self._tools[k].server_id == server_id:
                    self._tools.pop(k, None)

        if tool_names:
            logger.info(
                f"[ToolRegistry] 服务器 {server_id} 注销了 {len(tool_names)} 个工具"
            )

    # ========================================================================
    # 查询
    # ========================================================================

    def exists(self, tool_name: str) -> bool:
        """判断工具是否存在（注册表或动态）"""
        return tool_name in self._registry or tool_name in self._tools

    def get_tool(self, tool_name: str) -> Optional[MCPTool]:
        """根据名称获取动态 MCP 工具"""
        return self._tools.get(tool_name)

    def get_tools_for_server(self, server_id: str) -> List[MCPTool]:
        """获取指定服务器的所有工具"""
        tool_names = self._tools_by_server.get(server_id, [])
        return [self._tools[n] for n in tool_names if n in self._tools]

    def list_all(self) -> List[MCPTool]:
        """列出所有已注册动态工具"""
        return list(self._tools.values())

    def count(self) -> int:
        """已注册动态工具总数"""
        return len(self._tools)

    def get_tool_names(self) -> List[str]:
        """获取所有工具名称列表（注册表 + 动态）"""
        names = list(self._registry.keys())
        names.extend(self._tools.keys())
        return names

    def get_tool_spec(self, tool_name: str) -> Optional[ToolSpec]:
        """返回工具规格（不可变副本）"""
        return self._registry.get(tool_name)

    def get_tool_info(self, tool_name: str) -> Optional[Dict[str, object]]:
        """返回工具元信息（兼容旧版 dict 接口）"""
        spec = self._registry.get(tool_name)
        if spec is None:
            # 尝试动态工具
            tool = self._tools.get(tool_name)
            if tool is not None:
                return {
                    "name": tool.name,
                    "description": tool.description,
                    "risk_level": "low",
                    "params": {
                        k: {"type": v.get("type", "string"), "required": k in tool.required, "enum": v.get("enum"), "constraints": None}
                        for k, v in tool.parameters.items()
                    },
                }
            return None
        return {
            "name": spec.name,
            "description": spec.description,
            "risk_level": spec.default_risk,
            "params": {
                k: {
                    "type": v.type,
                    "required": v.required,
                    "enum": list(v.enum) or None,
                    "constraints": dict(v.constraints) if v.constraints else None,
                }
                for k, v in spec.params.items()
            },
        }

    def get_param_info(self, tool_name: str, param_name: str) -> Optional[Dict[str, Any]]:
        """获取工具参数的详细信息"""
        spec = self._registry.get(tool_name)
        if spec is None:
            return None
        p = spec.params.get(param_name)
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
        1. 动态工具（_tools）→ 直接取其 server_id
        2. 注册表工具（_registry）→ 查 _static_tool_to_server 映射
        3. 回退：查所有已注册服务器中是否有匹配的工具名
        4. 返回空字符串（调用方自行处理降级）

        返回空字符串时，MCPClient.call_tool 会返回 "无可用连接" 错误。
        """
        # 1. 动态工具
        tool = self._tools.get(tool_name)
        if tool is not None and tool.server_id:
            return tool.server_id

        # 2. 注册表工具 → 映射表
        if tool_name in self._registry:
            mapped = self._static_tool_to_server.get(tool_name, "")
            if mapped:
                return mapped

        # 3. 回退：遍历所有服务器找匹配工具
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
        包含注册表工具定义和动态 MCP 工具。
        """
        functions = []
        # 注册表工具 → OpenAI function 格式
        for spec in self._registry.values():
            properties = {}
            required_list = []
            for pname, pdef in spec.params.items():
                prop = {"type": pdef.type, "description": pdef.description or pname}
                if pdef.enum:
                    prop["enum"] = list(pdef.enum)
                properties[pname] = prop
                if pdef.required:
                    required_list.append(pname)
            functions.append({
                "type": "function",
                "function": {
                    "name": spec.name,
                    "description": spec.description,
                    "parameters": {
                        "type": "object",
                        "properties": properties,
                        "required": required_list,
                    },
                },
            })
        # 动态 MCP 工具 → OpenAI function 格式
        for tool in self._tools.values():
            functions.append(tool.to_openai_function())
        return functions

    # ========================================================================
    # 参数校验
    # ========================================================================

    def validate_params(
        self, tool_name: str, params: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        校验参数枚举和约束，返回 {"valid": bool, "errors": list[str]}

        优先使用注册表定义校验，回退到 MCP 工具的 required 检查。
        """
        spec = self._registry.get(tool_name)
        if spec is not None:
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

        # 回退到 MCP 工具的简单 required 检查
        tool = self._tools.get(tool_name)
        if not tool:
            return {"valid": False, "errors": [f"未知工具: {tool_name}"]}

        if not isinstance(params, dict):
            return {"valid": False, "errors": ["参数必须是字典"]}

        errors = []
        for key in tool.required:
            if key not in params:
                errors.append(f"缺少必填参数: {key}")
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
        spec = self._registry.get(tool_name)
        if spec is not None:
            return spec.default_risk
        # 回退到动态工具：默认 low
        tool = self._tools.get(tool_name)
        if tool:
            return "low"
        return None

    def get_risk_for_action(self, tool_name: str, action: Any) -> Optional[str]:
        """对带 action 参数的工具，按 action 返回具体风险"""
        spec = self._registry.get(tool_name)
        if spec is not None:
            if isinstance(action, str) and spec.action_field:
                return spec.action_risk_overrides.get(action, spec.default_risk)
            return spec.default_risk
        # 回退到动态工具
        return self.get_default_risk(tool_name)

    # ========================================================================
    # 审计
    # ========================================================================

    def build_audit_metadata(self, tool_name: str, params: Mapping[str, object]) -> dict[str, object]:
        """构建审计安全元数据

        注册表工具：对 params 做通用脱敏（审计策略待后续重构统一实现）
        动态 MCP 工具：对所有参数做通用脱敏

        特殊处理：cmd_exec 使用 build_cmd_exec_summary 做安全摘要
        """
        from app.services.audit_service import sanitize_sensitive_data

        # cmd_exec 特殊摘要
        if tool_name == "cmd_exec" and tool_name in self._registry:
            return sanitize_sensitive_data(build_cmd_exec_summary(params))  # type: ignore[return-value]

        # 注册表工具：通用脱敏
        if tool_name in self._registry:
            return sanitize_sensitive_data(dict(params))  # type: ignore[return-value]

        # 回退到动态 MCP 工具：通用脱敏
        if tool_name in self._tools:
            return sanitize_sensitive_data(dict(params))  # type: ignore[return-value]

        raise ValueError(f"找不到对应工具: {tool_name}")