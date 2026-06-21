"""监控数据接口

正式接口（最新前后端 API 统一规范 v1.0）：
  GET /api/monitor/metrics  → 嵌套结构 REST 快照
  GET /api/monitor/stream   → 扁平结构 SSE 实时流
"""
import asyncio
import json
import logging
import os
from datetime import datetime

import psutil
from fastapi import APIRouter
from fastapi.responses import StreamingResponse

logger = logging.getLogger(__name__)
router = APIRouter()

# 用于计算网络速率的上一轮采样值
_prev_net: dict = {"bytes_sent": 0, "bytes_recv": 0, "ts": 0.0}


def _collect_nested_metrics() -> dict:
    """采集系统指标 —— 嵌套结构，用于 /api/monitor/metrics"""
    cpu_pct = round(psutil.cpu_percent(interval=0.1), 1)
    mem = psutil.virtual_memory()
    disk = psutil.disk_usage("/")
    net = psutil.net_io_counters()

    total_gb = round(mem.total / (1024**3), 1)
    used_gb = round(mem.used / (1024**3), 1)
    disk_total_gb = round(disk.total / (1024**3), 1)
    disk_used_gb = round(disk.used / (1024**3), 1)

    # 计算网络速率（两次采样差值，非负保护避免计数器重置导致负数）
    global _prev_net
    now_ts = datetime.now().timestamp()
    rx_kbps, tx_kbps = 0.0, 0.0
    if _prev_net["ts"] > 0:
        elapsed = now_ts - _prev_net["ts"]
        if elapsed > 0:
            rx_delta = max(0, net.bytes_recv - _prev_net["bytes_recv"])
            tx_delta = max(0, net.bytes_sent - _prev_net["bytes_sent"])
            rx_kbps = round(rx_delta / elapsed / 1024, 1)
            tx_kbps = round(tx_delta / elapsed / 1024, 1)
    _prev_net = {"bytes_sent": net.bytes_sent, "bytes_recv": net.bytes_recv, "ts": now_ts}

    return {
        "cpu": {
            "percent": cpu_pct,
            "cores": psutil.cpu_count(logical=True),
        },
        "memory": {
            "percent": round(mem.percent, 1),
            "total_gb": total_gb,
            "used_gb": used_gb,
        },
        "disk": {
            "percent": round(disk.percent, 1),
            "total_gb": disk_total_gb,
            "used_gb": disk_used_gb,
        },
        "network": {
            "rx_kbps": rx_kbps,
            "tx_kbps": tx_kbps,
        },
        "timestamp": datetime.now().isoformat(),
    }


def _collect_flat_metrics() -> dict:
    """采集系统指标 —— 扁平结构，用于 /api/monitor/stream SSE"""
    nested = _collect_nested_metrics()
    # 采样 loadavg
    try:
        load_avg = [round(v, 2) for v in os.getloadavg()]
    except (OSError, AttributeError):
        load_avg = [0.0, 0.0, 0.0]

    return {
        "cpu_percent": nested["cpu"]["percent"],
        "load_avg": load_avg,
        "memory_percent": nested["memory"]["percent"],
        "disk_percent": nested["disk"]["percent"],
        "net_in_kbps": nested["network"]["rx_kbps"],
        "net_out_kbps": nested["network"]["tx_kbps"],
        "timestamp": nested["timestamp"],
    }


# ── 正式监控接口（最新规范 v1.0） ───────────────────────────────────


@router.get("/monitor/metrics")
async def get_metrics() -> dict:
    """系统指标 REST 快照 —— 嵌套结构，无 code/data 包装"""
    return _collect_nested_metrics()


@router.get("/monitor/stream")
async def monitor_stream():
    """SSE 实时流 —— 扁平结构，每 3 秒推送一次"""

    async def _generator():
        while True:
            try:
                data = _collect_flat_metrics()
                yield f"data: {json.dumps(data)}\n\n"
            except Exception as e:
                logger.error(f"[Monitor] SSE 采集失败: {e}")
                # fallback 保持与正常数据相同的字段结构，避免前端图表组件缺字段
                fallback = {
                    "cpu_percent": 0.0,
                    "load_avg": [0.0, 0.0, 0.0],
                    "memory_percent": 0.0,
                    "disk_percent": 0.0,
                    "net_in_kbps": 0.0,
                    "net_out_kbps": 0.0,
                    "timestamp": datetime.now().isoformat(),
                    "error": "采集失败",
                }
                yield f"data: {json.dumps(fallback, ensure_ascii=False)}\n\n"
            await asyncio.sleep(3)

    return StreamingResponse(
        _generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
        },
    )
