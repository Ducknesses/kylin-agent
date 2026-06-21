"""WebSocket 聊天接口

正式接口：WS /ws/chat/{session_id}（最新前后端 API 统一规范 v1.0）
前端消息类型：chat / confirm / ping
后端消息类型：status / risk_alert / chunk / tool_call / done / error / pong
"""
import json
import logging
import uuid
from typing import Any, Dict, Optional

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.audit.logger import log_chain
from app.core.security import risk_classify
from app.llm.deepseek import chat_with_llm
from app.mcp.executor import Executor
from config import settings

logger = logging.getLogger(__name__)
router = APIRouter()

# 简单内存连接管理（阶段1），后续可接入 Redis Pub/Sub
active_connections: Dict[str, WebSocket] = {}

# 待确认的中危操作（session_id → pending_request），内存存储
pending_confirm: Dict[str, dict] = {}

# ── 正式接口：最新前后端 API 统一规范 v1.0 ────────────────────────


@router.websocket("/chat/{session_id}")
async def chat_ws(websocket: WebSocket, session_id: str, token: str = None):
    """
    WebSocket 聊天核心流程：
    0. Token 认证（若配置了 API_TOKEN）
    1. 接收前端消息（type: chat / confirm / ping）
    2. ping → 立即 pong，不写审计不调 LLM
    3. 安全检测
    4. 高危 -> 返回风险告警
    5. 中危 -> 返回需确认
    6. confirm → 处理中危操作确认
    7. chat → 低危/已确认 → LLM 解析 → MCP 执行 → 流式返回
    8. 全程记录审计日志
    """
    # 0. Token 认证
    if settings.API_TOKEN:
        if not token or token != settings.API_TOKEN:
            logger.warning(f"[WebSocket] Token 认证失败: session={session_id}, token={'present' if token else 'missing'}")
            await websocket.close(code=4001, reason="auth_failed")
            return

    await websocket.accept()
    active_connections[session_id] = websocket
    logger.info(f"[WebSocket] 正式会话建立: {session_id}")

    try:
        while True:
            raw = await websocket.receive_text()
            await _handle_message(websocket, session_id, raw)
    except WebSocketDisconnect:
        logger.info(f"[WebSocket] 会话断开: {session_id}")
        active_connections.pop(session_id, None)
        pending_confirm.pop(session_id, None)
    except Exception as e:
        logger.exception(f"[WebSocket] 会话异常: {e}")
        try:
            await _send(websocket, "error", message=f"服务端异常: {str(e)}")
        except Exception:
            pass


# ── Legacy 接口：兼容旧版 /ws/chat，后续删除 ──────────────────────
# 内部复用同一 _handle_message，不维护独立业务逻辑


@router.websocket("/chat")
async def chat_ws_legacy(websocket: WebSocket):
    """[legacy] 旧版 WebSocket 入口，内部复用正式处理函数"""
    await websocket.accept()
    session_id = f"legacy-{str(uuid.uuid4())[:8]}"
    active_connections[session_id] = websocket
    logger.info(f"[WebSocket:LEGACY] 旧路径连接 session={session_id}")

    try:
        while True:
            raw = await websocket.receive_text()
            await _handle_message(websocket, session_id, raw)
    except WebSocketDisconnect:
        logger.info(f"[WebSocket:LEGACY] 会话断开: {session_id}")
        active_connections.pop(session_id, None)
    except Exception as e:
        logger.exception(f"[WebSocket:LEGACY] 会话异常: {e}")
        try:
            await _send(websocket, "error", message=f"服务端异常: {str(e)}")
        except Exception:
            pass


# ── 内部处理函数 ─────────────────────────────────────────────────


async def _handle_message(websocket: WebSocket, session_id: str, raw: str) -> None:
    """按最新规范 v1.0 分发处理 WebSocket 消息"""
    # 1. 解析 JSON
    try:
        msg = json.loads(raw)
    except json.JSONDecodeError:
        await _send(websocket, "error", message="消息格式非法，需为 JSON")
        return

    msg_type = msg.get("type", "")

    # ── ping：立即 pong，不进入任何业务逻辑 ──
    if msg_type == "ping":
        await _send(websocket, "pong")
        return

    # ── confirm：处理中危确认，校验 confirm_id 后才 pop ──
    if msg_type == "confirm":
        confirm_id = msg.get("confirm_id", "")
        decision = msg.get("decision", "")

        # 先 get 不 pop，校验失败时保留 pending
        pending = pending_confirm.get(session_id)
        if not pending:
            await _send(websocket, "error", message="没有待确认的操作")
            return

        if not confirm_id:
            await _send(websocket, "error", message="缺少 confirm_id")
            return

        if confirm_id != pending.get("confirm_id"):
            await _send(websocket, "error", message="confirm_id 不匹配")
            return

        if decision == "reject":
            pending_confirm.pop(session_id, None)
            await _send(websocket, "status", content="操作已取消")
            return

        if decision == "approve":
            pending_confirm.pop(session_id, None)
            user_input = pending.get("user_input", "")
            trace_id = pending.get("trace_id", str(uuid.uuid4())[:16])
            risk_level = pending.get("risk_level", "medium")
            await _send(websocket, "status", content="正在分析意图...", trace_id=trace_id)
            await _process_low_risk(websocket, session_id, user_input, trace_id, risk_level)
            return

        # 非法 decision —— 不 pop，保留 pending 供前端重试
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

    # 2. 安全检测
    risk = risk_classify(user_input)

    if risk["action"] == "reject":
        # 高危：直接返回 risk_alert
        await _send(
            websocket,
            "risk_alert",
            level=risk["level"],
            reason=risk["reason"],
            original_input=user_input,
            trace_id=trace_id,
        )
        await log_chain(
            trace_id=trace_id,
            user_input=user_input,
            risk_level=risk["level"],
            final_response=risk["reason"],
        )
        return

    if risk["action"] == "confirm":
        # 中危：返回 risk_alert + confirm_id，挂起等待前端确认
        confirm_id = f"cfm_{str(uuid.uuid4())[:8]}"
        pending_confirm[session_id] = {
            "user_input": user_input,
            "trace_id": trace_id,
            "risk_level": risk["level"],
            "confirm_id": confirm_id,
        }
        await _send(
            websocket,
            "risk_alert",
            level=risk["level"],
            reason=risk["reason"],
            original_input=user_input,
            confirm_id=confirm_id,
            trace_id=trace_id,
        )
        return

    # 3. 低危：进入 LLM 流式回复
    await _process_low_risk(websocket, session_id, user_input, trace_id, risk["level"])


async def _process_low_risk(
    websocket: WebSocket, session_id: str, user_input: str, trace_id: str, risk_level: str = "low"
) -> None:
    """低危/已确认中危输入：调用 LLM 并流式返回"""
    await _send(websocket, "status", content="正在分析意图...", trace_id=trace_id)

    messages = [{"role": "user", "content": user_input}]
    response_text = ""

    async for chunk in chat_with_llm(messages, stream=True):
        await _send(websocket, "chunk", content=chunk, trace_id=trace_id)
        response_text += chunk

    await _send(websocket, "done", trace_id=trace_id)

    # 记录审计
    await log_chain(
        trace_id=trace_id,
        user_input=user_input,
        risk_level=risk_level,
        final_response=response_text,
    )


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
    result: str | None = None,
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