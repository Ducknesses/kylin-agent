"""监控数据 SSE 接口"""
import asyncio
import json
import logging
from datetime import datetime

from fastapi import APIRouter
from fastapi.responses import StreamingResponse

from app.mcp.client import MCPClient
from config import settings

logger = logging.getLogger(__name__)
router = APIRouter()


async def _metrics_generator():
    """SSE 流式生成系统指标"""
    client = MCPClient()
    while True:
        data = {
            "cpu_percent": 12.5,
            "load_avg": [0.3, 0.2, 0.1],
            "memory_percent": 45.0,
            "disk_percent": 62.0,
            "net_in_kbps": 120.0,
            "net_out_kbps": 45.0,
            "timestamp": datetime.now().isoformat(),
        }
        try:
            result = await client.get_system_metrics()
            if result.get("success"):
                mcp_data = result.get("result", {})
                data.update({
                    "cpu_percent": mcp_data.get("cpu_percent", data["cpu_percent"]),
                    "memory_percent": mcp_data.get("memory_percent", data["memory_percent"]),
                    "disk_percent": mcp_data.get("disk_percent", data["disk_percent"]),
                    "load_avg": mcp_data.get("load_avg", data["load_avg"]),
                })
            elif not settings.DEMO_MODE:
                logger.warning(f"[Monitor] MCP 指标获取失败: {result.get('error')}")
        except Exception as e:
            logger.error(f"[Monitor] 获取指标失败: {e}")

        yield f"data: {json.dumps(data)}\n\n"
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
