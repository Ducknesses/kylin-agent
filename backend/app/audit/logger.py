"""安全审计日志记录 —— 委托 AuditService

为保持向后兼容，保留 log_chain / count_audit / query_audit 函数签名，
内部委托给 AuditService 单例。

新代码应直接使用 AuditService 而非本模块的独立函数。
"""

import logging
from typing import Optional

from app.services.audit_service import AuditService

logger = logging.getLogger(__name__)

# 模块级 AuditService 单例（与 app/dependencies.py 共享同一实例）
_service = AuditService()


async def log_chain(
    trace_id: str,
    user_input: str,
    risk_level: str,
    intent: Optional[str] = None,
    mcp_tool: Optional[str] = None,
    command: Optional[str] = None,
    raw_output: Optional[str] = None,
    llm_reasoning: Optional[str] = None,
    final_response: Optional[str] = None,
) -> None:
    """
    记录一次安全审计链路（委托 AuditService.save_event）

    参数:
        trace_id: 审计追踪 ID
        user_input: 用户原始输入
        risk_level: 风险等级 high/medium/low
        intent: LLM 解析的意图
        mcp_tool: 调用的 MCP 工具名
        command: 执行的命令
        raw_output: 原始输出
        llm_reasoning: 可展示的安全摘要（实际存储时强制为 None）
        final_response: 最终返回给用户的响应
    """
    await _service.save_event(
        trace_id=trace_id,
        user_input=user_input,
        risk_level=risk_level,
        intent=intent,
        mcp_tool=mcp_tool,
        command=command,
        raw_output=raw_output,
        llm_reasoning=llm_reasoning,
        final_response=final_response,
        event_type="tool_call",
    )


async def count_audit(
    start_date: str | None = None,
    end_date: str | None = None,
) -> int:
    """查询审计日志总条数 —— 用于前端分页表格 total 字段"""
    return await _service.count_records(start_date=start_date, end_date=end_date)


async def query_audit(
    limit: int = 50,
    offset: int = 0,
    start_date: str | None = None,
    end_date: str | None = None,
):
    """查询审计日志（委托 AuditService.list_records）"""
    return await _service.list_records(
        limit=limit, offset=offset,
        start_date=start_date, end_date=end_date,
    )
