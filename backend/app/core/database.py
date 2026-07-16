"""统一数据库引擎工厂

通过 DATABASE_URL 切换 SQLite / PostgreSQL，业务层无需感知具体数据库。
开发环境默认使用 SQLite（aiosqlite 驱动），生产环境切换到 PostgreSQL（asyncpg 驱动）。

使用方式：
    from app.core.database import get_session
    async with get_session() as session:
        ...
"""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from config import settings

logger = logging.getLogger(__name__)

# 异步引擎（模块级单例，全应用共享一个连接池）
# SQLite 使用 StaticPool（不支持 pool_size/max_overflow），PostgreSQL 使用 QueuePool
_is_postgresql = "postgresql" in settings.DATABASE_URL
_engine_kwargs: dict = {"echo": settings.DEBUG}
if "sqlite" in settings.DATABASE_URL:
    _engine_kwargs["connect_args"] = {"check_same_thread": False}
if _is_postgresql:
    _engine_kwargs.update(pool_size=5, max_overflow=10)

_engine = create_async_engine(settings.DATABASE_URL, **_engine_kwargs)

# 异步 session 工厂
_async_session_factory = async_sessionmaker(
    _engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


@asynccontextmanager
async def get_session() -> AsyncIterator[AsyncSession]:
    """获取数据库会话的上下文管理器。

    用法：
        async with get_session() as session:
            result = await session.execute(...)
    """
    async with _async_session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def init_engine() -> None:
    """应用启动时初始化数据库引擎 —— 创建所有 ORM 模型对应的表（幂等）。

    替代原来的 app.audit.models.init_db() 手动执行 raw SQL。
    """
    # 延迟导入避免循环依赖
    from app.models import Base

    async with _engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    logger.info(f"数据库引擎初始化完成: {settings.DATABASE_URL}")


async def dispose_engine() -> None:
    """应用关闭时释放连接池。"""
    await _engine.dispose()
    logger.info("数据库引擎已关闭")
