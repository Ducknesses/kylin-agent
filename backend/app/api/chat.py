"""WebSocket 聊天接口

正式接口：WS /ws/chat/{session_id}（最新前后端 API 统一规范 v1.0）
前端消息类型：chat / confirm / tool_confirm / ping
后端消息类型：status / chunk / risk_alert / tool_call / tool_rejected / pending_confirmation / error / done / pong

业务逻辑通过 Day5 Orchestrator.handle_chat 串起 IntentAgent → DiagnoseAgent →
AgentHarness → ReporterAgent → AuditService 全链路。
所有 Agent 均支持 LLM 增强路径（通过 LLM_ENABLED 配置开关）。
"""
import asyncio
import json
import logging
import uuid
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.audit.logger import log_chain
from app.core.auth import AuthContext, AuthLevel
from app.core.security import TokenStore
from app.dependencies import (fix_option_store, fix_planner, tool_registry, safety_guard, mcp_client, agent_harness, audit_service, message_repository, knowledge_service, confirmation_store)
from app.services.connection_manager import ConnectionManager
from app.services.orchestrator import Orchestrator

logger = logging.getLogger(__name__)
router = APIRouter()

# 连接管理器
manager = ConnectionManager()
# 编排器（注入来自 dependencies.py 的共享依赖）
_orchestrator = Orchestrator(
    safety_guard=safety_guard,
    tool_registry=tool_registry,
    mcp_client=mcp_client,
    agent_harness=agent_harness,
    audit_service=audit_service,
    fix_planner=fix_planner,
    fix_option_store=fix_option_store,
    knowledge_service=knowledge_service,
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

    认证：connect() 内部校验 ?token= 参数，认证失败时自动 close WebSocket。
    """
    auth = await manager.connect(websocket, session_id)

    # 如果 token 已配置但认证失败，connect() 已 close WebSocket，直接返回
    if not auth.is_authenticated and TokenStore.singleton().is_configured():
        return

    # 从 AuthContext 解析角色字符串，贯穿后续所有操作
    # 映射规则：ADMIN→admin, OP→operator, READ/ANONYMOUS→viewer
    role = _resolve_role(auth)

    try:
        while True:
            raw = await websocket.receive_text()
            await _handle_message(websocket, session_id, raw, role)
    except WebSocketDisconnect:
        logger.info(f"[WebSocket] 会话断开: {session_id}")
        manager.cancel_session(session_id)
    except Exception as e:
        logger.exception(f"[WebSocket] 会话异常: {e}")
        manager.cancel_session(session_id)
        try:
            await _send(websocket, "error", message="服务端处理异常，请稍后重试")
        except Exception:
            pass
    finally:
        manager.disconnect(session_id)


# ── 内部处理函数 ─────────────────────────────────────────────────


def _resolve_role(auth: AuthContext) -> str:
    """将 AuthContext.level 映射为 SafetyGuard 兼容的角色字符串

    映射规则（最小权限原则）：
      ANONYMOUS / READ → "viewer"   — 只能执行 low 风险查询
      OP               → "operator" — low 放行，medium 需确认
      ADMIN            → "admin"    — low 放行，medium 需确认，high 仍拒绝

    所有高危操作继续经过 SafetyGuard，不受角色影响。
    """
    if auth.level == AuthLevel.ADMIN:
        return "admin"
    if auth.level == AuthLevel.OP:
        return "operator"
    # READ / ANONYMOUS → viewer（最小权限）
    return "viewer"


async def _handle_message(websocket: WebSocket, session_id: str, raw: str, role: str = "viewer") -> None:
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
            await message_repository.save_message(
                session_id=session_id, role="system",
                content="已取消该风险操作。",
                message_type="status", trace_id=pending["trace_id"],
            )
            return

        if decision == "approve":
            popped = manager.pop_pending(session_id)
            assert popped is not None
            user_input = popped.get("user_input", "")
            trace_id = popped.get("trace_id", "")
            # 确认后走 Day5 Agent 主流程（confirmed=True，trace_id 保持连续性）
            # 从 ConnectionManager 获取认证信息，解析真实 role（不再硬编码 viewer）
            auth_ctx = manager.get_auth(session_id)
            role = _resolve_role(auth_ctx) if auth_ctx else "viewer"
            await _run_agent_flow(
                websocket, session_id, user_input, role, confirmed=True, trace_id=trace_id,
            )
            return

        await _send(websocket, "error", message=f"未知的 confirm 决策: {decision}")
        return

    # ── tool_confirm：中危工具调用确认/拒绝 ──
    if msg_type == "tool_confirm":
        tool_confirm_id = msg.get("tool_confirm_id", "")
        decision = msg.get("decision", "")

        # 兼容旧版前端可能发送的 approved boolean
        if not decision and "approved" in msg:
            decision = "approve" if msg.get("approved") in (True, "approve") else "reject"

        if not tool_confirm_id:
            await _send(websocket, "error", message="tool_confirm 缺少 tool_confirm_id")
            return

        if decision not in ("approve", "reject"):
            await _send(websocket, "error", message=f"tool_confirm 决策无效: {decision}，可用: approve / reject")
            return

        # 从 ConnectionManager 查找待确认的工具调用
        pending_tool = manager.pop_pending_tool(session_id)
        if pending_tool is None or pending_tool.get("tool_confirm_id") != tool_confirm_id:
            await _send(websocket, "error", message=f"未找到待确认的工具调用: {tool_confirm_id}")
            return

        trace_id = pending_tool.get("trace_id", "")

        if decision == "reject":
            tool_name = pending_tool.get('tool', '')
            await _send(websocket, "tool_rejected",
                        tool=tool_name, reason="用户拒绝该工具调用",
                        trace_id=trace_id)
            await _send(websocket, "done", trace_id=trace_id)
            await message_repository.save_message(
                session_id=session_id, role="system",
                content=f"已拒绝工具调用: {tool_name}",
                message_type="tool_rejected", trace_id=trace_id,
                metadata={"tool": tool_name},
            )
            logger.info(f"[tool_confirm] 用户拒绝: session={session_id}, tool_confirm_id={tool_confirm_id}")
            return

        # decision == "approve"：恢复上下文并执行工具
        from app.services.agent_context import AgentContext
        ctx = AgentContext(**pending_tool["context"])
        tool = pending_tool["tool"]
        params = pending_tool.get("params", {})

        await _send(websocket, "status", content="工具调用已批准，正在执行...", trace_id=trace_id)
        logger.info(f"[tool_confirm] 用户批准: session={session_id}, tool_confirm_id={tool_confirm_id}, tool={tool}")

        try:
            result = await agent_harness.run_tool(ctx, tool, params, confirmed=True)
        except Exception as e:
            logger.exception(f"[tool_confirm] 工具执行异常: {e}")
            await _send(websocket, "error", message="工具执行异常，请稍后重试", trace_id=trace_id)
            await _send(websocket, "done", trace_id=trace_id)
            return

        # 发送 tool_call 结果帧
        tool_frame = {
            "type": "tool_call", "trace_id": trace_id,
            "tool": tool, "params": _orchestrator._safe_params_for_display(params),
            "tool_call_id": tool_confirm_id,
            "ok": result.get("ok", False),
        }
        if result.get("ok"):
            tool_frame["result"] = result.get("result")
        else:
            tool_frame["error"] = result.get("error") or "工具调用失败"
        # 先持久化再推送 tool_call 结果帧：工具执行期间客户端可能已经断开
        # （例如重启本机 nginx 反代会切断 WebSocket），先落库保证刷新后仍能看到执行结果
        await _persist_frame(session_id, trace_id, tool_frame)
        try:
            await websocket.send_json(tool_frame)
        except Exception:
            logger.info(f"[tool_confirm] 客户端已断开，工具结果已持久化: session={session_id}, tool={tool}")
            return

        # 继续生成诊断报告
        try:
            report = await _orchestrator._generate_report(
                ctx.intent_result, ctx.observations, ctx.user_input, ctx.knowledge_result,
            )
        except Exception as e:
            logger.exception(f"[tool_confirm] 生成报告异常: {e}")
            report = "工具执行完成，但生成报告时发生异常。"

        for i in range(0, len(report), 500):
            await _send(websocket, "chunk", content=report[i:i + 500], trace_id=trace_id)

        title = await _generate_session_title_for_confirm(session_id, pending_tool, report)
        await _send(websocket, "done", trace_id=trace_id, session_id=session_id, title=title)
        await message_repository.save_message(
            session_id=session_id, role="assistant",
            content=report,
            message_type="chat", trace_id=trace_id,
        )
        # 审计
        try:
            await audit_service.save_context(ctx, event_type="chat_done")
        except Exception:
            logger.warning("[tool_confirm] 审计写入失败（已忽略）", exc_info=True)
        return

    # ── chat：核心对话流程 ──
    if msg_type != "chat":
        await _send(websocket, "error", message=f"不支持的消息类型: {msg_type}，可用类型: chat / confirm / tool_confirm / ping")
        return

    content = msg.get("content", "")
    if not content or not isinstance(content, str) or not content.strip():
        await _send(websocket, "error", message="输入不能为空或格式错误", trace_id=str(uuid.uuid4())[:16])
        return

    user_input = content.strip()
    trace_id = str(uuid.uuid4())[:16]

    # 确保会话在数据库中已注册（首次消息时自动创建）
    await message_repository.create_session(session_id)
    # 保存用户消息
    await message_repository.save_message(
        session_id=session_id, role="user", content=user_input,
        message_type="chat", trace_id=trace_id,
    )

    # SafetyGuard 检查（高危/中危由 chat 层处理，低危委托 Orchestrator）
    safety = safety_guard.analyze_user_input(user_input)

    if not safety["allowed"]:
        await _send(websocket, "risk_alert", level=safety["risk_level"], reason=safety["reason"],
                    original_input=user_input, trace_id=trace_id)
        await log_chain(trace_id=trace_id, user_input=user_input, risk_level=safety["risk_level"],
                        final_response=safety["reason"])
        await message_repository.save_message(
            session_id=session_id, role="system", content=safety["reason"],
            message_type="risk_alert", trace_id=trace_id,
            metadata={"level": safety["risk_level"]},
        )
        return

    if safety["requires_confirm"]:
        confirm_id = f"cfm_{str(uuid.uuid4())[:8]}"
        manager.set_pending(session_id, {"user_input": user_input, "trace_id": trace_id,
                                          "risk_level": safety["risk_level"], "confirm_id": confirm_id})
        await _send(websocket, "risk_alert", level=safety["risk_level"], reason=safety["reason"],
                    original_input=user_input, confirm_id=confirm_id, trace_id=trace_id)
        await message_repository.save_message(
            session_id=session_id, role="system", content=safety["reason"],
            message_type="risk_alert", trace_id=trace_id,
            metadata={"level": safety["risk_level"], "confirm_id": confirm_id},
        )
        return

    # 低危：Day5 Agent 主流程（Orchestrator.handle_chat）
    # role 由调用方从 AuthContext 解析传入，不再硬编码 viewer
    await _run_agent_flow(websocket, session_id, user_input, role, trace_id=trace_id, safety_result=safety)


# ── 旧风险路径已删除 ─────────────────────────────────────────────────
# 原有旧版编排函数（直接调用 LLM Router + MCP Executor，绕过安全层）
# 已在 LLM-Agent 大修复中删除，所有 chat 消息统一走 Orchestrator.handle_chat。


async def _run_agent_flow(
    websocket: WebSocket,
    session_id: str,
    user_input: str,
    role: str,
    confirmed: bool = False,
    trace_id: str | None = None,
    safety_result: dict | None = None,
) -> None:
    """执行 Orchestrator 主流程：逐帧发送到 WebSocket，并逐帧持久化。

    改进点（v1.1）：
    1. WS 断开后不再继续消费 generator，立即 aclose 释放后端资源
    2. 总超时 120s，防止 MCP/LLM 累积阻塞导致生成器永不结束
    3. chunk 帧按 trace_id 追加合并落库
    """
    effective_trace_id = trace_id or ""
    # 总超时保护：整个 orchestration 不超过 120 秒
    TOTAL_TIMEOUT = 120.0

    agen = _orchestrator.handle_chat(
        session_id=session_id, user_input=user_input, role=role,
        confirmed=confirmed, trace_id=trace_id,
        safety_result=safety_result,
    )

    async def _consume_frames() -> None:
        """消费 generator 并发送/持久化帧"""
        nonlocal effective_trace_id
        async for frame in agen:
            # 检查是否已被取消
            if manager.is_cancelled(session_id):
                logger.info(
                    f"[WebSocket] 会话已取消，中断 generator 消费: session={session_id}"
                )
                break

            # 中危工具确认帧：把上下文暂存到 ConnectionManager
            if frame.get("type") == "pending_confirmation":
                manager.set_pending_tool(session_id, {
                    "tool_confirm_id": frame.get("tool_confirm_id"),
                    "tool": frame.get("tool"),
                    "params": frame.get("params"),
                    "trace_id": frame.get("trace_id"),
                    "context": frame.get("context", {}),
                })
                frame = {k: v for k, v in frame.items() if k != "context"}

            # 发送到前端（容错：连接断开时不抛异常）
            try:
                await websocket.send_json(frame)
            except (WebSocketDisconnect, RuntimeError) as e:
                logger.warning(
                    f"[WebSocket] 连接已关闭，中断消费: session={session_id}, {e}"
                )
                manager.cancel_session(session_id)
                break

            await _persist_frame(session_id, frame.get("trace_id") or effective_trace_id, frame)

    try:
        await asyncio.wait_for(_consume_frames(), timeout=TOTAL_TIMEOUT)
    except asyncio.TimeoutError:
        logger.error(
            f"[WebSocket] orchestration 总超时 ({TOTAL_TIMEOUT}s): session={session_id}, "
            f"trace_id={effective_trace_id}"
        )
        manager.cancel_session(session_id)
        try:
            await websocket.send_json({
                "type": "error", "trace_id": effective_trace_id,
                "message": "处理超时，请稍后重试或缩短查询范围",
            })
        except Exception:
            pass
    finally:
        await agen.aclose()


async def _generate_session_title_for_confirm(session_id: str, pending_tool: dict, report: str) -> str:
    """tool_confirm 恢复路径的标题生成，委托给 orchestrator 的统一函数"""
    from app.services.orchestrator import _generate_session_title
    user_input = pending_tool.get("context", {}).get("user_input", "")
    return await _generate_session_title(session_id, user_input, report)


async def _persist_frame(session_id: str, trace_id: str, frame: dict[str, Any]) -> None:
    """持久化单条 assistant 关键帧；写库失败仅记录日志，不中断主流程"""
    try:
        ft = frame.get("type", "")
        if ft == "status":
            # status 帧仅为前端实时进度提示，不持久化到对话记录
            pass
        elif ft == "tool_call":
            meta = {
                "tool": frame.get("tool"),
                "tool_call_id": frame.get("tool_call_id"),
                "params": frame.get("params"),
                "ok": frame.get("ok"),
            }
            if frame.get("ok"):
                meta["result"] = frame.get("result")
            else:
                meta["error"] = frame.get("error")
            await message_repository.save_message(
                session_id=session_id, role="assistant",
                content=json.dumps(frame.get("result", frame.get("error", "")), ensure_ascii=False),
                message_type="tool_call", trace_id=trace_id, metadata=meta,
            )
        elif ft == "chunk":
            if trace_id:
                await message_repository.append_chunk(
                    session_id=session_id, trace_id=trace_id,
                    content=frame.get("content", ""),
                )
            else:
                await message_repository.save_message(
                    session_id=session_id, role="assistant",
                    content=frame.get("content", ""),
                    message_type="chunk",
                )
        elif ft == "fix_options":
            await message_repository.save_message(
                session_id=session_id, role="assistant",
                content=json.dumps(frame.get("options", []), ensure_ascii=False),
                message_type="fix_options", trace_id=trace_id,
            )
        elif ft == "pending_confirmation":
            await message_repository.save_message(
                session_id=session_id, role="assistant",
                content=frame.get("reason", ""),
                message_type="pending_confirmation", trace_id=trace_id,
                metadata={
                    "tool": frame.get("tool"),
                    "params": frame.get("params"),
                    "tool_confirm_id": frame.get("tool_confirm_id"),
                    "risk_level": frame.get("risk_level"),
                },
            )
        elif ft == "tool_rejected":
            await message_repository.save_message(
                session_id=session_id, role="system",
                content=f"已拒绝工具调用: {frame.get('tool', '')}",
                message_type="tool_rejected", trace_id=trace_id,
                metadata={"tool": frame.get("tool")},
            )
        elif ft == "error":
            await message_repository.save_message(
                session_id=session_id, role="assistant",
                content=frame.get("message", ""),
                message_type="error", trace_id=trace_id,
            )
        # done / risk_alert 不需要额外保存（已在上层处理）
    except Exception:
        logger.warning("[WebSocket] assistant 帧持久化失败（已忽略）", exc_info=True)


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
    session_id: str | None = None,
    title: str | None = None,
    tool: str | None = None,
    tool_call_id: str | None = None,
    tool_confirm_id: str | None = None,
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

    elif msg_type == "pending_confirmation":
        if tool is not None:
            payload["tool"] = tool
        if params is not None:
            payload["params"] = params
        if tool_confirm_id is not None:
            payload["tool_confirm_id"] = tool_confirm_id
        if reason is not None:
            payload["reason"] = reason
        if confirm_id is not None:
            payload["confirm_id"] = confirm_id
        if trace_id is not None:
            payload["trace_id"] = trace_id

    elif msg_type == "done":
        if trace_id is not None:
            payload["trace_id"] = trace_id
        if session_id is not None:
            payload["session_id"] = session_id
        if title is not None:
            payload["title"] = title

    elif msg_type == "error":
        if message is not None:
            payload["message"] = message
        if trace_id is not None:
            payload["trace_id"] = trace_id

    elif msg_type == "tool_rejected":
        if tool is not None:
            payload["tool"] = tool
        if reason is not None:
            payload["reason"] = reason
        if trace_id is not None:
            payload["trace_id"] = trace_id

    elif msg_type == "pong":
        pass  # 只发 {"type":"pong"}，无额外字段

    await ws.send_json(payload)
