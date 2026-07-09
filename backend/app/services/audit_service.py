"""AuditService —— 结构化审计封装

职责：
  - 统一审计写入入口（save_event / save_context）
  - 查询审计列表（list_records）
  - 敏感信息过滤
  - 自动迁移缺失字段
  - 不调用执行器客户端、不调用工具调用外壳、不记录模型原始思维链

与现有 audit/logger.py 的关系：
  AuditService 封装了 existing log_chain/query_audit 逻辑，增加了字段迁移、
  敏感过滤、event_type/error/session_id 等扩展字段支持。
  原有 log_chain() 仍可独立使用，AuditService 内部复用它而非重写。
"""

import logging
import os
import re
from datetime import datetime
from typing import Any

import aiosqlite

from app.audit.models import _compute_hash, get_last_hash
from config import settings

logger = logging.getLogger(__name__)

# ── 敏感信息过滤 ──────────────────────────────────────────────────────

_SENSITIVE_REPLACE = "[REDACTED]"

_SENSITIVE_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"sk-[a-zA-Z0-9]{20,}", re.IGNORECASE), _SENSITIVE_REPLACE),
    (re.compile(r"Bearer\s+[a-zA-Z0-9\-_\.]+", re.IGNORECASE), f"Bearer {_SENSITIVE_REPLACE}"),
    (re.compile(r'Authorization:\s*Bearer\s+[^\s"\']+', re.IGNORECASE), f"Authorization: Bearer {_SENSITIVE_REPLACE}"),
    (re.compile(r'(?i)deepseek_api_key[=:]\s*[^\s"\']+'), f"DEEPSEEK_API_KEY={_SENSITIVE_REPLACE}"),
    (re.compile(r'(?i)api_key[=:]\s*[^\s"\']+'), f"api_key={_SENSITIVE_REPLACE}"),
    (re.compile(r'(?i)password[=:]\s*[^\s"\']+'), f"password={_SENSITIVE_REPLACE}"),
    (re.compile(r'(?i)secret[=:]\s*[^\s"\']+'), f"secret={_SENSITIVE_REPLACE}"),
    (re.compile(r'(?i)secret_key[=:]\s*[^\s"\']+'), f"secret_key={_SENSITIVE_REPLACE}"),
    (re.compile(r'(?i)private_key[=:]\s*[^\s"\']+'), f"private_key={_SENSITIVE_REPLACE}"),
    (re.compile(r'(?i)access_key[=:]\s*[^\s"\']+'), f"access_key={_SENSITIVE_REPLACE}"),
    (re.compile(r'(?i)access_token[=:]\s*[^\s"\']+'), f"access_token={_SENSITIVE_REPLACE}"),
    (re.compile(r'(?i)token[=:]\s*[^\s"\']+'), f"token={_SENSITIVE_REPLACE}"),
    (re.compile(r'(?i)credential[s]?[=:]\s*[^\s"\']+'), f"credential={_SENSITIVE_REPLACE}"),
    (re.compile(r'eyJ[a-zA-Z0-9\-_]+\.[a-zA-Z0-9\-_]+\.[a-zA-Z0-9\-_]+'), _SENSITIVE_REPLACE),
]


def _sanitize(value: str | None) -> str | None:
    """过滤敏感信息，返回安全字符串"""
    if value is None:
        return None
    text = str(value)
    for pattern, replacement in _SENSITIVE_PATTERNS:
        text = pattern.sub(replacement, text)
    return text


def _truncate(value: str | None, max_len: int = 2000) -> str | None:
    """截断过长文本，避免数据库行膨胀"""
    if value is None:
        return None
    text = str(value)
    if len(text) <= max_len:
        return text
    return text[:max_len] + "...[TRUNCATED]"


# ── 字段迁移 SQL ──────────────────────────────────────────────────────
# 为旧版 audit_chain 表添加缺失字段，幂等执行（列已存在时跳过）
# 使用列表而非 split("--")，避免注释片段被当成 SQL 执行

_MIGRATION_STMTS: list[str] = [
    "ALTER TABLE audit_chain ADD COLUMN session_id TEXT",
    "ALTER TABLE audit_chain ADD COLUMN params TEXT",
    "ALTER TABLE audit_chain ADD COLUMN error TEXT",
    "ALTER TABLE audit_chain ADD COLUMN event_type TEXT",
]


class AuditService:
    """结构化审计服务

    使用方式：
        service = AuditService()
        await service.save_event(trace_id="...", risk_level="low", ...)
        records = await service.list_records(limit=50)
    """

    def __init__(self, db_path: str | None = None) -> None:
        """
        参数:
            db_path: 数据库文件路径，默认使用 settings.SQLITE_DB
        """
        self.db_path = db_path or settings.SQLITE_DB

    async def _ensure_db(self) -> None:
        """确保数据库文件和表存在，执行增量迁移"""
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        async with aiosqlite.connect(self.db_path) as db:
            # 基础表初始化（复用 models.py 的 INIT_SQL）
            from app.audit.models import INIT_SQL
            await db.executescript(INIT_SQL)
            await db.commit()
            # 增量迁移：逐条尝试添加缺失字段，幂等
            for stmt in _MIGRATION_STMTS:
                try:
                    await db.execute(stmt)
                    await db.commit()
                except aiosqlite.OperationalError as exc:
                    if "duplicate column name" in str(exc).lower():
                        logger.debug(f"[AuditService] 字段已存在，跳过: {stmt[:50]}")
                    else:
                        logger.warning(f"[AuditService] 迁移失败: {stmt[:60]} — {exc}")

    # ── 写入方法 ──────────────────────────────────────────────────────

    async def save_event(
        self,
        trace_id: str,
        user_input: str = "",
        risk_level: str = "low",
        session_id: str | None = None,
        intent: str | None = None,
        mcp_tool: str | None = None,
        params: dict | None = None,
        command: str | None = None,
        raw_output: str | None = None,
        final_response: str | None = None,
        error: str | None = None,
        event_type: str = "tool_call",
        llm_reasoning: str | None = None,
    ) -> dict:
        """保存一条结构化审计事件

        所有敏感字段在写入前自动过滤。
        返回写入的记录摘要（不含敏感信息）。
        """
        await self._ensure_db()

        # 过滤敏感信息
        user_input_safe = _sanitize(user_input)
        command_safe = _sanitize(command)
        raw_output_safe = _truncate(_sanitize(raw_output), 2000)
        final_response_safe = _truncate(_sanitize(final_response), 2000)
        error_safe = _sanitize(error)
        params_safe = _sanitize(str(params)) if params else None
        # llm_reasoning 强制为 None——不保存模型原始思维链
        llm_reasoning_safe = None

        timestamp = datetime.now().isoformat()

        record = {
            "trace_id": trace_id,
            "timestamp": timestamp,
            "user_input": user_input_safe or "",
            "intent": intent,
            "risk_level": risk_level,
            "mcp_tool": mcp_tool,
            "command": command_safe,
            "raw_output": raw_output_safe,
            "llm_reasoning": llm_reasoning_safe,
            "final_response": final_response_safe,
        }

        try:
            async with aiosqlite.connect(self.db_path) as db:
                prev_hash = await get_last_hash(db)
                record_hash = _compute_hash(record, prev_hash)

                # 检查 session_id/params/error/event_type 列是否存在
                has_extended = await self._has_extended_columns(db)

                if has_extended:
                    await db.execute(
                        """
                        INSERT INTO audit_chain
                        (trace_id, timestamp, user_input, intent, risk_level,
                         mcp_tool, command, raw_output, llm_reasoning, final_response,
                         prev_hash, record_hash, session_id, params, error, event_type)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            trace_id, timestamp, user_input_safe or "", intent, risk_level,
                            mcp_tool, command_safe, raw_output_safe, llm_reasoning_safe, final_response_safe,
                            prev_hash, record_hash, session_id, params_safe, error_safe, event_type,
                        ),
                    )
                else:
                    # 回退到基础字段
                    await db.execute(
                        """
                        INSERT INTO audit_chain
                        (trace_id, timestamp, user_input, intent, risk_level,
                         mcp_tool, command, raw_output, llm_reasoning, final_response,
                         prev_hash, record_hash)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            trace_id, timestamp, user_input_safe or "", intent, risk_level,
                            mcp_tool, command_safe, raw_output_safe, llm_reasoning_safe, final_response_safe,
                            prev_hash, record_hash,
                        ),
                    )
                await db.commit()

            logger.info(f"[AuditService] 事件已记录: trace_id={trace_id}, event_type={event_type}")
            return {"trace_id": trace_id, "timestamp": timestamp, "risk_level": risk_level, "event_type": event_type}
        except Exception as e:
            logger.error(f"[AuditService] 审计写入失败: {e}")
            return {"trace_id": trace_id, "error": str(e)}

    async def save_context(self, ctx: Any, event_type: str = "chat") -> dict:
        """从 AgentContext 生成审计记录

        读取 ctx.trace_id / session_id / user_input / intent / risk_level /
        tool_calls / observations / final_response。
        不要求 ctx 有 fix_options，不保存 chain-of-thought。
        """
        tool_calls = getattr(ctx, "tool_calls", []) or []
        observations = getattr(ctx, "observations", []) or []

        # mcp_tool：取最后一个工具名，多个时逗号拼接
        tools = [tc.get("tool", "") for tc in tool_calls if isinstance(tc, dict)]
        mcp_tool = ", ".join(tools) if tools else None

        # command：从 tool_calls 中提取
        command = None
        for tc in tool_calls:
            if isinstance(tc, dict) and tc.get("tool") == "cmd_exec":
                command = tc.get("params", {}).get("command")
                break

        # raw_output：摘要 observations
        raw_parts = []
        for obs in observations:
            if isinstance(obs, dict):
                ok = obs.get("ok", False)
                err = obs.get("error", "")
                raw_parts.append(f"[{'OK' if ok else 'ERR'}] {err}" if err else f"[{'OK' if ok else 'ERR'}]")
        raw_output = "; ".join(raw_parts) if raw_parts else None

        # final_response
        final_response = getattr(ctx, "final_response", None)

        # error：从 observations 收集失败
        errors = [obs.get("error", "") for obs in observations if isinstance(obs, dict) and not obs.get("ok") and obs.get("error")]
        error = "; ".join(errors) if errors else None

        return await self.save_event(
            trace_id=getattr(ctx, "trace_id", ""),
            session_id=getattr(ctx, "session_id", None),
            user_input=getattr(ctx, "user_input", ""),
            intent=getattr(ctx, "intent", None),
            risk_level=getattr(ctx, "risk_level", "low"),
            mcp_tool=mcp_tool,
            command=command,
            raw_output=raw_output,
            final_response=final_response,
            error=error,
            event_type=event_type,
        )

    # ── 查询方法 ──────────────────────────────────────────────────────

    async def list_records(
        self,
        limit: int = 50,
        offset: int = 0,
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> list[dict]:
        """分页查询审计记录，返回数组

        与 /api/audit v1.1 规范对齐：返回 list，不是 {records, total}。
        """
        await self._ensure_db()
        try:
            async with aiosqlite.connect(self.db_path) as db:
                db.row_factory = aiosqlite.Row
                where_clauses = []
                params: list = []
                if start_date:
                    where_clauses.append("timestamp >= ?")
                    params.append(start_date)
                if end_date:
                    where_clauses.append("timestamp <= ?")
                    params.append(end_date)
                where_sql = "WHERE " + " AND ".join(where_clauses) if where_clauses else ""
                sql = f"SELECT * FROM audit_chain {where_sql} ORDER BY id DESC LIMIT ? OFFSET ?"
                params.extend([limit, offset])
                async with db.execute(sql, params) as cursor:
                    rows = await cursor.fetchall()
                    return [dict(row) for row in rows]
        except Exception as e:
            logger.error(f"[AuditService] 查询失败: {e}")
            return []

    # ── 内部辅助 ──────────────────────────────────────────────────────

    async def _has_extended_columns(self, db: aiosqlite.Connection) -> bool:
        """检查是否有扩展字段（session_id/params/error/event_type）"""
        try:
            async with db.execute("SELECT session_id, params, error, event_type FROM audit_chain LIMIT 0") as _:
                return True
        except aiosqlite.OperationalError:
            return False

    async def count_records(
        self,
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> int:
        """查询审计记录总数"""
        await self._ensure_db()
        try:
            async with aiosqlite.connect(self.db_path) as db:
                where_clauses = []
                params: list = []
                if start_date:
                    where_clauses.append("timestamp >= ?")
                    params.append(start_date)
                if end_date:
                    where_clauses.append("timestamp <= ?")
                    params.append(end_date)
                where_sql = "WHERE " + " AND ".join(where_clauses) if where_clauses else ""
                sql = f"SELECT COUNT(*) FROM audit_chain {where_sql}"
                async with db.execute(sql, params) as cursor:
                    row = await cursor.fetchone()
                    return row[0] if row else 0
        except Exception as e:
            logger.error(f"[AuditService] 计数失败: {e}")
            return 0
