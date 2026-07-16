"""消息持久化 Repository 抽象接口

业务代码只依赖此接口，不感知具体数据库实现。
未来迁移 PostgreSQL 只需提供新的实现类。
"""

from abc import ABC, abstractmethod


class MessageRepository(ABC):
    """聊天消息和会话持久化的抽象接口"""

    @abstractmethod
    async def create_session(self, session_id: str, title: str = "新会话") -> None:
        """创建新会话记录（幂等：已存在则忽略）"""
        ...

    @abstractmethod
    async def list_sessions(self) -> list[dict]:
        """按更新时间倒序返回所有会话，每条包含 id/title/created_at/updated_at"""
        ...

    @abstractmethod
    async def get_session(self, session_id: str) -> dict | None:
        """获取单个会话元数据，不存在返回 None"""
        ...

    @abstractmethod
    async def save_message(
        self,
        session_id: str,
        role: str,
        content: str,
        message_type: str,
        trace_id: str | None = None,
        metadata: dict | None = None,
    ) -> None:
        """保存一条聊天消息（异常时静默失败，不中断业务流程）"""
        ...

    @abstractmethod
    async def get_messages(self, session_id: str) -> list[dict]:
        """按时间顺序返回指定会话的所有消息"""
        ...
