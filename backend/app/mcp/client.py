"""MCP 客户端：连接麒麟 V11 的 MCP Server"""
import json
import logging
from typing import Any, Dict

import httpx

from config import settings

logger = logging.getLogger(__name__)


def _mock_tool_result(tool_name: str, arguments: Dict[str, Any]) -> Dict:
    """演示模式：返回 MCP 工具 mock 结果"""
    if tool_name == "sys_info":
        metric = arguments.get("metric", "all")
        return {
            "success": True,
            "result": {
                "metric": metric,
                "cpu_percent": 15.2,
                "memory_percent": 42.0,
                "disk_percent": 62.5,
                "load_avg": [0.3, 0.25, 0.1],
                "timestamp": "2026-06-16T12:00:00",
            },
        }
    if tool_name == "service_mgr":
        return {
            "success": True,
            "result": {
                "service": arguments.get("service", "sshd"),
                "action": arguments.get("action", "status"),
                "status": "active (running)",
                "output": "服务运行正常",
            },
        }
    if tool_name == "log_reader":
        return {
            "success": True,
            "result": {
                "source": arguments.get("source", "/var/log/messages"),
                "lines": arguments.get("lines", 20),
                "content": "Jun 16 12:00:00 kylin sshd[1234]: Accepted password for user from 192.168.1.1\n...",
            },
        }
    if tool_name == "net_monitor":
        return {
            "success": True,
            "result": {
                "iface": arguments.get("iface", "all"),
                "rx_kbps": 120.5,
                "tx_kbps": 45.2,
            },
        }
    if tool_name == "file_guard":
        return {
            "success": True,
            "result": {
                "path": arguments.get("path", "/etc/hosts"),
                "size": 256,
                "content": "127.0.0.1 localhost\n::1 localhost",
            },
        }
    if tool_name == "cmd_exec":
        return {
            "success": True,
            "result": {
                "command": arguments.get("command", ""),
                "output": "命令执行完成（演示模式）",
            },
        }
    return {"success": True, "result": {"tool": tool_name, "arguments": arguments}}


class MCPClient:
    """MCP HTTP JSON-RPC 2.0 客户端"""

    def __init__(self, base_url: str = None):
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
        if settings.DEMO_MODE:
            logger.info(f"[MCP DEMO] 模拟调用 {tool_name}，参数: {arguments}")
            return _mock_tool_result(tool_name, arguments)

        payload = {
            "jsonrpc": "2.0",
            "method": "tools/call",
            "params": {
                "name": tool_name,
                "arguments": arguments,
            },
            "id": self._next_id(),
        }

        try:
            timeout = httpx.Timeout(settings.COMMAND_TIMEOUT + 5.0, connect=5.0)
            headers = {}
            if not settings.DEMO_MODE:
                headers["Authorization"] = f"Bearer {settings.MCP_API_TOKEN}"
            async with httpx.AsyncClient(timeout=timeout, headers=headers) as client:
                resp = await client.post(
                    f"{self.base_url}/mcp/v1/tools/call",
                    json=payload,
                )
                resp.raise_for_status()
                data = resp.json()

                if "error" in data:
                    logger.error(f"[MCP] 工具调用错误: {data['error']}")
                    return {"success": False, "error": data["error"]}

                logger.info(f"[MCP] 工具 {tool_name} 调用成功")
                return {"success": True, "result": data.get("result", {})}

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
        try:
            headers = {}
            if not settings.DEMO_MODE:
                headers["Authorization"] = f"Bearer {settings.MCP_API_TOKEN}"
            async with httpx.AsyncClient(timeout=httpx.Timeout(10.0, connect=5.0), headers=headers) as client:
                resp = await client.post(
                    f"{self.base_url}/mcp/v1/tools/list",
                    json=payload,
                )
                resp.raise_for_status()
                return resp.json()
        except Exception as e:
            logger.error(f"[MCP] 获取工具列表失败: {e}")
            return {"error": str(e)}
