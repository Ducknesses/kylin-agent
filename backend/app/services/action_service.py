"""ActionService —— 修复操作安全回查 + 执行层

职责：
  - precheck: session_id + option_id 回查预检
  - execute: low 风险 option 真实安全执行
  - rollback: 回滚已执行的操作（基于 FixOption.rollback 逆向指令）
  - 不直接调用 MCP客户端，全部通过 智能体执行器执行，保证审计上下文完整
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
    "executed", "failed", "error", "rejected",
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
    confirm_id: str | None = None


# ── 安全参数摘要 ──────────────────────────────────────────────────────



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
        confirmation_store: Any = None,
        tool_registry: Any = None,
    ) -> None:
        self._store = fix_option_store
        self._safety_guard = safety_guard
        self._harness = agent_harness
        self._audit = audit_service
        self._confirmation = confirmation_store
        self._tool_registry = tool_registry

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
        """执行入口：low → 真实执行，medium → 创建确认"""
        stored = self._store.get_option(session_id, option_id)
        if stored is None:
            return ActionPrecheck(result="not_found", message="修复选项不存在或不属于当前会话")

        if stored.option.risk_level == "high" or stored.status == "blocked":
            await self._audit_blocked(session_id, stored.trace_id, stored.option.option_id,
                                      stored.option.tool, stored.option.params, "high")
            return ActionPrecheck(result="blocked", option_id=stored.option.option_id,
                                  session_id=stored.session_id, trace_id=stored.trace_id,
                                  risk_level=stored.option.risk_level, message="高风险操作已被系统阻断")

        pre = self.precheck(session_id, option_id)

        # medium → 创建确认
        if pre.result == "confirm_required" or stored.option.risk_level == "medium":
            return await self._execute_medium(session_id, option_id)

        if pre.result != "ready":
            return pre

        return await self._execute_low(session_id, option_id, stored)

    async def _execute_medium(self, session_id: str, option_id: str) -> ActionPrecheck:
        # 重新读取最新 Store 快照（避免 stale snapshot）
        stored = self._store.get_option(session_id, option_id)
        if stored is None:
            return ActionPrecheck(result="not_found", message="修复选项不存在")
        # 幂等：已是 confirm_required 且有有效 pending confirmation
        if stored.status == "confirm_required" and self._confirmation is not None:
            existing = self._confirmation.get_by_option(session_id, option_id)
            if existing is not None and existing.status == "pending":
                return ActionPrecheck(result="confirm_required", option_id=stored.option.option_id,
                                      session_id=session_id, trace_id=stored.trace_id,
                                      risk_level="medium", requires_confirm=True,
                                      message="该操作需要二次确认后才可执行",
                                      confirm_id=existing.confirm_id)
        if stored.status not in ("pending",):
            return ActionPrecheck(result="conflict", option_id=stored.option.option_id,
                                  session_id=session_id, trace_id=stored.trace_id,
                                  message=f"修复选项状态为 {stored.status}，不可创建确认")
        tool = stored.option.tool
        params = stored.option.params
        ctx = AgentContext(session_id=session_id, user_input="action_execute", role="viewer")
        ctx.trace_id = stored.trace_id
        ctx.intent = "action_execute"
        ctx.risk_level = "medium"
        meta = self._tool_registry.build_audit_metadata(tool, dict(params))
        meta["option_id"] = stored.option.option_id
        ctx.add_tool_call(tool, dict(meta), None)

        if self._safety_guard is not None:
            safety = self._safety_guard.analyze_tool_call(tool=tool, params=params, role="viewer")
            if not safety.get("allowed", False):
                await self._safe_audit(ctx, "action_blocked")
                return ActionPrecheck(result="blocked", option_id=stored.option.option_id,
                                      session_id=session_id, trace_id=stored.trace_id,
                                      risk_level=safety.get("risk_level", "high"),
                                      message=f"安全检查未通过: {safety.get('reason', '')}")

        if self._confirmation is not None:
            conf, created = self._confirmation.create_or_get(session_id, stored.option.option_id, stored.trace_id)
            ctx.tool_calls[-1]["params"]["confirm_id"] = conf.confirm_id

            ok = self._store.mark_confirm_required(session_id, stored.option.option_id)
            if not ok:
                recheck = self._store.get_option(session_id, stored.option.option_id)
                if recheck is None or recheck.status != "confirm_required":
                    if created:
                        self._confirmation.remove_if_pending(session_id, stored.option.option_id, conf.confirm_id)
                    return ActionPrecheck(result="error", option_id=stored.option.option_id,
                                          session_id=session_id, trace_id=stored.trace_id,
                                          message="确认创建失败，请稍后重试")

            if created:
                await self._safe_audit(ctx, "action_confirm_created")
            return ActionPrecheck(result="confirm_required",
                                  option_id=stored.option.option_id,
                                  session_id=session_id, trace_id=stored.trace_id,
                                  risk_level="medium", requires_confirm=True,
                                  message="该操作需要二次确认后才可执行",
                                  confirm_id=conf.confirm_id)

        # No confirmation store — internal error
        await self._safe_audit(ctx, "action_confirm_error")
        return ActionPrecheck(result="error", option_id=stored.option.option_id,
                              session_id=session_id, trace_id=stored.trace_id,
                              risk_level="medium", requires_confirm=False,
                              message="确认服务暂时不可用")

    async def _execute_low(self, session_id: str, option_id: str, stored) -> ActionPrecheck:

        tool = stored.option.tool
        params = stored.option.params

        ctx = AgentContext(session_id=session_id, user_input="action_execute", role="viewer")
        ctx.trace_id = stored.trace_id
        ctx.intent = "action_execute"
        ctx.risk_level = "low"
        meta = self._tool_registry.build_audit_metadata(tool, dict(params))
        meta["option_id"] = stored.option.option_id
        ctx.add_tool_call(tool, dict(meta), None)

        if self._safety_guard is not None:
            safety = self._safety_guard.analyze_tool_call(tool=tool, params=params, role="viewer")
            if not safety.get("allowed", False):
                await self._safe_audit(ctx, "action_blocked")
                return ActionPrecheck(result="blocked", option_id=stored.option.option_id,
                                      session_id=session_id, trace_id=stored.trace_id,
                                      risk_level=safety.get("risk_level", "high"),
                                      message=f"安全检查未通过: {safety.get('reason', '')}")
            if safety.get("risk_level", "low") != "low":
                # low→medium 动态升级：委托 _execute_medium 创建确认
                await self._safe_audit(ctx, "action_confirm_required")
                return await self._execute_medium(session_id, option_id)

        claimed = self._store.claim_for_execution(session_id, option_id)
        if claimed is None:
            return ActionPrecheck(result="conflict", option_id=stored.option.option_id,
                                  session_id=session_id, trace_id=stored.trace_id,
                                  message="操作已被其他请求执行或状态冲突")

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

    # ── Confirm decision ───────────────────────────────────────────

    async def decide_confirmation(self, session_id: str, confirm_id: str, decision: str) -> ActionPrecheck:
        """approve/reject 消费 confirmation"""
        if self._confirmation is None:
            return ActionPrecheck(result="error", message="确认服务不可用")

        if decision == "reject":
            return await self._decide_reject(session_id, confirm_id)
        if decision == "approve":
            return await self._decide_approve(session_id, confirm_id)
        return ActionPrecheck(result="error", message="无效的 decision")

    async def _decide_reject(self, session_id: str, confirm_id: str) -> ActionPrecheck:
        res = self._confirmation.reject(session_id, confirm_id)
        if res.result == "not_found":
            return ActionPrecheck(result="not_found", message="确认不存在或不属于当前会话")
        if res.result == "expired":
            return ActionPrecheck(result="expired", message="确认已过期")
        if res.result == "conflict":
            return ActionPrecheck(result="conflict", message="确认已失效或无法操作")
        conf = res.confirmation
        self._store.mark_blocked(session_id, conf.option_id)
        stored_rej = self._store.get_option(session_id, conf.option_id)
        tool_name = stored_rej.option.tool if stored_rej else conf.option_id
        ctx = self._make_ctx(session_id, conf.trace_id, conf.option_id, "medium",
                             "action_execute", tool_name, confirm_id=confirm_id, decision="reject")
        await self._safe_audit(ctx, "action_confirm_rejected")
        return ActionPrecheck(result="rejected", option_id=conf.option_id,
                              session_id=session_id, trace_id=conf.trace_id,
                              message="操作已拒绝")

    async def _decide_approve(self, session_id: str, confirm_id: str) -> ActionPrecheck:
        res = self._confirmation.claim_approve(session_id, confirm_id)
        if res.result == "not_found":
            return ActionPrecheck(result="not_found", message="确认不存在或不属于当前会话")
        if res.result == "expired":
            return ActionPrecheck(result="expired", message="确认已过期")
        if res.result == "conflict":
            return ActionPrecheck(result="conflict", message="确认已失效或无法操作")
        conf = res.confirmation

        option_id = conf.option_id
        stored = self._store.get_option(session_id, option_id)
        if stored is None:
            self._confirmation.mark_failed(session_id, confirm_id)
            return ActionPrecheck(result="conflict", session_id=session_id, trace_id=conf.trace_id,
                                  message="修复选项不存在")
        if stored.status != "confirm_required":
            self._confirmation.mark_failed(session_id, confirm_id)
            return ActionPrecheck(result="conflict", session_id=session_id, trace_id=conf.trace_id,
                                  message="修复选项状态冲突")

        tool = stored.option.tool
        params = stored.option.params
        ctx = self._make_ctx(session_id, stored.trace_id, option_id, "medium",
                             "action_execute", stored.option.tool, confirm_id=confirm_id, decision="approve")

        if self._safety_guard is not None:
            safety = self._safety_guard.analyze_tool_call(tool=tool, params=params, role="viewer")
            if not safety.get("allowed", False) or safety.get("risk_level") == "high":
                self._confirmation.mark_blocked(session_id, confirm_id)
                await self._safe_audit(ctx, "action_confirm_blocked")
                return ActionPrecheck(result="blocked", option_id=option_id,
                                      session_id=session_id, trace_id=conf.trace_id,
                                      message="安全检查未通过")

        claimed = self._store.claim_for_execution(session_id, option_id)
        if claimed is None:
            self._confirmation.mark_failed(session_id, confirm_id)
            return ActionPrecheck(result="conflict", option_id=option_id,
                                  session_id=session_id, trace_id=conf.trace_id,
                                  message="执行冲突")
        try:
            result = await self._harness.run_tool(ctx, tool, dict(params))
        except Exception:
            self._store.mark_failed(session_id, option_id)
            self._confirmation.mark_failed(session_id, confirm_id)
            await self._safe_audit(ctx, "action_confirm_failed")
            return ActionPrecheck(result="failed", option_id=option_id,
                                  session_id=session_id, trace_id=conf.trace_id,
                                  message="执行异常")

        if result.get("ok"):
            self._store.mark_executed(session_id, option_id)
            self._confirmation.mark_consumed(session_id, confirm_id)
            await self._safe_audit(ctx, "action_confirm_executed")
            return ActionPrecheck(result="executed", option_id=option_id,
                                  session_id=session_id, trace_id=conf.trace_id,
                                  message="执行成功")
        else:
            self._store.mark_failed(session_id, option_id)
            self._confirmation.mark_failed(session_id, confirm_id)
            await self._safe_audit(ctx, "action_confirm_failed")
            return ActionPrecheck(result="failed", option_id=option_id,
                                  session_id=session_id, trace_id=conf.trace_id,
                                  message="执行失败")

    # ── Rollback ──────────────────────────────────────────────────

    async def rollback(self, session_id: str, option_id: str) -> ActionPrecheck:
        """回滚已执行的操作

        流程：
          1. 读取 FixOption，检查 status == "executed"
          2. 读取 option.rollback 逆向指令
          3. 执行回滚命令（通过 agent_harness）
          4. 记录回滚审计日志
        """
        stored = self._store.get_option(session_id, option_id)
        if stored is None:
            return ActionPrecheck(result="not_found", message="修复选项不存在或不属于当前会话")

        if stored.status != "executed":
            return ActionPrecheck(
                result="conflict",
                option_id=stored.option.option_id,
                session_id=session_id,
                trace_id=stored.trace_id,
                risk_level=stored.option.risk_level,
                message=f"仅已执行的操作可回滚（当前状态: {stored.status}）",
            )

        rollback_cmd = stored.option.rollback
        if not rollback_cmd or not rollback_cmd.strip():
            return ActionPrecheck(
                result="error",
                option_id=stored.option.option_id,
                session_id=session_id,
                trace_id=stored.trace_id,
                risk_level=stored.option.risk_level,
                message="该操作未提供回滚命令，无法自动回滚",
            )

        # 构建回滚审计上下文
        ctx = AgentContext(session_id=session_id, user_input="action_rollback", role="viewer")
        ctx.trace_id = stored.trace_id
        ctx.intent = "action_rollback"
        ctx.risk_level = stored.option.risk_level
        meta = self._tool_registry.build_audit_metadata("cmd_exec", {"command": rollback_cmd})
        meta["option_id"] = stored.option.option_id
        meta["rollback"] = True
        ctx.add_tool_call("cmd_exec", dict(meta), None)

        # 安全校验
        if self._safety_guard is not None:
            safety = self._safety_guard.analyze_tool_call(
                tool="cmd_exec", params={"command": rollback_cmd}, role="viewer",
            )
            if not safety.get("allowed", False):
                await self._safe_audit(ctx, "action_rollback_blocked")
                return ActionPrecheck(
                    result="blocked",
                    option_id=stored.option.option_id,
                    session_id=session_id,
                    trace_id=stored.trace_id,
                    risk_level=safety.get("risk_level", "high"),
                    message=f"回滚安全检查未通过: {safety.get('reason', '')}",
                )

        # 执行回滚
        try:
            result = await self._harness.run_tool(ctx, "cmd_exec", {"command": rollback_cmd})
        except Exception:
            logger.exception("[ActionService] 回滚执行异常")
            await self._safe_audit(ctx, "action_rollback_failed")
            return ActionPrecheck(
                result="failed",
                option_id=stored.option.option_id,
                session_id=session_id,
                trace_id=stored.trace_id,
                risk_level=stored.option.risk_level,
                message="回滚执行异常",
            )

        if result.get("ok"):
            await self._safe_audit(ctx, "action_rollback_executed")
            raw = result.get("result")
            safe = sanitize_sensitive_data(raw) if raw is not None else None
            summary = json.dumps(safe, ensure_ascii=False, default=str)[:200] if safe is not None else None
            return ActionPrecheck(
                result="executed",
                option_id=stored.option.option_id,
                session_id=session_id,
                trace_id=stored.trace_id,
                risk_level=stored.option.risk_level,
                message="回滚执行成功",
                result_summary=summary,
            )
        else:
            await self._safe_audit(ctx, "action_rollback_failed")
            return ActionPrecheck(
                result="failed",
                option_id=stored.option.option_id,
                session_id=session_id,
                trace_id=stored.trace_id,
                risk_level=stored.option.risk_level,
                message="回滚执行失败",
            )

    # ── 辅助方法 ──────────────────────────────────────────────────

    def _make_ctx(self, session_id: str, trace_id: str, option_id: str, risk: str, intent: str, tool_name: str,
                  confirm_id: str | None = None, decision: str | None = None) -> AgentContext:
        ctx = AgentContext(session_id=session_id, user_input=intent, role="viewer")
        ctx.trace_id = trace_id
        ctx.intent = intent
        ctx.risk_level = risk
        meta = {"option_id": option_id}
        if confirm_id:
            meta["confirm_id"] = confirm_id
        if decision:
            meta["decision"] = decision
        ctx.add_tool_call(tool_name, meta, None)
        return ctx

    async def _audit_blocked(self, session_id: str, trace_id: str, option_id: str,
                              tool: str, params: dict[str, object], risk: str) -> None:
        """Stored high/blocked 审计，不修改 Store"""
        if self._audit is None:
            return
        ctx = AgentContext(session_id=session_id, user_input="action_execute", role="viewer")
        ctx.trace_id = trace_id
        ctx.intent = "action_execute"
        ctx.risk_level = risk
        meta = self._tool_registry.build_audit_metadata(tool, dict(params))
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