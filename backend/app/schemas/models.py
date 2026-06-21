"""Pydantic 数据模型 —— 对齐最新前后端 API 统一规范 v1.0"""
from typing import Literal

from pydantic import BaseModel, Field


class SessionCreate(BaseModel):
    """创建会话请求"""
    title: str = Field(default="新会话", max_length=100)


class SessionOut(BaseModel):
    """会话信息响应"""
    id: str
    title: str
    created_at: str


class SessionMessage(BaseModel):
    """会话历史消息"""
    role: str  # "user" | "assistant"
    content: str
    timestamp: str
    tool_calls: list[dict] | None = None


class SessionMessagesOut(BaseModel):
    """会话历史响应"""
    session_id: str
    messages: list[SessionMessage] = Field(default_factory=list)


# ── WebSocket 消息模型 ────────────────────────────────────────────


class ChatMessage(BaseModel):
    """WebSocket 消息格式（前端 → 后端）

    支持类型: chat / confirm / ping
    """
    type: str = Field(..., description="消息类型: chat / confirm / ping")
    content: str | None = Field(None, description="消息内容（chat 类型必填）")
    confirm_id: str | None = Field(None, description="确认操作 ID（confirm 类型）")
    decision: Literal["approve", "reject"] | None = Field(
        None,
        description="决策: approve / reject（confirm 类型）",
    )


# ── 审计模型 ──────────────────────────────────────────────────────


class AuditRecordOut(BaseModel):
    """审计记录响应 —— 对齐 /api/audit items 字段"""
    trace_id: str
    timestamp: str
    user_input: str
    intent: str | None
    risk_level: str
    mcp_tool: str | None
    command: str | None
    raw_output: str | None
    llm_reasoning: str | None
    final_response: str | None


# ── 白名单配置模型 ────────────────────────────────────────────────


class WhitelistCommandEntry(BaseModel):
    """白名单命令条目"""
    pattern: str
    role: Literal["agent-read", "agent-op", "agent-admin"]
    risk: Literal["low", "medium", "high"]


class WhitelistUpdate(BaseModel):
    """更新白名单请求"""
    commands: list[WhitelistCommandEntry]
    blocked_patterns: list[str] = Field(default_factory=list)


# ── 监控指标模型 ──────────────────────────────────────────────────


class CPUMetrics(BaseModel):
    """CPU 指标"""
    percent: float
    cores: int


class MemoryMetrics(BaseModel):
    """内存指标（单位 GB）"""
    percent: float
    total_gb: float
    used_gb: float


class DiskMetrics(BaseModel):
    """磁盘指标（单位 GB）"""
    percent: float
    total_gb: float
    used_gb: float


class NetworkMetrics(BaseModel):
    """网络指标（单位 kbps）"""
    rx_kbps: float
    tx_kbps: float


class SystemMetrics(BaseModel):
    """系统指标 REST 快照 —— 嵌套结构，用于 /api/monitor/metrics"""
    cpu: CPUMetrics
    memory: MemoryMetrics
    disk: DiskMetrics
    network: NetworkMetrics
    timestamp: str


class SSEMetrics(BaseModel):
    """SSE 流式指标 —— 扁平结构，用于 /api/monitor/stream"""
    cpu_percent: float
    load_avg: list[float]
    memory_percent: float
    disk_percent: float
    net_in_kbps: float
    net_out_kbps: float
    timestamp: str
