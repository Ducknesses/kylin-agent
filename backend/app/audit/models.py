"""审计数据工具函数

表结构已迁移到 app/models/audit.py（SQLAlchemy ORM 模型），
应用启动时由 app.core.database.init_engine() 自动建表。

本模块保留：
- _compute_hash：防篡改哈希链计算
- save_config / load_config：键值配置持久化（委托 ORM）
"""

import hashlib
import logging

from config import settings

logger = logging.getLogger(__name__)


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


# ── 配置持久化（委托 SQLAlchemy ORM） ────────────────────────────────


async def save_config(key: str, value: str) -> None:
    """将配置键值存入 app_config 表，失败时抛出异常供调用方处理"""
    from datetime import datetime as dt

    from sqlalchemy import select

    from app.core.database import get_session
    from app.models.config import AppConfig

    try:
        async with get_session() as session:
            existing = await session.get(AppConfig, key)
            if existing:
                existing.value = value
                existing.updated_at = dt.now().isoformat()
            else:
                cfg = AppConfig(key=key, value=value, updated_at=dt.now().isoformat())
                session.add(cfg)
        logger.info(f"[Config] 配置已持久化: {key}")
    except Exception as e:
        logger.error(f"[Config] 配置持久化失败: {e}")
        raise


async def load_config(key: str) -> str | None:
    """从 app_config 表读取配置值"""
    from app.core.database import get_session
    from app.models.config import AppConfig

    try:
        async with get_session() as session:
            row = await session.get(AppConfig, key)
            return row.value if row else None
    except Exception as e:
        logger.error(f"[Config] 读取配置失败: {e}")
        return None
