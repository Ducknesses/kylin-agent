"""Action API —— 修复操作执行接口

POST /api/actions/execute
接收 session_id + option_id，后端回查 FixOptionStore 做安全预检。
本轮只做预检，不执行 FixOption。
"""
import logging

from fastapi import APIRouter, HTTPException

from app.dependencies import action_service
from app.schemas.action import ActionExecuteRequest, ActionExecuteResponse

logger = logging.getLogger(__name__)
router = APIRouter()


@router.post("/actions/execute", response_model=ActionExecuteResponse)
async def execute_action(req: ActionExecuteRequest):
    """执行修复操作预检

    前端只需提交 session_id + option_id，
    tool/params/risk_level 由后端从 FixOptionStore 回查。
    """
    result = action_service.precheck(req.session_id, req.option_id)

    if result.result == "not_found":
        raise HTTPException(status_code=404, detail=result.message)
    if result.result == "expired":
        raise HTTPException(status_code=410, detail=result.message)
    if result.result == "blocked":
        raise HTTPException(status_code=403, detail=result.message)
    if result.result == "conflict":
        raise HTTPException(status_code=409, detail=result.message)

    # ready / confirm_required
    return ActionExecuteResponse(
        option_id=result.option_id,
        session_id=result.session_id,
        trace_id=result.trace_id,
        status=result.result,  # type: ignore[arg-type]
        risk_level=result.risk_level,  # type: ignore[arg-type]
        message=result.message,
        requires_confirm=result.requires_confirm,
    )
