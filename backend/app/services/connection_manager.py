"""WebSocket 连接管理器"""
import logging
from typing import Any, Dict

from fastapi import WebSocket

logger = logging.getLogger(__name__)


class ConnectionManager:
    """管理 WebSocket 连接的生命周期，按 session_id 索引"""

    def __init__(self) -> None:
        self._connections: Dict[str, WebSocket] = {}
        self._pending_confirm: Dict[str, dict] = {}

    # ── 连接管理 ──

    async def connect(self, websocket: WebSocket, session_id: str) -> None:
        """接受连接并注册到管理器"""
        await websocket.accept()
        self._connections[session_id] = websocket
        logger.info(f"[Connection] 会话建立: {session_id}")

    def disconnect(self, session_id: str) -> None:
        """移除连接和关联的挂起操作"""
        self._connections.pop(session_id, None)
        self._pending_confirm.pop(session_id, None)
        logger.info(f"[Connection] 会话断开: {session_id}")

    def is_connected(self, session_id: str) -> bool:
        """检查会话是否在线"""
        return session_id in self._connections

    # ── 消息发送 ──

    async def send_json(self, session_id: str, data: dict[str, Any]) -> None:
        """向指定会话安全发送 JSON"""
        ws = self._connections.get(session_id)
        if ws:
            try:
                await ws.send_json(data)
            except Exception as e:
                logger.error(f"[Connection] 发送失败 session={session_id}: {e}")

    async def send_json_ws(self, ws: WebSocket, data: dict[str, Any]) -> None:
        """直接向 WebSocket 实例发送 JSON"""
        await ws.send_json(data)

    # ── 挂起操作（中危确认） ──

    def set_pending(self, session_id: str, request: dict) -> None:
        """设置待确认的中危操作"""
        self._pending_confirm[session_id] = request

    def get_pending(self, session_id: str) -> dict | None:
        """读取待确认操作（不删除）"""
        return self._pending_confirm.get(session_id)

    def pop_pending(self, session_id: str) -> dict | None:
        """取出并删除待确认操作"""
        return self._pending_confirm.pop(session_id, None)

    def has_pending(self, session_id: str) -> bool:
        """是否存在待确认操作"""
        return session_id in self._pending_confirm
