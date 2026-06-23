"""MCP 客户端：连接麒麟 V11 的 MCP Server"""
import json
import logging
from typing import Any, Dict

import httpx

from config import settings

logger = logging.getLogger(__name__)


class MCPClient:
    """MCP HTTP JSON-RPC 2.0 客户端"""

    def __init__(self, base_url: str | None = None):
        self.base_url = base_url or settings.MCP_SERVER_URL
        self._request_id = 0

    def _next_id(self) -> int:
        self._request_id += 1
        return self._request_id

    async def call_tool(self, tool_name: str, arguments: Dict[str, Any]) -> Dict:
        """
        调用 MCP 执行器工具

        参数:
            tool_name: 工具名（sys_info, service_mgr, log_reader, net_monitor, cmd_exec, file_guard）
            arguments: 工具参数

        返回:
            MCP Server 返回的 result 字段
        """
        payload = {
            "jsonrpc": "2.0",
            "method": "tools/call",
            "params": {
                "name": tool_name,
                "arguments": arguments,
            },
            "id": self._next_id(),
        }

        headers = {"Content-Type": "application/json"}
        # 执行器 C 通过 Bearer Token 校验后端身份，开发阶段可为空
        if settings.MCP_AUTH_TOKEN:
            headers["Authorization"] = f"Bearer {settings.MCP_AUTH_TOKEN}"

        try:
            timeout = httpx.Timeout(settings.COMMAND_TIMEOUT + 5.0, connect=5.0)
            async with httpx.AsyncClient(timeout=timeout) as client:
                resp = await client.post(
                    f"{self.base_url}/mcp/v1/tools/call",
                    headers=headers,
                    json=payload,
                )
                resp.raise_for_status()
                data = resp.json()

                if "error" in data:
                    logger.error(f"[MCP] 工具调用错误: {data['error']}")
                    return {"success": False, "error": data["error"]}

                result = data.get("result", {})
                # MCP Server 可能在 HTTP 200 下通过 result.blocked 表示安全拦截
                if isinstance(result, dict) and result.get("blocked") is True:
                    logger.warning(
                        f"[MCP] 工具 {tool_name} 被 MCP Server 安全拦截: "
                        f"{result.get('reason', '未知原因')}"
                    )
                    return {
                        "success": False,
                        "blocked": True,
                        "error": result.get("reason") or "MCP 工具调用被安全策略拦截",
                        "result": result,
                    }

                logger.info(f"[MCP] 工具 {tool_name} 调用成功")
                return {"success": True, "result": result}

        except httpx.TimeoutException:
            logger.error(f"[MCP] 工具 {tool_name} 调用超时")
            return {"success": False, "error": "MCP Server 请求超时"}
        except httpx.ConnectError as e:
            logger.error(f"[MCP] 连接失败: {e}")
            return {"success": False, "error": f"无法连接到 MCP Server: {self.base_url}"}
        except Exception as e:
            logger.exception(f"[MCP] 工具调用异常: {e}")
            return {"success": False, "error": str(e)}

    async def get_system_metrics(self) -> Dict:
        """获取系统指标（调用 sys_info 工具）"""
        return await self.call_tool("sys_info", {"metric": "all"})

    async def list_tools(self) -> Dict:
        """列出 MCP Server 上可用的工具（如果 Server 支持）"""
        payload = {
            "jsonrpc": "2.0",
            "method": "tools/list",
            "id": self._next_id(),
        }
        headers = {"Content-Type": "application/json"}
        if settings.MCP_AUTH_TOKEN:
            headers["Authorization"] = f"Bearer {settings.MCP_AUTH_TOKEN}"

        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(10.0, connect=5.0)) as client:
                resp = await client.post(
                    f"{self.base_url}/mcp/v1/tools/list",
                    headers=headers,
                    json=payload,
                )
                resp.raise_for_status()
                return resp.json()
        except Exception as e:
            logger.error(f"[MCP] 获取工具列表失败: {e}")
            return {"error": str(e)}
