"""IntentAgent LLM 单元测试 —— 全部使用 mock，不依赖真实 API Key

覆盖：
  1. LLM_ENABLED=false 时走规则版 fallback
  2. LLM 成功返回 JSON 时正常解析
  3. LLM 输出非法 JSON 时 fallback
  4. LLM 输出 intent 不在允许范围时 fallback
  5. LLM 输出 confidence 非法时 fallback
  6. LLM 返回非 JSON 时 fallback
  7. 空输入直接走规则版
  8. LLM 异常时 fallback
  9. 非字符串输入防御
"""

import asyncio
import json

import pytest
from unittest.mock import AsyncMock, patch

from app.services.intent_agent import IntentAgent
from app.services.llm_client import LLMResponse


@pytest.fixture
def agent() -> IntentAgent:
    return IntentAgent()


# ═══════════════════════════════════════════════════════════════════
# LLM_ENABLED=false 时走规则版
# ═══════════════════════════════════════════════════════════════════

def test_llm_disabled_uses_rules(agent, monkeypatch):
    """LLM_ENABLED=false 时，detect_with_llm 直接走规则版"""
    monkeypatch.setattr("config.settings.LLM_ENABLED", False)

    async def _run():
        return await agent.detect_with_llm("查看 CPU 使用率")

    result = asyncio.run(_run())
    assert result["intent"] == "cpu_query"


# ═══════════════════════════════════════════════════════════════════
# LLM 成功返回
# ═══════════════════════════════════════════════════════════════════

def test_llm_success_cpu_query(agent, monkeypatch):
    """LLM 返回合法 JSON → 使用 LLM 结果"""
    monkeypatch.setattr("config.settings.LLM_ENABLED", True)

    async def _run():
        mock_client = AsyncMock()
        mock_client.chat_simple.return_value = LLMResponse(
            ok=True,
            content=json.dumps({
                "intent": "cpu_query", "target_service": None,
                "confidence": 0.95, "entities": {},
            }),
            provider="deepseek", model="deepseek-v4-pro",
        )
        with patch("app.services.intent_agent.LLMClient", return_value=mock_client):
            return await agent.detect_with_llm("查看处理器使用情况")

    result = asyncio.run(_run())
    assert result["intent"] == "cpu_query"
    assert result["confidence"] == 0.95


def test_llm_success_service_query(agent, monkeypatch):
    """LLM 返回含 target_service 的结果"""
    monkeypatch.setattr("config.settings.LLM_ENABLED", True)

    async def _run():
        mock_client = AsyncMock()
        mock_client.chat_simple.return_value = LLMResponse(
            ok=True,
            content=json.dumps({
                "intent": "service_status_query",
                "target_service": "nginx",
                "confidence": 0.88,
                "entities": {"service": "nginx"},
            }),
            provider="deepseek", model="deepseek-v4-pro",
        )
        with patch("app.services.intent_agent.LLMClient", return_value=mock_client):
            return await agent.detect_with_llm("查看 nginx 状态")

    result = asyncio.run(_run())
    assert result["intent"] == "service_status_query"
    assert result["target_service"] == "nginx"
    assert 0.0 <= result["confidence"] <= 1.0


# ═══════════════════════════════════════════════════════════════════
# LLM 输出非法 → fallback
# ═══════════════════════════════════════════════════════════════════

def test_llm_invalid_json_fallback(agent, monkeypatch):
    """LLM 返回非 JSON → fallback 规则版"""
    monkeypatch.setattr("config.settings.LLM_ENABLED", True)

    async def _run():
        mock_client = AsyncMock()
        mock_client.chat_simple.return_value = LLMResponse(
            ok=True,
            content="这不是 JSON，是一段随意的文本回复",
            provider="deepseek", model="deepseek-v4-pro",
        )
        with patch("app.services.intent_agent.LLMClient", return_value=mock_client):
            return await agent.detect_with_llm("查看 CPU 使用率")

    result = asyncio.run(_run())
    assert result["intent"] == "cpu_query"


def test_llm_invalid_intent_fallback(agent, monkeypatch):
    """LLM 返回非法 intent → fallback"""
    monkeypatch.setattr("config.settings.LLM_ENABLED", True)

    async def _run():
        mock_client = AsyncMock()
        mock_client.chat_simple.return_value = LLMResponse(
            ok=True,
            content=json.dumps({
                "intent": "delete_everything",
                "target_service": None, "confidence": 0.99, "entities": {},
            }),
            provider="deepseek", model="deepseek-v4-pro",
        )
        with patch("app.services.intent_agent.LLMClient", return_value=mock_client):
            return await agent.detect_with_llm("查看 CPU")

    result = asyncio.run(_run())
    assert result["intent"] == "cpu_query"


def test_llm_confidence_out_of_range(agent, monkeypatch):
    """confidence 不在 0-1 → fallback"""
    monkeypatch.setattr("config.settings.LLM_ENABLED", True)

    async def _run():
        mock_client = AsyncMock()
        mock_client.chat_simple.return_value = LLMResponse(
            ok=True,
            content=json.dumps({
                "intent": "cpu_query", "confidence": 999.0,
            }),
            provider="deepseek", model="deepseek-v4-pro",
        )
        with patch("app.services.intent_agent.LLMClient", return_value=mock_client):
            return await agent.detect_with_llm("查看 CPU")

    result = asyncio.run(_run())
    assert result["intent"] == "cpu_query"


def test_llm_confidence_negative(agent, monkeypatch):
    """confidence 负数 → fallback"""
    monkeypatch.setattr("config.settings.LLM_ENABLED", True)

    async def _run():
        mock_client = AsyncMock()
        mock_client.chat_simple.return_value = LLMResponse(
            ok=True,
            content=json.dumps({"intent": "cpu_query", "confidence": -0.5}),
            provider="deepseek", model="deepseek-v4-pro",
        )
        with patch("app.services.intent_agent.LLMClient", return_value=mock_client):
            return await agent.detect_with_llm("查看 CPU")

    result = asyncio.run(_run())
    assert result["intent"] == "cpu_query"


def test_llm_missing_intent_field(agent, monkeypatch):
    """JSON 缺少 intent 字段 → fallback"""
    monkeypatch.setattr("config.settings.LLM_ENABLED", True)

    async def _run():
        mock_client = AsyncMock()
        mock_client.chat_simple.return_value = LLMResponse(
            ok=True,
            content=json.dumps({"confidence": 0.5}),
            provider="deepseek", model="deepseek-v4-pro",
        )
        with patch("app.services.intent_agent.LLMClient", return_value=mock_client):
            return await agent.detect_with_llm("查看 CPU")

    result = asyncio.run(_run())
    assert result["intent"] == "cpu_query"


# ═══════════════════════════════════════════════════════════════════
# LLM 调用失败
# ═══════════════════════════════════════════════════════════════════

def test_llm_call_failed_fallback(agent, monkeypatch):
    """LLMClient 返回 failure → fallback"""
    monkeypatch.setattr("config.settings.LLM_ENABLED", True)

    async def _run():
        mock_client = AsyncMock()
        mock_client.chat_simple.return_value = LLMResponse(
            ok=False, content="", error="LLM 调用失败，已回退到规则流程",
            provider="deepseek", model="deepseek-v4-pro", fallback_used=True,
        )
        with patch("app.services.intent_agent.LLMClient", return_value=mock_client):
            return await agent.detect_with_llm("查看内存使用情况")

    result = asyncio.run(_run())
    assert result["intent"] == "memory_query"


def test_llm_exception_fallback(agent, monkeypatch):
    """LLMClient 抛异常 → fallback"""
    monkeypatch.setattr("config.settings.LLM_ENABLED", True)

    async def _run():
        mock_client = AsyncMock()
        mock_client.chat_simple.side_effect = RuntimeError("connection refused")
        with patch("app.services.intent_agent.LLMClient", return_value=mock_client):
            return await agent.detect_with_llm("查看内存")

    result = asyncio.run(_run())
    assert result["intent"] == "memory_query"


# ═══════════════════════════════════════════════════════════════════
# 防御
# ═══════════════════════════════════════════════════════════════════

def test_empty_input_goes_to_rules(agent, monkeypatch):
    """空输入直接走规则版，不调 LLM"""
    monkeypatch.setattr("config.settings.LLM_ENABLED", True)

    async def _run():
        return await agent.detect_with_llm("")

    result = asyncio.run(_run())
    assert result["intent"] == "unknown"
    assert result["confidence"] == 0.0


def test_none_input_goes_to_rules(agent, monkeypatch):
    """None 输入不崩溃"""
    monkeypatch.setattr("config.settings.LLM_ENABLED", True)

    async def _run():
        return await agent.detect_with_llm(None)

    result = asyncio.run(_run())
    assert result["intent"] == "unknown"


def test_llm_markdown_json_block(agent, monkeypatch):
    """LLM 返回 markdown 代码块中的 JSON → 也能解析"""
    monkeypatch.setattr("config.settings.LLM_ENABLED", True)

    async def _run():
        mock_client = AsyncMock()
        mock_client.chat_simple.return_value = LLMResponse(
            ok=True,
            content='```json\n{"intent": "disk_query", "confidence": 0.8, "entities": {}}\n```',
            provider="deepseek", model="deepseek-v4-pro",
        )
        with patch("app.services.intent_agent.LLMClient", return_value=mock_client):
            return await agent.detect_with_llm("查看磁盘")

    result = asyncio.run(_run())
    assert result["intent"] == "disk_query"


def test_result_has_all_fields(agent, monkeypatch):
    """LLM 成功时返回完整结构"""
    monkeypatch.setattr("config.settings.LLM_ENABLED", True)

    async def _run():
        mock_client = AsyncMock()
        mock_client.chat_simple.return_value = LLMResponse(
            ok=True,
            content=json.dumps({
                "intent": "cpu_query", "target_service": None,
                "confidence": 0.9, "entities": {},
            }),
            provider="deepseek", model="deepseek-v4-pro",
        )
        with patch("app.services.intent_agent.LLMClient", return_value=mock_client):
            return await agent.detect_with_llm("查看 CPU")

    result = asyncio.run(_run())
    for key in ("intent", "target_service", "confidence", "entities", "original_input"):
        assert key in result, f"缺少字段: {key}"
