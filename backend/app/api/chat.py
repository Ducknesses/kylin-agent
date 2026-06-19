"""WebSocket 聊天接口

正式接口：/ws/chat（API 文档约定）
消息格式：{"type": "chat", "session_id": "...", "content": "..."}
"""
import json
import logging
import uuid
from typing import Any, Dict

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.audit.logger import log_chain
from app.core.security import risk_classify
from app.llm.deepseek import chat_with_llm

logger = logging.getLogger(__name__)
router = APIRouter()

# 简单内存连接管理（阶段1），后续可接入 Redis Pub/Sub
active_connections: Dict[str, WebSocket] = {}

# ── 正式接口：API 文档约定的前端 WebSocket 入口 ──────────────────


@router.websocket("/chat")
async def chat_ws(websocket: WebSocket):
    """
    WebSocket 聊天核心流程：
    1. 接收用户输入（JSON: type / session_id / content）
    2. 安全检测
    3. 高危 -> reject
    4. 中危 -> 返回需确认提示
    5. 低危 -> LLM 解析 -> 流式返回 chunk / done
    6. 全程记录审计日志
    """
    await websocket.accept()

    try:
        while True:
            raw = await websocket.receive_text()
            await _handle_message(websocket, raw)
    except WebSocketDisconnect:
        logger.info("[WebSocket] 会话断开")
    except Exception as e:
        logger.exception(f"[WebSocket] 会话异常: {e}")
        try:
            await _send(websocket, "error", message=f"服务端异常: {str(e)}")
        except Exception:
            pass


# ── 废弃接口：仅用于临时兼容旧调试脚本，后续删除 ──────────────────
# 前端已切换到 /ws/chat，此路由不再作为正式对接入口。


@router.websocket("/chat/{session_id}")
async def chat_ws_deprecated(websocket: WebSocket, session_id: str):
    """[deprecated] 旧版 WebSocket，session_id 从路径读取"""
    await websocket.accept()
    logger.info(f"[WebSocket:DEPRECATED] 旧路径连接 session={session_id}")

    try:
        while True:
            raw = await websocket.receive_text()
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                await _send(websocket, "error", message="消息格式非法，需为 JSON")
                continue
            # 旧路径没有 session_id 字段时，从路径参数补入
            msg.setdefault("session_id", session_id)
            # 转为字符串放回，复用 _handle_message
            await _handle_message(websocket, json.dumps(msg, ensure_ascii=False))
    except WebSocketDisconnect:
        logger.info(f"[WebSocket:DEPRECATED] 会话断开: {session_id}")
    except Exception as e:
        logger.exception(f"[WebSocket:DEPRECATED] 会话异常: {e}")
        try:
            await _send(websocket, "error", message=f"服务端异常: {str(e)}")
        except Exception:
            pass


# ── 内部处理函数 ─────────────────────────────────────────────────


async def _handle_message(websocket: WebSocket, raw: str) -> None:
    """处理单条 WebSocket 消息，抽取为独立函数以复用"""
    # 1. 解析 JSON
    try:
        msg = json.loads(raw)
    except json.JSONDecodeError:
        await _send(websocket, "error", message="消息格式非法，需为 JSON")
        return

    # API 文档要求 type 固定为 chat
    if msg.get("type") != "chat":
        await _send(websocket, "error", message="不支持的消息类型，请使用 type=chat")
        return

    session_id = msg.get("session_id", "").strip()
    user_input = msg.get("content", "").strip()

    if not session_id or not user_input:
        await _send(websocket, "error", message="缺少 session_id 或 content")
        return

    # 跟踪连接
    active_connections[session_id] = websocket
    trace_id = str(uuid.uuid4())[:16]

    # 2. 安全检测
    risk = risk_classify(user_input)

    if risk["action"] == "reject":
        await _send(
            websocket,
            "reject",
            reason=risk["reason"],
            risk_level=risk["level"],
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
        # 中危：告知前端需确认（阶段1不实现等待逻辑）
        await _send(
            websocket,
            "reject",
            reason=risk["reason"],
            risk_level=risk["level"],
            trace_id=trace_id,
        )
        await log_chain(
            trace_id=trace_id,
            user_input=user_input,
            risk_level=risk["level"],
            final_response=risk["reason"],
        )
        return

    # 3. 低危：进入 LLM 流式回复
    await _send(websocket, "chunk", content="正在思考...", trace_id=trace_id)

    messages = [{"role": "user", "content": user_input}]
    response_text = ""

    async for chunk in chat_with_llm(messages, stream=True):
        await _send(websocket, "chunk", content=chunk, trace_id=trace_id)
        response_text += chunk

    await _send(websocket, "done", content="", trace_id=trace_id)

    # 4. 记录审计
    await log_chain(
        trace_id=trace_id,
        user_input=user_input,
        risk_level=risk["level"],
        final_response=response_text,
    )


async def _send(
    ws: WebSocket,
    msg_type: str,
    content: str | None = None,
    message: str | None = None,
    reason: str | None = None,
    risk_level: str | None = None,
    trace_id: str | None = None,
) -> None:
    """统一发送 WebSocket 消息"""
    payload: dict[str, Any] = {"type": msg_type}

    # reject / error 类型使用固定字段名以匹配 API 文档
    if msg_type == "reject":
        if reason:
            payload["reason"] = reason
        if risk_level:
            payload["risk_level"] = risk_level
    elif msg_type == "error":
        if message:
            payload["message"] = message
    elif content is not None:
        payload["content"] = content

    if trace_id:
        payload["trace_id"] = trace_id

    await ws.send_json(payload)
