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
            try:
                result = await client.get_system_metrics()
                raw = result.get("result", {}) if result.get("success") else {}
            except Exception:
                raw = {}

            # 从 MCP 返回的嵌套结构中提取扁平指标
            cpu_data = raw.get("cpu", {})
            mem_data = raw.get("memory", {})
            disk_data = raw.get("disk", [{}])
            load_data = raw.get("load", {})

            disk_pct_str = (disk_data[0] if isinstance(disk_data, list) and disk_data else disk_data).get("percent", "0")
            try:
                disk_pct = float(str(disk_pct_str).replace("%", "").strip())
            except (ValueError, TypeError):
                disk_pct = 0.0

            data = {
                "cpu_percent": cpu_data.get("cpu_percent_snapshot", 0.0),
                "load_avg": load_data.get("load_avg", [0.0, 0.0, 0.0]),
                "memory_percent": mem_data.get("percent", 0.0),
                "disk_percent": disk_pct,
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
