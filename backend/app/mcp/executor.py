"""命令执行调度（带沙箱参数校验）"""
import logging
from typing import Dict

from app.core.rbac import check_command_permission, get_user_level
from app.mcp.client import MCPClient
from app.mcp.tools import get_tool_names

logger = logging.getLogger(__name__)

# 工具名到命令类型的映射（用于 RBAC 校验）
TOOL_COMMAND_TYPES = {
    "sys_info": "read",
    "service_mgr": "op",
    "log_reader": "read",
    "net_monitor": "read",
    "cmd_exec": "exec",
    "file_guard": "read",
}


class Executor:
    """命令执行调度器"""

    def __init__(self, client: MCPClient | None = None):
        self.client = client or MCPClient()

    async def execute(self, tool_name: str, arguments: Dict, user_id: str = "anonymous") -> Dict:
        """
        执行 MCP 工具调用，带权限校验

        参数:
            tool_name: 工具名称
            arguments: 工具参数
            user_id: 用户标识

        返回:
            {"success": bool, "result": {...} | "error": str}
        """
        # 1. 工具名白名单校验
        if tool_name not in get_tool_names():
            logger.warning(f"[Executor] 非法工具名: {tool_name}")
            return {"success": False, "error": f"未知工具: {tool_name}"}

        # 2. 如果是 cmd_exec，额外做命令白名单校验
        if tool_name == "cmd_exec" and "command" in arguments:
            user_level = get_user_level(user_id)
            cmd = arguments["command"]
            perm = check_command_permission(cmd, user_level)
            if not perm["allowed"]:
                logger.warning(f"[Executor] 命令权限拒绝: {perm['reason']}")
                return {"success": False, "error": f"权限不足: {perm['reason']}"}

        # 3. 调用 MCP Client
        logger.info(f"[Executor] 执行工具 {tool_name}，参数: {arguments}")
        return await self.client.call_tool(tool_name, arguments)
