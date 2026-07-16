"""AuditService —— 结构化审计封装（基于 SQLAlchemy ORM）

职责：
  - 统一审计写入入口（save_event / save_context）
  - 查询审计列表（list_records）
  - 敏感信息过滤
  - 通过统一数据库入口支持不同部署环境切换，避免业务层依赖具体数据库。

与旧版 audit/logger.py 的关系：
  旧版 log_chain() / query_audit() / count_audit() 独立函数已由 AuditService 替代。
  app/api/chat.py 中直接调用 log_chain() 的代码应迁移到 audit_service.save_event()。
"""

import logging
import re
from datetime import datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.audit.models import _compute_hash
from app.models.audit import AuditChain

logger = logging.getLogger(__name__)

# ── 敏感信息过滤 ──────────────────────────────────────────────────────

_SENSITIVE_REPLACE = "[REDACTED]"

_SENSITIVE_KEYS: set[str] = {
    "password", "passwd", "api_key", "secret", "secret_key",
    "token", "access_token", "refresh_token", "authorization",
    "mcp_auth_token", "deepseek_api_key",
}


def sanitize_sensitive_data(value: object) -> object:
    """公共递归脱敏 —— 可供 ActionService 等模块复用"""
    if isinstance(value, str):
        return _sanitize(value)
    if isinstance(value, dict):
        return {
            k: "[REDACTED]" if isinstance(k, str) and k.lower() in _SENSITIVE_KEYS
            else sanitize_sensitive_data(v)
            for k, v in value.items()
        }
    if isinstance(value, (list, tuple)):
        return type(value)(sanitize_sensitive_data(item) for item in value)
    return value


_SENSITIVE_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"sk-[a-zA-Z0-9]{20,}", re.IGNORECASE), _SENSITIVE_REPLACE),
    (re.compile(r"Bearer\s+[a-zA-Z0-9\-_\.]+", re.IGNORECASE), f"Bearer {_SENSITIVE_REPLACE}"),
    (re.compile(r'Authorization:[redacted]"\']+', re.IGNORECASE), f"Authorization: Bearer [redacted]"),
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


class AuditService:
    """结构化审计服务

    使用方式：
        service = AuditService()
        await service.save_event(trace_id="...", risk_level="low", ...)
        records = await service.list_records(limit=50)

    支持两种模式：
    - 生产：使用全局引擎（默认），通过 DATABASE_URL 切换 SQLite/PostgreSQL
    - 测试：传入 db_path 创建独立临时引擎
    """

    def __init__(self, db_path: str | None = None) -> None:
        """
        参数:
            db_path: 可选，指定 SQLite 文件路径（测试用）。
                     为 None 时使用全局 DATABASE_URL 引擎。
        """
        self._owns_engine = db_path is not None
        self.db_path = db_path  # 向后兼容：测试代码可能访问此属性
        if db_path is not None:
            url = f"sqlite+aiosqlite:///{db_path}"
            self._engine = create_async_engine(
                url,
                connect_args={"check_same_thread": False},
            )
            self._session_factory = async_sessionmaker(
                self._engine,
                class_=AsyncSession,
                expire_on_commit=False,
            )
        else:
            from app.core.database import _async_session_factory
            self._engine = None
            self._session_factory = _async_session_factory

    async def _ensure_db(self) -> None:
        """确保表存在（幂等）。生产模式由 init_engine() 负责，此方法作为兜底。"""
        if self._owns_engine and self._engine is not None:
            engine = self._engine
        else:
            from app.core.database import _engine
            engine = _engine
        async with engine.begin() as conn:
            from app.models import Base
            await conn.run_sync(Base.metadata.create_all)

    async def close(self) -> None:
        """释放测试引擎资源"""
        if self._owns_engine and self._engine is not None:
            await self._engine.dispose()

    async def _get_last_hash(self, session: AsyncSession) -> str:
        """获取最后一条审计记录的哈希值，用于防篡改链"""
        result = await session.execute(
            select(AuditChain.record_hash)
            .order_by(AuditChain.id.desc())
            .limit(1)
        )
        row = result.scalar_one_or_none()
        return row if row else "0"

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
            async with self._session_factory() as session:
                prev_hash = await self._get_last_hash(session)
                record_hash = _compute_hash(record, prev_hash)

                audit = AuditChain(
                    trace_id=trace_id,
                    timestamp=timestamp,
                    user_input=user_input_safe or "",
                    intent=intent,
                    risk_level=risk_level,
                    mcp_tool=mcp_tool,
                    command=command_safe,
                    raw_output=raw_output_safe,
                    llm_reasoning=llm_reasoning_safe,
                    final_response=final_response_safe,
                    prev_hash=prev_hash,
                    record_hash=record_hash,
                    session_id=session_id,
                    params=params_safe,
                    error=error_safe,
                    event_type=event_type,
                )
                session.add(audit)
                await session.commit()

            logger.info(f"[AuditService] 事件已记录: trace_id={trace_id}, event_type={event_type}")
            return {
                "trace_id": trace_id,
                "timestamp": timestamp,
                "risk_level": risk_level,
                "event_type": event_type,
            }
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
                raw_parts.append(
                    f"[{'OK' if ok else 'ERR'}] {err}" if err else f"[{'OK' if ok else 'ERR'}]"
                )
        raw_output = "; ".join(raw_parts) if raw_parts else None

        # final_response
        final_response = getattr(ctx, "final_response", None)

        # error：从 observations 收集失败
        errors = [
            obs.get("error", "")
            for obs in observations
            if isinstance(obs, dict) and not obs.get("ok") and obs.get("error")
        ]
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
        """分页查询审计记录，返回数组"""
        await self._ensure_db()
        try:
            async with self._session_factory() as session:
                stmt = select(AuditChain)
                if start_date:
                    stmt = stmt.where(AuditChain.timestamp >= start_date)
                if end_date:
                    stmt = stmt.where(AuditChain.timestamp <= end_date)
                stmt = stmt.order_by(AuditChain.id.desc()).limit(limit).offset(offset)

                result = await session.execute(stmt)
                rows = result.scalars().all()

                return [
                    {
                        "id": r.id,
                        "trace_id": r.trace_id,
                        "timestamp": r.timestamp,
                        "user_input": r.user_input,
                        "intent": r.intent,
                        "risk_level": r.risk_level,
                        "mcp_tool": r.mcp_tool,
                        "command": r.command,
                        "raw_output": r.raw_output,
                        "llm_reasoning": r.llm_reasoning,
                        "final_response": r.final_response,
                        "prev_hash": r.prev_hash,
                        "record_hash": r.record_hash,
                        "session_id": r.session_id,
                        "params": r.params,
                        "error": r.error,
                        "event_type": r.event_type,
                    }
                    for r in rows
                ]
        except Exception as e:
            logger.error(f"[AuditService] 查询失败: {e}")
            return []

    async def count_records(
        self,
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> int:
        """查询审计记录总数"""
        await self._ensure_db()
        try:
            async with self._session_factory() as session:
                stmt = select(func.count()).select_from(AuditChain)
                if start_date:
                    stmt = stmt.where(AuditChain.timestamp >= start_date)
                if end_date:
                    stmt = stmt.where(AuditChain.timestamp <= end_date)

                result = await session.execute(stmt)
                count = result.scalar_one()
                return count
        except Exception as e:
            logger.error(f"[AuditService] 计数失败: {e}")
            return 0
