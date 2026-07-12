"""Action API —— 修复操作执行接口

POST /api/actions/execute
接收 session_id + option_id，后端回查并安全执行。
仅 low 风险 option 可执行。
"""
import logging

from fastapi import APIRouter, HTTPException

from app.dependencies import action_service
from app.schemas.action import ActionExecuteRequest, ActionExecuteResponse

logger = logging.getLogger(__name__)
router = APIRouter()


@router.post("/actions/execute", response_model=ActionExecuteResponse)
async def execute_action(req: ActionExecuteRequest):
    """执行修复操作

    low 风险 → 真实执行；medium → confirm_required；high → blocked。
    """
    # ready 的 low option 走真实执行路径
    result = await action_service.execute(req.session_id, req.option_id)

    if result.result == "not_found":
        raise HTTPException(status_code=404, detail=result.message)
    if result.result == "expired":
        raise HTTPException(status_code=410, detail=result.message)
    if result.result == "blocked":
        raise HTTPException(status_code=403, detail=result.message)
    if result.result == "conflict":
        raise HTTPException(status_code=409, detail=result.message)
    if result.result in ("failed", "error"):
        raise HTTPException(status_code=502, detail=result.message)

    return ActionExecuteResponse(
        option_id=result.option_id,
        session_id=result.session_id,
        trace_id=result.trace_id,
        status=result.result,  # type: ignore[arg-type]
        risk_level=result.risk_level,  # type: ignore[arg-type]
        message=result.message,
        requires_confirm=result.requires_confirm,
        result_summary=result.result_summary,
    )
