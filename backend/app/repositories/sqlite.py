"""SQLite 消息持久化实现

使用 aiosqlite（项目已有依赖），复用 data/audit.db 文件。
表结构通过 audit/models.py 的 INIT_SQL 统一管理。
"""

import json
import logging
import os
from datetime import datetime, timezone

import aiosqlite

from app.repositories.base import MessageRepository
from config import settings

logger = logging.getLogger(__name__)


class SQLiteMessageRepository(MessageRepository):
    """SQLite 实现 —— MessageRepository 接口"""

    def __init__(self, db_path: str | None = None) -> None:
        self.db_path = db_path or settings.SQLITE_DB

    def _ensure_dir(self) -> None:
        """确保数据库目录存在"""
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)

    async def _ensure_tables(self) -> None:
        """确保 chat 表存在（幂等，首次调用时建表）"""
        self._ensure_dir()
        try:
            async with aiosqlite.connect(self.db_path) as db:
                await db.executescript("""
                    CREATE TABLE IF NOT EXISTS chat_sessions (
                        id TEXT PRIMARY KEY,
                        title TEXT NOT NULL DEFAULT '新会话',
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL
                    );
                    CREATE TABLE IF NOT EXISTS chat_messages (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        session_id TEXT NOT NULL REFERENCES chat_sessions(id),
                        trace_id TEXT,
                        role TEXT NOT NULL,
                        content TEXT NOT NULL,
                        message_type TEXT NOT NULL,
                        created_at TEXT NOT NULL,
                        metadata TEXT
                    );
                    CREATE INDEX IF NOT EXISTS idx_chat_messages_session
                        ON chat_messages(session_id, created_at);
                """)
                await db.commit()
        except Exception as e:
            logger.warning(f"[ChatHistory] 建表失败: {e}")

    # ── 会话 ──────────────────────────────────────────────────────

    async def create_session(self, session_id: str, title: str = "新会话") -> None:
        await self._ensure_tables()
        now = datetime.now(timezone.utc).isoformat()
        try:
            async with aiosqlite.connect(self.db_path) as db:
                await db.execute(
                    """INSERT OR IGNORE INTO chat_sessions (id, title, created_at, updated_at)
                       VALUES (?, ?, ?, ?)""",
                    (session_id, title, now, now),
                )
                await db.commit()
            logger.debug(f"[ChatHistory] 会话已创建: {session_id}")
        except Exception as e:
            logger.warning(f"[ChatHistory] 创建会话失败 (已忽略): {e}")

    async def list_sessions(self) -> list[dict]:
        await self._ensure_tables()
        try:
            async with aiosqlite.connect(self.db_path) as db:
                db.row_factory = aiosqlite.Row
                async with db.execute(
                    "SELECT id, title, created_at, updated_at FROM chat_sessions ORDER BY updated_at DESC"
                ) as cursor:
                    return [dict(row) for row in await cursor.fetchall()]
        except Exception as e:
            logger.warning(f"[ChatHistory] 查询会话列表失败: {e}")
            return []

    async def get_session(self, session_id: str) -> dict | None:
        await self._ensure_tables()
        try:
            async with aiosqlite.connect(self.db_path) as db:
                db.row_factory = aiosqlite.Row
                async with db.execute(
                    "SELECT id, title, created_at, updated_at FROM chat_sessions WHERE id = ?",
                    (session_id,),
                ) as cursor:
                    row = await cursor.fetchone()
                    return dict(row) if row else None
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
            async with aiosqlite.connect(self.db_path) as db:
                await db.execute(
                    """INSERT INTO chat_messages
                       (session_id, trace_id, role, content, message_type, created_at, metadata)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (session_id, trace_id, role, content, message_type, now, meta_json),
                )
                await db.commit()
                # 同时更新会话 updated_at
                await db.execute(
                    "UPDATE chat_sessions SET updated_at = ? WHERE id = ?",
                    (now, session_id),
                )
                await db.commit()
            logger.debug(f"[ChatHistory] 消息已保存: {session_id} {role} {message_type}")
        except Exception as e:
            logger.warning(f"[ChatHistory] 保存消息失败 (已忽略): {e}")

    async def get_messages(self, session_id: str) -> list[dict]:
        await self._ensure_tables()
        try:
            async with aiosqlite.connect(self.db_path) as db:
                db.row_factory = aiosqlite.Row
                async with db.execute(
                    """SELECT id, session_id, trace_id, role, content, message_type,
                              created_at, metadata
                       FROM chat_messages
                       WHERE session_id = ?
                       ORDER BY id ASC""",
                    (session_id,),
                ) as cursor:
                    rows = await cursor.fetchall()
                    result = []
                    for row in rows:
                        d = dict(row)
                        if d.get("metadata"):
                            try:
                                d["metadata"] = json.loads(d["metadata"])
                            except json.JSONDecodeError:
                                pass
                        result.append(d)
                    return result
        except Exception as e:
            logger.warning(f"[ChatHistory] 查询消息失败: {e}")
            return []
