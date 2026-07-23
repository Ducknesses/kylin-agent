"""
ToolRegistry —— 纯动态 MCP 工具注册表

职责：
  - 管理从 MCP 服务器动态发现的所有工具
  - 提供工具存在性检查、参数校验
  - 动态构建 OpenAI function calling 格式（LLM 每次调用时实时查询）
  - 不含任何静态工具定义 —— 所有工具来自 MCP 服务器 tools/list

与旧版的区别：
  - 删除了静态 _REGISTRY（ToolSpec / AuditPolicy 等）
  - 工具唯一来源是 MCPServerManager.register_from_server()
  - get_openai_functions() 每次都从 _tools 动态构建
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from app.mcp.client import MCPTool

logger = logging.getLogger(__name__)


class ToolRegistry:
    """
    MCP 工具注册表 —— 运行时查询工具元信息与参数校验

    使用方式：
        registry = ToolRegistry()
        # 由 MCPServerManager 调用
        registry.register_from_server("kylin-main", tools)
        # LLM 调用时
        functions = registry.get_openai_functions()
    """

    def __init__(self):
        # {tool_name: MCPTool}
        self._tools: Dict[str, MCPTool] = {}
        # {server_id: [tool_name, ...]}
        self._tools_by_server: Dict[str, List[str]] = {}

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
            if unique_name in self._tools:
                # 已有同名工具来自其他服务器，追加后缀
                unique_name = f"{tool.name}__{server_id}"
                tool = MCPTool(
                    name=unique_name,
                    description=f"[{tool.server_name or server_id}] {tool.description}",
                    parameters=tool.parameters,
                    required=tool.required,
                    server_id=server_id,
                    server_name=tool.server_name,
                )

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
            # 如果是去重后缀名，也要清除
            self._tools.pop(name, None)
            base_name = name.rsplit("__", 1)[0]
            # 检查是否还有同名去重后缀指向此服务器
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
        """判断工具是否存在"""
        return tool_name in self._tools

    def get_tool(self, tool_name: str) -> Optional[MCPTool]:
        """根据名称获取工具"""
        return self._tools.get(tool_name)

    def get_tools_for_server(self, server_id: str) -> List[MCPTool]:
        """获取指定服务器的所有工具"""
        tool_names = self._tools_by_server.get(server_id, [])
        return [self._tools[n] for n in tool_names if n in self._tools]

    def list_all(self) -> List[MCPTool]:
        """列出所有已注册工具"""
        return list(self._tools.values())

    def count(self) -> int:
        """已注册工具总数"""
        return len(self._tools)

    # ========================================================================
    # LLM 集成
    # ========================================================================

    def get_openai_functions(self) -> List[Dict[str, Any]]:
        """
        动态构建 OpenAI function calling 格式的工具列表

        LLM 每次调用时实时查询，确保工具列表始终是最新的。
        不含任何缓存或静态定义。
        """
        return [tool.to_openai_function() for tool in self._tools.values()]

    def get_tool_names(self) -> List[str]:
        """获取所有工具名称列表"""
        return list(self._tools.keys())

    # ========================================================================
    # 参数校验
    # ========================================================================

    def validate_params(
        self, tool_name: str, params: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        简单参数校验 —— 检查 required 字段是否存在

        Returns:
            {"valid": bool, "errors": list[str]}
        """
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