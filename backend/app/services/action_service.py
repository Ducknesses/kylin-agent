"""ActionService —— 修复操作安全回查层

职责：
  - 接收 session_id + option_id
  - 从 FixOptionStore 回查原始 FixOption
  - 根据状态/风险/过期做预检判定
  - 不调用 AgentHarness/MCPClient/SafetyGuard/LLMClient
  - 不执行 option，不修改 Store 状态
"""

import logging
from dataclasses import dataclass
from typing import Literal

from app.services.fix_option_store import FixOptionStore

logger = logging.getLogger(__name__)

# ── ActionService 返回类型 ───────────────────────────────────────────

ActionResult = Literal[
    "ready",            # low → 可执行
    "confirm_required",  # medium → 需确认
    "blocked",          # high / 被阻断
    "not_found",        # 不存在或 session 不匹配
    "expired",          # TTL 已过
    "conflict",         # 状态冲突 (executing/executed/failed)
]


@dataclass
class ActionPrecheck:
    """预检结果"""
    result: ActionResult
    option_id: str = ""
    session_id: str = ""
    trace_id: str = ""
    risk_level: str = "low"
    requires_confirm: bool = False
    message: str = ""


# ═══════════════════════════════════════════════════════════════════════
# ActionService
# ═══════════════════════════════════════════════════════════════════════

class ActionService:
    """修复操作安全回查层 —— 本轮只做预检，不执行

    使用方式：
        service = ActionService(fix_option_store=store)
        result = service.precheck("s1", "fix_abc12345")
    """

    def __init__(self, fix_option_store: FixOptionStore) -> None:
        self._store = fix_option_store

    def precheck(self, session_id: str, option_id: str) -> ActionPrecheck:
        """根据 session_id + option_id 回查并做预检判定

        不修改 Store 状态，不调用 claim_for_execution。
        """
        stored = self._store.get_option(session_id, option_id)
        if stored is None:
            return ActionPrecheck(
                result="not_found",
                message="修复选项不存在或不属于当前会话",
            )

        if stored.status == "expired":
            return ActionPrecheck(
                result="expired",
                option_id=stored.option.option_id,
                session_id=stored.session_id,
                trace_id=stored.trace_id,
                risk_level=stored.option.risk_level,
                message="修复选项已过期",
            )

        # high 不生成 FixOption，此处防御
        if stored.option.risk_level == "high":
            return ActionPrecheck(
                result="blocked",
                option_id=stored.option.option_id,
                session_id=stored.session_id,
                trace_id=stored.trace_id,
                risk_level=stored.option.risk_level,
                message="高风险操作已被系统阻断",
            )

        # 状态冲突
        if stored.status in ("executing", "executed", "failed"):
            return ActionPrecheck(
                result="conflict",
                option_id=stored.option.option_id,
                session_id=stored.session_id,
                trace_id=stored.trace_id,
                risk_level=stored.option.risk_level,
                message=f"修复操作状态为 {stored.status}，不可重复执行",
            )

        if stored.status == "blocked":
            return ActionPrecheck(
                result="blocked",
                option_id=stored.option.option_id,
                session_id=stored.session_id,
                trace_id=stored.trace_id,
                risk_level=stored.option.risk_level,
                message="该操作已被管理员阻断",
            )

        # confirm_required 状态——无论 risk_level 都返回需确认
        if stored.status == "confirm_required":
            return ActionPrecheck(
                result="confirm_required",
                option_id=stored.option.option_id,
                session_id=stored.session_id,
                trace_id=stored.trace_id,
                risk_level=stored.option.risk_level,
                requires_confirm=True,
                message="该操作需要二次确认后才可执行",
            )

        # pending → 根据 risk_level 判定
        if stored.option.risk_level == "medium":
            return ActionPrecheck(
                result="confirm_required",
                option_id=stored.option.option_id,
                session_id=stored.session_id,
                trace_id=stored.trace_id,
                risk_level=stored.option.risk_level,
                requires_confirm=True,
                message="该操作需要二次确认后才可执行",
            )

        # low
        return ActionPrecheck(
            result="ready",
            option_id=stored.option.option_id,
            session_id=stored.session_id,
            trace_id=stored.trace_id,
            risk_level=stored.option.risk_level,
            requires_confirm=False,
            message="修复操作已就绪，可以执行",
        )
