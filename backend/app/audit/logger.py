"""思维链审计日志记录"""
import logging
from datetime import datetime
from typing import Optional

import aiosqlite

from app.audit.models import _compute_hash, get_last_hash
from config import settings

logger = logging.getLogger(__name__)


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
    记录完整思维链到 SQLite

    参数:
        trace_id: 审计追踪 ID
        user_input: 用户原始输入
        risk_level: 风险等级 high/medium/low
        intent: LLM 解析的意图
        mcp_tool: 调用的 MCP 工具名
        command: 执行的命令
        raw_output: 原始输出
        llm_reasoning: LLM 推理过程
        final_response: 最终返回给用户的响应
    """
    timestamp = datetime.now().isoformat()

    record = {
        "trace_id": trace_id,
        "timestamp": timestamp,
        "user_input": user_input,
        "intent": intent,
        "risk_level": risk_level,
        "mcp_tool": mcp_tool,
        "command": command,
        "raw_output": raw_output,
        "llm_reasoning": llm_reasoning,
        "final_response": final_response,
    }

    try:
        async with aiosqlite.connect(settings.SQLITE_DB) as db:
            prev_hash = await get_last_hash(db)
            record_hash = _compute_hash(record, prev_hash)

            await db.execute(
                """
                INSERT INTO audit_chain
                (trace_id, timestamp, user_input, intent, risk_level,
                 mcp_tool, command, raw_output, llm_reasoning, final_response,
                 prev_hash, record_hash)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    trace_id,
                    timestamp,
                    user_input,
                    intent,
                    risk_level,
                    mcp_tool,
                    command,
                    raw_output,
                    llm_reasoning,
                    final_response,
                    prev_hash,
                    record_hash,
                ),
            )
            await db.commit()
        logger.info(f"[Audit] 记录已写入: trace_id={trace_id}")
    except Exception as e:
        logger.error(f"[Audit] 审计日志写入失败: {e}")


async def query_audit(limit: int = 50, offset: int = 0):
    """
    查询审计日志

    返回:
        记录列表
    """
    try:
        async with aiosqlite.connect(settings.SQLITE_DB) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT * FROM audit_chain ORDER BY id DESC LIMIT ? OFFSET ?",
                (limit, offset),
            ) as cursor:
                rows = await cursor.fetchall()
                return [dict(row) for row in rows]
    except Exception as e:
        logger.error(f"[Audit] 查询审计日志失败: {e}")
        return []
