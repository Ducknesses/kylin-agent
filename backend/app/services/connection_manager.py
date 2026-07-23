"""WebSocket 连接管理器（含 token 校验）"""
import asyncio
import logging
from typing import Any, Dict, Optional

from fastapi import WebSocket

from app.core.auth import AuthContext
from app.core.security import TokenStore

logger = logging.getLogger(__name__)


class ConnectionManager:
    """管理 WebSocket 连接的生命周期，按 session_id 索引

    认证流程：
    1. connect() 被调用时，从 query string 提取 ?token=
    2. 如果 API_TOKEN 已配置，校验 token；无效则 close(code=4001)
    3. 如果 API_TOKEN 未配置，放行（向后兼容）
    4. 返回 AuthContext 供调用方做 RBAC

    取消机制：
    - cancel_session() 设置 asyncio.Event，允许 _run_agent_flow
      在 WS 断开后主动中断长时间运行的 orchestrator generator。
    """

    def __init__(self) -> None:
        self._connections: Dict[str, WebSocket] = {}
        self._sessions_auth: Dict[str, AuthContext] = {}
        self._pending_confirm: Dict[str, dict] = {}
        self._pending_tool: Dict[str, dict] = {}
        self._cancel_events: Dict[str, asyncio.Event] = {}

    # ── 连接管理 ──

    async def connect(self, websocket: WebSocket, session_id: str) -> AuthContext:
        """接受 WebSocket 连接，校验 token，注册到管理器

        返回 AuthContext（调用方用于 RBAC 判定）。
        校验失败时直接 close WebSocket 并返回 AuthContext.anonymous()。
        """
        token_store = TokenStore.singleton()
        token = websocket.query_params.get("token", None)

        # 提取客户端 IP
        client_ip = ""
        if websocket.client:
            client_ip = websocket.client.host or ""

        # Token 校验
        if token_store.is_configured():
            auth = token_store.validate(token, client_ip)
            if auth is None:
                logger.warning(
                    f"[Connection] WS 认证失败 session={session_id}, ip={client_ip}"
                )
                await websocket.accept()
                await websocket.close(code=4001, reason="认证失败：令牌无效或缺失")
                return AuthContext.anonymous(client_ip)
        else:
            # 未配置 token → 匿名（向后兼容）
            auth = AuthContext.anonymous(client_ip)

        await websocket.accept()
        self._connections[session_id] = websocket
        self._sessions_auth[session_id] = auth
        # 新连接建立时清除旧的取消标记
        self._cancel_events.pop(session_id, None)
        logger.info(
            f"[Connection] 会话建立: {session_id}, "
            f"level={auth.level.value}, authenticated={auth.is_authenticated}"
        )
        return auth

    def disconnect(self, session_id: str) -> None:
        """移除连接和关联的挂起操作"""
        self._connections.pop(session_id, None)
        self._sessions_auth.pop(session_id, None)
        self._pending_confirm.pop(session_id, None)
        self._pending_tool.pop(session_id, None)
        logger.info(f"[Connection] 会话断开: {session_id}")

    def is_connected(self, session_id: str) -> bool:
        """检查会话是否在线"""
        return session_id in self._connections

    # ── 认证信息查询 ──

    def get_auth(self, session_id: str) -> Optional[AuthContext]:
        """获取会话的认证上下文"""
        return self._sessions_auth.get(session_id)

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

    # ── 挂起工具调用（中危工具二次确认） ──

    def set_pending_tool(self, session_id: str, request: dict) -> None:
        """设置待确认的中危工具调用"""
        self._pending_tool[session_id] = request

    def get_pending_tool(self, session_id: str) -> dict | None:
        """读取待确认的工具调用（不删除）"""
        return self._pending_tool.get(session_id)

    def pop_pending_tool(self, session_id: str) -> dict | None:
        """取出并删除待确认的工具调用"""
        return self._pending_tool.pop(session_id, None)

    def has_pending_tool(self, session_id: str) -> bool:
        """是否存在待确认的工具调用"""
        return session_id in self._pending_tool

    # ── 会话取消（WS 断开后中断长时间运行的 orchestrator） ──

    def get_cancel_event(self, session_id: str) -> asyncio.Event:
        """获取或创建会话取消事件。WS 断开时 set()，orchestrator 可 await 它来感知断开。"""
        if session_id not in self._cancel_events:
            self._cancel_events[session_id] = asyncio.Event()
        return self._cancel_events[session_id]

    def cancel_session(self, session_id: str) -> None:
        """标记会话已取消，通知 orchestrator 尽快终止。"""
        evt = self._cancel_events.get(session_id)
        if evt is not None:
            evt.set()
            logger.info(f"[Connection] 会话取消: {session_id}")

    def is_cancelled(self, session_id: str) -> bool:
        """检查会话是否已被取消"""
        evt = self._cancel_events.get(session_id)
        return evt is not None and evt.is_set()