"""审计日志查询接口"""
import logging
from typing import Dict, Any

from fastapi import APIRouter, Query

from app.audit.logger import query_audit, count_audit
from app.schemas.models import AuditRecordOut

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/audit")
async def get_audit(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> Dict[str, Any]:
    """查询审计日志，支持分页，返回 total 供前端分页条使用"""
    try:
        rows = await query_audit(limit=limit, offset=offset)
        total = await count_audit()
    except Exception as e:
        logger.error(f"[Audit] 查询失败: {e}")
        return {"error": str(e), "records": [], "total": 0}

    records = [
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
        for r in rows
    ]
    return {
        "records": [rec.model_dump() for rec in records],
        "total": total,
    }
