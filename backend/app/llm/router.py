"""LLM 请求路由：意图解析、根因分析调度"""
import json
import logging
from typing import Dict, List

from app.llm.deepseek import chat_with_llm, analyze_root_cause

logger = logging.getLogger(__name__)


async def parse_intent(user_input: str) -> Dict:
    """
    解析用户意图，决定调用哪个 MCP 工具

    返回:
        {"intent": "query|operate|analyze", "tool": "sys_info|service_mgr|log_reader|...", "args": {...}}
    """
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
        logger.warning(f"[LLM Router] 意图解析 JSON 失败， fallback 到 query")
        return {"intent": "query", "tool": "cmd_exec", "args": {"command": user_input}}


async def route_request(user_input: str, context: List[Dict] |None = None) -> Dict:
    """
    总路由：根据输入决定走查询/操作/分析流程

    返回:
        {"action": "tool_call|direct_reply|root_cause", "data": {...}}
    """
    if context is None:
        context=[]
    # 简单启发式：如果输入包含"分析"、"根因"、"为什么"等，走根因分析
    lower = user_input.lower()
    if any(k in lower for k in ["根因", "分析", "为什么", "怎么回事", "crash", "宕机"]):
        return {"action": "root_cause", "data": {"query": user_input}}

    # 否则走意图解析 -> 工具调用
    intent = await parse_intent(user_input)
    return {"action": "tool_call", "data": intent}
