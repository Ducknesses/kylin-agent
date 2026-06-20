"""DeepSeek API 封装"""
import json
import logging
from typing import AsyncIterator, Dict, List

import httpx

from config import settings

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = (
    "你是一个麒麟操作系统（Kylin OS）智能运维助手。"
    "你严格遵守安全规则，只执行用户授权范围内的操作。"
    "当遇到高危命令时，你会明确拒绝并给出安全提示。"
    "你的回答应该简洁、专业、可操作。"
)


async def chat_with_llm(
    messages: List[Dict[str, str]],
    stream: bool = True,
    model: str = "deepseek-chat",
) -> AsyncIterator[str]:
    """
    调用 DeepSeek API，支持流式返回

    参数:
        messages: 对话历史，格式 [{"role": "user", "content": "..."}, ...]
        stream: 是否流式返回
        model: 模型名称

    返回:
        流式返回时：逐块 yield content
        非流式时：yield 完整 content
    """
    if not settings.DEEPSEEK_API_KEY:
        logger.error("DeepSeek API Key 未配置")
        yield "[错误] 智能分析服务暂不可用，请检查后端环境配置"
        return

    headers = {
        "Authorization": f"Bearer {settings.DEEPSEEK_API_KEY}",
        "Content-Type": "application/json",
    }

    payload = {
        "model": model,
        "messages": [{"role": "system", "content": SYSTEM_PROMPT}] + messages,
        "stream": stream,
        "temperature": 0.3,
    }

    timeout = httpx.Timeout(30.0, connect=10.0)

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            if stream:
                async with client.stream(
                    "POST",
                    f"{settings.DEEPSEEK_BASE_URL}/chat/completions",
                    headers=headers,
                    json=payload,
                ) as response:
                    response.raise_for_status()
                    async for line in response.aiter_lines():
                        if line.startswith("data: "):
                            data = line[6:]
                            if data == "[DONE]":
                                break
                            try:
                                chunk = json.loads(data)
                                delta = chunk["choices"][0]["delta"]
                                if "content" in delta:
                                    yield delta["content"]
                            except (json.JSONDecodeError, KeyError) as e:
                                logger.debug(f"流式解析异常: {e}")
                                continue
            else:
                resp = await client.post(
                    f"{settings.DEEPSEEK_BASE_URL}/chat/completions",
                    headers=headers,
                    json=payload,
                )
                resp.raise_for_status()
                data = resp.json()
                content = data["choices"][0]["message"]["content"]
                yield content

    except httpx.TimeoutException:
        logger.error("DeepSeek API 请求超时")
        yield "[错误] LLM 请求超时，请稍后重试"
    except httpx.HTTPStatusError as e:
        logger.error(f"DeepSeek API HTTP 错误: {e.response.status_code}")
        yield f"[错误] LLM 服务异常 (HTTP {e.response.status_code})"
    except Exception as e:
        logger.exception(f"DeepSeek API 调用异常: {e}")
        yield "[错误] LLM 服务调用失败"


async def analyze_root_cause(logs: str, metrics: str) -> Dict:
    """
    根因分析专用接口

    输入:
        logs: 异常日志文本
        metrics: 系统指标 JSON 字符串

    输出:
        {"phenomenon": "...", "causes": [...], "evidence": "...", "fix": "..."}
    """
    prompt = (
        "你是一名资深系统运维工程师，请根据以下异常日志和系统指标进行根因分析。\n"
        "要求输出严格 JSON 格式，不要包含 markdown 代码块标记。\n"
        "JSON 字段：phenomenon(现象描述), causes(可能原因列表), evidence(证据链), fix(修复建议)\n\n"
        f"异常日志:\n{logs}\n\n"
        f"系统指标:\n{metrics}\n"
    )

    messages = [{"role": "user", "content": prompt}]
    result_text = ""
    async for chunk in chat_with_llm(messages, stream=False):
        result_text += chunk

    try:
        # 尝试提取 JSON
        result_text = result_text.strip()
        if result_text.startswith("```"):
            result_text = result_text.strip("`").strip()
            if result_text.startswith("json"):
                result_text = result_text[4:].strip()
        result = json.loads(result_text)
        return result
    except json.JSONDecodeError:
        logger.error(f"根因分析 JSON 解析失败: {result_text[:200]}")
        return {
            "phenomenon": "解析失败",
            "causes": ["LLM 返回格式不符合预期"],
            "evidence": result_text[:500],
            "fix": "请检查输入数据格式",
        }
