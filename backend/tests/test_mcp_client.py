"""MCP Client 测试 —— JSON-RPC 2.0 协议

覆盖：
  - 数据模型：MCPServerInfo、MCPTool、JSONRPCRequest/Response 编解码
  - MCPClient：注册/管理/连接/断连/工具发现/工具调用
  - 辅助函数：_ok / _fail

Mock HTTP transport，不发起真实网络请求。
"""
import json
from unittest.mock import AsyncMock, patch, MagicMock

import pytest

from app.mcp.client import (
    MCPClient,
    MCPTransport,
    MCPServerInfo,
    MCPTool,
    MCPPrompt,
    MCPResource,
    JSONRPCRequest,
    JSONRPCResponse,
    JSONRPCNotification,
    _ok,
    _fail,
    get_mcp_client,
)


# ═══════════════════════════════════════════════════════════════════════
# 数据模型测试
# ═══════════════════════════════════════════════════════════════════════

class TestMCPServerInfo:
    """MCPServerInfo 序列化/反序列化"""

    def test_defaults(self):
        s = MCPServerInfo(id="srv-1", name="test-server")
        assert s.id == "srv-1"
        assert s.name == "test-server"
        assert s.url == ""
        assert s.transport == MCPTransport.SSE
        assert s.auth_token is None
        assert s.enabled is True
        assert s.auto_discover is True
        assert s.tags == []
        assert s.metadata == {}

    def test_to_dict(self):
        s = MCPServerInfo(
            id="srv-1", name="test", url="http://localhost:8001",
            transport=MCPTransport.STREAMABLE_HTTP, auth_token="tok123",
            tags=["monitor"], metadata={"env": "prod"},
        )
        d = s.to_dict()
        assert d["id"] == "srv-1"
        assert d["name"] == "test"
        assert d["url"] == "http://localhost:8001"
        assert d["transport"] == "streamable_http"
        assert d["auth_token"] == "tok123"
        assert d["enabled"] is True
        assert d["auto_discover"] is True
        assert d["tags"] == ["monitor"]
        assert d["metadata"] == {"env": "prod"}

    def test_to_dict_auth_token_none(self):
        s = MCPServerInfo(id="srv-2", name="no-auth")
        assert s.to_dict()["auth_token"] == ""

    def test_from_dict_minimal(self):
        s = MCPServerInfo.from_dict({"id": "srv-1", "name": "test"})
        assert s.id == "srv-1"
        assert s.name == "test"
        assert s.transport == MCPTransport.SSE

    def test_from_dict_full(self):
        d = {
            "id": "srv-3", "name": "full", "url": "http://x:8001",
            "transport": "stdio", "auth_token": "abc",
            "enabled": False, "auto_discover": False,
            "tags": ["a"], "metadata": {"k": "v"},
        }
        s = MCPServerInfo.from_dict(d)
        assert s.transport == MCPTransport.STDIO
        assert s.auth_token == "abc"
        assert s.enabled is False
        assert s.auto_discover is False

    def test_from_dict_transport_invalid_raises(self):
        """非法的 transport 字符串触发 ValueError"""
        with pytest.raises(ValueError):
            MCPServerInfo.from_dict({"id": "x", "name": "x", "transport": "invalid_mode"})


class TestJSONRPCMessages:
    """JSON-RPC 2.0 请求/响应编解码"""

    def test_request_to_dict(self):
        req = JSONRPCRequest(method="tools/list", params={}, id="req-1")
        d = req.to_dict()
        assert d["jsonrpc"] == "2.0"
        assert d["id"] == "req-1"
        assert d["method"] == "tools/list"
        assert d["params"] == {}

    def test_request_to_dict_with_params(self):
        req = JSONRPCRequest(
            method="tools/call",
            params={"name": "sys_info", "arguments": {"metric": "cpu"}},
        )
        d = req.to_dict()
        assert d["params"]["name"] == "sys_info"
        assert d["params"]["arguments"]["metric"] == "cpu"

    def test_request_auto_id(self):
        req1 = JSONRPCRequest(method="test")
        req2 = JSONRPCRequest(method="test")
        assert req1.id != req2.id  # unique UUIDs

    def test_notification_to_dict(self):
        notif = JSONRPCNotification(method="notifications/initialized")
        d = notif.to_dict()
        assert d["jsonrpc"] == "2.0"
        assert d["method"] == "notifications/initialized"
        assert "id" not in d  # notifications have no id

    def test_response_from_dict_success(self):
        data = {
            "jsonrpc": "2.0",
            "id": "resp-1",
            "result": {"tools": [{"name": "sys_info", "inputSchema": {}}]},
        }
        resp = JSONRPCResponse.from_dict(data)
        assert resp.id == "resp-1"
        assert resp.is_error is False
        assert resp.result["tools"][0]["name"] == "sys_info"
        assert resp.error is None

    def test_response_from_dict_error(self):
        data = {
            "jsonrpc": "2.0",
            "id": "resp-2",
            "error": {"code": -32601, "message": "Method not found"},
        }
        resp = JSONRPCResponse.from_dict(data)
        assert resp.is_error is True
        assert resp.error["code"] == -32601
        assert resp.result is None

    def test_response_is_error_default(self):
        resp = JSONRPCResponse(id="x", result={})
        assert resp.is_error is False
        resp = JSONRPCResponse(id="x", error={"code": -1})
        assert resp.is_error is True


class TestMCPTool:
    """MCPTool 模型"""

    def test_basic_fields(self):
        t = MCPTool(name="sys_info", description="系统信息",
                    parameters={"metric": {"type": "string"}},
                    required=["metric"],
                    server_id="srv-1", server_name="test")
        assert t.name == "sys_info"
        assert t.server_id == "srv-1"
        assert t.meta == {}

    def test_to_openai_function(self):
        t = MCPTool(
            name="df", description="磁盘使用",
            parameters={"path": {"type": "string"}},
            required=["path"],
        )
        func = t.to_openai_function()
        assert func["type"] == "function"
        assert func["function"]["name"] == "df"
        assert func["function"]["parameters"]["type"] == "object"
        assert func["function"]["parameters"]["required"] == ["path"]

    def test_to_openai_function_empty_params(self):
        t = MCPTool(name="uptime")
        func = t.to_openai_function()
        assert func["function"]["parameters"]["type"] == "object"
        assert func["function"]["parameters"]["properties"] == {}


# ═══════════════════════════════════════════════════════════════════════
# _ok / _fail 辅助函数
# ═══════════════════════════════════════════════════════════════════════

class TestHelpers:
    def test_ok_default(self):
        result = _ok()
        assert result == {"ok": True, "result": {}, "error": None}

    def test_ok_with_data(self):
        result = _ok({"cpu": 23.5})
        assert result == {"ok": True, "result": {"cpu": 23.5}, "error": None}

    def test_fail(self):
        result = _fail("timeout")
        assert result == {"ok": False, "result": None, "error": "timeout"}

    def test_fail_with_result(self):
        result = _fail("blocked", {"reason": "danger"})
        assert result["ok"] is False
        assert result["result"] == {"reason": "danger"}
        assert result["error"] == "blocked"


# ═══════════════════════════════════════════════════════════════════════
# MCPClient 测试
# ═══════════════════════════════════════════════════════════════════════

class TestMCPClientLifecycle:
    """MCPClient 构造、注册/注销、连接状态"""

    def test_constructor_empty(self):
        client = MCPClient()
        assert client.list_servers() == []
        assert client.is_connected is False

    def test_register_server(self):
        client = MCPClient()
        srv = MCPServerInfo(id="srv-1", name="test", url="http://localhost:8001")
        client.register_server(srv)
        assert len(client.list_servers()) == 1
        assert client.get_server("srv-1") is srv
        assert client.is_initialized("srv-1") is False

    def test_register_duplicate(self):
        client = MCPClient()
        srv1 = MCPServerInfo(id="srv-1", name="first")
        srv2 = MCPServerInfo(id="srv-1", name="second")
        client.register_server(srv1)
        client.register_server(srv2)
        assert client.get_server("srv-1").name == "second"

    def test_unregister_server(self):
        client = MCPClient()
        srv = MCPServerInfo(id="srv-1", name="test")
        client.register_server(srv)
        client.unregister_server("srv-1")
        assert client.get_server("srv-1") is None
        assert client.list_servers() == []

    def test_unregister_nonexistent(self):
        client = MCPClient()
        client.unregister_server("nonexistent")  # no error

    def test_list_servers_copies(self):
        client = MCPClient()
        client.register_server(MCPServerInfo(id="a", name="A"))
        client.register_server(MCPServerInfo(id="b", name="B"))
        servers = client.list_servers()
        assert len(servers) == 2
        assert {s.id for s in servers} == {"a", "b"}

    def test_is_connected_none_initialized(self):
        client = MCPClient()
        assert client.is_connected is False

    def test_is_connected_after_register_only(self):
        client = MCPClient()
        client.register_server(MCPServerInfo(id="a", name="A"))
        assert client.is_connected is False  # not yet initialized

    def test_get_server_capabilities_default(self):
        client = MCPClient()
        assert client.get_server_capabilities("nonexistent") is None

    def test_is_initialized_default(self):
        client = MCPClient()
        assert client.is_initialized("any") is False


class TestMCPClientConnect:
    """connect_server 模拟 HTTP transport"""

    @pytest.fixture
    def client(self):
        return MCPClient()

    @pytest.fixture
    def server(self):
        return MCPServerInfo(
            id="mcp-1", name="test-mcp", url="http://localhost:8001",
            transport=MCPTransport.STREAMABLE_HTTP,
        )

    @pytest.mark.asyncio
    async def test_connect_success(self, client, server):
        """模拟成功握手：initialize → 返回 capabilities"""
        mock_transport = MagicMock()
        init_resp = JSONRPCResponse(
            id="init-1",
            result={
                "protocolVersion": "2024-11-05",
                "serverInfo": {"name": "kylin-mcp", "version": "1.0"},
                "capabilities": {"tools": {}},
            },
        )

        async def mock_send_request(request):
            if request.method == "initialize":
                return init_resp
            # notifications/initialized
            resp = JSONRPCResponse(id=request.id, result={})
            return resp

        mock_transport.send_request = mock_send_request

        with patch(
            "app.mcp.client.MCPHTTPTransport", return_value=mock_transport
        ):
            result = await client.connect_server(server)
            assert result is True
            assert client.is_initialized("mcp-1") is True
            caps = client.get_server_capabilities("mcp-1")
            assert caps is not None
            assert caps["serverInfo"]["name"] == "kylin-mcp"

    @pytest.mark.asyncio
    async def test_connect_initialize_error(self, client, server):
        """initialize 返回错误时 connect 失败"""
        mock_transport = MagicMock()
        error_resp = JSONRPCResponse(
            id="init-1",
            error={"code": -32000, "message": "Server unavailable"},
        )

        async def mock_send_request(request):
            return error_resp

        mock_transport.send_request = mock_send_request

        with patch(
            "app.mcp.client.MCPHTTPTransport", return_value=mock_transport
        ):
            result = await client.connect_server(server)
            assert result is False
            assert client.is_initialized("mcp-1") is False

    @pytest.mark.asyncio
    async def test_connect_transport_exception(self, client, server):
        """transport 抛异常时 connect 返回 False"""
        mock_transport = MagicMock()
        mock_transport.send_request = AsyncMock(
            side_effect=Exception("Connection refused")
        )

        with patch(
            "app.mcp.client.MCPHTTPTransport", return_value=mock_transport
        ):
            result = await client.connect_server(server)
            assert result is False

    @pytest.mark.asyncio
    async def test_connect_stdio_not_implemented(self, client):
        """STDIO 传输返回 False"""
        server = MCPServerInfo(id="srv", name="srv", transport=MCPTransport.STDIO)
        result = await client.connect_server(server)
        assert result is False


class TestMCPClientDiscoverTools:
    """discover_tools 工具发现"""

    @pytest.fixture
    def client(self):
        c = MCPClient()
        srv = MCPServerInfo(id="mcp-1", name="test", url="http://localhost:8001")
        c.register_server(srv)
        return c, srv

    @pytest.mark.asyncio
    async def test_discover_tools_success(self, client):
        """模拟 tools/list 响应，验证 MCPTool 解析"""
        cli, srv = client

        # 创建 mock transport 并注册
        mock_transport = MagicMock()
        cli.transports["mcp-1"] = mock_transport
        cli._initialized["mcp-1"] = True

        tools_resp = JSONRPCResponse(
            id="tl-1",
            result={
                "tools": [
                    {
                        "name": "sys_info",
                        "description": "系统信息查询",
                        "inputSchema": {
                            "type": "object",
                            "properties": {
                                "metric": {
                                    "type": "string",
                                    "enum": ["cpu", "memory", "disk"],
                                }
                            },
                            "required": ["metric"],
                        },
                        "_meta": {"category": "monitor", "suggested_risk": "low"},
                    },
                    {
                        "name": "cmd_exec",
                        "description": "命令执行",
                        "inputSchema": {
                            "type": "object",
                            "properties": {},
                        },
                    },
                ]
            },
        )

        async def mock_send(request):
            return tools_resp

        mock_transport.send_request = mock_send

        tools = await cli.discover_tools()
        assert len(tools) == 2

        sys_info = tools[0]
        assert sys_info.name == "sys_info"
        assert sys_info.description == "系统信息查询"
        assert sys_info.parameters["metric"]["type"] == "string"
        assert sys_info.required == ["metric"]
        assert sys_info.server_id == "mcp-1"
        assert sys_info.server_name == "test"
        assert sys_info.meta == {"category": "monitor", "suggested_risk": "low"}

        cmd_exec = tools[1]
        assert cmd_exec.name == "cmd_exec"
        assert cmd_exec.parameters == {}
        assert cmd_exec.required == []

    @pytest.mark.asyncio
    async def test_discover_tools_empty(self, client):
        """空工具列表"""
        cli, srv = client

        mock_transport = MagicMock()
        cli.transports["mcp-1"] = mock_transport
        cli._initialized["mcp-1"] = True

        resp = JSONRPCResponse(id="tl-1", result={"tools": []})

        async def mock_send(request):
            return resp

        mock_transport.send_request = mock_send
        tools = await cli.discover_tools()
        assert tools == []

    @pytest.mark.asyncio
    async def test_discover_tools_error_response(self, client):
        """tools/list 返回错误"""
        cli, srv = client

        mock_transport = MagicMock()
        cli.transports["mcp-1"] = mock_transport
        cli._initialized["mcp-1"] = True

        resp = JSONRPCResponse(
            id="tl-1", error={"code": -32601, "message": "Not found"}
        )

        async def mock_send(request):
            return resp

        mock_transport.send_request = mock_send
        tools = await cli.discover_tools()
        assert tools == []  # error → empty list

    @pytest.mark.asyncio
    async def test_discover_tools_transport_exception(self, client):
        """transport 异常时返回空"""
        cli, srv = client

        mock_transport = MagicMock()
        cli.transports["mcp-1"] = mock_transport
        cli._initialized["mcp-1"] = True
        mock_transport.send_request = AsyncMock(
            side_effect=Exception("timeout")
        )

        tools = await cli.discover_tools()
        assert tools == []

    @pytest.mark.asyncio
    async def test_discover_tools_filter_server(self, client):
        """按 server_id 过滤"""
        cli, srv = client

        srv2 = MCPServerInfo(id="mcp-2", name="second", url="http://localhost:8002")
        cli.register_server(srv2)

        t1 = MagicMock()
        cli.transports["mcp-1"] = t1
        cli._initialized["mcp-1"] = True
        t2 = MagicMock()
        cli.transports["mcp-2"] = t2
        cli._initialized["mcp-2"] = True

        resp = JSONRPCResponse(
            id="tl-1",
            result={"tools": [{"name": "t1", "inputSchema": {}}]},
        )
        t1.send_request = AsyncMock(return_value=resp)
        t2.send_request = AsyncMock(return_value=resp)

        # 只发现 mcp-1
        tools = await cli.discover_tools(server_id="mcp-1")
        assert len(tools) == 1
        t1.send_request.assert_called_once()
        t2.send_request.assert_not_called()

    @pytest.mark.asyncio
    async def test_discover_tools_skips_uninitialized(self, client):
        """未初始化的 server 跳过"""
        cli, srv = client
        # auto_discover=True 但未 initialized
        tools = await cli.discover_tools()
        assert tools == []

    @pytest.mark.asyncio
    async def test_discover_tools_skips_auto_discover_false(self, client):
        """auto_discover=False 的 server 跳过"""
        cli, srv = client
        srv2 = MCPServerInfo(
            id="mcp-2", name="s2", url="http://localhost:8002", auto_discover=False
        )
        cli.register_server(srv2)

        t1 = MagicMock()
        cli.transports["mcp-1"] = t1
        cli._initialized["mcp-1"] = True
        t2 = MagicMock()
        cli.transports["mcp-2"] = t2
        cli._initialized["mcp-2"] = True

        resp = JSONRPCResponse(
            id="tl-1", result={"tools": [{"name": "t", "inputSchema": {}}]}
        )
        t1.send_request = AsyncMock(return_value=resp)
        t2.send_request = AsyncMock(return_value=resp)

        tools = await cli.discover_tools()
        assert len(tools) == 1  # only mcp-1 (auto_discover=True)
        t2.send_request.assert_not_called()


class TestMCPClientCallTool:
    """call_tool 测试"""

    @pytest.fixture
    def client(self):
        c = MCPClient()
        srv = MCPServerInfo(id="mcp-1", name="test", url="http://localhost:8001")
        c.register_server(srv)
        return c

    @pytest.mark.asyncio
    async def test_call_tool_no_transport(self, client):
        """没有 transport 时返回错误"""
        result = await client.call_tool("sys_info", {"metric": "cpu"}, "mcp-1")
        assert result["isError"] is True
        assert "无可用连接" in result["content"][0]["text"]

    @pytest.mark.asyncio
    async def test_call_tool_success_standard_format(self, client):
        """MCP 标准格式 {content: [...], isError: false}"""
        mock_transport = MagicMock()
        client.transports["mcp-1"] = mock_transport

        resp = JSONRPCResponse(id="tc-1", result={
            "content": [{"type": "text", "text": "CPU: 23.5%"}],
            "isError": False,
        })
        mock_transport.send_request = AsyncMock(return_value=resp)

        result = await client.call_tool("sys_info", {"metric": "cpu"}, "mcp-1")
        assert result["isError"] is False
        assert result["content"][0]["text"] == "CPU: 23.5%"

    @pytest.mark.asyncio
    async def test_call_tool_success_raw_dict_format(self, client):
        """mcp-server 插件原始格式（非标准 content）→ 包装为 content"""
        mock_transport = MagicMock()
        client.transports["mcp-1"] = mock_transport

        # raw plugin return: {"stdout": "...", "returncode": 0}
        resp = JSONRPCResponse(id="tc-1", result={
            "stdout": "Filesystem ...",
            "stderr": "",
            "returncode": 0,
        })
        mock_transport.send_request = AsyncMock(return_value=resp)

        result = await client.call_tool("cmd_exec", {"command": "df -h"}, "mcp-1")
        assert result["isError"] is False
        assert "stdout" in result["content"][0]["text"] or "Filesystem" in result["content"][0]["text"]

    @pytest.mark.asyncio
    async def test_call_tool_blocked_result(self, client):
        """mcp-server 返回 blocked 时 isError=True"""
        mock_transport = MagicMock()
        client.transports["mcp-1"] = mock_transport

        resp = JSONRPCResponse(id="tc-1", result={
            "blocked": True,
            "error": "命令不在白名单中",
        })
        mock_transport.send_request = AsyncMock(return_value=resp)

        result = await client.call_tool("cmd_exec", {"command": "rm -rf /"}, "mcp-1")
        assert result["isError"] is True

    @pytest.mark.asyncio
    async def test_call_tool_rpc_error(self, client):
        """JSON-RPC error 响应"""
        mock_transport = MagicMock()
        client.transports["mcp-1"] = mock_transport

        resp = JSONRPCResponse(id="tc-1", error={
            "code": -32602, "message": "Invalid params"
        })
        mock_transport.send_request = AsyncMock(return_value=resp)

        result = await client.call_tool("bad_tool", {}, "mcp-1")
        assert result["isError"] is True

    @pytest.mark.asyncio
    async def test_call_tool_transport_exception(self, client):
        """transport 异常"""
        mock_transport = MagicMock()
        client.transports["mcp-1"] = mock_transport
        mock_transport.send_request = AsyncMock(
            side_effect=Exception("Network error")
        )

        result = await client.call_tool("sys_info", {}, "mcp-1")
        assert result["isError"] is True
        assert "调用异常" in result["content"][0]["text"]

    @pytest.mark.asyncio
    async def test_call_tool_result_not_dict(self, client):
        """result 不是 dict 时包装为字符串"""
        mock_transport = MagicMock()
        client.transports["mcp-1"] = mock_transport

        resp = JSONRPCResponse(id="tc-1", result="plain text result")
        mock_transport.send_request = AsyncMock(return_value=resp)

        result = await client.call_tool("echo", {}, "mcp-1")
        assert result["isError"] is False
        assert result["content"][0]["text"] == "plain text result"


class TestMCPClientPromptsResources:
    """prompts / resources 测试"""

    @pytest.fixture
    def client(self):
        c = MCPClient()
        srv = MCPServerInfo(id="mcp-1", name="test", url="http://localhost:8001")
        c.register_server(srv)
        mock_t = MagicMock()
        c.transports["mcp-1"] = mock_t
        c._initialized["mcp-1"] = True
        return c

    @pytest.mark.asyncio
    async def test_list_prompts(self, client):
        resp = JSONRPCResponse(id="pl-1", result={
            "prompts": [{"name": "diagnose", "description": "诊断问题"}]
        })
        client.transports["mcp-1"].send_request = AsyncMock(return_value=resp)

        prompts = await client.list_prompts()
        assert len(prompts) == 1
        assert prompts[0].name == "diagnose"
        assert prompts[0].server_id == "mcp-1"

    @pytest.mark.asyncio
    async def test_list_prompts_error(self, client):
        resp = JSONRPCResponse(id="pl-1", error={"code": -1, "message": "err"})
        client.transports["mcp-1"].send_request = AsyncMock(return_value=resp)

        prompts = await client.list_prompts()
        assert prompts == []

    @pytest.mark.asyncio
    async def test_list_resources(self, client):
        resp = JSONRPCResponse(id="rl-1", result={
            "resources": [{"uri": "file:///logs", "name": "系统日志", "mimeType": "text/plain"}]
        })
        client.transports["mcp-1"].send_request = AsyncMock(return_value=resp)

        resources = await client.list_resources()
        assert len(resources) == 1
        assert resources[0].uri == "file:///logs"
        assert resources[0].mime_type == "text/plain"
        assert resources[0].server_id == "mcp-1"

    @pytest.mark.asyncio
    async def test_read_resource(self, client):
        resp = JSONRPCResponse(id="rr-1", result={"contents": [{"text": "log data"}]})
        client.transports["mcp-1"].send_request = AsyncMock(return_value=resp)

        result = await client.read_resource("mcp-1", "file:///logs")
        assert result is not None
        assert result["contents"][0]["text"] == "log data"

    @pytest.mark.asyncio
    async def test_read_resource_no_transport(self, client):
        result = await client.read_resource("no-srv", "file:///x")
        assert result is None

    @pytest.mark.asyncio
    async def test_read_resource_error(self, client):
        resp = JSONRPCResponse(id="rr-1", error={"code": -1, "message": "err"})
        client.transports["mcp-1"].send_request = AsyncMock(return_value=resp)

        result = await client.read_resource("mcp-1", "file:///logs")
        assert result is None


class TestMCPClientDisconnect:
    """断连测试"""

    @pytest.mark.asyncio
    async def test_disconnect_server(self):
        client = MCPClient()
        srv = MCPServerInfo(id="mcp-1", name="test", url="http://localhost:8001")
        client.register_server(srv)

        mock_t = AsyncMock()
        client.transports["mcp-1"] = mock_t
        client._initialized["mcp-1"] = True

        await client.disconnect_server("mcp-1")
        mock_t.close.assert_called_once()
        assert client.is_initialized("mcp-1") is False

    @pytest.mark.asyncio
    async def test_disconnect_nonexistent(self):
        client = MCPClient()
        await client.disconnect_server("nonexistent")  # no error

    @pytest.mark.asyncio
    async def test_disconnect_all(self):
        client = MCPClient()
        for i in range(3):
            sid = f"mcp-{i}"
            client.register_server(MCPServerInfo(id=sid, name=sid, url=f"http://localhost:800{i}"))
            mock_t = AsyncMock()
            client.transports[sid] = mock_t
            client._initialized[sid] = True

        await client.disconnect_all()
        for sid in ["mcp-0", "mcp-1", "mcp-2"]:
            assert client.is_initialized(sid) is False


# ═══════════════════════════════════════════════════════════════════════
# 全局单例
# ═══════════════════════════════════════════════════════════════════════

class TestGetMCPClient:
    @pytest.mark.asyncio
    async def test_singleton(self):
        import app.mcp.client as mod
        mod._mcp_client = None  # reset
        c1 = await get_mcp_client()
        c2 = await get_mcp_client()
        assert c1 is c2