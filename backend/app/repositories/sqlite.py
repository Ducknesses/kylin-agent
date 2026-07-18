"""消息持久化实现 —— 基于 SQLAlchemy ORM

通过统一的 AsyncSession 访问数据库，不感知底层是 SQLite 还是 PostgreSQL。
表结构由 app/models/chat.py 的 ORM 模型定义，应用启动时自动创建。
"""

import json
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models.chat import ChatMessage, ChatSession
from app.repositories.base import MessageRepository

logger = logging.getLogger(__name__)


class SQLiteMessageRepository(MessageRepository):
    """消息持久化仓储 —— 基于 SQLAlchemy AsyncSession

    支持两种使用模式：
    - 生产：使用全局引擎（默认），通过 DATABASE_URL 切换 SQLite/PostgreSQL
    - 测试：传入 db_path 创建独立临时引擎，数据隔离
    """

    def __init__(self, db_path: str | None = None) -> None:
        """
        参数:
            db_path: 可选，指定 SQLite 文件路径（测试用）。
                     为 None 时使用全局 DATABASE_URL 引擎。
        """
        self._owns_engine = db_path is not None
        if db_path is not None:
            # 测试模式：为临时文件创建独立引擎，与全局引擎完全隔离
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
            # 生产模式：使用全局引擎
            from app.core.database import _async_session_factory
            self._engine = None
            self._session_factory = _async_session_factory

    async def _ensure_tables(self) -> None:
        """确保表存在（幂等）。生产模式由 init_engine() 负责，此方法作为兜底。"""
        if self._owns_engine and self._engine is not None:
            engine = self._engine
        else:
            from app.core.database import _engine
            engine = _engine
        async with engine.begin() as conn:
            from app.models import Base
            await conn.run_sync(Base.metadata.create_all)

    @asynccontextmanager
    async def _get_session(self) -> AsyncIterator[AsyncSession]:
        """获取会话的上下文管理器（自动提交/回滚）"""
        async with self._session_factory() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    async def close(self) -> None:
        """释放测试引擎资源"""
        if self._owns_engine and self._engine is not None:
            await self._engine.dispose()

    # ── 会话 ──────────────────────────────────────────────────────

    async def create_session(self, session_id: str, title: str = "新会话") -> None:
        """创建新会话记录（幂等：已存在则忽略）"""
        await self._ensure_tables()
        now = datetime.now(timezone.utc).isoformat()
        try:
            async with self._get_session() as session:
                # 幂等：先检查是否存在
                existing = await session.get(ChatSession, session_id)
                if existing is None:
                    cs = ChatSession(
                        id=session_id, title=title,
                        created_at=now, updated_at=now,
                    )
                    session.add(cs)
            logger.debug(f"[ChatHistory] 会话已创建: {session_id}")
        except Exception as e:
            logger.warning(f"[ChatHistory] 创建会话失败 (已忽略): {e}")

    async def list_sessions(self) -> list[dict]:
        """按更新时间倒序返回所有会话"""
        await self._ensure_tables()
        try:
            async with self._get_session() as session:
                result = await session.execute(
                    select(ChatSession).order_by(ChatSession.updated_at.desc())
                )
                rows = result.scalars().all()
                return [
                    {
                        "id": r.id, "title": r.title,
                        "created_at": r.created_at, "updated_at": r.updated_at,
                    }
                    for r in rows
                ]
        except Exception as e:
            logger.warning(f"[ChatHistory] 查询会话列表失败: {e}")
            return []

    async def get_session(self, session_id: str) -> dict | None:
        """获取单个会话元数据，不存在返回 None"""
        await self._ensure_tables()
        try:
            async with self._get_session() as session:
                row = await session.get(ChatSession, session_id)
                if row is None:
                    return None
                return {
                    "id": row.id, "title": row.title,
                    "created_at": row.created_at, "updated_at": row.updated_at,
                }
        except Exception as e:
            logger.warning(f"[ChatHistory] 查询会话失败: {e}")
            return None

    # ── 消息 ──────────────────────────────────────────────────────

    async def save_message(
        self,
        session_id: str,
        role: str,
        content: str,
        message_type: str,
        trace_id: str | None = None,
        metadata: dict | None = None,
    ) -> None:
        """保存一条消息。异常静默，不中断业务流程。"""
        await self._ensure_tables()
        now = datetime.now(timezone.utc).isoformat()
        meta_json = json.dumps(metadata, ensure_ascii=False) if metadata else None
        try:
            async with self._get_session() as session:
                msg = ChatMessage(
                    session_id=session_id,
                    trace_id=trace_id,
                    role=role,
                    content=content,
                    message_type=message_type,
                    created_at=now,
                    meta_json=meta_json,
                )
                session.add(msg)
                # 同时更新会话 updated_at
                await session.execute(
                    update(ChatSession)
                    .where(ChatSession.id == session_id)
                    .values(updated_at=now)
                )
            logger.debug(f"[ChatHistory] 消息已保存: {session_id} {role} {message_type}")
        except Exception as e:
            logger.warning(f"[ChatHistory] 保存消息失败 (已忽略): {e}")

    async def append_chunk(self, session_id: str, trace_id: str, content: str) -> None:
        """追加式保存 chunk：同 session + trace_id 合并为一行，不存在则新增。"""
        await self._ensure_tables()
        now = datetime.now(timezone.utc).isoformat()
        try:
            async with self._get_session() as session:
                result = await session.execute(
                    select(ChatMessage)
                    .where(
                        ChatMessage.session_id == session_id,
                        ChatMessage.trace_id == trace_id,
                        ChatMessage.message_type == "chunk",
                    )
                    .order_by(ChatMessage.id.desc())
                    .limit(1)
                )
                existing = result.scalars().first()
                if existing is not None:
                    # 追加合并到已有行
                    existing.content = (existing.content or "") + content
                else:
                    # 首次插入 chunk 行
                    msg = ChatMessage(
                        session_id=session_id,
                        trace_id=trace_id,
                        role="assistant",
                        content=content,
                        message_type="chunk",
                        created_at=now,
                    )
                    session.add(msg)
                # 更新会话 updated_at
                await session.execute(
                    update(ChatSession)
                    .where(ChatSession.id == session_id)
                    .values(updated_at=now)
                )
            logger.debug(f"[ChatHistory] chunk 已追加: {session_id} {trace_id}")
        except Exception as e:
            logger.warning(f"[ChatHistory] 追加 chunk 失败 (已忽略): {e}")

    async def get_messages(self, session_id: str) -> list[dict]:
        """按时间顺序返回指定会话的所有消息"""
        await self._ensure_tables()
        try:
            async with self._get_session() as session:
                result = await session.execute(
                    select(ChatMessage)
                    .where(ChatMessage.session_id == session_id)
                    .order_by(ChatMessage.id.asc())
                )
                rows = result.scalars().all()
                msgs = []
                for r in rows:
                    d = {
                        "id": r.id, "session_id": r.session_id,
                        "trace_id": r.trace_id, "role": r.role,
                        "content": r.content, "message_type": r.message_type,
                        "created_at": r.created_at, "metadata": None,
                    }
                    if r.meta_json:
                        try:
                            d["metadata"] = json.loads(r.meta_json)
                        except json.JSONDecodeError:
                            pass
                    msgs.append(d)
                return msgs
        except Exception as e:
            logger.warning(f"[ChatHistory] 查询消息失败: {e}")
            return []
