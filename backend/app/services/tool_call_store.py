"""ToolCallStateStore —— Tool Call 状态持久化（预留扩展接口）

职责（当前）：
  - 提供 Redis 持久化的 ToolCall 状态存储接口定义
  - 预留扩展点，不实现完整业务流程

未来启用时：
  - 使用 Redis key 格式: agent:tool_call:{trace_id}:{tool_call_id}
  - 默认 TTL: 1 小时
"""
from app.services.storage.redis_storage import RedisStorage

_KEY_PREFIX = "agent:tool_call"


class ToolCallStateStore:
    """Tool Call 状态存储 —— 预留扩展接口

    当前项目中 tool_call 状态在 agent_context.py 中以内存 list 追踪，
    暂不需要独立持久化。本类仅提供 Redis 接入骨架，待需求明确后实现。
    """

    # 默认 TTL：1 小时（按生产化要求）
    DEFAULT_TTL_SECONDS: int = 3600

    def __init__(self, storage: RedisStorage | None = None) -> None:
        self._storage = storage or RedisStorage()
        self._ttl = self.DEFAULT_TTL_SECONDS

    # 预留扩展接口（未实现）
    # def save_tool_call(self, trace_id: str, tool_call_id: str, data: dict) -> None:
    #     ...
    #
    # def get_tool_call(self, trace_id: str, tool_call_id: str) -> dict | None:
    #     ...
    #
    # def list_tool_calls(self, trace_id: str) -> list[dict]:
    #     ...
    pass
