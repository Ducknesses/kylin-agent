"""Pydantic 数据模型"""
from datetime import datetime
from typing import Optional, List
from pydantic import BaseModel, Field


class SessionCreate(BaseModel):
    """创建会话请求"""
    title: str = Field(default="新会话", max_length=100)


class SessionOut(BaseModel):
    """会话信息响应"""
    id: str
    title: str
    created_at: str


class ChatMessage(BaseModel):
    """WebSocket 消息格式"""
    type: str = Field(..., description="消息类型: user/assistant/risk_alert/confirm/error")
    content: str = Field(..., description="消息内容")
    trace_id: Optional[str] = Field(None, description="审计追踪ID")


class AuditRecordOut(BaseModel):
    """审计记录响应"""
    trace_id: str
    timestamp: str
    user_input: str
    intent: Optional[str]
    risk_level: str
    mcp_tool: Optional[str]
    command: Optional[str]
    raw_output: Optional[str]
    llm_reasoning: Optional[str]
    final_response: Optional[str]


class WhitelistUpdate(BaseModel):
    """更新白名单请求"""
    commands: List[str] = Field(..., description="允许的命令列表")


class SystemMetrics(BaseModel):
    """系统指标数据"""
    cpu_percent: float
    load_avg: List[float]
    memory_percent: float
    disk_percent: float
    timestamp: str
