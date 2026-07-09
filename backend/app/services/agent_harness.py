"""AgentHarness —— 统一工具调用入口

职责：
  - 所有 MCP 工具调用必须通过 run_tool 统一入口
  - 按顺序：工具存在性 → 参数校验 → 安全裁决 → 执行 → 记录 → 审计
  - 安全拒绝或需确认时不调用 MCPClient
  - 所有异常被捕获并返回安全错误结构，不让上层崩溃

注意：
  - 不拼 JSON-RPC，不写 HTTP 请求细节——这些属于 MCPClient
  - 不做安全规则裁决——裁决归 SafetyGuard
  - audit_service 可选，本轮不强制实现完整 AuditService
"""

import logging
import re
from typing import Any

from app.audit.logger import log_chain
from app.services.agent_context import AgentContext

logger = logging.getLogger(__name__)

# ── observation 脱敏 ───────────────────────────────────────────────────
# 对写入 ctx.observations 的值做敏感信息过滤，不改变原始参数传给 MCP

_SENSITIVE_RE = re.compile(
    r'(?:sk-[a-zA-Z0-9]{20,})'  # API Key
    r'|(?:Bearer\s+[a-zA-Z0-9\-_\.]+)'  # Bearer token
    r'|(?:eyJ[a-zA-Z0-9\-_]+\.[a-zA-Z0-9\-_]+\.[a-zA-Z0-9\-_]+)'  # JWT
    , re.IGNORECASE,
)

_SENSITIVE_KEY_RE = re.compile(
    r'(?i)^(token|access_token|api_key|password|secret|secret_key|'
    r'private_key|access_key|credential|credentials)$'
)


def _sanitize_observation(value: Any) -> Any:
    """递归脱敏 observation 中的敏感值，保留 dict/list 结构"""
    if isinstance(value, str):
        return _SENSITIVE_RE.sub("[REDACTED]", value)
    if isinstance(value, dict):
        return {
            k: "[REDACTED]" if isinstance(k, str) and _SENSITIVE_KEY_RE.match(k) and isinstance(v, str)
            else _sanitize_observation(v)
            for k, v in value.items()
        }
    if isinstance(value, list):
        return [_sanitize_observation(item) for item in value]
    return value


class AgentHarness:
    """统一工具调用入口 —— 所有 Agent 通过此类调用 MCP 工具

    使用方式：
        harness = AgentHarness(safety_guard, tool_registry, mcp_client)
        result = await harness.run_tool(ctx, "sys_info", {"metric": "cpu"})
    """

    def __init__(
        self,
        safety_guard: Any,
        tool_registry: Any,
        mcp_client: Any,
        audit_service: Any = None,
    ) -> None:
        """
        参数:
            safety_guard: SafetyGuard 实例，提供 analyze_tool_call 方法
            tool_registry: ToolRegistry 实例，提供 exists/validate_params 方法
            mcp_client: MCPClient 实例，提供 call_tool 方法
            audit_service: 可选审计服务，有 log_chain 方法时优先调用，否则回退到模块级 log_chain
        """
        self.safety_guard = safety_guard
        self.tool_registry = tool_registry
        self.mcp_client = mcp_client
        self.audit_service = audit_service

    # ── 主入口 ──────────────────────────────────────────────────────

    async def run_tool(
        self, ctx: AgentContext, tool_name: str, params: dict | None = None
    ) -> dict[str, Any]:
        """统一工具调用入口

        执行顺序：
          1. 工具存在性检查
          2. 参数结构校验
          3. SafetyGuard 安全裁决
          4. 记录 tool_call 到 ctx
          5. 调用 MCPClient.call_tool
          6. 记录 observation 到 ctx
          7. 写审计日志

        返回结构统一为 {"ok": bool, ...}，具体字段按场景不同。
        """
        params = params or {}

        # ── 1. 工具存在性检查 ──
        if not self.tool_registry.exists(tool_name):
            return self._record_and_return(
                ctx,
                tool_name,
                params,
                ok=False,
                error=f"未知工具: {tool_name}",
                risk_level="high",
                blocked=True,
                status="unknown_tool",
            )

        # ── 2. 参数结构校验 ──
        validation = self.tool_registry.validate_params(tool_name, params)
        if not validation["valid"]:
            return self._record_and_return(
                ctx,
                tool_name,
                params,
                ok=False,
                error=f"参数校验失败: {'; '.join(validation['errors'])}",
                risk_level="medium",
                blocked=True,
                status="invalid_params",
            )

        # ── 3. SafetyGuard 安全裁决 ──
        try:
            safety = self.safety_guard.analyze_tool_call(
                tool=tool_name,
                params=params,
                role=ctx.role,
            )
        except Exception as e:
            logger.exception(f"[AgentHarness] SafetyGuard 异常: {e}")
            return self._record_and_return(
                ctx,
                tool_name,
                params,
                ok=False,
                error="安全检查服务异常",
                risk_level="high",
                blocked=True,
                status="safety_error",
            )

        # ── 高危 / 拒绝：不调用 MCPClient ──
        if not safety.get("allowed", False):
            return self._record_and_return(
                ctx,
                tool_name,
                params,
                ok=False,
                error=safety.get("reason", "安全策略拒绝"),
                risk_level=safety.get("risk_level", "high"),
                blocked=True,
                status="blocked",
                safety=safety,
            )

        # ── 中危需确认：不调用 MCPClient ──
        if safety.get("requires_confirm", False):
            result = {
                "ok": False,
                "requires_confirm": True,
                "tool": tool_name,
                "params": params,
                "reason": safety.get("reason", "需要二次确认"),
                "risk_level": safety.get("risk_level", "medium"),
            }
            # 如果 SafetyGuard 返回了 confirm_id 则带上
            if "confirm_id" in safety:
                result["confirm_id"] = safety["confirm_id"]
            # 记录到 ctx
            ctx.add_tool_call(tool_name, params, None)
            ctx.tool_calls[-1]["status"] = "requires_confirm"
            ctx.tool_calls[-1]["safety"] = {
                "risk_level": safety.get("risk_level"),
                "reason": safety.get("reason"),
            }
            return result

        # ── 4. 记录 tool_call（放行时） ──
        ctx.add_tool_call(tool_name, params, None)
        ctx.tool_calls[-1]["status"] = "executing"
        ctx.tool_calls[-1]["safety"] = {
            "risk_level": safety.get("risk_level"),
            "reason": safety.get("reason"),
        }

        # ── 5. 调用 MCPClient ──
        # MCPClient.call_tool 的参数名为 arguments（不是 params）
        try:
            mcp_result = await self.mcp_client.call_tool(
                tool_name, arguments=params
            )
        except Exception as e:
            logger.exception(f"[AgentHarness] MCPClient 异常: {e}")
            mcp_result = {"ok": False, "result": None, "error": "MCP 工具调用异常"}

        # ── 更新 ctx.tool_calls 中本条记录 ──
        ctx.tool_calls[-1]["result"] = mcp_result.get("result") if mcp_result.get("ok") else None
        ctx.tool_calls[-1]["status"] = "done" if mcp_result.get("ok") else "mcp_error"
        ctx.tool_calls[-1]["mcp_error"] = mcp_result.get("error") if not mcp_result.get("ok") else None

        # ── 6. 记录 observation（脱敏后） ──
        obs = {
            "tool": tool_name,
            "params": _sanitize_observation(params),
            "ok": mcp_result.get("ok", False),
        }
        if mcp_result.get("ok"):
            obs["result"] = _sanitize_observation(mcp_result.get("result"))
        else:
            obs["error"] = _sanitize_observation(mcp_result.get("error"))
        ctx.add_observation(obs)

        # ── 7. 审计 ──
        await self._audit(ctx, tool_name, params, safety, mcp_result)

        # ── 构造返回 ──
        if mcp_result.get("ok"):
            return {
                "ok": True,
                "tool": tool_name,
                "params": params,
                "result": mcp_result.get("result"),
                "risk_level": safety.get("risk_level", "low"),
            }
        else:
            return {
                "ok": False,
                "tool": tool_name,
                "params": params,
                "error": mcp_result.get("error", "MCP 调用失败"),
                "risk_level": safety.get("risk_level", "low"),
                "mcp_error": True,
            }

    # ── 内部辅助方法 ─────────────────────────────────────────────────

    def _record_and_return(
        self,
        ctx: AgentContext,
        tool_name: str,
        params: dict,
        *,
        ok: bool,
        error: str,
        risk_level: str,
        blocked: bool = False,
        status: str = "blocked",
        safety: dict | None = None,
    ) -> dict[str, Any]:
        """统一记录 tool_call 到 ctx 并返回错误结构"""
        ctx.add_tool_call(tool_name, params, None)
        ctx.tool_calls[-1]["status"] = status
        ctx.tool_calls[-1]["blocked"] = blocked
        if safety:
            ctx.tool_calls[-1]["safety"] = {
                "risk_level": safety.get("risk_level"),
                "reason": safety.get("reason"),
            }
        return {
            "ok": ok,
            "error": error,
            "risk_level": risk_level,
            "blocked": blocked,
        }

    async def _audit(
        self,
        ctx: AgentContext,
        tool_name: str,
        params: dict,
        safety: dict,
        mcp_result: dict,
    ) -> None:
        """写审计日志 —— 失败不抛异常，不影响主流程"""
        try:
            # 如果传入了 audit_service 且有 log_chain 方法，优先使用
            if self.audit_service is not None and hasattr(self.audit_service, "log_chain"):
                await self.audit_service.log_chain(
                    trace_id=ctx.trace_id,
                    user_input=ctx.user_input,
                    risk_level=safety.get("risk_level", "low"),
                    mcp_tool=tool_name,
                    command=params.get("command"),
                    raw_output=str(mcp_result.get("result", ""))[:500] if mcp_result.get("ok") else None,
                    final_response=None,
                )
            else:
                # 回退到模块级 log_chain 函数
                await log_chain(
                    trace_id=ctx.trace_id,
                    user_input=ctx.user_input,
                    risk_level=safety.get("risk_level", "low"),
                    mcp_tool=tool_name,
                    command=params.get("command"),
                    raw_output=str(mcp_result.get("result", ""))[:500] if mcp_result.get("ok") else None,
                )
        except Exception:
            # 审计失败不能导致 run_tool 崩溃
            logger.warning("[AgentHarness] 审计日志写入失败（已忽略）", exc_info=True)
