"""LLMClient 单元测试 —— 全部使用 mock，不依赖真实 API Key

覆盖：
  1. LLM_ENABLED=false 时返回 fallback
  2. LLM_ENABLED=true 但 API Key 缺失时 fallback
  3. mock provider 成功返回内容
  4. 超时 fallback
  5. provider 抛出异常 fallback
  6. 统一返回结构校验
  7. error 不包含敏感信息
  8. LocalOpenAICompatibleProvider mock
"""

import asyncio
import os

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from app.services.llm_client import (
    LLMClient, LLMResponse, DeepSeekProvider,
    LocalOpenAICompatibleProvider, _sanitize_error, _fail_response,
)


class MockProvider:
    """可控制的 mock provider，用于注入 LLMClient"""
    def __init__(self, response=None, should_raise=None):
        self.response = response
        self.should_raise = should_raise
        self.calls = []

    async def chat(self, messages, model="", temperature=0.2,
                   max_tokens=2048, timeout=20, **kwargs):
        self.calls.append(dict(messages=messages, model=model,
                               temperature=temperature, max_tokens=max_tokens,
                               timeout=timeout))
        if self.should_raise:
            raise self.should_raise
        return self.response or LLMResponse(
            ok=True, content="mock response", provider="mock", model="mock-model")


class TestLLMResponse:
    def test_ok_response_to_dict(self):
        resp = LLMResponse(ok=True, content="hello", provider="deepseek", model="deepseek-v4-pro")
        d = resp.to_dict()
        assert d["ok"] is True
        assert d["content"] == "hello"
        assert d["fallback_used"] is False

    def test_fail_response_to_dict(self):
        resp = LLMResponse(ok=False, content="", error="timeout",
                           provider="deepseek", model="deepseek-v4-pro", fallback_used=True)
        d = resp.to_dict()
        assert d["ok"] is False
        assert d["fallback_used"] is True
        assert d["error"] == "timeout"


# ── LLM_ENABLED=false ─────────────────────────────────────────────

def test_llm_disabled_fallback(monkeypatch):
    monkeypatch.setattr("config.settings.LLM_ENABLED", False)
    async def _run():
        client = LLMClient()
        return await client.chat([{"role": "user", "content": "test"}])
    resp = asyncio.run(_run())
    assert resp.ok is False
    assert resp.fallback_used is True
    assert "未启用" in resp.error


# ── mock provider 成功 ─────────────────────────────────────────────

def test_mock_provider_success(monkeypatch):
    monkeypatch.setattr("config.settings.LLM_ENABLED", True)
    async def _run():
        mock = MockProvider(response=LLMResponse(
            ok=True, content="分析结果：CPU 正常", provider="deepseek", model="deepseek-v4-pro"))
        client = LLMClient(provider=mock)
        return mock, await client.chat([{"role": "user", "content": "查看 CPU"}])
    mock, resp = asyncio.run(_run())
    assert resp.ok is True
    assert "CPU" in resp.content
    assert resp.fallback_used is False
    assert len(mock.calls) == 1


def test_chat_simple(monkeypatch):
    monkeypatch.setattr("config.settings.LLM_ENABLED", True)
    async def _run():
        mock = MockProvider()
        client = LLMClient(provider=mock)
        await client.chat_simple(system_prompt="你是助手", user_prompt="你好")
        return mock
    mock = asyncio.run(_run())
    assert len(mock.calls) == 1
    assert mock.calls[0]["messages"][0]["role"] == "system"
    assert mock.calls[0]["messages"][1]["role"] == "user"


# ── provider 异常 ──────────────────────────────────────────────────

def test_provider_exception_fallback(monkeypatch):
    monkeypatch.setattr("config.settings.LLM_ENABLED", True)
    async def _run():
        mock = MockProvider(should_raise=RuntimeError("boom"))
        client = LLMClient(provider=mock)
        return await client.chat([{"role": "user", "content": "test"}])
    resp = asyncio.run(_run())
    assert resp.ok is False
    assert resp.fallback_used is True
    assert "回退" in resp.error or "异常" in resp.error


def test_provider_returns_failure(monkeypatch):
    monkeypatch.setattr("config.settings.LLM_ENABLED", True)
    async def _run():
        mock = MockProvider(response=LLMResponse(
            ok=False, content="", error="timeout", provider="deepseek", model="v4", fallback_used=True))
        client = LLMClient(provider=mock)
        return await client.chat([{"role": "user", "content": "test"}])
    resp = asyncio.run(_run())
    assert resp.ok is False
    assert resp.fallback_used is True


# ── DeepSeekProvider ───────────────────────────────────────────────

def test_deepseek_provider_no_key(monkeypatch):
    monkeypatch.setattr("config.settings.DEEPSEEK_API_KEY", "")
    async def _run():
        provider = DeepSeekProvider()
        return await provider.chat([{"role": "user", "content": "test"}])
    resp = asyncio.run(_run())
    assert resp.ok is False
    assert resp.fallback_used is True
    assert "未配置" in resp.error


def test_deepseek_provider_success(monkeypatch):
    monkeypatch.setattr("config.settings.DEEPSEEK_API_KEY", "sk-test-key")
    import httpx
    async def _run():
        mr = MagicMock()
        mr.status_code = 200
        mr.json.return_value = {"choices": [{"message": {"content": "CPU 使用率 23%"}}]}
        mr.raise_for_status = MagicMock()
        mc = MagicMock()
        mc.__aenter__ = AsyncMock(return_value=mc)
        mc.__aexit__ = AsyncMock(return_value=None)
        mc.post = AsyncMock(return_value=mr)
        with patch.object(httpx, "AsyncClient", return_value=mc):
            return await DeepSeekProvider().chat([{"role": "user", "content": "test"}])
    resp = asyncio.run(_run())
    assert resp.ok is True
    assert "23%" in resp.content
    assert resp.provider == "deepseek"


def test_deepseek_provider_timeout(monkeypatch):
    monkeypatch.setattr("config.settings.DEEPSEEK_API_KEY", "sk-test-key")
    import httpx
    async def _run():
        with patch.object(httpx, "AsyncClient", side_effect=httpx.TimeoutException("timeout")):
            return await DeepSeekProvider().chat([{"role": "user", "content": "test"}])
    resp = asyncio.run(_run())
    assert resp.ok is False
    assert resp.fallback_used is True
    assert "超时" in resp.error


def test_deepseek_provider_http_error(monkeypatch):
    monkeypatch.setattr("config.settings.DEEPSEEK_API_KEY", "sk-test-key")
    import httpx
    async def _run():
        mr = MagicMock()
        mr.status_code = 500
        mc = MagicMock()
        mc.__aenter__ = AsyncMock(return_value=mc)
        mc.__aexit__ = AsyncMock(return_value=None)
        mc.post = AsyncMock(return_value=mr)
        mr.raise_for_status = MagicMock(
            side_effect=httpx.HTTPStatusError("error", request=MagicMock(), response=mr))
        with patch.object(httpx, "AsyncClient", return_value=mc):
            return await DeepSeekProvider().chat([{"role": "user", "content": "test"}])
    resp = asyncio.run(_run())
    assert resp.ok is False
    assert resp.fallback_used is True
    assert "500" in resp.error


# ── LocalOpenAICompatibleProvider ──────────────────────────────────

def test_local_provider_success(monkeypatch):
    import httpx
    async def _run():
        mr = MagicMock()
        mr.status_code = 200
        mr.json.return_value = {"choices": [{"message": {"content": "local model response"}}]}
        mr.raise_for_status = MagicMock()
        mc = MagicMock()
        mc.__aenter__ = AsyncMock(return_value=mc)
        mc.__aexit__ = AsyncMock(return_value=None)
        mc.post = AsyncMock(return_value=mr)
        with patch.object(httpx, "AsyncClient", return_value=mc):
            p = LocalOpenAICompatibleProvider(base_url="http://localhost:8000/v1", model="local-model")
            return await p.chat([{"role": "user", "content": "test"}])
    resp = asyncio.run(_run())
    assert resp.ok is True
    assert "local model response" in resp.content
    assert resp.provider == "local_openai_compatible"


def test_local_provider_connect_error(monkeypatch):
    import httpx
    async def _run():
        with patch.object(httpx, "AsyncClient", side_effect=httpx.ConnectError("refused")):
            p = LocalOpenAICompatibleProvider(base_url="http://localhost:8000/v1", model="local-model")
            return await p.chat([{"role": "user", "content": "test"}])
    resp = asyncio.run(_run())
    assert resp.ok is False
    assert resp.fallback_used is True
    assert "未就绪" in resp.error


# ── 敏感信息脱敏 ───────────────────────────────────────────────────

class TestSanitizeError:
    def test_sanitize_api_key(self):
        text = "Error: DEEPSEEK_API_KEY=sk-abc123def456789012345678"
        result = _sanitize_error(text)
        assert "sk-" not in result
        assert "[REDACTED]" in result

    def test_sanitize_bearer_token(self):
        fake_token = "fake-auth-token-for-testing"
        auth_scheme = "Bearer" + " "
        text = "Authorization: " + auth_scheme + fake_token
        result = _sanitize_error(text)
        assert "Bearer" not in result
        assert "Authorization" not in result
        assert fake_token not in result
        assert "[REDACTED]" in result

    def test_sanitize_normal_text_unchanged(self):
        text = "正常的错误信息：连接超时"
        result = _sanitize_error(text)
        assert result == text

    def test_fail_response_sanitizes(self):
        resp = _fail_response("API error: api_key=sk-leaked-key-here-12345", "deepseek", "v4")
        assert "sk-" not in resp.error
        assert resp.ok is False
        assert resp.fallback_used is True


# ── 属性 ───────────────────────────────────────────────────────────

class TestLLMClientProperties:
    def test_is_enabled_false(self, monkeypatch):
        monkeypatch.setattr("config.settings.LLM_ENABLED", False)
        assert LLMClient().is_enabled is False

    def test_is_enabled_true(self, monkeypatch):
        monkeypatch.setattr("config.settings.LLM_ENABLED", True)
        assert LLMClient().is_enabled is True

    def test_provider_name_default(self, monkeypatch):
        monkeypatch.setattr("config.settings.LLM_PROVIDER", "deepseek")
        assert LLMClient().provider_name == "deepseek"

    def test_model_name_default(self, monkeypatch):
        monkeypatch.setattr("config.settings.LLM_MODEL", "deepseek-v4-pro")
        assert LLMClient().model_name == "deepseek-v4-pro"
