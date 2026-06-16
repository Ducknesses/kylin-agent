"""LLM 请求路由：意图解析、根因分析调度"""
import json
import logging
from typing import Dict, List

from app.llm.deepseek import chat_with_llm, analyze_root_cause
from config import settings

logger = logging.getLogger(__name__)


# 关键词到工具的简单映射，用于 DEMO 模式或 LLM 不可用时的兜底
_KEYWORD_TOOL_MAP = {
    "sys_info": ["cpu", "内存", "mem", "磁盘", "disk", "负载", "load", "系统信息"],
    "service_mgr": ["服务", "systemctl", "启动", "重启", "停止", "service"],
    "log_reader": ["日志", "log", "journalctl", "messages"],
    "net_monitor": ["网络", "网卡", "流量", "ping", "net", "network"],
    "file_guard": ["文件", "读取", "cat", "查看文件"],
}


def _heuristic_intent(user_input: str) -> Dict:
    """基于关键词的意图兜底"""
    lower = user_input.lower()
    for tool, keywords in _KEYWORD_TOOL_MAP.items():
        if any(k in lower for k in keywords):
            args = {}
            if tool == "sys_info":
                if "cpu" in lower or "负载" in lower:
                    args["metric"] = "cpu"
                elif "mem" in lower or "内存" in lower:
                    args["metric"] = "memory"
                elif "disk" in lower or "磁盘" in lower:
                    args["metric"] = "disk"
                else:
                    args["metric"] = "all"
            elif tool == "service_mgr":
                # 简单提取服务名：取最后一个词
                parts = user_input.split()
                args = {"action": "status", "service": parts[-1] if parts else "sshd"}
            elif tool == "log_reader":
                args = {"source": "/var/log/messages", "lines": 20}
            elif tool == "net_monitor":
                args = {"iface": "all"}
            elif tool == "file_guard":
                args = {"path": "/etc/hosts"}
            return {"intent": "query", "tool": tool, "args": args}
    # 默认走 cmd_exec，让 RBAC 决定能否执行
    return {"intent": "query", "tool": "cmd_exec", "args": {"command": user_input}}


async def parse_intent(user_input: str) -> Dict:
    """
    解析用户意图，决定调用哪个 MCP 工具

    返回:
        {"intent": "query|operate|analyze", "tool": "sys_info|service_mgr|log_reader|...", "args": {...}}
    """
    if settings.DEMO_MODE:
        return _heuristic_intent(user_input)

    prompt = (
        "你是一个意图分类器。根据用户输入，判断运维意图和需要调用的工具。\n"
        "可选工具：sys_info(系统信息), service_mgr(服务管理), log_reader(日志读取), "
        "net_monitor(网络监控), cmd_exec(命令执行), file_guard(文件查看)\n"
        "输出严格 JSON：{\"intent\": \"query|operate|analyze\", \"tool\": \"...\", \"args\": {...}}\n"
        "用户输入：" + user_input
    )

    messages = [{"role": "user", "content": prompt}]
    result_text = ""
    async for chunk in chat_with_llm(messages, stream=False):
        result_text += chunk

    try:
        result_text = result_text.strip()
        if result_text.startswith("```"):
            result_text = result_text.strip("`").strip()
            if result_text.startswith("json"):
                result_text = result_text[4:].strip()
        result = json.loads(result_text)
        logger.info(f"[LLM Router] 意图解析: {result.get('intent')} -> {result.get('tool')}")
        return result
    except json.JSONDecodeError:
        logger.warning(f"[LLM Router] 意图解析 JSON 失败， fallback 到关键词规则")
        return _heuristic_intent(user_input)


async def route_request(user_input: str, context: List[Dict] = None) -> Dict:
    """
    总路由：根据输入决定走查询/操作/分析流程

    返回:
        {"action": "tool_call|direct_reply|root_cause", "data": {...}}
    """
    # 简单启发式：如果输入包含"分析"、"根因"、"为什么"等，走根因分析
    lower = user_input.lower()
    if any(k in lower for k in ["根因", "分析", "为什么", "怎么回事", "crash", "宕机"]):
        return {"action": "root_cause", "data": {"query": user_input}}

    # 否则走意图解析 -> 工具调用
    intent = await parse_intent(user_input)
    return {"action": "tool_call", "data": intent}
