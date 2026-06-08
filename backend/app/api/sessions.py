"""会话管理 REST 接口"""
import logging
import uuid
from datetime import datetime
from typing import List

from fastapi import APIRouter

from app.core.redis_client import get_session, list_sessions, set_session
from app.schemas.models import SessionCreate, SessionOut

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/sessions")
async def get_sessions() -> List[SessionOut]:
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
