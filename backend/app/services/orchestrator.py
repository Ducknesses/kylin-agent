"""Mock 业务编排器 —— Day2 阶段不接真实 LLM 和 MCP Server

所有工具调用结果均为 Mock 数据，仅用于前端联调和协议验证。
"""
import logging
import uuid
from typing import Any, AsyncIterator

logger = logging.getLogger(__name__)

# Mock 结果数据
_MOCK_CPU_RESULT = {
    "cpu_percent": 23.5,
    "load_avg": [0.52, 0.31, 0.18],
    "cores": 4,
}

_MOCK_NGINX_STATUS_RESULT = {
    "service": "nginx",
    "status": "active",
    "sub_state": "running",
    "uptime": "3 days 12:34:56",
}

_MOCK_NGINX_RESTART_RESULT = {
    "ok": True,
    "message": "mock restart accepted",
}

# 意图识别关键词
_CPU_KEYWORDS = ["CPU", "cpu", "处理器", "使用率"]
_NGINX_STATUS_KEYWORDS = ["nginx", "nginx 状态", "nginx status", "查询 nginx"]
_RESTART_NGINX_KEYWORDS = ["重启 nginx", "restart nginx"]


def _match_any(text: str, keywords: list[str]) -> bool:
    """检查文本是否包含任一关键词"""
    return any(k in text for k in keywords)


async def mock_orchestrate(user_input: str) -> AsyncIterator[dict[str, Any]]:
    """Mock 业务编排，根据用户输入返回流式消息序列

    消息类型: status / tool_call / chunk / done

    识别规则:
      - CPU 相关 → mock sys_info 工具调用
      - 重启 nginx → mock service_mgr restart 工具调用（优先判断，避免被普通 nginx 查询误匹配）
      - nginx 状态 → mock service_mgr status 工具调用
      - 其他 → 通用 mock 回复
    """
    trace_id = str(uuid.uuid4())[:16]

    # ── CPU 查询 ──
    if _match_any(user_input, _CPU_KEYWORDS):
        yield {
            "type": "status",
            "content": "正在查询 CPU 使用率...",
            "trace_id": trace_id,
        }
        yield {
            "type": "tool_call",
            "tool": "sys_info",
            "tool_call_id": f"tc_{str(uuid.uuid4())[:8]}",
            "params": {"metric": "cpu"},
            "result": _MOCK_CPU_RESULT,
            "trace_id": trace_id,
        }
        yield {
            "type": "chunk",
            "content": (
                f"当前 CPU 使用率约为 {_MOCK_CPU_RESULT['cpu_percent']}%，"
                f"系统负载为 {', '.join(str(v) for v in _MOCK_CPU_RESULT['load_avg'])}。"
            ),
            "trace_id": trace_id,
        }
        yield {"type": "done", "trace_id": trace_id}
        return

    # ── 重启 nginx（中危确认后由 chat.py 调用） ──
    # 必须放在 nginx 状态查询之前，避免 "重启 nginx" 被 "nginx" 关键词误匹配为状态查询
    if _match_any(user_input, _RESTART_NGINX_KEYWORDS):
        yield {
            "type": "status",
            "content": "已收到确认，正在模拟重启 nginx...",
            "trace_id": trace_id,
        }
        yield {
            "type": "tool_call",
            "tool": "service_mgr",
            "tool_call_id": f"tc_{str(uuid.uuid4())[:8]}",
            "params": {"action": "restart", "service": "nginx"},
            "result": _MOCK_NGINX_RESTART_RESULT,
            "trace_id": trace_id,
        }
        yield {
            "type": "chunk",
            "content": "已模拟提交 nginx 重启操作。当前仍为 Mock 流程，未调用真实 MCP。",
            "trace_id": trace_id,
        }
        yield {"type": "done", "trace_id": trace_id}
        return

    # ── nginx 状态查询 ──
    if _match_any(user_input, _NGINX_STATUS_KEYWORDS):
        yield {
            "type": "status",
            "content": "正在查询 nginx 服务状态...",
            "trace_id": trace_id,
        }
        yield {
            "type": "tool_call",
            "tool": "service_mgr",
            "tool_call_id": f"tc_{str(uuid.uuid4())[:8]}",
            "params": {"action": "status", "service": "nginx"},
            "result": _MOCK_NGINX_STATUS_RESULT,
            "trace_id": trace_id,
        }
        status_info = _MOCK_NGINX_STATUS_RESULT
        yield {
            "type": "chunk",
            "content": (
                f"nginx 服务当前为 {status_info['status']} ({status_info['sub_state']})，"
                f"已运行约 {status_info['uptime']}。"
            ),
            "trace_id": trace_id,
        }
        yield {"type": "done", "trace_id": trace_id}
        return

    # ── 通用 mock 回复 ──
    yield {
        "type": "status",
        "content": "正在分析意图...",
        "trace_id": trace_id,
    }
    yield {
        "type": "chunk",
        "content": f"已收到您的输入：「{user_input}」。当前为 Mock 模式，暂不支持真实运维操作。",
        "trace_id": trace_id,
    }
    yield {"type": "done", "trace_id": trace_id}


