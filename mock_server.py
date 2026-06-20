#!/usr/bin/env python3
"""
前端功能测试服务器 (Mock Server)

根据 frontend_api_reference.md 实现，用于前端开发阶段独立联调。
不依赖 DeepSeek、MCP、Redis、SQLite，所有数据均为模拟数据。

启动:
    python mock_server.py

默认监听 0.0.0.0:8000，与 Vite 代理配置保持一致。
"""

import asyncio
import json
import logging
import random
import time
import uuid
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, Query, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

# ---------------------- 日志配置 ----------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

# ---------------------- 应用初始化 ----------------------
app = FastAPI(
    title="kylin-agent 前端测试 Mock Server",
    description="按 frontend_api_reference.md 实现的前端联调测试服务器",
    version="0.2.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------- 工具函数 ----------------------
def ok(data: Any = None) -> dict:
    """标准成功响应"""
    return {"code": 200, "data": data}


def err(code: int, message: str) -> dict:
    """标准错误响应"""
    return {"code": code, "message": message}


def now_iso() -> str:
    return datetime.now().isoformat()


# ---------------------- 内存数据存储 ----------------------
class MockStore:
    """模拟持久化存储"""

    def __init__(self):
        self.whitelist = {
            "commands": [
                {"pattern": "systemctl status *", "role": "agent-op", "risk": "low"},
                {"pattern": "systemctl start *", "role": "agent-op", "risk": "medium"},
                {"pattern": "systemctl stop *", "role": "agent-op", "risk": "medium"},
                {"pattern": "systemctl restart *", "role": "agent-admin", "risk": "medium"},
                {"pattern": "journalctl -u *", "role": "agent-read", "risk": "low"},
                {"pattern": "df -h", "role": "agent-read", "risk": "low"},
                {"pattern": "free -h", "role": "agent-read", "risk": "low"},
                {"pattern": "ps aux", "role": "agent-read", "risk": "low"},
                {"pattern": "top -bn1", "role": "agent-read", "risk": "low"},
                {"pattern": "netstat -tlnp", "role": "agent-read", "risk": "low"},
                {"pattern": "ip addr", "role": "agent-read", "risk": "low"},
                {"pattern": "ls *", "role": "agent-read", "risk": "low"},
                {"pattern": "cat *", "role": "agent-read", "risk": "low"},
                {"pattern": "passwd *", "role": "agent-admin", "risk": "medium"},
            ],
            "blocked_patterns": [
                "rm -rf *",
                "mkfs.*",
                "dd if=/dev/zero *",
                "> /etc/*",
                ":(){ :|:& };:",
                "iptables -F",
            ],
        }
        self.audit_logs: List[dict] = self._generate_audit_logs(35)
        self.sessions: Dict[str, WebSocket] = {}

    def _generate_audit_logs(self, count: int) -> List[dict]:
        """生成模拟审计日志"""
        samples = [
            ("查看nginx状态", "service_query", "low", "service_mgr", "systemctl status nginx",
             "nginx正在运行..."),
            ("CPU使用率是多少", "metric_query", "low", "sys_info", "top -bn1 | head", "当前CPU使用率: 23%"),
            ("磁盘还剩多少空间", "metric_query", "low", "sys_info", "df -h", "磁盘剩余 13.2GB (33%)"),
            ("重启ssh服务", "service_op", "medium", "service_mgr", "systemctl restart sshd",
             "ssh服务已重启"),
            ("查看防火墙规则", "security_query", "low", "sys_info", "iptables -L", "..."),
            ("删除所有日志", "malicious", "high", None, None, "检测到高危命令: rm -rf /var/log"),
            ("查看内存使用情况", "metric_query", "low", "sys_info", "free -h", "内存使用 45% (3.6GB/8GB)"),
        ]
        logs = []
        base_time = datetime.now() - timedelta(days=7)
        for i in range(count):
            sample = random.choice(samples)
            ts = base_time + timedelta(minutes=i * 47)
            logs.append({
                "trace_id": str(uuid.uuid4())[:16],
                "timestamp": ts.isoformat(),
                "user_input": sample[0],
                "intent": sample[1],
                "risk_level": sample[2],
                "mcp_tool": sample[3],
                "command": sample[4],
                "raw_output": "{}" if sample[3] else None,
                "llm_reasoning": f"识别意图为 {sample[1]}" if sample[3] else None,
                "final_response": sample[5],
            })
        # 按时间倒序
        logs.sort(key=lambda x: x["timestamp"], reverse=True)
        return logs


store = MockStore()


# ---------------------- REST API ----------------------
@app.get("/api/audit/logs")
async def get_audit_logs(
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=200),
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
):
    """审计日志查询（支持分页与日期过滤）"""
    filtered = store.audit_logs[:]

    if start_date:
        filtered = [r for r in filtered if r["timestamp"] >= start_date]
    if end_date:
        end_boundary = f"{end_date}T23:59:59"
        filtered = [r for r in filtered if r["timestamp"] <= end_boundary]

    total = len(filtered)
    offset = (page - 1) * limit
    page_data = filtered[offset:offset + limit]

    return ok({"total": total, "list": page_data})


@app.get("/api/monitor/metrics")
async def get_metrics_snapshot():
    """监控指标快照（REST 轮询降级用）"""
    data = {
        "cpu": {"percent": random.randint(10, 85), "cores": 4},
        "memory": {
            "percent": random.randint(20, 75),
            "total": "8GB",
            "used": f"{random.uniform(1.5, 6.0):.1f}GB",
        },
        "disk": {
            "percent": random.randint(30, 90),
            "total": "40GB",
            "used": f"{random.uniform(10.0, 36.0):.1f}GB",
        },
        "network": {
            "rx": f"{random.uniform(0.1, 5.0):.1f}MB/s",
            "tx": f"{random.uniform(0.1, 3.0):.1f}MB/s",
        },
        "timestamp": now_iso(),
    }
    return ok(data)


async def _metrics_sse_generator():
    """SSE 监控指标流，每 3 秒推送一次"""
    while True:
        data = {
            "cpu_percent": random.randint(10, 85),
            "memory_percent": random.randint(20, 75),
            "disk_percent": random.randint(30, 90),
            "net_in_kbps": random.randint(100, 5000),
            "net_out_kbps": random.randint(100, 3000),
            "timestamp": now_iso(),
        }
        yield f"data: {json.dumps(data)}\n\n"
        await asyncio.sleep(3)


@app.get("/api/monitor/stream")
async def monitor_stream():
    """SSE 系统监控流"""
    return StreamingResponse(
        _metrics_sse_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
        },
    )


@app.get("/api/config/whitelist")
async def get_whitelist():
    """获取白名单配置"""
    return ok(store.whitelist)


class WhitelistPayload(BaseModel):
    commands: List[dict]
    blocked_patterns: List[str]


@app.put("/api/config/whitelist")
async def update_whitelist(payload: WhitelistPayload):
    """更新白名单配置（内存持久化）"""
    store.whitelist["commands"] = payload.commands
    store.whitelist["blocked_patterns"] = payload.blocked_patterns
    logger.info(f"[Config] 白名单已更新: {len(payload.commands)} 条命令, {len(payload.blocked_patterns)} 条拦截规则")
    return ok({"message": "白名单配置已更新"})


@app.get("/health")
async def health_check():
    return {"status": "ok", "mode": "mock"}


# ---------------------- WebSocket 聊天 ----------------------
DANGEROUS_KEYWORDS = ["rm -rf", "mkfs", "dd if=/dev/zero", "> /etc/passwd", "fork bomb", "iptables -F"]
MEDIUM_KEYWORDS = ["systemctl stop", "systemctl restart", "passwd", "shutdown", "reboot", "iptables"]


def _classify_input(text: str) -> dict:
    """简单风险分级"""
    t = text.lower()
    for kw in DANGEROUS_KEYWORDS:
        if kw in t:
            return {"action": "reject", "level": "high", "reason": f"检测到高危命令: {kw}"}
    for kw in MEDIUM_KEYWORDS:
        if kw in t:
            return {"action": "confirm", "level": "medium", "reason": f"检测到潜在风险操作: {kw}"}
    return {"action": "allow", "level": "low", "reason": "安全"}


async def _stream_reply(websocket: WebSocket, user_input: str, trace_id: str):
    """模拟 LLM 流式回复 + 工具调用"""
    lowered = user_input.lower()

    # 根据输入内容模拟不同工具调用与回复
    if any(k in lowered for k in ["cpu", "处理器", "cpu使用率"]):
        await _send_ws(websocket, "tool_call", tool="sys_info", params={"metric": "cpu"}, trace_id=trace_id)
        await asyncio.sleep(0.3)
        reply = "当前 CPU 使用率为 23%，负载较低，运行平稳。"
    elif any(k in lowered for k in ["内存", "memory", "ram"]):
        await _send_ws(websocket, "tool_call", tool="sys_info", params={"metric": "memory"}, trace_id=trace_id)
        await asyncio.sleep(0.3)
        reply = "内存使用率为 45%，已用 3.6GB / 总共 8GB。"
    elif any(k in lowered for k in ["磁盘", "disk", "空间"]):
        await _send_ws(websocket, "tool_call", tool="sys_info", params={"metric": "disk"}, trace_id=trace_id)
        await asyncio.sleep(0.3)
        reply = "磁盘使用率为 67%，已用 26.8GB / 总共 40GB。"
    elif any(k in lowered for k in ["nginx", "服务状态"]):
        await _send_ws(websocket, "tool_call", tool="service_mgr", params={"action": "status", "service": "nginx"}, trace_id=trace_id)
        await asyncio.sleep(0.3)
        reply = "nginx 服务当前正在运行，进程 PID 为 1234。"
    else:
        reply = f"已收到您的问题：{user_input}。这是来自测试服务器的模拟回复，用于验证前端流式渲染效果。"

    # 模拟流式分片
    chunk_size = 4
    for i in range(0, len(reply), chunk_size):
        await _send_ws(websocket, "chunk", content=reply[i:i + chunk_size], trace_id=trace_id)
        await asyncio.sleep(0.05)

    await _send_ws(websocket, "done", trace_id=trace_id)


async def _send_ws(websocket: WebSocket, msg_type: str, content: str = "", trace_id: str = None, **extra):
    payload: Dict[str, Any] = {"type": msg_type, "content": content}
    if trace_id:
        payload["trace_id"] = trace_id
    payload.update(extra)
    await websocket.send_json(payload)


@app.websocket("/ws/chat/{session_id}")
async def chat_websocket(websocket: WebSocket, session_id: str):
    """WebSocket 聊天接口"""
    await websocket.accept()
    store.sessions[session_id] = websocket
    logger.info(f"[WebSocket] 会话连接: {session_id}")

    try:
        while True:
            raw = await websocket.receive_text()
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                await _send_ws(websocket, "error", content="消息格式非法，需为 JSON")
                continue

            msg_type = msg.get("type", "chat")

            # 心跳
            if msg_type == "ping":
                await _send_ws(websocket, "pong")
                continue

            if msg_type != "chat":
                continue

            user_input = msg.get("content", "").strip()
            if not user_input:
                await _send_ws(websocket, "error", content="输入不能为空")
                continue

            trace_id = str(uuid.uuid4())[:16]
            risk = _classify_input(user_input)

            # 记录审计日志
            store.audit_logs.insert(0, {
                "trace_id": trace_id,
                "timestamp": now_iso(),
                "user_input": user_input,
                "intent": "chat",
                "risk_level": risk["level"],
                "mcp_tool": None,
                "command": None,
                "raw_output": None,
                "llm_reasoning": None,
                "final_response": risk["reason"] if risk["action"] != "allow" else None,
            })

            if risk["action"] == "reject":
                await _send_ws(
                    websocket,
                    "reject",
                    content=f"【安全拦截】{risk['reason']}，操作已被拒绝。",
                    trace_id=trace_id,
                )
            elif risk["action"] == "confirm":
                await _send_ws(
                    websocket,
                    "risk_alert",
                    content=f"【风险确认】{risk['reason']}，请确认是否继续执行？",
                    trace_id=trace_id,
                )
            else:
                await _stream_reply(websocket, user_input, trace_id)

    except WebSocketDisconnect:
        logger.info(f"[WebSocket] 会话断开: {session_id}")
    except Exception as e:
        logger.exception(f"[WebSocket] 会话异常: {e}")
        try:
            await _send_ws(websocket, "error", content=f"服务端异常: {str(e)}")
        except Exception:
            pass
    finally:
        store.sessions.pop(session_id, None)


# ---------------------- 启动入口 ----------------------
if __name__ == "__main__":
    import uvicorn

    host = "0.0.0.0"
    port = 8000
    logger.info(f"启动前端测试 Mock Server: http://{host}:{port}")
    logger.info("API 文档: http://localhost:8000/docs")
    uvicorn.run(app, host=host, port=port)
