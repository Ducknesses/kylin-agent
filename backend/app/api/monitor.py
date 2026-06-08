"""监控数据 SSE 接口"""
import asyncio
import json
import logging
from datetime import datetime

from fastapi import APIRouter
from fastapi.responses import StreamingResponse

from app.mcp.client import MCPClient

logger = logging.getLogger(__name__)
router = APIRouter()


async def _metrics_generator():
    """SSE 流式生成系统指标"""
    client = MCPClient()
    while True:
        try:
            # 阶段1：先用 mock 数据，阶段2接入真实 MCP
            # result = await client.get_system_metrics()
            # data = result.get("result", {})
            data = {
                "cpu_percent": 12.5,
                "load_avg": [0.3, 0.2, 0.1],
                "memory_percent": 45.0,
                "disk_percent": 62.0,
                "timestamp": datetime.now().isoformat(),
            }
            yield f"data: {json.dumps(data)}\n\n"
        except Exception as e:
            logger.error(f"[Monitor] 获取指标失败: {e}")
            yield f"data: {json.dumps({'error': str(e)})}\n\n"
        await asyncio.sleep(3)


@router.get("/monitor/stream")
async def monitor_stream():
    """SSE 流式推送系统指标，每 3 秒一次"""
    return StreamingResponse(
        _metrics_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
        },
    )
