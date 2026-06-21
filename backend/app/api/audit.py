"""审计日志查询接口

正式接口：GET /api/audit（最新前后端 API 统一规范 v1.0）
参数：limit / offset / start_date / end_date
返回：{total, records}
"""
import logging
from typing import Dict, Any

from fastapi import APIRouter, Query

from app.audit.logger import count_audit, query_audit
from app.schemas.models import AuditRecordOut

logger = logging.getLogger(__name__)
router = APIRouter()


# ── 正式审计接口（最新规范 v1.0） ───────────────────────────────────


@router.get("/audit")
async def get_audit_logs(
    limit: int = Query(50, ge=1, le=200, description="每页条数"),
    offset: int = Query(0, ge=0, description="偏移量"),
    start_date: str | None = Query(None, description="开始时间 ISO-8601"),
    end_date: str | None = Query(None, description="结束时间 ISO-8601"),
) -> Dict[str, Any]:
    """
    分页查询审计日志，支持日期范围过滤，返回 {total, records}
    """
    try:
        rows = await query_audit(
            limit=limit,
            offset=offset,
            start_date=start_date,
            end_date=end_date,
        )
        total = await count_audit(start_date=start_date, end_date=end_date)
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


# ── Legacy 接口：兼容旧 /api/audit/logs，后续删除 ──────────────────


@router.get("/audit/logs")
async def get_audit_logs_legacy(
    page: int = Query(1, ge=1, description="页码"),
    limit: int = Query(20, ge=1, le=200, description="每页条数"),
    start_date: str | None = Query(None, description="开始时间"),
    end_date: str | None = Query(None, description="结束时间"),
) -> Dict[str, Any]:
    """[legacy] 旧版审计查询，内部复用同一 query_audit"""
    offset = (page - 1) * limit
    rows = await query_audit(limit=limit, offset=offset, start_date=start_date, end_date=end_date)
    total = await count_audit(start_date=start_date, end_date=end_date)
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
        "code": 200,
        "data": {
            "total": total,
            "list": [rec.model_dump() for rec in records],
        },
    }