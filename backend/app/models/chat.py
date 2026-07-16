"""聊天持久化模型 —— 会话与消息"""

from sqlalchemy import Column, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import relationship

from app.models import Base


class ChatSession(Base):
    """对话会话元数据"""

    __tablename__ = "chat_sessions"

    id = Column(String, primary_key=True)
    title = Column(String, nullable=False, default="新会话")
    created_at = Column(String, nullable=False)
    updated_at = Column(String, nullable=False)

    messages = relationship("ChatMessage", back_populates="session", lazy="selectin")


class ChatMessage(Base):
    """聊天消息"""

    __tablename__ = "chat_messages"

    id = Column(Integer, primary_key=True, autoincrement=True)
    session_id = Column(String, ForeignKey("chat_sessions.id"), nullable=False, index=True)
    trace_id = Column(String)
    role = Column(String, nullable=False)
    content = Column(Text, nullable=False)
    message_type = Column(String, nullable=False)
    created_at = Column(String, nullable=False)
    # 额外元数据（JSON 字符串），字段名 meta_json 避免与 SQLAlchemy MetaData 冲突
    meta_json = Column("metadata", Text)

    session = relationship("ChatSession", back_populates="messages")

    __table_args__ = (
        Index("idx_chat_messages_session", "session_id", "created_at"),
    )
