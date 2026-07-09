"""Mock 业务编排器 —— Day2 阶段不接真实 LLM 和 MCP Server

所有工具调用结果均为 Mock 数据，仅用于前端联调和协议验证。
"""
import logging
import uuid
from typing import Any, AsyncIterator

logger = logging.getLogger(__name__)

# Mock 结果数据
_MOCK_CPU_RESULT = {
    "cpu_percent": 23.5,
    "load_avg": [0.52, 0.31, 0.18],
    "cores": 4,
}

_MOCK_NGINX_STATUS_RESULT = {
    "service": "nginx",
    "status": "active",
    "sub_state": "running",
    "uptime": "3 days 12:34:56",
}

_MOCK_NGINX_RESTART_RESULT = {
    "ok": True,
    "message": "mock restart accepted",
}

# 意图识别关键词
_CPU_KEYWORDS = ["CPU", "cpu", "处理器", "使用率"]
_NGINX_STATUS_KEYWORDS = ["nginx", "nginx 状态", "nginx status", "查询 nginx"]
_RESTART_NGINX_KEYWORDS = ["重启 nginx", "restart nginx"]


def _match_any(text: str, keywords: list[str]) -> bool:
    """检查文本是否包含任一关键词"""
    return any(k in text for k in keywords)


def _create_default_mcp_client() -> Any:
    """创建默认 MCP 客户端实例（模块级函数，避免 Orchestrator 类体内出现导入字符串）"""
    from app.mcp.client import MCPClient
    return MCPClient()


async def mock_orchestrate(user_input: str) -> AsyncIterator[dict[str, Any]]:
    """Mock 业务编排，根据用户输入返回流式消息序列

    消息类型: status / tool_call / chunk / done

    识别规则:
      - CPU 相关 → mock sys_info 工具调用
      - 重启 nginx → mock service_mgr restart 工具调用（优先判断，避免被普通 nginx 查询误匹配）
      - nginx 状态 → mock service_mgr status 工具调用
      - 其他 → 通用 mock 回复
    """
    trace_id = str(uuid.uuid4())[:16]

    # ── CPU 查询 ──
    if _match_any(user_input, _CPU_KEYWORDS):
        yield {
            "type": "status",
            "content": "正在查询 CPU 使用率...",
            "trace_id": trace_id,
        }
        yield {
            "type": "tool_call",
            "tool": "sys_info",
            "tool_call_id": f"tc_{str(uuid.uuid4())[:8]}",
            "params": {"metric": "cpu"},
            "result": _MOCK_CPU_RESULT,
            "trace_id": trace_id,
        }
        yield {
            "type": "chunk",
            "content": (
                f"当前 CPU 使用率约为 {_MOCK_CPU_RESULT['cpu_percent']}%，"
                f"系统负载为 {', '.join(str(v) for v in _MOCK_CPU_RESULT['load_avg'])}。"
            ),
            "trace_id": trace_id,
        }
        yield {"type": "done", "trace_id": trace_id}
        return

    # ── 重启 nginx（中危确认后由 chat.py 调用） ──
    # 必须放在 nginx 状态查询之前，避免 "重启 nginx" 被 "nginx" 关键词误匹配为状态查询
    if _match_any(user_input, _RESTART_NGINX_KEYWORDS):
        yield {
            "type": "status",
            "content": "已收到确认，正在模拟重启 nginx...",
            "trace_id": trace_id,
        }
        yield {
            "type": "tool_call",
            "tool": "service_mgr",
            "tool_call_id": f"tc_{str(uuid.uuid4())[:8]}",
            "params": {"action": "restart", "service": "nginx"},
            "result": _MOCK_NGINX_RESTART_RESULT,
            "trace_id": trace_id,
        }
        yield {
            "type": "chunk",
            "content": "已模拟提交 nginx 重启操作。当前仍为 Mock 流程，未调用真实 MCP。",
            "trace_id": trace_id,
        }
        yield {"type": "done", "trace_id": trace_id}
        return

    # ── nginx 状态查询 ──
    if _match_any(user_input, _NGINX_STATUS_KEYWORDS):
        yield {
            "type": "status",
            "content": "正在查询 nginx 服务状态...",
            "trace_id": trace_id,
        }
        yield {
            "type": "tool_call",
            "tool": "service_mgr",
            "tool_call_id": f"tc_{str(uuid.uuid4())[:8]}",
            "params": {"action": "status", "service": "nginx"},
            "result": _MOCK_NGINX_STATUS_RESULT,
            "trace_id": trace_id,
        }
        status_info = _MOCK_NGINX_STATUS_RESULT
        yield {
            "type": "chunk",
            "content": (
                f"nginx 服务当前为 {status_info['status']} ({status_info['sub_state']})，"
                f"已运行约 {status_info['uptime']}。"
            ),
            "trace_id": trace_id,
        }
        yield {"type": "done", "trace_id": trace_id}
        return

    # ── 通用 mock 回复 ──
    yield {
        "type": "status",
        "content": "正在分析意图...",
        "trace_id": trace_id,
    }
    yield {
        "type": "chunk",
        "content": f"已收到您的输入：「{user_input}」。当前为 Mock 模式，暂不支持真实运维操作。",
        "trace_id": trace_id,
    }
    yield {"type": "done", "trace_id": trace_id}


# ── Day5 Orchestrator（Agent 主流程闭环） ────────────────────────────

class Orchestrator:
    """Agent 主流程编排器 —— 串起 IntentAgent → DiagnoseAgent → AgentHarness → ReporterAgent → AuditService

    handle_chat 是一个 async generator，按 WebSocket v1.1 规范产出事件帧 dict。
    所有工具调用必须通过 AgentHarness.run_tool，不直接调用执行器客户端。
    """

    def __init__(
        self,
        safety_guard: Any,
        tool_registry: Any,
        mcp_client: Any,
        intent_agent: Any = None,
        diagnose_agent: Any = None,
        agent_harness: Any = None,
        reporter_agent: Any = None,
        audit_service: Any = None,
    ) -> None:
        self.safety_guard = safety_guard
        self.tool_registry = tool_registry
        self.mcp_client = mcp_client

        # 延迟导入，避免循环依赖
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
            # 默认依赖：如果外部未传入则构造默认实例（WebSocket 路径需要）
            _tr = tool_registry if tool_registry is not None else ToolRegistry()
            _mc = mcp_client
            if _mc is None:
                _mc = _create_default_mcp_client()
            agent_harness = AgentHarness(
                safety_guard=safety_guard,
                tool_registry=_tr,
                mcp_client=_mc,
                audit_service=audit_service,
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
        self, session_id: str, user_input: str, role: str = "viewer", confirmed: bool = False
    ) -> AsyncIterator[dict[str, Any]]:
        """处理一次完整的用户对话 —— async generator

        参数:
            confirmed: True 表示已通过 WebSocket 二次确认，跳过 SafetyGuard 用户输入检查
                       但高危拦截仍会生效（SafetyGuard 内部逐工具裁决）。
        """
        from app.services.agent_context import AgentContext

        # ── 1. 创建上下文 ──
        ctx = AgentContext(session_id=session_id, user_input=user_input, role=role)
        trace_id = ctx.trace_id

        try:
            # ── 2. 用户输入安全检查 ──
            # confirmed=True 仅跳过中危二次确认，高危始终拦截
            safety = self.safety_guard.analyze_user_input(user_input)
            ctx.risk_level = safety.get("risk_level", "low")  # 始终写入，避免 AuditService NOT NULL

            # 高危：无论 confirmed 与否，始终拒绝
            if not safety.get("allowed", False):
                ctx.risk_level = safety.get("risk_level", "high")
                ctx.final_response = safety.get("reason", "安全策略拒绝")

                yield {
                    "type": "risk_alert",
                    "trace_id": trace_id,
                    "level": ctx.risk_level,
                    "message": safety.get("reason", ""),
                }
                yield {
                    "type": "done",
                    "trace_id": trace_id,
                    "session_id": session_id,
                    "final_response": ctx.final_response,
                }
                await self._safe_audit(ctx, "blocked")
                return

            # 中危 + 未确认：要求二次确认
            if safety.get("requires_confirm") and not confirmed:
                ctx.risk_level = safety.get("risk_level", "medium")
                ctx.final_response = "等待用户确认"
                yield {
                    "type": "risk_alert",
                    "trace_id": trace_id,
                    "level": "medium",
                    "message": safety.get("reason", ""),
                }
                yield {
                    "type": "done",
                    "trace_id": trace_id,
                    "session_id": session_id,
                    "final_response": ctx.final_response,
                }
                await self._safe_audit(ctx, "confirm_required")
                return

            # ── 3. IntentAgent 识别意图 ──
            yield {
                "type": "status",
                "trace_id": trace_id,
                "message": "正在分析您的请求...",
            }

            intent_result = self.intent_agent.detect(user_input)
            ctx.intent = intent_result.get("intent")

            # ── 4. DiagnoseAgent 生成工具计划 ──
            plan_result = self.diagnose_agent.plan(intent_result)
            plans = plan_result.get("plans", [])

            if not plans:
                # 无工具计划：直接生成报告
                report = self.reporter_agent.generate(
                    intent_result, ctx.observations, user_input
                )
                ctx.final_response = report

                # 分块输出报告（每 500 字符一块）
                for i in range(0, len(report), 500):
                    yield {
                        "type": "chunk",
                        "trace_id": trace_id,
                        "content": report[i:i + 500],
                    }
                yield {
                    "type": "done",
                    "trace_id": trace_id,
                    "session_id": session_id,
                    "final_response": ctx.final_response,
                }
                await self._safe_audit(ctx, "chat_done")
                return

            # ── 5. 逐个执行工具计划 ──
            yield {
                "type": "status",
                "trace_id": trace_id,
                "message": f"正在执行 {len(plans)} 个诊断步骤...",
            }

            for plan_item in plans:
                tool_name = plan_item["tool"]
                params = plan_item["params"]

                # 通过 AgentHarness 统一入口执行
                result = await self.agent_harness.run_tool(ctx, tool_name, params)

                yield {
                    "type": "tool_call",
                    "trace_id": trace_id,
                    "tool": tool_name,
                    "params": params,
                    "ok": result.get("ok", False),
                }

            # ── 6. ReporterAgent 生成最终报告 ──
            report = self.reporter_agent.generate(
                intent_result, ctx.observations, user_input
            )
            ctx.final_response = report

            for i in range(0, len(report), 500):
                yield {
                    "type": "chunk",
                    "trace_id": trace_id,
                    "content": report[i:i + 500],
                }

            yield {
                "type": "done",
                "trace_id": trace_id,
                "session_id": session_id,
                "final_response": ctx.final_response,
            }
            await self._safe_audit(ctx, "chat_done")

        except Exception as e:
            logger.exception(f"[Orchestrator] 处理异常: {e}")
            ctx.final_response = "系统处理异常，请稍后重试"
            yield {
                "type": "error",
                "trace_id": trace_id,
                "message": "系统处理异常，请稍后重试",
            }
            yield {
                "type": "done",
                "trace_id": trace_id,
                "session_id": session_id,
                "final_response": ctx.final_response,
            }
            await self._safe_audit(ctx, "error")

    # ── 内部辅助 ──────────────────────────────────────────────────────

    async def _safe_audit(self, ctx: Any, event_type: str) -> None:
        """安全记审计 —— 失败不抛异常，不影响主流程"""
        try:
            await self.audit_service.save_context(ctx, event_type=event_type)
        except Exception:
            logger.warning("[Orchestrator] 审计写入失败（已忽略）", exc_info=True)