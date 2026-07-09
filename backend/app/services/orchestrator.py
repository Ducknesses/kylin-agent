"""Orchestrator —— Agent 主流程编排器

串起 IntentAgent → DiagnoseAgent → AgentHarness → ReporterAgent → AuditService。
支持 LLM 增强路径（通过 LLM_ENABLED 配置开关）与规则版 fallback。
"""
import logging
import uuid
from typing import Any, AsyncIterator

from config import settings

logger = logging.getLogger(__name__)

# Mock 结果数据（保留向后兼容）
_MOCK_CPU_RESULT = {
    "cpu_percent": 23.5, "load_avg": [0.52, 0.31, 0.18], "cores": 4,
}
_MOCK_NGINX_STATUS_RESULT = {
    "service": "nginx", "status": "active", "sub_state": "running", "uptime": "3 days 12:34:56",
}
_MOCK_NGINX_RESTART_RESULT = {"ok": True, "message": "mock restart accepted"}

_CPU_KEYWORDS = ["CPU", "cpu", "处理器", "使用率"]
_NGINX_STATUS_KEYWORDS = ["nginx", "nginx 状态", "nginx status", "查询 nginx"]
_RESTART_NGINX_KEYWORDS = ["重启 nginx", "restart nginx"]


def _match_any(text: str, keywords: list[str]) -> bool:
    return any(k in text for k in keywords)


def _create_default_mcp_client() -> Any:
    from app.mcp.client import MCPClient
    return MCPClient()


async def mock_orchestrate(user_input: str) -> AsyncIterator[dict[str, Any]]:
    """Mock 业务编排，根据用户输入返回流式消息序列"""
    trace_id = str(uuid.uuid4())[:16]

    if _match_any(user_input, _CPU_KEYWORDS):
        yield {"type": "status", "content": "正在查询 CPU 使用率...", "trace_id": trace_id}
        yield {"type": "tool_call", "tool": "sys_info", "tool_call_id": f"tc_{str(uuid.uuid4())[:8]}",
               "params": {"metric": "cpu"}, "result": _MOCK_CPU_RESULT, "trace_id": trace_id}
        yield {"type": "chunk", "content": f"当前 CPU 使用率约为 {_MOCK_CPU_RESULT['cpu_percent']}%，系统负载为 {', '.join(str(v) for v in _MOCK_CPU_RESULT['load_avg'])}。", "trace_id": trace_id}
        yield {"type": "done", "trace_id": trace_id}
        return

    if _match_any(user_input, _RESTART_NGINX_KEYWORDS):
        yield {"type": "status", "content": "已收到确认，正在模拟重启 nginx...", "trace_id": trace_id}
        yield {"type": "tool_call", "tool": "service_mgr", "tool_call_id": f"tc_{str(uuid.uuid4())[:8]}",
               "params": {"action": "restart", "service": "nginx"}, "result": _MOCK_NGINX_RESTART_RESULT, "trace_id": trace_id}
        yield {"type": "chunk", "content": "已模拟提交 nginx 重启操作。当前仍为 Mock 流程，未调用真实 MCP。", "trace_id": trace_id}
        yield {"type": "done", "trace_id": trace_id}
        return

    if _match_any(user_input, _NGINX_STATUS_KEYWORDS):
        yield {"type": "status", "content": "正在查询 nginx 服务状态...", "trace_id": trace_id}
        yield {"type": "tool_call", "tool": "service_mgr", "tool_call_id": f"tc_{str(uuid.uuid4())[:8]}",
               "params": {"action": "status", "service": "nginx"}, "result": _MOCK_NGINX_STATUS_RESULT, "trace_id": trace_id}
        si = _MOCK_NGINX_STATUS_RESULT
        yield {"type": "chunk", "content": f"nginx 服务当前为 {si['status']} ({si['sub_state']})，已运行约 {si['uptime']}。", "trace_id": trace_id}
        yield {"type": "done", "trace_id": trace_id}
        return

    yield {"type": "status", "content": "正在分析意图...", "trace_id": trace_id}
    yield {"type": "chunk", "content": f"已收到您的输入：「{user_input}」。当前为 Mock 模式，暂不支持真实运维操作。", "trace_id": trace_id}
    yield {"type": "done", "trace_id": trace_id}


# ═══════════════════════════════════════════════════════════════════════
# Orchestrator（Agent 主流程闭环）
# ═══════════════════════════════════════════════════════════════════════

class Orchestrator:
    """Agent 主流程编排器 —— 串起 IntentAgent → DiagnoseAgent → AgentHarness → ReporterAgent → AuditService

    handle_chat 是一个 async generator，按 WebSocket v1.1 规范产出事件帧 dict。
    所有工具调用必须通过 AgentHarness.run_tool，不直接调用执行器客户端。
    LLM_ENABLED=true 时自动使用 Agent 的 LLM 增强路径（*_with_llm 方法）。
    """

    def __init__(
        self, safety_guard: Any, tool_registry: Any, mcp_client: Any,
        intent_agent: Any = None, diagnose_agent: Any = None,
        agent_harness: Any = None, reporter_agent: Any = None,
        audit_service: Any = None,
    ) -> None:
        self.safety_guard = safety_guard
        self.tool_registry = tool_registry
        self.mcp_client = mcp_client

        if intent_agent is None:
            from app.services.intent_agent import IntentAgent
            intent_agent = IntentAgent()
        self.intent_agent = intent_agent

        if diagnose_agent is None:
            from app.services.diagnose_agent import DiagnoseAgent
            diagnose_agent = DiagnoseAgent(tool_registry=tool_registry)
        self.diagnose_agent = diagnose_agent

        if agent_harness is None:
            from app.services.agent_harness import AgentHarness
            from app.services.tool_registry import ToolRegistry
            _tr = tool_registry if tool_registry is not None else ToolRegistry()
            _mc = mcp_client
            if _mc is None:
                _mc = _create_default_mcp_client()
            agent_harness = AgentHarness(
                safety_guard=safety_guard, tool_registry=_tr,
                mcp_client=_mc, audit_service=audit_service,
            )
        self.agent_harness = agent_harness

        if reporter_agent is None:
            from app.services.reporter_agent import ReporterAgent
            reporter_agent = ReporterAgent()
        self.reporter_agent = reporter_agent

        if audit_service is None:
            from app.services.audit_service import AuditService
            audit_service = AuditService()
        self.audit_service = audit_service

    # ── 主入口 ──────────────────────────────────────────────────────

    async def handle_chat(
        self, session_id: str, user_input: str, role: str = "viewer",
        confirmed: bool = False, trace_id: str | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        from app.services.agent_context import AgentContext

        ctx = AgentContext(session_id=session_id, user_input=user_input, role=role)
        if trace_id:
            ctx.trace_id = trace_id
        trace_id = ctx.trace_id

        try:
            # ── 2. 安全检查 ──
            safety = self.safety_guard.analyze_user_input(user_input)
            ctx.risk_level = safety.get("risk_level", "low")

            if not safety.get("allowed", False):
                ctx.risk_level = safety.get("risk_level", "high")
                ctx.final_response = safety.get("reason", "安全策略拒绝")
                yield {"type": "risk_alert", "trace_id": trace_id, "level": ctx.risk_level, "message": safety.get("reason", "")}
                yield {"type": "done", "trace_id": trace_id, "session_id": session_id, "final_response": ctx.final_response}
                await self._safe_audit(ctx, "blocked")
                return

            if safety.get("requires_confirm") and not confirmed:
                ctx.risk_level = safety.get("risk_level", "medium")
                ctx.final_response = "等待用户确认"
                yield {"type": "risk_alert", "trace_id": trace_id, "level": "medium", "message": safety.get("reason", "")}
                yield {"type": "done", "trace_id": trace_id, "session_id": session_id, "final_response": ctx.final_response}
                await self._safe_audit(ctx, "confirm_required")
                return

            # ── 3. IntentAgent ──
            yield {"type": "status", "trace_id": trace_id, "message": "正在分析您的请求..."}
            if settings.LLM_ENABLED:
                intent_result = await self.intent_agent.detect_with_llm(user_input)
            else:
                intent_result = self.intent_agent.detect(user_input)
            ctx.intent = intent_result.get("intent")

            # ── 4. DiagnoseAgent ──
            if settings.LLM_ENABLED:
                plan_result = await self.diagnose_agent.plan_with_llm(intent_result)
            else:
                plan_result = self.diagnose_agent.plan(intent_result)
            plans = plan_result.get("plans", [])

            # ── 无计划场景 ──
            if not plans:
                report = await self._generate_report(intent_result, ctx.observations, user_input)
                ctx.final_response = report
                for i in range(0, len(report), 500):
                    yield {"type": "chunk", "trace_id": trace_id, "content": report[i:i + 500]}
                yield {"type": "done", "trace_id": trace_id, "session_id": session_id, "final_response": ctx.final_response}
                await self._safe_audit(ctx, "chat_done")
                return

            # ── 5. 逐个执行工具 ──
            yield {"type": "status", "trace_id": trace_id, "message": f"正在执行 {len(plans)} 个诊断步骤..."}

            for plan_item in plans:
                tool_name = plan_item["tool"]
                params = plan_item["params"]
                result = await self.agent_harness.run_tool(ctx, tool_name, params)
                # 对前端帧脱敏 params，不影响真实执行
                safe_params = self._safe_params_for_display(params)
                yield {
                    "type": "tool_call", "trace_id": trace_id,
                    "tool": tool_name, "params": safe_params,
                    "ok": result.get("ok", False),
                }

            # ── 6. ReporterAgent ──
            report = await self._generate_report(intent_result, ctx.observations, user_input)
            ctx.final_response = report
            for i in range(0, len(report), 500):
                yield {"type": "chunk", "trace_id": trace_id, "content": report[i:i + 500]}
            yield {"type": "done", "trace_id": trace_id, "session_id": session_id, "final_response": ctx.final_response}
            await self._safe_audit(ctx, "chat_done")

        except Exception as e:
            logger.exception(f"[Orchestrator] 处理异常: {e}")
            ctx.final_response = "系统处理异常，请稍后重试"
            yield {"type": "error", "trace_id": trace_id, "message": "系统处理异常，请稍后重试"}
            yield {"type": "done", "trace_id": trace_id, "session_id": session_id, "final_response": ctx.final_response}
            await self._safe_audit(ctx, "error")

    # ── 内部辅助 ──────────────────────────────────────────────────────

    async def _generate_report(self, intent_result: dict, observations: list, user_input: str) -> str:
        """根据 LLM_ENABLED 选择报告生成路径"""
        if settings.LLM_ENABLED:
            return await self.reporter_agent.generate_with_llm(
                intent_result=intent_result, observations=observations, user_input=user_input,
            )
        return self.reporter_agent.generate(
            intent_result=intent_result, observations=observations, user_input=user_input,
        )

    @staticmethod
    def _safe_params_for_display(params: dict) -> dict:
        """对前端展示用的 params 进行脱敏，不影响真实执行参数"""
        from app.services.agent_harness import _sanitize_observation
        sanitized = _sanitize_observation(params)
        if isinstance(sanitized, dict):
            return sanitized
        return params  # 防御：如果脱敏返回非 dict，退回原始值

    async def _safe_audit(self, ctx: Any, event_type: str) -> None:
        try:
            await self.audit_service.save_context(ctx, event_type=event_type)
        except Exception:
            logger.warning("[Orchestrator] 审计写入失败（已忽略）", exc_info=True)
