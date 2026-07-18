"""审计日志查询接口

正式接口：GET /api/audit（安全智能运维 Agent API 统一规范 v1.1）
参数：limit / offset / start_date / end_date + 多维筛选（user / role / action_type / risk_level）
返回：{ records: [...], total: N }

底层委托 AuditService.list_records() 查询。
"""
import logging
from typing import Any

from fastapi import APIRouter, Depends, Query

from app.core.auth import AuthContext, AuthLevel
from app.dependencies import require_auth
from app.schemas.models import AuditRecordOut
from app.services.audit_service import AuditService

logger = logging.getLogger(__name__)
router = APIRouter()

# 模块级 AuditService 实例
_audit_service = AuditService()


# ── 正式审计接口（v1.1 规范：返回 {records, total}） ─────────────────


@router.get("/audit")
async def get_audit_logs(
    auth: AuthContext = Depends(require_auth(AuthLevel.READ)),
    limit: int = Query(50, ge=1, le=200, description="每页条数"),
    offset: int = Query(0, ge=0, description="偏移量"),
    start_date: str | None = Query(None, description="开始时间 ISO-8601"),
    end_date: str | None = Query(None, description="结束时间 ISO-8601"),
    user: str | None = Query(None, description="按用户输入模糊筛选"),
    role: str | None = Query(None, description="按角色/意图筛选"),
    action_type: str | None = Query(None, description="按操作类型筛选（tool_call/chat/fix_action）"),
    risk_level: str | None = Query(None, description="按风险等级筛选（low/medium/high）"),
) -> dict[str, Any]:
    """
    分页查询审计日志，支持多维筛选，返回 {records, total}
    """
    try:
        records, total = await _audit_service.list_records(
            limit=limit,
            offset=offset,
            start_date=start_date,
            end_date=end_date,
            user=user,
            role=role,
            action_type=action_type,
            risk_level=risk_level,
        )
    except Exception as e:
        logger.error(f"[Audit] 查询失败: {e}")
        return {"records": [], "total": 0}

    out_records = [
        AuditRecordOut(
            trace_id=r.get("trace_id", ""),
            timestamp=r.get("timestamp", ""),
            user_input=r.get("user_input", ""),
            intent=r.get("intent"),
            risk_level=r.get("risk_level", ""),
            mcp_tool=r.get("mcp_tool"),
            command=r.get("command"),
            raw_output=r.get("raw_output"),
            llm_reasoning=r.get("llm_reasoning"),
            final_response=r.get("final_response"),
        )
        for r in records
    ]
    return {"records": [rec.model_dump() for rec in out_records], "total": total}