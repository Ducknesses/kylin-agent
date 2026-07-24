"""
MCP (Model Context Protocol) 客户端 —— 纯标准协议实现

支持 MCP 2024-11-05 规范：
  - JSON-RPC 2.0 消息格式
  - initialize / tools/list / tools/call / prompts/list / resources/list / resources/read
  - SSE / Streamable HTTP / STDIO 传输
  - 多服务器连接管理
  - 动态工具发现

不含 mock 模式 —— 所有工具定义均来自 MCP 服务器 tools/list 响应。
"""
from __future__ import annotations

import asyncio
import json
import logging
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional

import httpx

logger = logging.getLogger(__name__)

# ============================================================================
# 协议常量
# ============================================================================

MCP_PROTOCOL_VERSION = "2024-11-05"
JSONRPC_VERSION = "2.0"

# ============================================================================
# 枚举定义
# ============================================================================


class MCPTransport(str, Enum):
    """传输方式"""
    SSE = "sse"
    STREAMABLE_HTTP = "streamable_http"
    STDIO = "stdio"


# ============================================================================
# 数据模型
# ============================================================================


@dataclass
class MCPServerInfo:
    """MCP 服务器注册信息"""
    id: str
    name: str
    url: str = ""
    transport: MCPTransport = MCPTransport.SSE
    auth_token: Optional[str] = None
    enabled: bool = True
    auto_discover: bool = True
    tags: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "url": self.url,
            "transport": self.transport.value,
            "auth_token": self.auth_token if self.auth_token else "",
            "enabled": self.enabled,
            "auto_discover": self.auto_discover,
            "tags": self.tags,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "MCPServerInfo":
        transport = data.get("transport", "sse")
        if isinstance(transport, str):
            transport = MCPTransport(transport)
        return cls(
            id=data.get("id", ""),
            name=data.get("name", ""),
            url=data.get("url", ""),
            transport=transport,
            auth_token=data.get("auth_token") or None,
            enabled=data.get("enabled", True),
            auto_discover=data.get("auto_discover", True),
            tags=data.get("tags", []),
            metadata=data.get("metadata", {}),
        )


@dataclass
class MCPTool:
    """MCP 工具描述"""
    name: str
    description: str = ""
    parameters: Dict[str, Any] = field(default_factory=dict)
    required: List[str] = field(default_factory=list)
    server_id: Optional[str] = None
    server_name: Optional[str] = None
    meta: Dict[str, Any] = field(default_factory=dict)  # _meta 扩展属性（suggested_risk, category 等）

    def to_openai_function(self) -> Dict[str, Any]:
        """转换为 OpenAI function calling 格式"""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": {
                    "type": "object",
                    "properties": self.parameters,
                    "required": self.required,
                },
            },
        }


@dataclass
class MCPPrompt:
    """MCP 提示模板"""
    name: str
    description: str = ""
    arguments: List[Dict[str, Any]] = field(default_factory=list)
    server_id: Optional[str] = None
    server_name: Optional[str] = None


@dataclass
class MCPResource:
    """MCP 资源"""
    uri: str
    name: str
    description: str = ""
    mime_type: str = "text/plain"
    server_id: Optional[str] = None
    server_name: Optional[str] = None


# ============================================================================
# JSON-RPC 2.0 消息
# ============================================================================


@dataclass
class JSONRPCRequest:
    jsonrpc: str = JSONRPC_VERSION
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    method: str = ""
    params: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "jsonrpc": self.jsonrpc,
            "id": self.id,
            "method": self.method,
            "params": self.params,
        }


@dataclass
class JSONRPCNotification:
    jsonrpc: str = JSONRPC_VERSION
    method: str = ""
    params: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "jsonrpc": self.jsonrpc,
            "method": self.method,
            "params": self.params,
        }


@dataclass
class JSONRPCResponse:
    jsonrpc: str = JSONRPC_VERSION
    id: str = ""
    result: Any = None
    error: Optional[Dict[str, Any]] = None

    @property
    def is_error(self) -> bool:
        return self.error is not None

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "JSONRPCResponse":
        return cls(
            jsonrpc=data.get("jsonrpc", JSONRPC_VERSION),
            id=data.get("id", ""),
            result=data.get("result"),
            error=data.get("error"),
        )


# ============================================================================
# HTTP 传输层
# ============================================================================


class MCPHTTPTransport:
    """
    MCP HTTP 传输客户端
    支持 SSE (Server-Sent Events) 与 Streamable HTTP 两种模式

    Streamable HTTP: POST → 202 + Location 头 → GET 轮询
    SSE:            POST → 流式 text/event-stream 响应
    """

    def __init__(
        self,
        base_url: str,
        auth_token: Optional[str] = None,
        timeout: float = 30.0,
    ):
        self.base_url = base_url.rstrip("/")
        self.auth_token = auth_token
        self.timeout = timeout
        self.client = httpx.AsyncClient(timeout=httpx.Timeout(timeout))
        self.session_id: Optional[str] = None

    async def close(self) -> None:
        await self.client.aclose()

    def _build_headers(self) -> Dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }
        if self.session_id:
            headers["Mcp-Session-Id"] = self.session_id
        if self.auth_token:
            headers["Authorization"] = f"Bearer {self.auth_token}"
        return headers

    async def send_request(self, request: JSONRPCRequest) -> JSONRPCResponse:
        """发送 JSON-RPC 请求并解析响应"""
        data = request.to_dict()
        headers = self._build_headers()

        try:
            response = await self.client.post(
                self.base_url,
                json=data,
                headers=headers,
                timeout=self.timeout,
            )
            return await self._handle_response(response, request)

        except httpx.TimeoutException:
            logger.error(f"MCP 请求超时: {request.method} → {self.base_url}")
            return JSONRPCResponse(
                id=request.id,
                error={"code": -32000, "message": f"请求超时 ({self.timeout}s)"},
            )
        except httpx.ConnectError as e:
            logger.error(f"MCP 连接失败: {self.base_url} - {e}")
            return JSONRPCResponse(
                id=request.id,
                error={"code": -32000, "message": f"无法连接到 MCP 服务器"},
            )
        except Exception as e:
            logger.exception(f"MCP 请求异常: {request.method} → {self.base_url}")
            return JSONRPCResponse(
                id=request.id,
                error={"code": -32603, "message": f"内部错误: {e}"},
            )

    async def _handle_response(
        self, response: httpx.Response, request: JSONRPCRequest
    ) -> JSONRPCResponse:
        """处理 HTTP 响应，兼容多种传输模式"""

        # Streamable HTTP 202 Accepted → 轮询
        if response.status_code == 202:
            return await self._handle_accepted(response, request)

        # 提取 Session ID
        if "mcp-session-id" in response.headers:
            self.session_id = response.headers["mcp-session-id"]

        content_type = response.headers.get("content-type", "")

        # SSE 流式响应
        if "text/event-stream" in content_type:
            return await self._parse_sse(response, request)

        # 普通 JSON 响应
        try:
            body = response.json()
        except Exception:
            return JSONRPCResponse(
                id=request.id,
                error={"code": -32000, "message": "响应不是有效的 JSON"},
            )
        return JSONRPCResponse.from_dict(body)

    async def _handle_accepted(
        self, response: httpx.Response, request: JSONRPCRequest
    ) -> JSONRPCResponse:
        """处理 202 Accepted + Location 头轮询"""
        poll_url = response.headers.get("location")
        if not poll_url:
            return JSONRPCResponse(
                id=request.id,
                error={"code": -32000, "message": "202 但无 Location 头"},
            )

        max_retries = 10
        for attempt in range(max_retries):
            await asyncio.sleep(0.5 * (attempt + 1))
            try:
                poll_resp = await self.client.get(
                    poll_url,
                    headers={"Accept": "application/json"},
                    timeout=10.0,
                )
                if poll_resp.status_code == 200:
                    return JSONRPCResponse.from_dict(poll_resp.json())
                elif poll_resp.status_code != 202:
                    return JSONRPCResponse(
                        id=request.id,
                        error={"code": -32000, "message": f"轮询失败 HTTP {poll_resp.status_code}"},
                    )
            except Exception as e:
                logger.warning(f"MCP 轮询异常 (attempt={attempt + 1}): {e}")

        return JSONRPCResponse(
            id=request.id,
            error={"code": -32000, "message": "轮询超时"},
        )

    async def _parse_sse(
        self, response: httpx.Response, request: JSONRPCRequest
    ) -> JSONRPCResponse:
        """解析 SSE text/event-stream 响应"""
        body = response.text
        data_lines = []

        for line in body.split("\n"):
            line = line.strip()
            if line.startswith("data:"):
                data_str = line[5:].strip()
                if data_str:
                    data_lines.append(data_str)

        for data_str in data_lines:
            try:
                parsed = json.loads(data_str)
                if "id" in parsed and ("result" in parsed or "error" in parsed):
                    return JSONRPCResponse.from_dict(parsed)
            except json.JSONDecodeError:
                pass

        return JSONRPCResponse(
            id=request.id,
            error={"code": -32000, "message": "SSE 流中未包含有效响应"},
        )


# ============================================================================
# MCP 客户端
# ============================================================================


class MCPClient:
    """
    MCP 客户端 —— 管理多个 MCP 服务器的连接与工具调用

    功能：
    - 注册/注销 MCP 服务器
    - initialize 协议握手
    - 动态工具发现（tools/list）
    - 工具调用（tools/call）
    - prompts / resources 查询

    不含 mock 模式 —— 所有工具均通过 MCP 协议从远程服务器获取。
    """

    def __init__(self):
        self.servers: Dict[str, MCPServerInfo] = {}
        self.transports: Dict[str, MCPHTTPTransport] = {}
        self._initialized: Dict[str, bool] = {}
        self._server_capabilities: Dict[str, Dict[str, Any]] = {}
        self._lock = asyncio.Lock()

    # ========================================================================
    # 服务器管理
    # ========================================================================

    def register_server(self, server: MCPServerInfo) -> None:
        """注册一个 MCP 服务器（不连接）"""
        self.servers[server.id] = server
        self._initialized[server.id] = False
        logger.info(
            f"MCP 服务器已注册: {server.name} (id={server.id}, transport={server.transport.value})"
        )

    def unregister_server(self, server_id: str) -> None:
        """注销一个 MCP 服务器，断开连接"""
        transport = self.transports.pop(server_id, None)
        if transport:
            # 异步关闭在外部处理
            pass
        self.servers.pop(server_id, None)
        self._server_capabilities.pop(server_id, None)
        self._initialized.pop(server_id, None)
        logger.info(f"MCP 服务器已注销: {server_id}")

    async def unregister_server_async(self, server_id: str) -> None:
        """异步注销 MCP 服务器（含传输关闭）"""
        transport = self.transports.pop(server_id, None)
        if transport:
            await transport.close()
        self.servers.pop(server_id, None)
        self._server_capabilities.pop(server_id, None)
        self._initialized.pop(server_id, None)
        logger.info(f"MCP 服务器已注销: {server_id}")

    def get_server(self, server_id: str) -> Optional[MCPServerInfo]:
        return self.servers.get(server_id)

    def list_servers(self) -> List[MCPServerInfo]:
        return list(self.servers.values())

    @property
    def is_connected(self) -> bool:
        return any(self._initialized.values())

    # ========================================================================
    # 连接管理
    # ========================================================================

    async def connect_server(self, server: MCPServerInfo) -> bool:
        """
        连接并初始化单个 MCP 服务器：
        1. 创建 HTTP transport
        2. 发送 initialize 请求
        3. 发送 notifications/initialized
        """
        logger.info(f"正在连接 MCP 服务器: {server.name} ({server.transport.value})")

        if server.transport == MCPTransport.STDIO:
            logger.warning(f"STDIO 传输暂未实现: {server.name}")
            return False

        try:
            transport = MCPHTTPTransport(
                base_url=server.url,
                auth_token=server.auth_token,
                timeout=30.0,
            )
            self.transports[server.id] = transport

            # initialize
            init_request = JSONRPCRequest(
                method="initialize",
                params={
                    "protocolVersion": MCP_PROTOCOL_VERSION,
                    "capabilities": {"tools": {}},
                    "clientInfo": {
                        "name": "kylin-agent",
                        "version": "1.0.0",
                    },
                },
            )
            init_resp = await transport.send_request(init_request)

            if init_resp.is_error:
                logger.error(
                    f"MCP initialize 失败: {server.name} - {init_resp.error}"
                )
                return False

            result = init_resp.result
            if isinstance(result, dict):
                self._server_capabilities[server.id] = result
                server_info = result.get("serverInfo", {})
                logger.info(
                    f"MCP 服务器能力: {server.name} - "
                    f"protocol={result.get('protocolVersion', 'unknown')}, "
                    f"server={server_info.get('name', 'unknown')} v{server_info.get('version', '?')}"
                )

            # initialized 通知
            try:
                notif = JSONRPCNotification(method="notifications/initialized")
                await transport.send_request(
                    JSONRPCRequest(method="notifications/initialized", params={})
                )
            except Exception:
                pass

            self._initialized[server.id] = True
            logger.info(f"MCP 连接成功: {server.name}")
            return True

        except Exception as e:
            logger.exception(f"连接 MCP 服务器异常 {server.name}: {e}")
            return False

    async def disconnect_server(self, server_id: str) -> None:
        """断开单个服务器"""
        transport = self.transports.pop(server_id, None)
        if transport:
            await transport.close()
        self._initialized.pop(server_id, None)
        logger.info(f"MCP 已断开: {server_id}")

    async def disconnect_all(self) -> None:
        """断开所有连接"""
        for sid in list(self.transports.keys()):
            await self.disconnect_server(sid)
        logger.info("所有 MCP 连接已断开")

    # ========================================================================
    # 工具发现
    # ========================================================================

    async def discover_tools(
        self, server_id: Optional[str] = None
    ) -> List[MCPTool]:
        """发现工具列表"""
        discovered: List[MCPTool] = []

        targets = (
            [server_id]
            if server_id
            else [
                sid
                for sid, s in self.servers.items()
                if s.auto_discover and self._initialized.get(sid, False)
            ]
        )

        for sid in targets:
            server = self.servers.get(sid)
            if not server:
                continue

            transport = self.transports.get(sid)
            if not transport:
                continue

            try:
                tools = await self._list_tools(sid, server, transport)
                discovered.extend(tools)
            except Exception as e:
                logger.error(f"发现工具失败 {server.name}: {e}")

        return discovered

    async def _list_tools(
        self,
        server_id: str,
        server: MCPServerInfo,
        transport: MCPHTTPTransport,
    ) -> List[MCPTool]:
        """从单个服务器获取工具列表"""
        request = JSONRPCRequest(method="tools/list", params={})
        response = await transport.send_request(request)

        if response.is_error:
            logger.error(f"tools/list 失败 {server.name}: {response.error}")
            return []

        result = response.result
        if not isinstance(result, dict):
            return []

        raw_tools = result.get("tools", [])
        tools = []

        for raw in raw_tools:
            tool = MCPTool(
                name=raw.get("name", ""),
                description=raw.get("description", ""),
                parameters=raw.get("inputSchema", {}).get("properties", {}),
                required=raw.get("inputSchema", {}).get("required", []),
                server_id=server_id,
                server_name=server.name,
                meta=raw.get("_meta", {}),
            )
            tools.append(tool)

        logger.info(f"发现 {len(tools)} 个工具 (server={server.name})")
        return tools

    # ========================================================================
    # 工具调用
    # ========================================================================

    async def call_tool(
        self, tool_name: str, arguments: Dict[str, Any], server_id: str
    ) -> Dict[str, Any]:
        """
        调用指定 MCP 服务器上的工具

        Args:
            tool_name: 工具名称
            arguments: 工具参数
            server_id: 目标服务器 ID

        Returns:
            {"content": [...], "isError": bool} 或 {"error": str}
        """
        transport = self.transports.get(server_id)
        if not transport:
            return {
                "content": [{"type": "text", "text": f"服务器 {server_id} 无可用连接"}],
                "isError": True,
            }

        try:
            request = JSONRPCRequest(
                method="tools/call",
                params={
                    "name": tool_name,
                    "arguments": arguments,
                },
            )
            response = await transport.send_request(request)

            if response.is_error:
                return {
                    "content": [{"type": "text", "text": json.dumps(response.error)}],
                    "isError": True,
                }

            result = response.result
            if isinstance(result, dict):
                # MCP 标准格式：{content: [...], isError: false}
                if "content" in result:
                    return {
                        "content": result.get("content", []),
                        "isError": result.get("isError", False),
                    }
                # mcp-server 插件原始格式：直接返回结果 dict
                # 包装为 MCP 标准 content 格式
                text_content = json.dumps(result, ensure_ascii=False) if result else ""
                return {
                    "content": [{"type": "text", "text": text_content}],
                    "isError": bool(result.get("blocked") or result.get("error")),
                }

            return {
                "content": [{"type": "text", "text": str(result)}],
                "isError": False,
            }

        except Exception as e:
            logger.exception(f"工具调用异常: {tool_name}")
            return {
                "content": [{"type": "text", "text": f"工具调用异常: {e}"}],
                "isError": True,
            }

    # ========================================================================
    # Prompts & Resources
    # ========================================================================

    async def list_prompts(self) -> List[MCPPrompt]:
        """获取所有服务器的提示模板"""
        prompts = []
        for sid, transport in self.transports.items():
            server = self.servers.get(sid)
            if not server or not self._initialized.get(sid):
                continue
            try:
                request = JSONRPCRequest(method="prompts/list", params={})
                response = await transport.send_request(request)
                if not response.is_error and isinstance(response.result, dict):
                    for p in response.result.get("prompts", []):
                        prompts.append(
                            MCPPrompt(
                                name=p.get("name", ""),
                                description=p.get("description", ""),
                                arguments=p.get("arguments", []),
                                server_id=sid,
                                server_name=server.name,
                            )
                        )
            except Exception as e:
                logger.error(f"获取 prompts 失败 {server.name}: {e}")
        return prompts

    async def list_resources(self) -> List[MCPResource]:
        """获取所有服务器的资源"""
        resources = []
        for sid, transport in self.transports.items():
            server = self.servers.get(sid)
            if not server or not self._initialized.get(sid):
                continue
            try:
                request = JSONRPCRequest(method="resources/list", params={})
                response = await transport.send_request(request)
                if not response.is_error and isinstance(response.result, dict):
                    for r in response.result.get("resources", []):
                        resources.append(
                            MCPResource(
                                uri=r.get("uri", ""),
                                name=r.get("name", ""),
                                description=r.get("description", ""),
                                mime_type=r.get("mimeType", "text/plain"),
                                server_id=sid,
                                server_name=server.name,
                            )
                        )
            except Exception as e:
                logger.error(f"获取 resources 失败 {server.name}: {e}")
        return resources

    async def read_resource(
        self, server_id: str, uri: str
    ) -> Optional[Dict[str, Any]]:
        """读取指定资源"""
        transport = self.transports.get(server_id)
        if not transport:
            return None
        try:
            request = JSONRPCRequest(method="resources/read", params={"uri": uri})
            response = await transport.send_request(request)
            if not response.is_error:
                return response.result
        except Exception as e:
            logger.error(f"读取资源失败: {e}")
        return None

    # ========================================================================
    # 服务器能力查询
    # ========================================================================

    def is_initialized(self, server_id: str) -> bool:
        return self._initialized.get(server_id, False)

    def get_server_capabilities(self, server_id: str) -> Optional[Dict[str, Any]]:
        return self._server_capabilities.get(server_id)


# ========================================================================
# 全局单例
# ========================================================================

_mcp_client: Optional[MCPClient] = None


async def get_mcp_client() -> MCPClient:
    """获取全局 MCP 客户端单例"""
    global _mcp_client
    if _mcp_client is None:
        _mcp_client = MCPClient()
    return _mcp_client


# ========================================================================
# 辅助函数（向后兼容旧版测试/调用方）
# ========================================================================

def _ok(result: Dict[str, Any] | None = None) -> Dict[str, Any]:
    """构造成功响应 {ok: True, result: {...}, error: None}"""
    return {"ok": True, "result": result or {}, "error": None}


def _fail(error: str, result: Dict[str, Any] | None = None) -> Dict[str, Any]:
    """构造失败响应 {ok: False, result: None, error: str}"""
    return {"ok": False, "result": result, "error": error}
