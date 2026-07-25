"""MCP 服务器管理 API —— 支持前端热更新注册/删除/测试"""
from __future__ import annotations

import logging
from typing import Any, Dict, List

from fastapi import APIRouter, Depends, HTTPException, Request

from app.core.auth import AuthContext, AuthLevel
from app.dependencies import require_auth, server_manager
from app.schemas.mcp import (
    MCPServerCreate,
    MCPServerStatus,
    MCPServerTestRequest,
    MCPServerUpdate,
    MCPTestResponse,
    MCPToolInfo,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/mcp/servers", tags=["mcp"])


def _require_admin():
    """MCP 服务器管理需要 ADMIN 权限"""
    return require_auth(AuthLevel.ADMIN)


# ── 列表 ──────────────────────────────────────────────────────────


@router.get("", response_model=List[MCPServerStatus])
async def list_servers(
    auth: AuthContext = Depends(_require_admin()),
) -> List[Dict[str, Any]]:
    """列出所有 MCP 服务器及其状态"""
    statuses = server_manager.list_server_statuses()
    return statuses


# ── 详情 ──────────────────────────────────────────────────────────


@router.get("/{server_id}", response_model=MCPServerStatus)
async def get_server(
    server_id: str,
    auth: AuthContext = Depends(_require_admin()),
) -> Dict[str, Any]:
    """获取单个 MCP 服务器详情"""
    status = server_manager.get_server_status(server_id)
    if not status:
        raise HTTPException(status_code=404, detail=f"MCP 服务器不存在: {server_id}")
    return status


# ── 注册 ──────────────────────────────────────────────────────────


@router.post("")
async def register_server(
    req: MCPServerCreate,
    auth: AuthContext = Depends(_require_admin()),
) -> Dict[str, Any]:
    """注册新的 MCP 服务器（自动连接并发现工具）"""
    from app.mcp.client import MCPServerInfo, MCPTransport

    transport = MCPTransport(req.transport) if req.transport in ("sse", "streamable_http", "stdio") else MCPTransport.SSE

    info = MCPServerInfo(
        id=req.id,
        name=req.name,
        url=req.url,
        transport=transport,
        auth_token=req.auth_token,
        enabled=req.enabled,
        auto_discover=req.auto_discover,
    )

    result = await server_manager.register_and_connect(info)

    if not result["success"]:
        raise HTTPException(status_code=400, detail=result.get("error", "注册失败"))

    return result


# ── 删除 ──────────────────────────────────────────────────────────


@router.delete("/{server_id}")
async def delete_server(
    server_id: str,
    auth: AuthContext = Depends(_require_admin()),
) -> Dict[str, Any]:
    """注销 MCP 服务器并断开连接"""
    result = await server_manager.unregister_and_disconnect(server_id)

    if not result["success"]:
        raise HTTPException(status_code=404, detail=result.get("error", "服务器不存在"))

    return result


# ── 更新 ──────────────────────────────────────────────────────────


@router.put("/{server_id}")
async def update_server(
    server_id: str,
    req: MCPServerUpdate,
    auth: AuthContext = Depends(_require_admin()),
) -> Dict[str, Any]:
    """更新 MCP 服务器配置并重连"""
    updates = req.dict(exclude_unset=True)
    if not updates:
        raise HTTPException(status_code=400, detail="没有要更新的字段")

    result = await server_manager.update_server(server_id, updates)

    if not result["success"]:
        raise HTTPException(status_code=400, detail=result.get("error", "更新失败"))

    return result


# ── 刷新 ──────────────────────────────────────────────────────────


@router.post("/{server_id}/refresh")
async def refresh_server(
    server_id: str,
    auth: AuthContext = Depends(_require_admin()),
) -> Dict[str, Any]:
    """刷新 MCP 服务器的工具列表"""
    result = await server_manager.refresh_server(server_id)

    if not result["success"]:
        raise HTTPException(status_code=400, detail=result.get("error", "刷新失败"))

    return result


# ── 测试连接 ──────────────────────────────────────────────────────


@router.post("/test", response_model=MCPTestResponse)
async def test_connection(
    req: MCPServerTestRequest,
    auth: AuthContext = Depends(_require_admin()),
) -> Dict[str, Any]:
    """测试 MCP 服务器连接（不注册，不持久化）"""
    result = await server_manager.test_connection(
        url=req.url,
        transport=req.transport,
        auth_token=req.auth_token,
    )
    return result