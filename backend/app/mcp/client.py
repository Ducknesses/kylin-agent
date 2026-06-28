"""MCP 客户端：连接麒麟 V11 的 MCP Server

支持两种模式：
  - mock：返回本地 mock 数据，用于 B 端独立开发（默认）
  - real：通过 HTTP JSON-RPC 2.0 调用执行器 C
"""
import json
import logging
from typing import Any, Dict

import httpx

from config import settings

logger = logging.getLogger(__name__)


class MCPClient:
    """MCP HTTP JSON-RPC 2.0 客户端

    初始化参数均可在构造时显式传入（便于测试），未传入时从全局 Settings 读取。
    """

    def __init__(
        self,
        base_url: str | None = None,
        mode: str | None = None,
        auth_token: str | None = None,
        timeout: int | None = None,
    ):
        self.base_url = base_url or settings.MCP_SERVER_URL
        self.mode = mode if mode is not None else settings.MCP_MODE
        self.auth_token = auth_token if auth_token is not None else settings.MCP_AUTH_TOKEN
        self.timeout = timeout or settings.COMMAND_TIMEOUT
        self._request_id = 0

    def _next_id(self) -> int:
        self._request_id += 1
        return self._request_id

    # ── 内部构造方法（便于单独测试） ──────────────────────────────

    def _build_url(self, method: str = "tools/call") -> str:
        """构造 MCP JSON-RPC 端点 URL"""
        return f"{self.base_url}/mcp/v1/{method}"

    def _build_payload(self, tool_name: str, arguments: Dict[str, Any]) -> Dict:
        """构造 JSON-RPC 2.0 请求体（使用 params.arguments，不使用 params.args）"""
        return {
            "jsonrpc": "2.0",
            "method": "tools/call",
            "params": {
                "name": tool_name,
                "arguments": arguments,
            },
            "id": self._next_id(),
        }

    def _build_headers(self) -> Dict[str, str]:
        """构造 HTTP 请求头（包含 Authorization Bearer，不含完整 token 日志输出）"""
        headers = {"Content-Type": "application/json; charset=utf-8"}
        if self.auth_token:
            headers["Authorization"] = f"Bearer {self.auth_token}"
        return headers

    # ── Mock 分支 ────────────────────────────────────────────────

    def _mock_call_tool(self, tool_name: str, arguments: Dict[str, Any] | None = None) -> Dict:
        """Mock 模式：返回本地模拟数据，不发起任何网络请求"""
        args = arguments or {}

        if tool_name == "sys_info":
            metric = args.get("metric", "all")
            if metric in ("cpu", "all"):
                return {
                    "success": True,
                    "result": {
                        "cpu_percent": 23.5,
                        "load_avg": [0.52, 0.31, 0.18],
                        "cores": 4,
                    },
                }
            # 其他 metric 暂未完整实现
            return {
                "success": True,
                "result": {
                    "message": f"mock sys_info metric={metric} (仅 cpu/all 有完整 mock 数据)",
                },
            }

        # 未知工具
        return {
            "success": False,
            "error": f"Mock 未实现工具: {tool_name}",
        }

    # ── 主调用入口 ───────────────────────────────────────────────

    async def call_tool(
        self,
        tool_name: str,
        arguments: Dict[str, Any] | None = None,
    ) -> Dict:
        """
        调用 MCP 执行器工具

        参数:
            tool_name: 工具名（sys_info, service_mgr, log_reader, net_monitor, cmd_exec, file_guard）
            arguments: 工具参数，可选，默认为空字典

        返回:
            {"success": bool, "result": {...} | "error": str}
        """
        arguments = arguments or {}

        if self.mode == "mock":
            return self._mock_call_tool(tool_name, arguments)

        # ── Real 模式：HTTP JSON-RPC 2.0 调用 ──
        payload = self._build_payload(tool_name, arguments)
        headers = self._build_headers()
        url = self._build_url()

        try:
            timeout = httpx.Timeout(self.timeout + 5.0, connect=5.0)
            async with httpx.AsyncClient(timeout=timeout) as client:
                resp = await client.post(url, headers=headers, json=payload)
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

    # ── 便捷方法 ────────────────────────────────────────────────

    async def get_system_metrics(self) -> Dict:
        """获取系统指标（调用 sys_info 工具）"""
        return await self.call_tool("sys_info", {"metric": "all"})

    async def list_tools(self) -> Dict:
        """列出 MCP Server 上可用的工具（如果 Server 支持）"""
        if self.mode == "mock":
            return {"tools": []}

        payload = {
            "jsonrpc": "2.0",
            "method": "tools/list",
            "id": self._next_id(),
        }
        headers = self._build_headers()
        url = self._build_url("tools/list")

        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(10.0, connect=5.0)) as client:
                resp = await client.post(url, headers=headers, json=payload)
                resp.raise_for_status()
                return resp.json()
        except Exception as e:
            logger.error(f"[MCP] 获取工具列表失败: {e}")
            return {"error": str(e)}
