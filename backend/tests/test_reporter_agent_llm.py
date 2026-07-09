"""ReporterAgent LLM 单元测试 —— 全部使用 mock，不依赖真实 API Key

覆盖：
  1. LLM_ENABLED=false 时走规则版 fallback
  2. LLM 成功生成报告
  3. LLM 失败 fallback
  4. LLM 返回空内容 fallback
  5. LLM 输出危险命令 fallback
  6. observations 为空直接走规则版
  7. 报告不包含 API Key
"""

import asyncio

import pytest
from unittest.mock import AsyncMock, patch

from app.services.reporter_agent import ReporterAgent
from app.services.llm_client import LLMResponse


@pytest.fixture
def agent() -> ReporterAgent:
    return ReporterAgent()


_CPU_OBS = [
    {"tool": "sys_info", "params": {"metric": "cpu"}, "ok": True,
     "result": {"cpu": {"cpu_count": 4, "cpu_percent_snapshot": 23.5}}}
]

_NGINX_OBS = [
    {"tool": "service_mgr", "params": {"action": "status", "service": "nginx"}, "ok": True,
     "result": {"is_active": True, "active_state": "active (running)"}}
]

_FAILED_OBS = [
    {"tool": "sys_info", "params": {"metric": "cpu"}, "ok": False, "error": "MCP 连接超时"}
]


# ── LLM_ENABLED=false ─────────────────────────────────────────────

def test_llm_disabled_uses_rules(agent, monkeypatch):
    monkeypatch.setattr("config.settings.LLM_ENABLED", False)
    async def _run():
        return await agent.generate_with_llm({"intent": "cpu_query"}, _CPU_OBS, "查看 CPU")
    report = asyncio.run(_run())
    assert "CPU 使用率" in report
    assert "23.5" in report


# ── LLM 成功 ──────────────────────────────────────────────────────

def test_llm_success_cpu_report(agent, monkeypatch):
    monkeypatch.setattr("config.settings.LLM_ENABLED", True)
    async def _run():
        mc = AsyncMock()
        mc.chat_simple.return_value = LLMResponse(
            ok=True,
            content="## 诊断报告\n\n### 现象\n查询 CPU 使用率。\n\n### 证据\n- CPU 使用率：23.5%\n- CPU 核心数：4\n\n### 判断\nCPU 使用率处于正常范围。\n\n### 建议\n1. 如使用率持续偏高，考虑扩容\n",
            provider="deepseek", model="deepseek-v4-pro")
        with patch("app.services.reporter_agent.LLMClient", return_value=mc):
            return await agent.generate_with_llm({"intent": "cpu_query"}, _CPU_OBS, "查看 CPU")
    report = asyncio.run(_run())
    assert "诊断报告" in report
    assert "23.5" in report


def test_llm_success_service_report(agent, monkeypatch):
    monkeypatch.setattr("config.settings.LLM_ENABLED", True)
    async def _run():
        mc = AsyncMock()
        mc.chat_simple.return_value = LLMResponse(
            ok=True,
            content="## 诊断报告\n\n### 现象\n查询 nginx 服务状态。\n\n### 证据\n- nginx 运行中\n\n### 判断\nnginx 服务正常。\n\n### 建议\n无需操作。\n",
            provider="deepseek", model="deepseek-v4-pro")
        with patch("app.services.reporter_agent.LLMClient", return_value=mc):
            return await agent.generate_with_llm(
                {"intent": "service_status_query", "target_service": "nginx"}, _NGINX_OBS, "查看 nginx")
    report = asyncio.run(_run())
    assert "诊断报告" in report
    assert "nginx" in report.lower()


# ── LLM 失败 ──────────────────────────────────────────────────────

def test_llm_call_failed_fallback(agent, monkeypatch):
    monkeypatch.setattr("config.settings.LLM_ENABLED", True)
    async def _run():
        mc = AsyncMock()
        mc.chat_simple.return_value = LLMResponse(
            ok=False, content="", error="timeout", provider="deepseek", model="v4", fallback_used=True)
        with patch("app.services.reporter_agent.LLMClient", return_value=mc):
            return await agent.generate_with_llm({"intent": "cpu_query"}, _CPU_OBS, "查看 CPU")
    report = asyncio.run(_run())
    assert "CPU 使用率" in report
    assert "23.5" in report


def test_llm_empty_content_fallback(agent, monkeypatch):
    monkeypatch.setattr("config.settings.LLM_ENABLED", True)
    async def _run():
        mc = AsyncMock()
        mc.chat_simple.return_value = LLMResponse(ok=True, content="   \n  ", provider="deepseek", model="v4")
        with patch("app.services.reporter_agent.LLMClient", return_value=mc):
            return await agent.generate_with_llm({"intent": "cpu_query"}, _CPU_OBS, "查看 CPU")
    report = asyncio.run(_run())
    assert "CPU 使用率" in report


def test_llm_exception_fallback(agent, monkeypatch):
    monkeypatch.setattr("config.settings.LLM_ENABLED", True)
    async def _run():
        mc = AsyncMock()
        mc.chat_simple.side_effect = RuntimeError("boom")
        with patch("app.services.reporter_agent.LLMClient", return_value=mc):
            return await agent.generate_with_llm({"intent": "cpu_query"}, _CPU_OBS, "查看 CPU")
    report = asyncio.run(_run())
    assert "CPU 使用率" in report


# ── 危险命令检测 ──────────────────────────────────────────────────

def test_llm_dangerous_command_fallback(agent, monkeypatch):
    monkeypatch.setattr("config.settings.LLM_ENABLED", True)
    async def _run():
        mc = AsyncMock()
        mc.chat_simple.return_value = LLMResponse(
            ok=True, content="建议执行 rm -rf / 来清理磁盘", provider="deepseek", model="v4")
        with patch("app.services.reporter_agent.LLMClient", return_value=mc):
            return await agent.generate_with_llm({"intent": "disk_query"}, _CPU_OBS, "查看磁盘")
    report = asyncio.run(_run())
    assert "rm -rf" not in report


def test_llm_chmod_777_fallback(agent, monkeypatch):
    monkeypatch.setattr("config.settings.LLM_ENABLED", True)
    async def _run():
        mc = AsyncMock()
        mc.chat_simple.return_value = LLMResponse(
            ok=True, content="请执行 chmod 777 /var/www", provider="deepseek", model="v4")
        with patch("app.services.reporter_agent.LLMClient", return_value=mc):
            return await agent.generate_with_llm({"intent": "cpu_query"}, _CPU_OBS, "查看 CPU")
    report = asyncio.run(_run())
    assert "chmod 777" not in report


# ── observations 为空 ─────────────────────────────────────────────

def test_empty_observations_goes_to_rules(agent, monkeypatch):
    monkeypatch.setattr("config.settings.LLM_ENABLED", True)
    async def _run():
        return await agent.generate_with_llm({"intent": "cpu_query"}, [], "查看 CPU")
    report = asyncio.run(_run())
    assert "诊断报告" in report
    assert "尚无可用观测结果" in report or "无法确认" in report


def test_failed_observations_still_sent(agent, monkeypatch):
    monkeypatch.setattr("config.settings.LLM_ENABLED", True)
    async def _run():
        mc = AsyncMock()
        mc.chat_simple.return_value = LLMResponse(
            ok=True,
            content="## 诊断报告\n\n### 现象\n查询 CPU 失败。\n\n### 证据\n- MCP 连接超时\n\n### 判断\n无法确认。\n\n### 建议\n检查 MCP Server。\n",
            provider="deepseek", model="v4")
        with patch("app.services.reporter_agent.LLMClient", return_value=mc):
            return await agent.generate_with_llm({"intent": "cpu_query"}, _FAILED_OBS, "查看 CPU")
    report = asyncio.run(_run())
    assert "诊断报告" in report


# ── 敏感信息 ──────────────────────────────────────────────────────

def test_report_no_api_key_leak(agent, monkeypatch):
    monkeypatch.setattr("config.settings.LLM_ENABLED", True)
    async def _run():
        mc = AsyncMock()
        mc.chat_simple.return_value = LLMResponse(
            ok=True,
            content="## 诊断报告\n\n### 现象\nAPI Key 是 sk-abc123def456789012345678，请妥善保管。\n\n### 证据\n无\n\n### 判断\n无\n\n### 建议\n无\n",
            provider="deepseek", model="v4")
        with patch("app.services.reporter_agent.LLMClient", return_value=mc):
            return await agent.generate_with_llm({"intent": "cpu_query"}, _CPU_OBS, "查看 CPU")
    report = asyncio.run(_run())
    assert "sk-abc123" not in report


def test_report_no_bearer_token(agent, monkeypatch):
    monkeypatch.setattr("config.settings.LLM_ENABLED", True)
    auth_scheme = "Bearer" + " "
    fake_token = "FAKE-BEARER-TOKEN-FOR-TESTING"
    async def _run():
        mc = AsyncMock()
        mc.chat_simple.return_value = LLMResponse(
            ok=True,
            content="## 诊断报告\n\n### 现象\n请使用 " + auth_scheme + fake_token + " 认证。\n\n### 证据\n无\n\n### 判断\n无\n\n### 建议\n无\n",
            provider="deepseek", model="v4")
        with patch("app.services.reporter_agent.LLMClient", return_value=mc):
            return await agent.generate_with_llm({"intent": "cpu_query"}, _CPU_OBS, "查看 CPU")
    report = asyncio.run(_run())
    assert fake_token not in report


# ── 格式验证 ──────────────────────────────────────────────────────

def test_llm_report_has_expected_structure(agent, monkeypatch):
    monkeypatch.setattr("config.settings.LLM_ENABLED", True)
    async def _run():
        mc = AsyncMock()
        mc.chat_simple.return_value = LLMResponse(
            ok=True,
            content="## 诊断报告\n\n### 现象\nCPU 使用率查询。\n\n### 证据\n- CPU 23.5%\n\n### 判断\n正常。\n\n### 建议\n无需操作。\n",
            provider="deepseek", model="v4")
        with patch("app.services.reporter_agent.LLMClient", return_value=mc):
            return await agent.generate_with_llm({"intent": "cpu_query"}, _CPU_OBS, "查看 CPU")
    report = asyncio.run(_run())
    assert "### 现象" in report
    assert "### 证据" in report
    assert "### 判断" in report
    assert "### 建议" in report
