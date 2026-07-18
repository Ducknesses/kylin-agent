"""会话管理 REST 接口

正式接口（最新前后端 API 统一规范 v1.0）：
  GET  /api/sessions                       → 会话列表（需要 READ 权限）
  POST /api/sessions                       → 创建会话（需要 READ 权限）
  GET  /api/sessions/{session_id}/messages → 会话历史消息（需要 READ 权限）

数据源：SQLite（通过 MessageRepository），未来可替换为 PostgreSQL。
"""
import logging
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException

from app.core.auth import AuthContext, AuthLevel
from app.dependencies import message_repository, require_auth
from app.repositories.base import MessageRepository
from app.schemas.models import SessionCreate, SessionMessage, SessionMessagesOut, SessionOut

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/sessions")
async def get_sessions(
    auth: AuthContext = Depends(require_auth(AuthLevel.READ)),
) -> list[SessionOut]:
    """获取所有会话列表（从 SQLite 读取）"""
    sessions = await message_repository.list_sessions()
    return [
        SessionOut(
            id=s["id"],
            title=s["title"],
            created_at=s["created_at"],
        )
        for s in sessions
    ]


@router.post("/sessions")
async def create_session(
    body: SessionCreate,
    auth: AuthContext = Depends(require_auth(AuthLevel.READ)),
) -> SessionOut:
    """创建新会话并存入 SQLite"""
    sid = str(uuid.uuid4())[:12]
    now = datetime.now(timezone.utc).isoformat()
    await message_repository.create_session(sid, body.title)
    logger.info(f"[Session] 创建会话: {sid} - {body.title}")
    return SessionOut(id=sid, title=body.title, created_at=now)


@router.get("/sessions/{session_id}/messages")
async def get_session_messages(
    session_id: str,
    auth: AuthContext = Depends(require_auth(AuthLevel.READ)),
) -> SessionMessagesOut:
    """
    获取会话历史消息。

    从 SQLite 读取持久化的消息记录，重建为前端可消费的格式。
    """
    session = await message_repository.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="会话不存在")

    raw_messages = await message_repository.get_messages(session_id)

    # 组装消息：user 消息直接映射，assistant 消息按 message_type 分组
    messages: list[SessionMessage] = []
    for raw in raw_messages:
        role = raw.get("role", "assistant")
        content = raw.get("content", "")
        created_at = raw.get("created_at", "")
        msg_meta = raw.get("metadata")

        # 对 assistant 的 tool_call 消息，展开 metadata
        tool_calls = None
        if raw.get("message_type") == "tool_call" and msg_meta:
            tool_calls = [{
                "tool": msg_meta.get("tool", ""),
                "tool_call_id": msg_meta.get("tool_call_id", ""),
                "params": msg_meta.get("params"),
                "ok": msg_meta.get("ok"),
                "result": msg_meta.get("result"),
                "error": msg_meta.get("error"),
            }]

        messages.append(SessionMessage(
            role=role,
            content=content,
            timestamp=created_at,
            message_type=raw.get("message_type"),
            tool_calls=tool_calls,
        ))

    return SessionMessagesOut(session_id=session_id, messages=messages)


@router.delete("/sessions/{session_id}", status_code=204)
async def delete_session(
    session_id: str,
    auth: AuthContext = Depends(require_auth(AuthLevel.OP)),
) -> None:
    """删除会话及其所有关联消息（需要 OP 权限）"""
    repo: MessageRepository = message_repository
    deleted = await repo.delete_session(session_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="会话不存在")
    logger.info(f"[Session] 删除会话: {session_id} (by {auth.token})")
