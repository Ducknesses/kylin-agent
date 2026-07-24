"""
MCP 服务器管理器 —— 生命周期管理 + 持久化 + 工具同步

职责：
  - 管理 MCP 服务器注册/注销/连接/断开
  - 持久化服务器列表到 SQLite
  - 连接后自动发现工具并同步到 ToolRegistry
  - 支持前端热更新（注册/删除/测试 MCP 服务器）
"""
from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional

from app.mcp.client import MCPClient, MCPTool, MCPServerInfo, MCPTransport
from app.services.tool_registry import ToolRegistry

logger = logging.getLogger(__name__)

PERSIST_KEY = "mcp_servers"


class MCPServerManager:
    """MCP 服务器生命周期管理器"""

    def __init__(
        self,
        mcp_client: MCPClient,
        tool_registry: ToolRegistry,
    ):
        self._client = mcp_client
        self._registry = tool_registry

    # ========================================================================
    # 持久化
    # ========================================================================

    async def load_from_db(self) -> List[MCPServerInfo]:
        """从 SQLite 加载持久化的服务器列表"""
        from app.audit.models import load_config

        raw = await load_config(PERSIST_KEY)
        if not raw:
            logger.info("[MCPServerManager] 数据库中无 MCP 服务器配置")
            return []

        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            logger.warning("[MCPServerManager] MCP 服务器配置 JSON 解析失败")
            return []

        servers = []
        for item in data:
            try:
                server = MCPServerInfo.from_dict(item)
                servers.append(server)
                self._client.register_server(server)
            except Exception as e:
                logger.error(f"[MCPServerManager] 加载服务器配置失败: {item.get('id', '?')} - {e}")

        logger.info(f"[MCPServerManager] 从数据库加载了 {len(servers)} 个 MCP 服务器")
        return servers

    async def save_to_db(self) -> None:
        """将当前服务器列表持久化到 SQLite"""
        from app.audit.models import save_config

        data = [s.to_dict() for s in self._client.list_servers()]
        await save_config(PERSIST_KEY, json.dumps(data, ensure_ascii=False))
        logger.info(f"[MCPServerManager] 已持久化 {len(data)} 个 MCP 服务器")

    # ========================================================================
    # 连接与工具发现
    # ========================================================================

    async def connect_all_and_discover(self) -> Dict[str, Any]:
        """
        连接所有启用的服务器并发现工具
        启动时调用
        """
        enabled = [s for s in self._client.list_servers() if s.enabled]
        results = []

        for server in enabled:
            result = await self._connect_and_discover_one(server)
            results.append(result)

        success_count = sum(1 for r in results if r["success"])
        logger.info(
            f"[MCPServerManager] 连接完成: {success_count}/{len(enabled)} 成功"
        )
        return {"total": len(enabled), "success": success_count, "results": results}

    async def _connect_and_discover_one(self, server: MCPServerInfo) -> Dict[str, Any]:
        """连接单个服务器并发现工具"""
        result = {
            "server_id": server.id,
            "success": False,
            "tools_count": 0,
            "error": None,
        }

        try:
            connected = await self._client.connect_server(server)
            if not connected:
                result["error"] = "连接或初始化失败"
                return result

            if server.auto_discover:
                tools = await self._client.discover_tools(server.id)
                self._registry.register_from_server(server.id, tools)
                result["tools_count"] = len(tools)
                result["success"] = True
        except Exception as e:
            logger.exception(f"[MCPServerManager] 连接异常 {server.name}: {e}")
            result["error"] = str(e)

        return result

    # ========================================================================
    # 热更新 API
    # ========================================================================

    async def register_and_connect(
        self, info: MCPServerInfo
    ) -> Dict[str, Any]:
        """注册新 MCP 服务器并连接发现工具"""
        # 检查是否已存在
        if info.id in self._client.servers:
            return {"success": False, "error": f"服务器 ID 已存在: {info.id}"}

        self._client.register_server(info)
        result = await self._connect_and_discover_one(info)

        if result["success"]:
            await self.save_to_db()

        return {
            "success": result["success"],
            "server_id": info.id,
            "tools_count": result["tools_count"],
            "error": result["error"],
        }

    async def unregister_and_disconnect(self, server_id: str) -> Dict[str, Any]:
        """注销 MCP 服务器并断开连接"""
        server = self._client.get_server(server_id)
        if not server:
            return {"success": False, "error": f"服务器不存在: {server_id}"}

        # 清除工具注册表
        self._registry.unregister_server(server_id)

        # 断开连接
        await self._client.unregister_server_async(server_id)

        # 持久化
        await self.save_to_db()

        logger.info(f"[MCPServerManager] 服务器已注销: {server.name}")
        return {"success": True, "server_id": server_id}

    async def refresh_server(self, server_id: str) -> Dict[str, Any]:
        """刷新服务器的工具列表（重连 + 重新发现）"""
        server = self._client.get_server(server_id)
        if not server:
            return {"success": False, "error": f"服务器不存在: {server_id}"}

        # 清除旧工具
        self._registry.unregister_server(server_id)

        # 断开旧连接
        await self._client.disconnect_server(server_id)

        # 重连 + 发现
        result = await self._connect_and_discover_one(server)
        return {
            "success": result["success"],
            "server_id": server_id,
            "tools_count": result["tools_count"],
            "error": result["error"],
        }

    async def update_server(
        self, server_id: str, updates: Dict[str, Any]
    ) -> Dict[str, Any]:
        """更新服务器配置并重连"""
        server = self._client.get_server(server_id)
        if not server:
            return {"success": False, "error": f"服务器不存在: {server_id}"}

        # 更新字段
        for key, value in updates.items():
            if value is not None and hasattr(server, key):
                if key == "transport" and isinstance(value, str):
                    value = MCPTransport(value)
                setattr(server, key, value)

        # 重连
        return await self.refresh_server(server_id)

    async def test_connection(
        self, url: str, transport: str = "sse", auth_token: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        测试 MCP 服务器连接（不注册，不持久化）
        返回连接测试结果含工具预览
        """
        server = MCPServerInfo(
            id="__test__",
            name="Test",
            url=url,
            transport=MCPTransport(transport) if transport in ("sse", "streamable_http", "stdio") else MCPTransport.SSE,
            auth_token=auth_token,
            enabled=True,
            auto_discover=True,
        )

        result = {
            "success": False,
            "protocol_version": None,
            "server_info": None,
            "capabilities": None,
            "tools": [],
            "tools_count": 0,
            "error": None,
        }

        try:
            # 临时注册
            self._client.register_server(server)

            connected = await self._client.connect_server(server)
            if not connected:
                result["error"] = "连接或初始化失败"
                return result

            # 获取能力信息
            caps = self._client.get_server_capabilities("__test__")
            if caps:
                result["protocol_version"] = caps.get("protocolVersion")
                result["server_info"] = caps.get("serverInfo")
                result["capabilities"] = {
                    k: v for k, v in caps.items()
                    if k not in ("protocolVersion", "serverInfo")
                }

            # 发现工具
            tools = await self._client.discover_tools("__test__")
            result["tools"] = [
                {
                    "name": t.name,
                    "description": t.description,
                    "parameters": t.parameters,
                    "required": t.required,
                }
                for t in tools
            ]
            result["tools_count"] = len(tools)
            result["success"] = True

        except Exception as e:
            logger.exception(f"[MCPServerManager] 连接测试异常: {e}")
            result["error"] = str(e)
        finally:
            # 清理临时注册
            await self._client.unregister_server_async("__test__")

        return result

    # ========================================================================
    # 状态查询
    # ========================================================================

    def get_server_status(self, server_id: str) -> Optional[Dict[str, Any]]:
        """获取服务器状态详情"""
        server = self._client.get_server(server_id)
        if not server:
            return None

        tools = self._registry.get_tools_for_server(server_id)
        caps = self._client.get_server_capabilities(server_id)

        return {
            "id": server.id,
            "name": server.name,
            "url": server.url,
            "transport": server.transport.value,
            "auth_token": server.auth_token or "",
            "enabled": server.enabled,
            "connected": server.id in self._client.transports and self._client.is_initialized(server.id),
            "initialized": self._client.is_initialized(server.id),
            "tools_count": len(tools),
            "tools": [
                {
                    "name": t.name,
                    "description": t.description,
                    "parameters": t.parameters,
                    "required": t.required,
                }
                for t in tools
            ],
            "server_info": caps.get("serverInfo") if caps else None,
            "error": None,
        }

    def list_server_statuses(self) -> List[Dict[str, Any]]:
        """列出所有服务器状态"""
        return [
            status
            for s in self._client.list_servers()
            if (status := self.get_server_status(s.id)) is not None
        ]