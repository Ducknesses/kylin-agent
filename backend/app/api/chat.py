"""WebSocket 聊天接口 /ws/chat/{session_id}"""
import json
import logging
import uuid
from typing import Dict, List

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.audit.logger import log_chain
from app.core.security import risk_classify
from app.llm.deepseek import chat_with_llm, analyze_root_cause
from app.llm.router import route_request
from app.mcp.executor import Executor

logger = logging.getLogger(__name__)
router = APIRouter()

# 简单内存连接管理（阶段1），后续可接入 Redis Pub/Sub
active_connections: Dict[str, WebSocket] = {}

# 会话级状态：对话历史、待确认的中危操作
class _SessionState:
    def __init__(self):
        self.history: List[Dict[str, str]] = []
        self.pending_confirms: Dict[str, Dict] = {}


session_states: Dict[str, _SessionState] = {}

executor = Executor()


@router.websocket("/chat/{session_id}")
async def chat_ws(websocket: WebSocket, session_id: str):
    """
    WebSocket 聊天核心流程（阶段 2）：
    1. 接收用户输入 / 确认消息
    2. 安全检测
    3. 高危 -> 返回风险告警
    4. 中危 -> 生成 confirm_id，等待前端确认
    5. 低危 -> 意图路由 -> MCP 工具调用 -> 流式总结返回
    6. 全程记录审计日志
    """
    await websocket.accept()
    active_connections[session_id] = websocket
    if session_id not in session_states:
        session_states[session_id] = _SessionState()
    state = session_states[session_id]
    logger.info(f"[WebSocket] 会话连接: {session_id}")

    try:
        while True:
            # 1. 接收消息
            raw = await websocket.receive_text()
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                await _send_json(websocket, {"type": "error", "message": "消息格式非法，需为 JSON"})
                continue

            msg_type = msg.get("type", "chat")
            trace_id = str(uuid.uuid4())[:16]

            # 处理中危二次确认
            if msg_type == "confirm":
                confirm_id = msg.get("confirm_id", "")
                pending = state.pending_confirms.pop(confirm_id, None)
                if not pending:
                    await _send_json(
                        websocket,
                        {"type": "error", "message": "确认 ID 无效或已过期", "trace_id": trace_id},
                    )
                    continue

                await _execute_allowed_request(
                    websocket,
                    session_id,
                    state,
                    pending["user_input"],
                    trace_id,
                    confirmed=True,
                )
                continue

            # 普通聊天消息
            user_input = msg.get("content", "").strip()
            if not user_input:
                await _send_json(websocket, {"type": "error", "message": "输入不能为空"})
                continue

            # 2. 安全检测
            risk = risk_classify(user_input)

            if risk["action"] == "reject":
                await _send_json(
                    websocket,
                    {
                        "type": "risk_alert",
                        "level": "high",
                        "reason": f"【安全拦截】{risk['reason']}，操作已被拒绝。",
                        "original_input": user_input,
                        "trace_id": trace_id,
                    },
                )
                await log_chain(
                    trace_id=trace_id,
                    user_input=user_input,
                    risk_level=risk["level"],
                    final_response=risk["reason"],
                )
                continue

            if risk["action"] == "confirm":
                confirm_id = str(uuid.uuid4())[:16]
                state.pending_confirms[confirm_id] = {
                    "user_input": user_input,
                    "risk": risk,
                    "trace_id": trace_id,
                }
                await _send_json(
                    websocket,
                    {
                        "type": "risk_alert",
                        "level": "medium",
                        "reason": f"【风险确认】{risk['reason']}，请确认是否继续执行？",
                        "original_input": user_input,
                        "confirm_id": confirm_id,
                        "trace_id": trace_id,
                    },
                )
                await log_chain(
                    trace_id=trace_id,
                    user_input=user_input,
                    risk_level=risk["level"],
                    final_response=risk["reason"],
                )
                continue

            # 3. 低危：进入 意图路由 -> MCP -> LLM 总结 流程
            await _execute_allowed_request(
                websocket, session_id, state, user_input, trace_id, confirmed=False
            )

    except WebSocketDisconnect:
        logger.info(f"[WebSocket] 会话断开: {session_id}")
    except Exception as e:
        logger.exception(f"[WebSocket] 会话异常: {e}")
        try:
            await _send_json(websocket, {"type": "error", "message": f"服务端异常: {str(e)}"})
        except Exception:
            pass
    finally:
        active_connections.pop(session_id, None)


async def _execute_allowed_request(
    websocket: WebSocket,
    session_id: str,
    state: _SessionState,
    user_input: str,
    trace_id: str,
    confirmed: bool,
):
    """执行已通过安全检测的请求：意图路由 -> MCP -> LLM 总结"""
    await _send_json(
        websocket,
        {"type": "status", "content": "正在分析意图...", "trace_id": trace_id},
    )

    # 记录用户输入到历史
    state.history.append({"role": "user", "content": user_input})

    # 意图路由
    route = await route_request(user_input, context=state.history)
    action = route.get("action", "direct_reply")
    data = route.get("data", {})

    mcp_tool = None
    command = None
    raw_output = None
    final_response = ""

    if action == "tool_call":
        tool_name = data.get("tool", "cmd_exec")
        arguments = data.get("args", {})
        mcp_tool = tool_name
        if tool_name == "cmd_exec" and "command" in arguments:
            command = arguments["command"]

        await _send_json(
            websocket,
            {"type": "status", "content": f"正在调用工具 {tool_name}...", "trace_id": trace_id},
        )

        exec_result = await executor.execute(tool_name, arguments, user_id=session_id)

        # 发送工具调用卡片给前端
        await _send_json(
            websocket,
            {
                "type": "tool_call",
                "tool": tool_name,
                "tool_call_id": trace_id,
                "params": arguments,
                "result": exec_result,
                "trace_id": trace_id,
            },
        )

        if exec_result.get("success"):
            raw_output = json.dumps(exec_result.get("result", {}), ensure_ascii=False)
            # 使用 LLM 对工具结果做自然语言总结
            summary_prompt = (
                f"用户请求：{user_input}\n"
                f"工具 {tool_name} 返回结果：{raw_output}\n"
                f"请用简洁中文总结上述结果。"
            )
            summary_messages = [{"role": "user", "content": summary_prompt}]
            async for chunk in chat_with_llm(summary_messages, stream=True):
                await _send_json(websocket, {"type": "chunk", "content": chunk, "trace_id": trace_id})
                final_response += chunk
        else:
            error_msg = exec_result.get("error", "工具调用失败")
            await _send_json(
                websocket,
                {"type": "error", "message": f"工具调用失败: {error_msg}", "trace_id": trace_id},
            )
            final_response = error_msg

    elif action == "root_cause":
        await _send_json(
            websocket,
            {"type": "status", "content": "正在收集日志与指标...", "trace_id": trace_id},
        )
        # 阶段2简化：直接读取系统指标作为输入，后续可扩展读取日志
        metrics_result = await executor.execute("sys_info", {"metric": "all"}, user_id=session_id)
        metrics_json = json.dumps(metrics_result.get("result", {}), ensure_ascii=False)
        rc = await analyze_root_cause(logs="", metrics=metrics_json)
        final_response = json.dumps(rc, ensure_ascii=False, indent=2)
        for chunk in [final_response[i : i + 16] for i in range(0, len(final_response), 16)]:
            await _send_json(websocket, {"type": "chunk", "content": chunk, "trace_id": trace_id})

    else:
        # direct_reply：直接走 LLM 流式回复
        async for chunk in chat_with_llm(state.history, stream=True):
            await _send_json(websocket, {"type": "chunk", "content": chunk, "trace_id": trace_id})
            final_response += chunk

    await _send_json(websocket, {"type": "done", "trace_id": trace_id})

    # 记录 assistant 回复到历史，维护多轮上下文
    state.history.append({"role": "assistant", "content": final_response})
    # 防止历史过长
    if len(state.history) > 20:
        state.history = state.history[-20:]

    # 记录审计
    await log_chain(
        trace_id=trace_id,
        user_input=user_input,
        risk_level="low" if not confirmed else "medium-confirmed",
        intent=action,
        mcp_tool=mcp_tool,
        command=command,
        raw_output=raw_output,
        final_response=final_response,
    )


async def _send_json(ws: WebSocket, payload: Dict):
    """统一发送 JSON 消息"""
    await ws.send_json(payload)
