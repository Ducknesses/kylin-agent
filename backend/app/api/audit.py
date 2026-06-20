"""审计日志查询接口

正式接口：GET /api/audit/logs（API 文档约定）
参数：page / limit / start_date / end_date
"""
import logging
from typing import Optional

from fastapi import APIRouter, Query

from app.audit.logger import count_audit, query_audit
from app.schemas.models import AuditRecordOut

logger = logging.getLogger(__name__)
router = APIRouter()


def _format_audit_records(rows: list) -> list:
    """将数据库行转换为 API 文档约定的审计记录格式"""
    return [
        {
            "trace_id": r.get("trace_id", ""),
            "timestamp": r.get("timestamp", ""),
            "user_input": r.get("user_input", ""),
            "intent": r.get("intent"),
            "risk_level": r.get("risk_level", ""),
            "mcp_tool": r.get("mcp_tool"),
            "command": r.get("command"),
            "final_response": r.get("final_response"),
        }
        for r in rows
    ]


# ── 正式审计接口（API 文档约定） ───────────────────────────────────


@router.get("/audit/logs")
async def get_audit_logs(
    page: int = Query(1, ge=1, description="页码，从 1 开始"),
    limit: int = Query(20, ge=1, le=200, description="每页条数"),
    start_date: Optional[str] = Query(None, description="开始时间 ISO-8601"),
    end_date: Optional[str] = Query(None, description="结束时间 ISO-8601"),
) -> dict:
    """
    分页查询审计日志

    内部复用 query_audit(limit, offset)，通过 page/limit 计算 offset。
    count_audit 提供 total 字段满足前端分页表格需求。
    """
    # page/limit 转 offset 以复用现有 query_audit
    offset = (page - 1) * limit

    rows = await query_audit(
        limit=limit,
        offset=offset,
        start_date=start_date,
        end_date=end_date,
    )
    total = await count_audit(start_date=start_date, end_date=end_date)

    return {
        "code": 200,
        "data": {
            "total": total,
            "list": _format_audit_records(rows),
        },
    }


# ── 废弃接口：仅保留以防旧调用方启动失败，后续删除 ──────────────────
# 新代码统一使用 /api/audit/logs，此路由不计入正式接口文档。


@router.get("/audit")
async def get_audit_deprecated(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> list:
    """[deprecated] 旧版审计查询，直接返回列表，内部复用同一 query_audit"""
    rows = await query_audit(limit=limit, offset=offset)
    return _format_audit_records(rows)
