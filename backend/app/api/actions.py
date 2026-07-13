"""Action API —— 修复操作执行与确认接口

POST /api/actions/execute  — 执行/预检
POST /api/actions/confirm  — approve/reject 确认
"""
import logging

from fastapi import APIRouter, HTTPException

from app.dependencies import action_service
from app.schemas.action import (
    ActionConfirmRequest,
    ActionConfirmResponse,
    ActionExecuteRequest,
    ActionExecuteResponse,
)

logger = logging.getLogger(__name__)
router = APIRouter()


def _raise_for_result(result) -> None:
    """统一 ActionResult → HTTP 映射: error→500, failed→502"""
    if result.result == "not_found":
        raise HTTPException(status_code=404, detail=result.message)
    if result.result == "expired":
        raise HTTPException(status_code=410, detail=result.message)
    if result.result == "blocked":
        raise HTTPException(status_code=403, detail=result.message)
    if result.result == "conflict":
        raise HTTPException(status_code=409, detail=result.message)
    if result.result == "failed":
        raise HTTPException(status_code=502, detail=result.message)
    if result.result == "error":
        raise HTTPException(status_code=500, detail=result.message)


@router.post("/actions/execute", response_model=ActionExecuteResponse)
async def execute_action(req: ActionExecuteRequest):
    result = await action_service.execute(req.session_id, req.option_id)
    _raise_for_result(result)

    return ActionExecuteResponse(
        option_id=result.option_id, session_id=result.session_id,
        trace_id=result.trace_id, status=result.result, risk_level=result.risk_level,  # type: ignore[arg-type]
        message=result.message, requires_confirm=result.requires_confirm,
        result_summary=result.result_summary, confirm_id=result.confirm_id,
    )


@router.post("/actions/confirm", response_model=ActionConfirmResponse)
async def confirm_action(req: ActionConfirmRequest):
    result = await action_service.decide_confirmation(req.session_id, req.confirm_id, req.decision)
    _raise_for_result(result)

    return ActionConfirmResponse(
        confirm_id=req.confirm_id, option_id=result.option_id or "",
        session_id=req.session_id, trace_id=result.trace_id or "",
        decision=req.decision, status=result.result,  # type: ignore[arg-type]
        message=result.message, result_summary=result.result_summary,
    )
