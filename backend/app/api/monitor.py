"""监控数据接口

正式接口：GET /api/monitor/metrics（API 文档约定）
非正式 SSE 流：/api/monitor/stream（保留用于调试，不作为前端对接契约）
"""
import asyncio
import json
import logging
from datetime import datetime

from fastapi import APIRouter
from fastapi.responses import StreamingResponse

from app.mcp.client import MCPClient

logger = logging.getLogger(__name__)
router = APIRouter()


def build_mock_metrics() -> dict:
    """
    构建 Mock 系统指标数据

    当前为 Day 1 Mock 数据，字段结构已对齐 API 文档。
    后续可切换到 MCPClient.get_system_metrics() 获取真实数据。
    """
    return {
        "cpu": {"percent": 23, "cores": 4},
        "memory": {"percent": 45, "total": "8GB", "used": "3.6GB"},
        "disk": {"percent": 67, "total": "40GB", "used": "26.8GB"},
        "network": {"rx": "1.2MB/s", "tx": "0.8MB/s"},
        "timestamp": datetime.now().isoformat(),
    }


# ── 正式监控接口（API 文档约定） ───────────────────────────────────


@router.get("/monitor/metrics")
async def get_metrics() -> dict:
    """API 文档约定的监控指标接口，当前返回 Mock 数据"""
    return {
        "code": 200,
        "data": build_mock_metrics(),
    }


# ── 非正式 SSE 实时流接口（保留用于调试，不在文档中推荐） ──────────


async def _metrics_generator():
    """SSE 流式生成系统指标，复用 build_mock_metrics()"""
    while True:
        try:
            data = build_mock_metrics()
            yield f"data: {json.dumps(data)}\n\n"
        except Exception as e:
            logger.error(f"[Monitor] 获取指标失败: {e}")
            yield f"data: {json.dumps({'error': str(e)})}\n\n"
        await asyncio.sleep(3)


@router.get("/monitor/stream")
async def monitor_stream():
    """[非正式] SSE 流式推送系统指标，每 3 秒一次。不作为前端文档契约。"""
    return StreamingResponse(
        _metrics_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
        },
    )
