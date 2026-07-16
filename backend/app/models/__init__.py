"""SQLAlchemy ORM 模型 —— 统一管理所有数据库表定义

所有表通过 Base.metadata.create_all() 自动创建，无需手动执行 DDL。
"""

from sqlalchemy.orm import declarative_base

Base = declarative_base()

from app.models.audit import AuditChain  # noqa: E402, F401
from app.models.chat import ChatMessage, ChatSession  # noqa: E402, F401
from app.models.config import AppConfig  # noqa: E402, F401

__all__ = ["Base", "AuditChain", "ChatSession", "ChatMessage", "AppConfig"]
