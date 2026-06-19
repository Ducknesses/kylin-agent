#!/usr/bin/env python3
"""
前端测试 Mock 后端服务器

模拟 frontend_api_reference.md 中定义的所有 API 端点，
让前端开发者可以在不依赖真实后端/MCP Server 的情况下独立测试。

启动方式:
    python mock_server.py                # 默认监听 8030 端口
    python mock_server.py --port 9090    # 自定义端口
    python mock_server.py --host 0.0.0.0 --port 8030  # 允许外部访问

端点覆盖:
    GET  /api/monitor/stream       — SSE 实时监控流 (每3秒推送)
    GET  /api/monitor/metrics      — REST 监控指标快照
    GET  /api/audit/logs           — 审计日志 (支持分页/日期过滤)
    GET  /api/config/whitelist     — 白名单配置查询
    PUT  /api/config/whitelist     — 白名单配置更新
    WS   /ws/chat/{session_id}     — WebSocket 对话模拟
    GET  /health                    — 健康检查
"""

import argparse
import asyncio
import json
import logging
import random
import time
import uuid
from datetime import datetime, timedelta
from typing import Optional

from fastapi import FastAPI, Query, WebSocket, WebSocketDisconnect, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse, JSONResponse
import uvicorn

# ---------------------------------------------------------------------------
# 日志配置
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("mock-server")

# ---------------------------------------------------------------------------
# FastAPI 应用
# ---------------------------------------------------------------------------
app = FastAPI(
    title="Kylin Agent Mock Server",
    description="前端测试 Mock 后端 — 模拟全部 API 端点",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------------------------------------------------------------------
# 工具函数
# ---------------------------------------------------------------------------

def ok(data=None):
    """标准成功响应包裹 {code:200, data:...}"""
    return {"code": 200, "data": data}


def err(code: int, message: str):
    """标准错误响应"""
    return {"code": code, "message": message}


def random_cpu():
    """模拟 CPU 使用率 (20-80%)"""
    return round(random.uniform(15, 85), 1)


def random_memory():
    """模拟内存使用率 (30-70%)"""
    pct = round(random.uniform(30, 70), 1)
    total = 8192  # 8GB in MB
    used = int(total * pct / 100)
    return pct, f"{total/1024:.0f}GB", f"{used/1024:.1f}GB"


def random_disk():
    """模拟磁盘使用率 (40-75%)"""
    pct = round(random.uniform(40, 75), 1)
    total = 40960  # 40GB in MB
    used = int(total * pct / 100)
    return pct, f"{total/1024:.0f}GB", f"{used/1024:.1f}GB"


def random_network():
    """模拟网络流量，返回 KB/s 数值"""
    rx = round(random.uniform(100, 5000), 0)
    tx = round(random.uniform(50, 3000), 0)
    return rx, tx


# ---------------------------------------------------------------------------
# 内存状态存储
# ---------------------------------------------------------------------------

# 白名单配置（内存中可修改）
_whitelist_state = {
    "commands": [
        {"pattern": "systemctl status *", "role": "agent-op", "risk": "low"},
        {"pattern": "journalctl -u *", "role": "agent-read", "risk": "low"},
        {"pattern": "df -h", "role": "agent-read", "risk": "low"},
        {"pattern": "free -m", "role": "agent-read", "risk": "low"},
        {"pattern": "ps aux | grep *", "role": "agent-op", "risk": "low"},
        {"pattern": "ss -tlnp", "role": "agent-read", "risk": "low"},
        {"pattern": "du -sh /var/log/*", "role": "agent-read", "risk": "low"},
    ],
    "blocked_patterns": [
        "rm -rf *",
        "mkfs.*",
        "> /etc/*",
        "dd if=* of=/dev/*",
        ":(){ :|:& };:",
        "chmod 777 /etc/*",
    ],
}

# 审计日志预生成数据
_AUDIT_ACTIONS = [
    ("查看CPU使用率", "monitor_query", "sys_info", "low"),
    ("查看内存状态", "monitor_query", "sys_info", "low"),
    ("查看磁盘使用情况", "monitor_query", "sys_info", "low"),
    ("检查nginx运行状态", "service_query", "service_mgr", "low"),
    ("查看sshd服务日志", "log_query", "log_reader", "low"),
    ("检查端口监听状态", "net_query", "net_monitor", "low"),
    ("查看系统负载", "monitor_query", "sys_info", "low"),
    ("重启nginx服务", "service_action", "service_mgr", "medium"),
    ("清理/tmp临时文件", "file_operation", "file_guard", "medium"),
    ("检查SSH登录失败记录", "security_audit", "log_reader", "medium"),
    ("删除旧日志文件", "file_operation", "file_guard", "medium"),
    ("修改/etc/hosts配置", "config_change", "file_guard", "high"),
    ("删除用户目录", "dangerous_action", "cmd_exec", "high"),
    ("执行rm -rf命令", "dangerous_action", "cmd_exec", "high"),
    ("修改系统服务配置", "config_change", "file_guard", "medium"),
]

_AUDIT_COMMANDS = [
    "sys_info --metric=cpu",
    "sys_info --metric=memory",
    "sys_info --metric=disk",
    "systemctl status nginx",
    "journalctl -u sshd --since '1 hour ago'",
    "ss -tlnp | grep LISTEN",
    "uptime && cat /proc/loadavg",
    "systemctl restart nginx",
    "find /tmp -type f -mtime +7 -exec rm {} \\;",
    "grep 'Failed password' /var/log/auth.log | tail -20",
    "find /var/log -name '*.log' -mtime +30 -delete",
    "echo '127.0.0.1 test.local' >> /etc/hosts",
    "rm -rf /home/user/.cache",
    "rm -rf /var/cache/apt/archives/*",
    "sed -i 's/^Port .*/Port 2222/' /etc/ssh/sshd_config",
]

_LLM_REASONINGS = [
    "用户请求查看系统CPU使用率，这是一个低风险的监控查询操作，直接调用sys_info工具获取实时数据。",
    "根据用户意图分析，这是一个服务状态查询请求，正在通过service_mgr工具获取nginx运行状态。",
    "检测到用户意图为日志审计相关操作，正在通过log_reader工具检索SSH认证日志中的异常记录。",
    "用户试图执行文件删除操作，经过风险评估该操作风险等级为中等，已在沙箱环境中执行并记录审计日志。",
    "这是一个系统配置修改请求，属于高风险操作，系统已记录该操作的完整审计链，建议管理员复核。",
]

_FINAL_RESPONSES = [
    "当前CPU使用率为23%，系统负载正常，8个核心均处于正常工作状态。",
    "内存使用率45%，总计8GB，已用3.6GB，剩余空间充足，无需清理。",
    "磁盘使用率67%，总计40GB，已用26.8GB。建议关注/var/log目录增长趋势。",
    "nginx服务当前状态: active (running)，已持续运行3天12小时，无异常重启记录。",
    "SSH日志分析完成：最近1小时内有3次失败登录尝试，来源IP已记录，未发现成功入侵迹象。",
    "端口监听状态正常：8000(backend)、8001(mcp-server)、22(sshd)均处于LISTEN状态。",
    "系统负载: 1min=0.85, 5min=0.72, 15min=0.68，系统运行平稳。",
    "nginx服务已成功重启，新配置已生效，建议通过浏览器验证服务可访问性。",
]


def _generate_audit_records(count: int = 50) -> list:
    """生成模拟审计日志记录"""
    records = []
    base_time = datetime.now() - timedelta(days=7)
    for i in range(count):
        ts = base_time + timedelta(hours=i * 3 + random.randint(0, 2))
        action_idx = i % len(_AUDIT_ACTIONS)
        user_input, intent, mcp_tool, risk = _AUDIT_ACTIONS[action_idx]
        record = {
            "trace_id": str(uuid.uuid4())[:8],
            "timestamp": ts.strftime("%Y-%m-%dT%H:%M:%S"),
            "user_input": user_input,
            "intent": intent,
            "risk_level": risk,
            "mcp_tool": mcp_tool,
            "command": _AUDIT_COMMANDS[action_idx],
            "raw_output": json.dumps(
                {"status": "success", "elapsed_ms": random.randint(50, 2000)},
                ensure_ascii=False,
            ),
            "llm_reasoning": _LLM_REASONINGS[i % len(_LLM_REASONINGS)],
            "final_response": _FINAL_RESPONSES[i % len(_FINAL_RESPONSES)],
        }
        records.append(record)
    # 按时间倒序排列
    records.sort(key=lambda r: r["timestamp"], reverse=True)
    return records


# 启动时生成一次
_audit_records = _generate_audit_records(60)

# ---------------------------------------------------------------------------
# 健康检查
# ---------------------------------------------------------------------------

@app.get("/health")
async def health_check():
    return {
        "status": "ok",
        "service": "Kylin Agent Mock Server",
        "version": "1.0.0",
        "endpoints": [
            "GET /api/monitor/stream (SSE)",
            "GET /api/monitor/metrics",
            "GET /api/audit/logs",
            "GET /api/config/whitelist",
            "PUT /api/config/whitelist",
            "WS /ws/chat/{session_id}",
        ],
    }


# ---------------------------------------------------------------------------
# 1. SSE 监控流 — GET /api/monitor/stream
# ---------------------------------------------------------------------------

@app.get("/api/monitor/stream")
async def monitor_stream(request: Request):
    """Server-Sent Events 实时监控数据推送，每 3 秒发送一次"""

    async def event_generator():
        seq = 0
        while True:
            # 检查客户端是否已断开
            if await request.is_disconnected():
                logger.info("SSE 客户端已断开")
                break

            mem_pct, mem_total, mem_used = random_memory()
            disk_pct, disk_total, disk_used = random_disk()
            net_rx, net_tx = random_network()

            # SSE 按接口约定返回数值（KB/s）
            data = {
                "cpu_percent": random_cpu(),
                "memory_percent": mem_pct,
                "disk_percent": disk_pct,
                "net_in_kbps": net_rx,
                "net_out_kbps": net_tx,
                "timestamp": datetime.now().isoformat(),
            }

            seq += 1
            yield f"id: {seq}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"

            await asyncio.sleep(3)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# ---------------------------------------------------------------------------
# 2. 监控指标快照 — GET /api/monitor/metrics
# ---------------------------------------------------------------------------

@app.get("/api/monitor/metrics")
async def get_monitor_metrics():
    """REST 降级端点：返回单次监控指标快照"""
    mem_pct, mem_total, mem_used = random_memory()
    disk_pct, disk_total, disk_used = random_disk()
    net_rx, net_tx = random_network()

    data = {
        "cpu": {
            "percent": random_cpu(),
            "cores": 8,
        },
        "memory": {
            "percent": mem_pct,
            "total": mem_total,
            "used": mem_used,
        },
        "disk": {
            "percent": disk_pct,
            "total": disk_total,
            "used": disk_used,
        },
        "network": {
            # REST 按接口约定返回带单位的可读字符串
            "rx": f"{net_rx / 1024:.2f}MB/s",
            "tx": f"{net_tx / 1024:.2f}MB/s",
        },
        "timestamp": datetime.now().isoformat(),
    }
    return ok(data)


# ---------------------------------------------------------------------------
# 3. 审计日志 — GET /api/audit/logs
# ---------------------------------------------------------------------------

@app.get("/api/audit/logs")
async def get_audit_logs(
    page: int = Query(1, ge=1, description="页码，从1开始"),
    limit: int = Query(20, ge=1, le=200, description="每页条数"),
    start_date: Optional[str] = Query(None, description="开始日期 YYYY-MM-DD"),
    end_date: Optional[str] = Query(None, description="结束日期 YYYY-MM-DD"),
):
    """审计日志查询，支持分页和日期过滤"""
    records = _audit_records

    # 日期过滤
    if start_date:
        records = [r for r in records if r["timestamp"] >= start_date]
    if end_date:
        # end_date 包含整天，所以加上 T23:59:59
        end_dt = end_date + "T23:59:59"
        records = [r for r in records if r["timestamp"] <= end_dt]

    total = len(records)
    offset = (page - 1) * limit
    page_records = records[offset : offset + limit]

    return ok({"total": total, "list": page_records})


# ---------------------------------------------------------------------------
# 4. 白名单配置查询 — GET /api/config/whitelist
# ---------------------------------------------------------------------------

@app.get("/api/config/whitelist")
async def get_whitelist():
    """查询当前白名单配置"""
    global _whitelist_state
    return ok(_whitelist_state)


# ---------------------------------------------------------------------------
# 5. 白名单配置更新 — PUT /api/config/whitelist
# ---------------------------------------------------------------------------

@app.put("/api/config/whitelist")
async def update_whitelist(payload: dict):
    """更新白名单配置（写入内存）"""
    global _whitelist_state

    commands = payload.get("commands")
    blocked_patterns = payload.get("blocked_patterns")

    if commands is not None:
        _whitelist_state["commands"] = commands
    if blocked_patterns is not None:
        _whitelist_state["blocked_patterns"] = blocked_patterns

    logger.info(
        f"白名单已更新: commands={len(_whitelist_state['commands'])}条, "
        f"blocked={len(_whitelist_state['blocked_patterns'])}条"
    )
    return ok({"message": "白名单配置已更新", "saved": _whitelist_state})


# ---------------------------------------------------------------------------
# 6. WebSocket 对话 — WS /ws/chat/{session_id}
# ---------------------------------------------------------------------------

# 危险关键词 → 触发 reject
_DANGER_KEYWORDS = ["rm -rf", "删除系统", "格式化", "mkfs", "高危", "危险命令"]
# 错误关键词 → 触发 error
_ERROR_KEYWORDS = ["错误", "error", "超时", "连接失败"]


@app.websocket("/ws/chat/{session_id}")
async def ws_chat(websocket: WebSocket, session_id: str):
    """模拟 WebSocket 对话流程"""
    await websocket.accept()
    logger.info(f"WS 连接已建立: session={session_id}")

    try:
        while True:
            raw = await websocket.receive_text()
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                await websocket.send_json({"type": "error", "message": "无效的 JSON 格式"})
                continue

            msg_type = msg.get("type", "")
            content = msg.get("content", "").strip()

            # 心跳处理
            if msg_type == "ping":
                await websocket.send_json({"type": "pong"})
                continue

            if msg_type != "chat" or not content:
                continue

            logger.info(f"[{session_id[:8]}] 收到: {content}")

            # 检查危险关键词
            is_danger = any(kw in content for kw in _DANGER_KEYWORDS)
            is_error = any(kw in content for kw in _ERROR_KEYWORDS)

            if is_danger:
                # 模拟 reject 拦截
                await asyncio.sleep(0.3)
                await websocket.send_json({
                    "type": "reject",
                    "reason": f"检测到高危命令意图，已拦截: {content}",
                    "risk_level": "high",
                })
                continue

            if is_error:
                # 模拟错误
                await asyncio.sleep(0.5)
                await websocket.send_json({
                    "type": "error",
                    "message": "MCP Server 连接超时，请稍后重试",
                })
                continue

            # 正常对话流程: chunk(s) → tool_call → done
            # 步骤1: 发送流式 chunks
            chunk_texts = _generate_chunks(content)
            for chunk_text in chunk_texts:
                await asyncio.sleep(random.uniform(0.1, 0.3))
                await websocket.send_json({"type": "chunk", "content": chunk_text})

            # 步骤2: 模拟工具调用
            await asyncio.sleep(0.3)
            tool_name = random.choice(["sys_info", "service_mgr", "net_monitor", "log_reader", "file_guard"])
            tool_params = _get_tool_params(tool_name, content)
            await websocket.send_json({
                "type": "tool_call",
                "tool": tool_name,
                "params": tool_params,
            })

            # 步骤3: 完成
            await asyncio.sleep(0.2)
            await websocket.send_json({"type": "done"})

    except WebSocketDisconnect:
        logger.info(f"WS 连接已断开: session={session_id}")


def _generate_chunks(user_input: str) -> list:
    """根据用户输入生成模拟的流式响应片段"""
    templates = [
        ["正在分析您的请求...", "识别意图为系统监控查询。", "正在调用相关工具获取数据...", "查询完成，以下是结果：", f"根据'{user_input}'的查询，系统运行状态正常。"],
        ["收到请求，", "正在连接 MCP Server...", "工具调用中...", "数据获取成功。", "当前系统各项指标均在正常范围内。"],
        ["分析中...", f"您的请求'{user_input}'已被解析。", "正在执行安全审计检查...", "未发现异常。", "操作已成功完成。"],
    ]
    return random.choice(templates)


def _get_tool_params(tool_name: str, content: str) -> dict:
    """根据工具名生成模拟参数"""
    params_map = {
        "sys_info": {"metric": random.choice(["cpu", "memory", "disk", "all"])},
        "service_mgr": {"action": "status", "service": "nginx"},
        "net_monitor": {"metric": "listen"},
        "log_reader": {"type": "file", "source": "syslog", "lines": 20},
        "file_guard": {"action": "read", "path": "/var/log/syslog"},
    }
    return params_map.get(tool_name, {"query": content})


# ---------------------------------------------------------------------------
# 全局异常处理
# ---------------------------------------------------------------------------

@app.exception_handler(404)
async def not_found_handler(request: Request, exc):
    return JSONResponse(status_code=404, content=err(404, "请求的资源不存在"))


@app.exception_handler(500)
async def internal_error_handler(request: Request, exc):
    return JSONResponse(status_code=500, content=err(500, "服务器内部错误"))


# ---------------------------------------------------------------------------
# 启动入口
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Kylin Agent 前端测试 Mock Server")
    parser.add_argument("--host", default="127.0.0.1", help="监听地址 (默认 127.0.0.1)")
    parser.add_argument("--port", type=int, default=8030, help="监听端口 (默认 8030)")
    parser.add_argument("--reload", action="store_true", help="开启热重载 (开发模式)")
    args = parser.parse_args()

    print("=" * 60)
    print("  Kylin Agent Mock Server — 前端测试后端")
    print("=" * 60)
    print(f"  监听地址: http://{args.host}:{args.port}")
    print(f"  API 文档: http://{args.host}:{args.port}/docs")
    print()
    print("  端点列表:")
    print(f"    GET  /health                     健康检查")
    print(f"    GET  /api/monitor/stream         SSE 实时监控流")
    print(f"    GET  /api/monitor/metrics        REST 监控快照")
    print(f"    GET  /api/audit/logs             审计日志查询")
    print(f"    GET  /api/config/whitelist       白名单配置查询")
    print(f"    PUT  /api/config/whitelist       白名单配置更新")
    print(f"    WS   /ws/chat/{{session_id}}       WebSocket 对话")
    print("=" * 60)

    uvicorn.run(
        "mock_server:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
        log_level="info",
    )