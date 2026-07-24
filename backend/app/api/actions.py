"""Action API —— 修复操作执行与确认接口 + 工具定义管理

GET  /api/actions/status/{session_id}      — 查询 session 下所有修复选项状态
POST /api/actions/execute                   — 执行/预检（需要 OP 权限）
POST /api/actions/confirm                   — approve/reject 确认（需要 OP 权限）
POST /api/actions/rollback                  — 回滚已执行操作（需要 OP 权限）

GET  /api/tools/definitions                  — 获取所有工具定义（需要 READ 权限）
GET  /api/tools/definitions/{tool_name}      — 获取单个工具定义（需要 READ 权限）
PUT  /api/tools/definitions/{tool_name}/risk — 修改工具风险等级（需要 ADMIN 权限）
PUT  /api/tools/definitions/{tool_name}/audit — 修改审计策略（需要 ADMIN 权限）
"""
import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.core.auth import AuthContext, AuthLevel
from app.dependencies import action_service, fix_option_store, require_auth, server_manager, tool_registry
from app.schemas.action import (
    ActionConfirmRequest,
    ActionConfirmResponse,
    ActionExecuteRequest,
    ActionExecuteResponse,
    ActionRollbackRequest,
)

logger = logging.getLogger(__name__)
router = APIRouter()

# ── 选项状态响应模型 ─────────────────────────────────────────────────

class OptionStatusItem(BaseModel):
    """单个修复选项的状态摘要"""
    option_id: str
    status: str
    risk_level: str
    message: str | None = None
    result_summary: str | None = None


class OptionStatusResponse(BaseModel):
    """查询 session 下所有 repair option 状态的响应"""
    session_id: str
    options: list[OptionStatusItem]


# ── 状态查询接口 ──────────────────────────────────────────────────────

@router.get("/actions/status/{session_id}", response_model=OptionStatusResponse)
async def get_actions_status(
    session_id: str,
    auth: AuthContext = Depends(require_auth(AuthLevel.READ)),
):
    stored_list = fix_option_store.list_options(session_id)
    items: list[OptionStatusItem] = []
    for stored in stored_list:
        opt = stored.option
        item = OptionStatusItem(
            option_id=opt.option_id,
            status=stored.status,
            risk_level=opt.risk_level,
        )
        if stored.status == "executed":
            item.message = "执行成功"
        elif stored.status == "failed":
            item.message = "执行失败"
        elif stored.status == "blocked":
            item.message = "已被阻断"
        elif stored.status == "rolled_back":
            item.message = "已回滚"
        elif stored.status == "expired":
            item.message = "已过期"
        elif stored.status == "confirm_required":
            item.message = "等待二次确认"
        elif stored.status == "executing":
            item.message = "正在执行中..."
        item.result_summary = stored.result_summary
        items.append(item)
    return OptionStatusResponse(session_id=session_id, options=items)


def _raise_for_result(result) -> None:
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
async def execute_action(
    req: ActionExecuteRequest,
    auth: AuthContext = Depends(require_auth(AuthLevel.OP)),
):
    result = await action_service.execute(req.session_id, req.option_id)
    _raise_for_result(result)
    return ActionExecuteResponse(
        option_id=result.option_id, session_id=result.session_id,
        trace_id=result.trace_id, status=result.result, risk_level=result.risk_level,  # type: ignore[arg-type]
        message=result.message, requires_confirm=result.requires_confirm,
        result_summary=result.result_summary, confirm_id=result.confirm_id,
    )


@router.post("/actions/confirm", response_model=ActionConfirmResponse)
async def confirm_action(
    req: ActionConfirmRequest,
    auth: AuthContext = Depends(require_auth(AuthLevel.OP)),
):
    result = await action_service.decide_confirmation(req.session_id, req.confirm_id, req.decision)
    _raise_for_result(result)
    return ActionConfirmResponse(
        confirm_id=req.confirm_id, option_id=result.option_id or "",
        session_id=req.session_id, trace_id=result.trace_id or "",
        decision=req.decision, status=result.result,  # type: ignore[arg-type]
        message=result.message, result_summary=result.result_summary,
    )


@router.post("/actions/rollback", response_model=ActionExecuteResponse)
async def rollback_action(
    req: ActionRollbackRequest,
    auth: AuthContext = Depends(require_auth(AuthLevel.OP)),
):
    result = await action_service.rollback(req.session_id, req.option_id)
    _raise_for_result(result)
    return ActionExecuteResponse(
        option_id=result.option_id, session_id=result.session_id,
        trace_id=result.trace_id, status=result.result, risk_level=result.risk_level,  # type: ignore[arg-type]
        message=result.message, requires_confirm=False,
        result_summary=result.result_summary, confirm_id=None,
    )


# ═══════════════════════════════════════════════════════════════════════
# 工具定义管理 API
# ═══════════════════════════════════════════════════════════════════════


class UpdateToolRiskRequest(BaseModel):
    default_risk: str | None = None
    action_risk_overrides: dict[str, str] | None = None


class UpdateToolAuditRequest(BaseModel):
    mode: str | None = None
    safe_fields: list[str] | None = None
    summary_builder: str | None = None


@router.get("/tools/definitions")
async def get_tool_definitions(
    auth: AuthContext = Depends(require_auth(AuthLevel.READ)),
):
    definitions = tool_registry.get_all_tool_definitions()
    return {"tools": definitions, "count": len(definitions)}


@router.get("/tools/definitions/{tool_name}")
async def get_tool_definition(
    tool_name: str,
    auth: AuthContext = Depends(require_auth(AuthLevel.READ)),
):
    definition = tool_registry.get_tool_definition(tool_name)
    if definition is None:
        raise HTTPException(status_code=404, detail=f"工具 '{tool_name}' 不存在")
    return definition


@router.put("/tools/definitions/{tool_name}/risk")
async def update_tool_risk(
    tool_name: str,
    req: UpdateToolRiskRequest,
    auth: AuthContext = Depends(require_auth(AuthLevel.ADMIN)),
):
    if req.default_risk is None and req.action_risk_overrides is None:
        raise HTTPException(status_code=400, detail="至少需要提供 default_risk 或 action_risk_overrides 之一")
    if req.default_risk is not None and req.default_risk not in ("low", "medium", "high"):
        raise HTTPException(status_code=400, detail=f"无效的 default_risk: {req.default_risk}，可选: low, medium, high")
    if req.action_risk_overrides is not None:
        for action, risk in req.action_risk_overrides.items():
            if risk not in ("low", "medium", "high"):
                raise HTTPException(status_code=400, detail=f"无效的风险等级 '{risk}' for action '{action}'")
    success = tool_registry.update_tool_risk(
        tool_name=tool_name,
        default_risk=req.default_risk,
        action_risk_overrides=req.action_risk_overrides,
    )
    if not success:
        raise HTTPException(status_code=404, detail=f"工具 '{tool_name}' 不存在或参数无效")
    tool_registry.save_config()
    logger.info("用户修改了工具 '%s' 的风险等级: default_risk=%s, overrides=%s",
                tool_name, req.default_risk, req.action_risk_overrides)
    return tool_registry.get_tool_definition(tool_name)


@router.put("/tools/definitions/{tool_name}/audit")
async def update_tool_audit(
    tool_name: str,
    req: UpdateToolAuditRequest,
    auth: AuthContext = Depends(require_auth(AuthLevel.ADMIN)),
):
    if req.mode is None and req.safe_fields is None and req.summary_builder is None:
        raise HTTPException(status_code=400, detail="至少需要提供 mode、safe_fields 或 summary_builder 之一")
    if req.mode is not None and req.mode not in ("whitelist", "summary", "full"):
        raise HTTPException(status_code=400, detail=f"无效的 mode: {req.mode}，可选: whitelist, summary, full")
    success = tool_registry.update_tool_audit_policy(
        tool_name=tool_name,
        mode=req.mode,
        safe_fields=req.safe_fields,
        summary_builder=req.summary_builder,
    )
    if not success:
        raise HTTPException(status_code=404, detail=f"工具 '{tool_name}' 不存在或参数无效")
    tool_registry.save_config()
    logger.info("用户修改了工具 '%s' 的审计策略: mode=%s", tool_name, req.mode)
    return tool_registry.get_tool_definition(tool_name)


# ── 工具刷新接口 ─────────────────────────────────────────────────────────


@router.post("/tools/definitions/refresh")
async def refresh_tool_definitions(
    auth: AuthContext = Depends(require_auth(AuthLevel.ADMIN)),
):
    """手动触发 MCP 工具重新发现

    调用 mcp_server_manager.connect_all_and_discover() 并返回发现结果。
    需要 ADMIN 权限。
    """
    result = await server_manager.connect_all_and_discover()
    if result["success"] == 0 and result["total"] > 0:
        raise HTTPException(status_code=502, detail="所有 MCP 服务器连接失败，请检查服务器状态")
    return {
        "message": "工具列表已刷新",
        "total_servers": result["total"],
        "success_count": result["success"],
        "details": result.get("results", []),
        "available_tools": tool_registry.get_tool_names(),
    }
