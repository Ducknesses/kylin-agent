"""MCP 服务器管理 —— 请求/响应模型"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class MCPServerCreate(BaseModel):
    """注册新 MCP 服务器"""
    id: str = Field(..., description="唯一标识，如 kylin-main")
    name: str = Field(..., description="显示名称")
    url: str = Field(..., description="MCP 端点，如 http://192.168.56.101:8001")
    transport: str = Field(default="sse", description="传输方式: sse | streamable_http | stdio")
    auth_token: Optional[str] = Field(default=None, description="认证令牌（Bearer）")
    enabled: bool = Field(default=True, description="是否启用")
    auto_discover: bool = Field(default=True, description="自动发现工具")


class MCPServerUpdate(BaseModel):
    """更新 MCP 服务器配置"""
    name: Optional[str] = None
    url: Optional[str] = None
    transport: Optional[str] = None
    auth_token: Optional[str] = None
    enabled: Optional[bool] = None
    auto_discover: Optional[bool] = None


class MCPServerTestRequest(BaseModel):
    """测试 MCP 服务器连接（不注册）"""
    url: str = Field(..., description="MCP 端点")
    transport: str = Field(default="sse")
    auth_token: Optional[str] = None


class MCPToolInfo(BaseModel):
    """工具简要信息（返回给前端）"""
    name: str
    description: str
    parameters: Dict[str, Any] = Field(default_factory=dict)
    required: List[str] = Field(default_factory=list)


class MCPServerStatus(BaseModel):
    """服务器状态"""
    id: str
    name: str
    url: str
    transport: str
    auth_token: Optional[str] = None
    enabled: bool
    connected: bool
    initialized: bool
    tools_count: int
    tools: List[MCPToolInfo] = Field(default_factory=list)
    server_info: Optional[Dict[str, Any]] = None
    error: Optional[str] = None


class MCPServerListResponse(BaseModel):
    """服务器列表响应"""
    servers: List[MCPServerStatus]


class MCPTestResponse(BaseModel):
    """连接测试响应"""
    success: bool
    protocol_version: Optional[str] = None
    server_info: Optional[Dict[str, Any]] = None
    capabilities: Optional[Dict[str, Any]] = None
    tools: List[MCPToolInfo] = Field(default_factory=list)
    tools_count: int = 0
    error: Optional[str] = None