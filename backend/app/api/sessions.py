"""会话管理 REST 接口

正式接口（最新前后端 API 统一规范 v1.0）：
  GET  /api/sessions                       → 会话列表
  POST /api/sessions                       → 创建会话
  GET  /api/sessions/{session_id}/messages → 会话历史消息
"""
import logging
import uuid
from datetime import datetime

from fastapi import APIRouter, HTTPException

from app.core.redis_client import get_session, list_sessions, set_session
from app.schemas.models import SessionCreate, SessionOut, SessionMessagesOut

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/sessions")
async def get_sessions() -> list[SessionOut]:
    """获取所有会话列表（从 Redis 读取）"""
    sessions = list_sessions()
    return [
        SessionOut(
            id=s["id"],
            title=s["title"],
            created_at=s["created_at"],
        )
        for s in sessions
    ]


@router.post("/sessions")
async def create_session(body: SessionCreate) -> SessionOut:
    """创建新会话并存入 Redis"""
    sid = str(uuid.uuid4())[:12]
    now = datetime.now().isoformat()
    session_data = {
        "id": sid,
        "title": body.title,
        "created_at": now,
    }
    set_session(sid, session_data, expire=3600)
    logger.info(f"[Session] 创建会话并存入 Redis: {sid} - {body.title}")
    return SessionOut(id=sid, title=body.title, created_at=now)


@router.get("/sessions/{session_id}/messages")
async def get_session_messages(session_id: str) -> SessionMessagesOut:
    """
    获取会话历史消息。

    当前阶段消息持久化尚未实现。
    已存在会话返回空列表；会话不存在时返回 404。
    """
    session = get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="会话不存在")
    return SessionMessagesOut(session_id=session_id, messages=[])
