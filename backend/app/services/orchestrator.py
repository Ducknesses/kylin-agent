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


async def _generate_session_title(session_id: str, user_input: str, report: str) -> str:
    """根据对话内容生成会话标题，LLM 不可用时降级为用户输入截断。

    同时写入数据库（幂等：仅当当前标题以 '新会话' 开头时才更新）。
    """
    # 延迟导入避免循环依赖
    import json
    from app.dependencies import message_repository
    from app.services.llm_client import LLMClient

    client = LLMClient()

    async def _try_generate(system: str, user: str) -> str:
        """尝试生成标题，返回 content（可能为空）"""
        resp = await client.chat_simple(
            system_prompt=system,
            user_prompt=user,
            max_tokens=2048,
            temperature=0.2,
            timeout=20,
        )
        return resp.content or ""

    # 第一次尝试：标准 prompt
    title = await _try_generate(
        system=(
            "你是一个标题生成助手。请把运维对话总结为一个短标题（15汉字以内），"
            "只输出标题本身，不要解释、不要加引号、不要加序号。"
        ),
        user=(
            f"把以下运维对话总结为一个短标题（15汉字以内）：\n"
            f"用户问题：{user_input}\n"
            f"助手回答：{report[:300]}"
        ),
    )

    # 第二次尝试：JSON 格式 prompt（模型对结构化输出更稳定）
    if not title or len(title.strip()) < 2:
        logger.info("[Title] 第一次尝试返回空，使用 JSON 格式重试")
        resp = await client.chat_simple(
            system_prompt=(
                "你是一个标题生成助手。请把运维对话总结为一个短标题（15汉字以内）。"
                "只输出 JSON 对象，不要解释、不要加 markdown 代码块标记。"
            ),
            user_prompt=(
                f"请总结以下运维对话的标题，输出 JSON 格式：\n"
                f"用户问题：{user_input}\n"
                f"助手回答：{report[:300]}\n\n"
                f'输出格式：{{"title": "短标题内容"}}'
            ),
            max_tokens=2048,
            temperature=0.2,
            timeout=20,
        )
        raw = resp.content or ""
        try:
            parsed = json.loads(raw.strip().strip("`").strip())
            if isinstance(parsed, dict) and "title" in parsed:
                title = parsed["title"]
        except (json.JSONDecodeError, AttributeError):
            title = raw.strip()

    # 第三次尝试：增大 temperature 增强生成多样性
    if not title or len(title.strip()) < 2:
        logger.info("[Title] 第二次尝试仍失败，增大 temperature 重试")
        resp = await client.chat_simple(
            system_prompt=(
                "你是一个标题生成助手。请把运维对话总结为一个短标题（15汉字以内），"
                "只输出标题本身，不要解释、不要加引号、不要加序号。"
            ),
            user_prompt=(
                f"把以下运维对话总结为一个短标题（15汉字以内）：\n"
                f"用户问题：{user_input}\n"
                f"助手回答：{report[:300]}"
            ),
            max_tokens=2048,
            temperature=0.7,
            timeout=20,
        )
        title = (resp.content or "").strip()

    # 清理大模型可能额外包裹的引号
    for quote in ('"', "'", "「", "」", "『", "』", "“", "”"):
        title = title.strip(quote)

    # 清理可能的序号前缀（如 "1. "、"1、"等）
    import re
    title = re.sub(r'^[\s\d]+[.、．)\]]\s*', '', title)

    # 如果 title 被引号包裹后只剩空串，再 strip 一次空格
    title = title.strip()

    if len(title) > 30:
        title = title[:30]

    # LLM 不可用或返回内容过短时的降级规则：用户输入截断 30 字
    if len(title) < 2:
        logger.info(
            f"[Title] LLM 标题生成最终失败，降级为用户输入截断: session={session_id}"
        )
        text = user_input.strip()
        title = text[:30] + ("…" if len(text) > 30 else "")
    else:
        logger.info(f"[Title] LLM 生成标题成功: session={session_id}, title={title!r}")

    # 写入 DB（幂等，失败静默）
    try:
        await message_repository.update_session_title(session_id, title)
    except Exception:
        logger.warning("[Orchestrator] 标题写入 DB 失败（已忽略）", exc_info=True)

    return title


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
        fix_planner: Any = None,
        fix_option_store: Any = None,
        knowledge_service: Any = None,
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
            diagnose_agent = DiagnoseAgent(
                tool_registry=tool_registry,
                knowledge_service=knowledge_service,
            )
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

        # FixPlannerAgent 和 FixOptionStore（可选，默认创建新实例）
        if fix_planner is None:
            from app.services.fix_planner_agent import FixPlannerAgent
            fix_planner = FixPlannerAgent(tool_registry=tool_registry)
        self.fix_planner = fix_planner

        if fix_option_store is None:
            from app.services.fix_option_store import FixOptionStore
            fix_option_store = FixOptionStore()
        self.fix_option_store = fix_option_store

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
            ctx.intent_result = intent_result
            # 保存 intent_result 供后续 FixPlanner 使用
            intent = ctx.intent or "unknown"
            target_service = intent_result.get("target_service")

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
                title = await _generate_session_title(session_id, user_input, report)
                yield {"type": "done", "trace_id": trace_id, "session_id": session_id, "title": title, "final_response": ctx.final_response}
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
                tool_call_record = ctx.tool_calls[-1] if ctx.tool_calls else {}
                tool_call_id = tool_call_record.get("tool_call_id") or f"tc_{str(uuid.uuid4())[:8]}"

                # 中危工具需要二次确认：发送 pending_confirmation 帧并暂停当前回合
                if result.get("requires_confirm"):
                    yield {
                        "type": "status", "trace_id": trace_id,
                        "content": "该工具操作存在风险，正在等待用户确认...",
                    }
                    yield {
                        "type": "pending_confirmation", "trace_id": trace_id,
                        "tool": tool_name, "params": safe_params,
                        "tool_confirm_id": result.get("tool_confirm_id"),
                        "reason": result.get("reason"),
                        "risk_level": result.get("risk_level", "medium"),
                        "context": ctx.__dict__,
                    }
                    return

                frame: dict[str, Any] = {
                    "type": "tool_call", "trace_id": trace_id,
                    "tool": tool_name, "params": safe_params,
                    "tool_call_id": tool_call_id,
                    "ok": result.get("ok", False),
                }
                if result.get("ok"):
                    frame["result"] = result.get("result")
                else:
                    # 失败时优先使用 error，缺失则使用 reason（如 requires_confirm 场景）
                    frame["error"] = result.get("error") or result.get("reason") or "工具调用失败"
                yield frame

            # ── 5.5 知识库查询 ──
            ctx.knowledge_result = await self.diagnose_agent.search_knowledge(
                intent=intent or "unknown",
                observations=ctx.observations,
                user_input=user_input,
            )

            # ── 6. ReporterAgent ──
            report = await self._generate_report(
                intent_result, ctx.observations, user_input, ctx.knowledge_result,
            )
            ctx.final_response = report

            # ── 7. FixPlannerAgent → FixOptionStore → fix_options 帧 ──
            yield {"type": "status", "trace_id": trace_id, "content": "正在分析诊断结果，生成修复建议..."}
            if settings.LLM_ENABLED:
                fix_options = await self.fix_planner.plan_with_llm(
                    intent=intent,
                    observations=ctx.observations,
                    report=report,
                    target_service=target_service,
                )
            else:
                fix_options = self.fix_planner.plan(
                    intent=intent,
                    observations=ctx.observations,
                    report=report,
                    target_service=target_service,
                )
            if fix_options:
                # 保存失败会进入外层 except，产生 error + done
                self.fix_option_store.save_options(
                    session_id=session_id,
                    trace_id=trace_id,
                    options=fix_options,
                )

            for i in range(0, len(report), 500):
                yield {"type": "chunk", "trace_id": trace_id, "content": report[i:i + 500]}

            if fix_options:
                yield {
                    "type": "fix_options",
                    "trace_id": trace_id,
                    "options": [
                        opt.dict() for opt in fix_options
                    ],
                }
            title = await _generate_session_title(session_id, user_input, report)
            yield {"type": "done", "trace_id": trace_id, "session_id": session_id, "title": title, "final_response": ctx.final_response}
            await self._safe_audit(ctx, "chat_done")

        except Exception as e:
            logger.exception(f"[Orchestrator] 处理异常: {e}")
            ctx.final_response = "系统处理异常，请稍后重试"
            yield {"type": "error", "trace_id": trace_id, "message": "系统处理异常，请稍后重试"}
            yield {"type": "done", "trace_id": trace_id, "session_id": session_id, "final_response": ctx.final_response}
            await self._safe_audit(ctx, "error")

    # ── 内部辅助 ──────────────────────────────────────────────────────

    async def _generate_report(self, intent_result: dict, observations: list, user_input: str, knowledge_result: dict | None = None) -> str:
        """根据 LLM_ENABLED 选择报告生成路径"""
        if settings.LLM_ENABLED:
            return await self.reporter_agent.generate_with_llm(
                intent_result=intent_result, observations=observations, user_input=user_input,
                knowledge_result=knowledge_result,
            )
        return self.reporter_agent.generate(
            intent_result=intent_result, observations=observations, user_input=user_input,
            knowledge_result=knowledge_result,
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
