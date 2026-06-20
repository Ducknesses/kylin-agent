"""WebSocket 聊天接口 /ws/chat/{session_id}"""
import json
import logging
import uuid
from typing import Dict, Optional

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


@router.websocket("/chat/{session_id}")
async def chat_ws(websocket: WebSocket, session_id: str, token: str = None):
    """
    WebSocket 聊天核心流程：
    0. Token 认证（若配置了 API_TOKEN）
    1. 接收用户输入
    2. 安全检测
    3. 高危 -> 返回风险告警
    4. 中危 -> 返回需确认
    5. 低危 -> LLM 解析 -> MCP 执行 -> 流式返回
    6. 全程记录审计日志
    """
    # 0. Token 认证
    if settings.API_TOKEN:
        if not token or token != settings.API_TOKEN:
            logger.warning(f"[WebSocket] Token 认证失败: session={session_id}, token={'present' if token else 'missing'}")
            await websocket.close(code=4001, reason="auth_failed")
            return

    await websocket.accept()
    active_connections[session_id] = websocket
    logger.info(f"[WebSocket] 会话连接: {session_id}")

    try:
        while True:
            # 1. 接收消息
            raw = await websocket.receive_text()
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                await _send_msg(websocket, "error", "消息格式非法，需为 JSON")
                continue

            user_input = msg.get("content", "").strip()
            if not user_input:
                await _send_msg(websocket, "error", "输入不能为空")
                continue

            trace_id = str(uuid.uuid4())[:16]

            # 2. 安全检测
            risk = risk_classify(user_input)

            if risk["action"] == "reject":
                # 高危：直接拒绝
                await _send_msg(
                    websocket,
                    "risk_alert",
                    f"【安全拦截】{risk['reason']}，操作已被拒绝。",
                    trace_id=trace_id,
                )
                await log_chain(
                    trace_id=trace_id,
                    user_input=user_input,
                    risk_level=risk["level"],
                    final_response=risk["reason"],
                )
                continue

            if risk["action"] == "confirm":
                # 中危：等待前端确认（阶段1简单返回提示）
                await _send_msg(
                    websocket,
                    "risk_alert",
                    f"【风险确认】{risk['reason']}，请确认是否继续执行？",
                    trace_id=trace_id,
                )
                await log_chain(
                    trace_id=trace_id,
                    user_input=user_input,
                    risk_level=risk["level"],
                    final_response=risk["reason"],
                )
                # 阶段1：不实现等待 confirm 的逻辑，前端可再次发送带 confirm 标志的消息
                continue

            # 3. 低危：进入 LLM -> MCP 流程
            await _send_msg(websocket, "status", "正在思考...", trace_id=trace_id)

            # 阶段1：简化流程，直接走 LLM 流式回复（不实际调用 MCP，阶段2接入）
            messages = [{"role": "user", "content": user_input}]
            response_text = ""

            async for chunk in chat_with_llm(messages, stream=True):
                await _send_msg(websocket, "chunk", chunk, trace_id=trace_id)
                response_text += chunk

            await _send_msg(websocket, "done", "", trace_id=trace_id)

            # 4. 记录审计
            await log_chain(
                trace_id=trace_id,
                user_input=user_input,
                risk_level=risk["level"],
                final_response=response_text,
            )

    except WebSocketDisconnect:
        logger.info(f"[WebSocket] 会话断开: {session_id}")
    except Exception as e:
        logger.exception(f"[WebSocket] 会话异常: {e}")
        try:
            await _send_msg(websocket, "error", f"服务端异常: {str(e)}")
        except Exception:
            pass
    finally:
        active_connections.pop(session_id, None)


async def _send_msg(
    ws: WebSocket,
    msg_type: str,
    content: str,
    trace_id: str = None,
):
    """统一发送 WebSocket 消息"""
    payload = {
        "type": msg_type,
        "content": content,
    }
    if trace_id:
        payload["trace_id"] = trace_id
    await ws.send_json(payload)
