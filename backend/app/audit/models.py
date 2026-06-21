"""SQLite 表定义与初始化"""
import hashlib
import logging
import os
from datetime import datetime
from typing import Optional

import aiosqlite

from config import settings

logger = logging.getLogger(__name__)

INIT_SQL = """
CREATE TABLE IF NOT EXISTS audit_chain (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    trace_id TEXT NOT NULL,
    timestamp TEXT NOT NULL,
    user_input TEXT NOT NULL,
    intent TEXT,
    risk_level TEXT NOT NULL,
    mcp_tool TEXT,
    command TEXT,
    raw_output TEXT,
    llm_reasoning TEXT,
    final_response TEXT,
    prev_hash TEXT,
    record_hash TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS app_config (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_trace_id ON audit_chain(trace_id);
CREATE INDEX IF NOT EXISTS idx_timestamp ON audit_chain(timestamp);
"""


async def init_db() -> None:
    """初始化 SQLite 数据库和 audit 表"""
    db_path = settings.SQLITE_DB
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    try:
        async with aiosqlite.connect(db_path) as db:
            await db.executescript(INIT_SQL)
            await db.commit()
        logger.info(f"数据库初始化完成: {db_path}")
    except Exception as e:
        logger.error(f"数据库初始化失败: {e}")
        raise


def _compute_hash(record: dict, prev_hash: str = "0") -> str:
    """
    计算单条审计记录的 SHA256 哈希，用于防篡改
    串联 prev_hash 形成链式结构
    """
    content = (
        f"{record.get('trace_id', '')}|"
        f"{record.get('timestamp', '')}|"
        f"{record.get('user_input', '')}|"
        f"{record.get('intent', '')}|"
        f"{record.get('risk_level', '')}|"
        f"{record.get('mcp_tool', '')}|"
        f"{record.get('command', '')}|"
        f"{record.get('raw_output', '')}|"
        f"{record.get('llm_reasoning', '')}|"
        f"{record.get('final_response', '')}|"
        f"{prev_hash}"
    )
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


async def get_last_hash(db: aiosqlite.Connection) -> str:
    """获取最后一条记录的哈希值"""
    async with db.execute(
        "SELECT record_hash FROM audit_chain ORDER BY id DESC LIMIT 1"
    ) as cursor:
        row = await cursor.fetchone()
        return row[0] if row else "0"


# ── 配置持久化 ────────────────────────────────────────────────────


async def save_config(key: str, value: str) -> None:
    """将配置键值存入 app_config 表，失败时抛出异常供调用方处理"""
    from datetime import datetime as dt
    try:
        async with aiosqlite.connect(settings.SQLITE_DB) as db:
            await db.execute(
                "INSERT OR REPLACE INTO app_config (key, value, updated_at) VALUES (?, ?, ?)",
                (key, value, dt.now().isoformat()),
            )
            await db.commit()
        logger.info(f"[Config] 配置已持久化: {key}")
    except Exception as e:
        logger.error(f"[Config] 配置持久化失败: {e}")
        raise


async def load_config(key: str) -> str | None:
    """从 app_config 表读取配置值"""
    try:
        async with aiosqlite.connect(settings.SQLITE_DB) as db:
            async with db.execute(
                "SELECT value FROM app_config WHERE key = ?", (key,)
            ) as cursor:
                row = await cursor.fetchone()
                return row[0] if row else None
    except Exception as e:
        logger.error(f"[Config] 读取配置失败: {e}")
        return None
