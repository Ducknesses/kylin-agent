"""审计日志查询接口"""
import logging
from typing import List

from fastapi import APIRouter, Query

from app.audit.logger import query_audit
from app.schemas.models import AuditRecordOut

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/audit")
async def get_audit(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> List[AuditRecordOut]:
    """查询审计日志，支持分页"""
    rows = await query_audit(limit=limit, offset=offset)
    return [
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
