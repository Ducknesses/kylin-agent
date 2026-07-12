"""ActionService —— 修复操作安全回查 + 执行层

职责：
  - precheck: session_id + option_id 回查预检
  - execute: low 风险 option 真实安全执行
  - 不直接调用 MCPClient，全部通过 AgentHarness
"""

import json
import logging
from dataclasses import dataclass
from typing import Any, Literal

from app.services.agent_context import AgentContext
from app.services.audit_service import sanitize_sensitive_data
from app.services.fix_option_store import FixOptionStore

logger = logging.getLogger(__name__)

ActionResult = Literal[
    "ready", "confirm_required", "blocked",
    "not_found", "expired", "conflict",
    "executed", "failed", "error",
]


@dataclass
class ActionPrecheck:
    result: ActionResult
    option_id: str = ""
    session_id: str = ""
    trace_id: str = ""
    risk_level: str = "low"
    requires_confirm: bool = False
    message: str = ""
    result_summary: str | None = None


# ── 安全参数摘要 ──────────────────────────────────────────────────────
# 审计中只保留有限安全元数据，不记录完整 params

def _safe_metadata(tool: str, params: dict[str, object]) -> dict[str, object]:
    """提取有限安全元数据，经公共脱敏"""
    meta: dict[str, object] = {}
    if tool == "service_mgr":
        for k in ("action", "service"):
            if k in params:
                meta[k] = params[k]
    elif tool == "sys_info":
        if "metric" in params:
            meta["metric"] = params["metric"]
    elif tool == "log_reader":
        for k in ("service", "lines"):
            if k in params:
                meta[k] = params[k]
    elif tool == "cmd_exec":
        cmd = params.get("command")
        if cmd:
            meta["command"] = sanitize_sensitive_data(str(cmd))
    elif tool == "file_guard":
        for k in ("action", "path"):
            if k in params:
                meta[k] = params[k]
    return sanitize_sensitive_data(meta)  # type: ignore[return-value]


# ═══════════════════════════════════════════════════════════════════════
# ActionService
# ═══════════════════════════════════════════════════════════════════════

class ActionService:
    """修复操作安全回查 + 执行层"""

    def __init__(
        self,
        fix_option_store: FixOptionStore,
        safety_guard: Any = None,
        agent_harness: Any = None,
        audit_service: Any = None,
    ) -> None:
        self._store = fix_option_store
        self._safety_guard = safety_guard
        self._harness = agent_harness
        self._audit = audit_service

    # ── 预检 ──────────────────────────────────────────────────────

    def precheck(self, session_id: str, option_id: str) -> ActionPrecheck:
        stored = self._store.get_option(session_id, option_id)
        if stored is None:
            return ActionPrecheck(result="not_found", message="修复选项不存在或不属于当前会话")
        if stored.status == "expired":
            return ActionPrecheck(result="expired", option_id=stored.option.option_id,
                                  session_id=stored.session_id, trace_id=stored.trace_id,
                                  risk_level=stored.option.risk_level, message="修复选项已过期")
        if stored.option.risk_level == "high":
            return ActionPrecheck(result="blocked", option_id=stored.option.option_id,
                                  session_id=stored.session_id, trace_id=stored.trace_id,
                                  risk_level=stored.option.risk_level, message="高风险操作已被系统阻断")
        if stored.status in ("executing", "executed", "failed"):
            return ActionPrecheck(result="conflict", option_id=stored.option.option_id,
                                  session_id=stored.session_id, trace_id=stored.trace_id,
                                  risk_level=stored.option.risk_level,
                                  message=f"修复操作状态为 {stored.status}，不可重复执行")
        if stored.status == "blocked":
            return ActionPrecheck(result="blocked", option_id=stored.option.option_id,
                                  session_id=stored.session_id, trace_id=stored.trace_id,
                                  risk_level=stored.option.risk_level, message="该操作已被管理员阻断")
        if stored.status == "confirm_required":
            return ActionPrecheck(result="confirm_required", option_id=stored.option.option_id,
                                  session_id=stored.session_id, trace_id=stored.trace_id,
                                  risk_level=stored.option.risk_level, requires_confirm=True,
                                  message="该操作需要二次确认后才可执行")
        if stored.option.risk_level == "medium":
            return ActionPrecheck(result="confirm_required", option_id=stored.option.option_id,
                                  session_id=stored.session_id, trace_id=stored.trace_id,
                                  risk_level=stored.option.risk_level, requires_confirm=True,
                                  message="该操作需要二次确认后才可执行")
        return ActionPrecheck(result="ready", option_id=stored.option.option_id,
                              session_id=stored.session_id, trace_id=stored.trace_id,
                              risk_level=stored.option.risk_level, requires_confirm=False,
                              message="修复操作已就绪，可以执行")

    # ── 执行 ──────────────────────────────────────────────────────

    async def execute(self, session_id: str, option_id: str) -> ActionPrecheck:
        """low 风险 option 真实安全执行"""
        stored = self._store.get_option(session_id, option_id)
        if stored is None:
            return ActionPrecheck(result="not_found", message="修复选项不存在或不属于当前会话")

        # stored high/blocked → action_blocked
        if stored.option.risk_level == "high" or stored.status == "blocked":
            await self._audit_blocked(session_id, stored.trace_id, stored.option.option_id,
                                      stored.option.tool, stored.option.params, "high")
            return ActionPrecheck(result="blocked", option_id=stored.option.option_id,
                                  session_id=stored.session_id, trace_id=stored.trace_id,
                                  risk_level=stored.option.risk_level, message="高风险操作已被系统阻断")

        pre = self.precheck(session_id, option_id)
        if pre.result != "ready":
            return pre

        tool = stored.option.tool
        params = stored.option.params

        # 构造审计上下文 —— 真实 tool + option_id 在安全元数据中
        ctx = AgentContext(session_id=session_id, user_input="action_execute", role="viewer")
        ctx.trace_id = stored.trace_id
        ctx.intent = "action_execute"
        ctx.risk_level = "low"
        meta = _safe_metadata(tool, params)
        meta["option_id"] = stored.option.option_id
        ctx.add_tool_call(tool, dict(meta), None)

        # 二次 SafetyGuard
        if self._safety_guard is not None:
            safety = self._safety_guard.analyze_tool_call(tool=tool, params=params, role="viewer")
            if not safety.get("allowed", False):
                await self._safe_audit(ctx, "action_blocked")
                return ActionPrecheck(result="blocked", option_id=stored.option.option_id,
                                      session_id=session_id, trace_id=stored.trace_id,
                                      risk_level=safety.get("risk_level", "high"),
                                      message=f"安全检查未通过: {safety.get('reason', '')}")
            if safety.get("risk_level", "low") != "low":
                await self._safe_audit(ctx, "action_confirm_required")
                return ActionPrecheck(result="confirm_required", option_id=stored.option.option_id,
                                      session_id=session_id, trace_id=stored.trace_id,
                                      risk_level=safety.get("risk_level", "medium"),
                                      requires_confirm=True,
                                      message="二次安全检查判定需要确认")

        # claim
        claimed = self._store.claim_for_execution(session_id, option_id)
        if claimed is None:
            return ActionPrecheck(result="conflict", option_id=stored.option.option_id,
                                  session_id=session_id, trace_id=stored.trace_id,
                                  message="操作已被其他请求执行或状态冲突")

        # 执行
        try:
            result = await self._harness.run_tool(ctx, tool, dict(params))
        except Exception:
            logger.exception("[ActionService] AgentHarness 异常")
            self._store.mark_failed(session_id, option_id)
            await self._safe_audit(ctx, "action_failed")
            return ActionPrecheck(result="failed", option_id=stored.option.option_id,
                                  session_id=session_id, trace_id=stored.trace_id,
                                  message="修复操作执行异常")

        if result.get("ok"):
            self._store.mark_executed(session_id, option_id)
            await self._safe_audit(ctx, "action_executed")
            raw = result.get("result")
            safe = sanitize_sensitive_data(raw) if raw is not None else None
            summary = json.dumps(safe, ensure_ascii=False, default=str)[:200] if safe is not None else None
            return ActionPrecheck(result="executed", option_id=stored.option.option_id,
                                  session_id=session_id, trace_id=stored.trace_id,
                                  risk_level="low", message="修复操作执行成功",
                                  result_summary=summary)
        else:
            self._store.mark_failed(session_id, option_id)
            await self._safe_audit(ctx, "action_failed")
            return ActionPrecheck(result="failed", option_id=stored.option.option_id,
                                  session_id=session_id, trace_id=stored.trace_id,
                                  message="修复操作执行失败")

    async def _audit_blocked(self, session_id: str, trace_id: str, option_id: str,
                              tool: str, params: dict[str, object], risk: str) -> None:
        """Stored high/blocked 审计，不修改 Store"""
        if self._audit is None:
            return
        ctx = AgentContext(session_id=session_id, user_input="action_execute", role="viewer")
        ctx.trace_id = trace_id
        ctx.intent = "action_execute"
        ctx.risk_level = risk
        meta = _safe_metadata(tool, params)
        meta["option_id"] = option_id
        ctx.add_tool_call(tool, dict(meta), None)
        await self._safe_audit(ctx, "action_blocked")

    async def _safe_audit(self, ctx: AgentContext, event_type: str) -> None:
        if self._audit is None:
            return
        try:
            await self._audit.save_context(ctx, event_type=event_type)
        except Exception:
            logger.warning("[ActionService] 审计写入失败", exc_info=True)
