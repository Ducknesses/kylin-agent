"""WebSocket 聊天接口

正式接口：WS /ws/chat/{session_id}（最新前后端 API 统一规范 v1.0）
前端消息类型：chat / confirm / ping
后端消息类型：status / chunk / risk_alert / tool_call / error / done / pong

业务逻辑通过 Day5 Orchestrator.handle_chat 串起 IntentAgent → DiagnoseAgent →
AgentHarness → ReporterAgent → AuditService 全链路。
所有 Agent 均支持 LLM 增强路径（通过 LLM_ENABLED 配置开关）。
"""
import json
import logging
import uuid
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.audit.logger import log_chain
from app.dependencies import fix_option_store, fix_planner
from app.services.connection_manager import ConnectionManager
from app.services.orchestrator import Orchestrator
from app.services.safety_guard import SafetyGuard

logger = logging.getLogger(__name__)
router = APIRouter()

# 连接管理器、安全护栏（模块级单例）
manager = ConnectionManager()
safety_guard = SafetyGuard()
# 编排器（注入来自 dependencies.py 的共享 FixPlanner / FixOptionStore）
_orchestrator = Orchestrator(
    safety_guard=safety_guard,
    tool_registry=None,  # 使用 Orchestrator 默认构造
    mcp_client=None,     # 使用 Orchestrator 默认构造
    fix_planner=fix_planner,
    fix_option_store=fix_option_store,
)

# ── 正式接口：最新前后端 API 统一规范 v1.0 ────────────────────────


@router.websocket("/chat/{session_id}")
async def chat_ws(websocket: WebSocket, session_id: str):
    """
    WebSocket 聊天核心流程：
    1. 接收前端消息（type: chat / confirm / ping）
    2. ping → 立即 pong
    3. chat → 安全检测 → risk_alert / Orchestrator.handle_chat
    4. confirm → 处理中危确认
    5. 全程记录审计日志
    """
    await manager.connect(websocket, session_id)

    try:
        while True:
            raw = await websocket.receive_text()
            await _handle_message(websocket, session_id, raw)
    except WebSocketDisconnect:
        logger.info(f"[WebSocket] 会话断开: {session_id}")
    except Exception as e:
        logger.exception(f"[WebSocket] 会话异常: {e}")
        try:
            await _send(websocket, "error", message="服务端处理异常，请稍后重试")
        except Exception:
            pass
    finally:
        manager.disconnect(session_id)


# ── 内部处理函数 ─────────────────────────────────────────────────


async def _handle_message(websocket: WebSocket, session_id: str, raw: str) -> None:
    """按最新规范 v1.0 分发处理 WebSocket 消息

    协议层校验完成后，业务逻辑委托给 Orchestrator.handle_chat。
    """
    # 1. 解析 JSON
    try:
        msg = json.loads(raw)
    except json.JSONDecodeError:
        await _send(websocket, "error", message="消息格式非法，需为 JSON")
        return

    msg_type = msg.get("type", "")

    # ── ping：立即 pong ──
    if msg_type == "ping":
        await _send(websocket, "pong")
        return

    # ── confirm：校验 confirm_id 后才 pop ──
    if msg_type == "confirm":
        confirm_id = msg.get("confirm_id", "")
        decision = msg.get("decision", "")

        pending = manager.get_pending(session_id)
        if pending is None:
            await _send(websocket, "error", message="没有待确认的操作")
            return

        if not confirm_id:
            await _send(websocket, "error", message="缺少 confirm_id")
            return

        if confirm_id != pending.get("confirm_id"):
            await _send(websocket, "error", message="confirm_id 不匹配")
            return

        # confirm 协议中 decision=reject 表示用户取消中危操作；
        # 后端不发送旧版 type=reject，只返回 status + done
        if decision == "reject":
            manager.pop_pending(session_id)
            await _send(websocket, "status", content="已取消该风险操作。", trace_id=pending["trace_id"])
            await _send(websocket, "done", trace_id=pending["trace_id"])
            return

        if decision == "approve":
            popped = manager.pop_pending(session_id)
            assert popped is not None
            user_input = popped.get("user_input", "")
            # 确认后走 Day5 Agent 主流程（confirmed=True，trace_id 保持连续性）
            async for frame in _orchestrator.handle_chat(
                session_id=session_id, user_input=user_input, role="viewer", confirmed=True,
                trace_id=popped.get("trace_id"),
            ):
                await websocket.send_json(frame)
            return

        await _send(websocket, "error", message=f"未知的 confirm 决策: {decision}")
        return

    # ── chat：核心对话流程 ──
    if msg_type != "chat":
        await _send(websocket, "error", message=f"不支持的消息类型: {msg_type}，可用类型: chat / confirm / ping")
        return

    content = msg.get("content", "")
    if not content or not isinstance(content, str) or not content.strip():
        await _send(websocket, "error", message="输入不能为空或格式错误", trace_id=str(uuid.uuid4())[:16])
        return

    user_input = content.strip()
    trace_id = str(uuid.uuid4())[:16]

    # SafetyGuard 检查（高危/中危由 chat 层处理，低危委托 Orchestrator）
    safety = safety_guard.analyze_user_input(user_input)

    if not safety["allowed"]:
        await _send(websocket, "risk_alert", level=safety["risk_level"], reason=safety["reason"],
                    original_input=user_input, trace_id=trace_id)
        await log_chain(trace_id=trace_id, user_input=user_input, risk_level=safety["risk_level"],
                        final_response=safety["reason"])
        return

    if safety["requires_confirm"]:
        confirm_id = f"cfm_{str(uuid.uuid4())[:8]}"
        manager.set_pending(session_id, {"user_input": user_input, "trace_id": trace_id,
                                          "risk_level": safety["risk_level"], "confirm_id": confirm_id})
        await _send(websocket, "risk_alert", level=safety["risk_level"], reason=safety["reason"],
                    original_input=user_input, confirm_id=confirm_id, trace_id=trace_id)
        return

    # 低危：Day5 Agent 主流程（Orchestrator.handle_chat）
    async for frame in _orchestrator.handle_chat(
        session_id=session_id, user_input=user_input, role="viewer", trace_id=trace_id,
    ):
        await websocket.send_json(frame)


# ── 旧风险路径已删除 ─────────────────────────────────────────────────
# 原有旧版编排函数（直接调用 LLM Router + MCP Executor，绕过安全层）
# 已在 LLM-Agent 大修复中删除，所有 chat 消息统一走 Orchestrator.handle_chat。


async def _send(
    ws: WebSocket,
    msg_type: str,
    content: str | None = None,
    message: str | None = None,
    reason: str | None = None,
    level: str | None = None,
    original_input: str | None = None,
    confirm_id: str | None = None,
    trace_id: str | None = None,
    tool: str | None = None,
    tool_call_id: str | None = None,
    params: dict[str, Any] | None = None,
    result: Any = None,
) -> None:
    """统一发送 WebSocket 消息 —— 对齐最新规范 v1.0 所有后端消息类型"""
    payload: dict[str, Any] = {"type": msg_type}

    if msg_type == "risk_alert":
        if level is not None:
            payload["level"] = level
        if reason is not None:
            payload["reason"] = reason
        if original_input is not None:
            payload["original_input"] = original_input
        if confirm_id is not None:
            payload["confirm_id"] = confirm_id
        if trace_id is not None:
            payload["trace_id"] = trace_id

    elif msg_type in ("status", "chunk"):
        if content is not None:
            payload["content"] = content
        if trace_id is not None:
            payload["trace_id"] = trace_id

    elif msg_type == "tool_call":
        if tool is not None:
            payload["tool"] = tool
        if tool_call_id is not None:
            payload["tool_call_id"] = tool_call_id
        if params is not None:
            payload["params"] = params
        if result is not None:
            payload["result"] = result
        if trace_id is not None:
            payload["trace_id"] = trace_id

    elif msg_type == "done":
        if trace_id is not None:
            payload["trace_id"] = trace_id

    elif msg_type == "error":
        if message is not None:
            payload["message"] = message
        if trace_id is not None:
            payload["trace_id"] = trace_id

    elif msg_type == "pong":
        pass  # 只发 {"type":"pong"}，无额外字段

    await ws.send_json(payload)
