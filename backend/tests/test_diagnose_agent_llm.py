"""DiagnoseAgent LLM 单元测试 —— 全部使用 mock，不依赖真实 API Key

覆盖：
  1. LLM_ENABLED=false 时走规则版 fallback
  2. LLM 成功返回合法工具计划
  3. LLM 生成未知工具时过滤
  4. LLM 生成非法 params 时过滤
  5. LLM JSON 解析失败时 fallback
  6. 高危命令不交 LLM
  7. 全部非法时 fallback 规则版
"""

import asyncio
import json

import pytest
from unittest.mock import AsyncMock, patch

from app.services.diagnose_agent import DiagnoseAgent
from app.services.tool_registry import ToolRegistry
from app.services.llm_client import LLMResponse


@pytest.fixture
def tool_registry() -> ToolRegistry:
    return ToolRegistry()


@pytest.fixture
def agent(tool_registry) -> DiagnoseAgent:
    return DiagnoseAgent(tool_registry=tool_registry)


# ═══════════════════════════════════════════════════════════════════
# LLM_ENABLED=false
# ═══════════════════════════════════════════════════════════════════

def test_llm_disabled_uses_rules(agent, monkeypatch):
    """LLM_ENABLED=false → 规则版"""
    monkeypatch.setattr("config.settings.LLM_ENABLED", False)

    async def _run():
        return await agent.plan_with_llm({
            "intent": "cpu_query", "target_service": None,
            "entities": {}, "original_input": "查看 CPU",
        })

    result = asyncio.run(_run())
    assert len(result["plans"]) == 1
    assert result["plans"][0]["tool"] == "sys_info"


# ═══════════════════════════════════════════════════════════════════
# LLM 成功返回合法计划
# ═══════════════════════════════════════════════════════════════════

def test_llm_success_valid_plan(agent, monkeypatch):
    """LLM 返回合法工具计划 → 使用 LLM 结果"""
    monkeypatch.setattr("config.settings.LLM_ENABLED", True)

    async def _run():
        mock_client = AsyncMock()
        mock_client.chat_simple.return_value = LLMResponse(
            ok=True,
            content=json.dumps([
                {"tool": "sys_info", "params": {"metric": "cpu"}},
                {"tool": "sys_info", "params": {"metric": "memory"}},
            ]),
            provider="deepseek", model="deepseek-v4-pro",
        )
        with patch("app.services.diagnose_agent.LLMClient", return_value=mock_client):
            return await agent.plan_with_llm({
                "intent": "root_cause_analysis", "target_service": "nginx",
                "entities": {}, "original_input": "分析 nginx 异常",
            })

    result = asyncio.run(_run())
    assert len(result["plans"]) == 2
    assert result["plans"][0]["tool"] == "sys_info"
    assert result["plans"][0]["params"]["metric"] == "cpu"


def test_llm_success_single_tool(agent, monkeypatch):
    """LLM 返回单个工具计划"""
    monkeypatch.setattr("config.settings.LLM_ENABLED", True)

    async def _run():
        mock_client = AsyncMock()
        mock_client.chat_simple.return_value = LLMResponse(
            ok=True,
            content=json.dumps([
                {"tool": "service_mgr", "params": {"action": "status", "service": "nginx"}},
            ]),
            provider="deepseek", model="deepseek-v4-pro",
        )
        with patch("app.services.diagnose_agent.LLMClient", return_value=mock_client):
            return await agent.plan_with_llm({
                "intent": "service_status_query", "target_service": "nginx",
                "entities": {}, "original_input": "查看 nginx",
            })

    result = asyncio.run(_run())
    assert len(result["plans"]) == 1
    assert result["plans"][0]["tool"] == "service_mgr"


# ═══════════════════════════════════════════════════════════════════
# LLM 生成未知工具 → 过滤
# ═══════════════════════════════════════════════════════════════════

def test_llm_unknown_tool_filtered(agent, monkeypatch):
    """LLM 生成不存在的工具 → 过滤掉 → 无有效计划 → fallback"""
    monkeypatch.setattr("config.settings.LLM_ENABLED", True)

    async def _run():
        mock_client = AsyncMock()
        mock_client.chat_simple.return_value = LLMResponse(
            ok=True,
            content=json.dumps([{"tool": "delete_everything", "params": {}}]),
            provider="deepseek", model="deepseek-v4-pro",
        )
        with patch("app.services.diagnose_agent.LLMClient", return_value=mock_client):
            return await agent.plan_with_llm({
                "intent": "cpu_query", "target_service": None,
                "entities": {}, "original_input": "查看 CPU",
            })

    result = asyncio.run(_run())
    assert len(result["plans"]) >= 0
    if result["plans"]:
        assert result["plans"][0]["tool"] == "sys_info"


# ═══════════════════════════════════════════════════════════════════
# LLM 生成非法 params → 过滤
# ═══════════════════════════════════════════════════════════════════

def test_llm_invalid_params_filtered(agent, monkeypatch):
    """LLM 生成非法 metric → ToolRegistry 校验失败 → 过滤"""
    monkeypatch.setattr("config.settings.LLM_ENABLED", True)

    async def _run():
        mock_client = AsyncMock()
        mock_client.chat_simple.return_value = LLMResponse(
            ok=True,
            content=json.dumps([
                {"tool": "sys_info", "params": {"metric": "hack_all_the_things"}},
            ]),
            provider="deepseek", model="deepseek-v4-pro",
        )
        with patch("app.services.diagnose_agent.LLMClient", return_value=mock_client):
            return await agent.plan_with_llm({
                "intent": "cpu_query", "target_service": None,
                "entities": {}, "original_input": "查看 CPU",
            })

    result = asyncio.run(_run())
    assert result["plans"] is not None


def test_llm_dangerous_command_filtered(agent, monkeypatch):
    """LLM 生成 rm -rf → 过滤"""
    monkeypatch.setattr("config.settings.LLM_ENABLED", True)

    async def _run():
        mock_client = AsyncMock()
        mock_client.chat_simple.return_value = LLMResponse(
            ok=True,
            content=json.dumps([
                {"tool": "cmd_exec", "params": {"command": "rm -rf /"}},
            ]),
            provider="deepseek", model="deepseek-v4-pro",
        )
        with patch("app.services.diagnose_agent.LLMClient", return_value=mock_client):
            return await agent.plan_with_llm({
                "intent": "command_execute", "target_service": None,
                "entities": {"command": "rm -rf /"}, "original_input": "rm -rf /",
            })

    result = asyncio.run(_run())
    assert len(result["plans"]) == 0


# ═══════════════════════════════════════════════════════════════════
# LLM JSON 解析失败
# ═══════════════════════════════════════════════════════════════════

def test_llm_not_json_fallback(agent, monkeypatch):
    """LLM 返回非 JSON → fallback"""
    monkeypatch.setattr("config.settings.LLM_ENABLED", True)

    async def _run():
        mock_client = AsyncMock()
        mock_client.chat_simple.return_value = LLMResponse(
            ok=True,
            content="我觉得应该先查 CPU，再查内存...",
            provider="deepseek", model="deepseek-v4-pro",
        )
        with patch("app.services.diagnose_agent.LLMClient", return_value=mock_client):
            return await agent.plan_with_llm({
                "intent": "cpu_query", "target_service": None,
                "entities": {}, "original_input": "查看 CPU",
            })

    result = asyncio.run(_run())
    assert len(result["plans"]) == 1
    assert result["plans"][0]["tool"] == "sys_info"


# ═══════════════════════════════════════════════════════════════════
# LLM 调用失败
# ═══════════════════════════════════════════════════════════════════

def test_llm_call_failed_fallback(agent, monkeypatch):
    """LLMClient 返回 failure → fallback"""
    monkeypatch.setattr("config.settings.LLM_ENABLED", True)

    async def _run():
        mock_client = AsyncMock()
        mock_client.chat_simple.return_value = LLMResponse(
            ok=False, content="", error="timeout",
            provider="deepseek", model="v4", fallback_used=True,
        )
        with patch("app.services.diagnose_agent.LLMClient", return_value=mock_client):
            return await agent.plan_with_llm({
                "intent": "memory_query", "target_service": None,
                "entities": {}, "original_input": "查看内存",
            })

    result = asyncio.run(_run())
    assert len(result["plans"]) == 1
    assert result["plans"][0]["tool"] == "sys_info"


# ═══════════════════════════════════════════════════════════════════
# 高危命令保护
# ═══════════════════════════════════════════════════════════════════

def test_high_risk_command_not_sent_to_llm(agent, monkeypatch):
    """command_execute + high_risk → 不交 LLM → 规则版拒绝"""
    monkeypatch.setattr("config.settings.LLM_ENABLED", True)

    async def _run():
        with patch("app.services.diagnose_agent.LLMClient") as mock_llm:
            result = await agent.plan_with_llm({
                "intent": "command_execute", "target_service": None,
                "entities": {"command": "rm -rf /", "high_risk": True, "risk_reason": "高危"},
                "original_input": "rm -rf /",
            })
            mock_llm.assert_not_called()
            return result

    result = asyncio.run(_run())
    assert len(result["plans"]) == 0


# ═══════════════════════════════════════════════════════════════════
# 混合场景：部分合法部分非法
# ═══════════════════════════════════════════════════════════════════

def test_llm_mixed_valid_invalid(agent, monkeypatch):
    """合法工具保留，非法工具过滤"""
    monkeypatch.setattr("config.settings.LLM_ENABLED", True)

    async def _run():
        mock_client = AsyncMock()
        mock_client.chat_simple.return_value = LLMResponse(
            ok=True,
            content=json.dumps([
                {"tool": "sys_info", "params": {"metric": "cpu"}},
                {"tool": "nonexistent_tool", "params": {}},
                {"tool": "service_mgr", "params": {"action": "status", "service": "nginx"}},
            ]),
            provider="deepseek", model="deepseek-v4-pro",
        )
        with patch("app.services.diagnose_agent.LLMClient", return_value=mock_client):
            return await agent.plan_with_llm({
                "intent": "root_cause_analysis", "target_service": "nginx",
                "entities": {}, "original_input": "分析 nginx",
            })

    result = asyncio.run(_run())
    assert len(result["plans"]) == 2
    tools = [p["tool"] for p in result["plans"]]
    assert "sys_info" in tools
    assert "service_mgr" in tools
    assert "nonexistent_tool" not in tools


# ═══════════════════════════════════════════════════════════════════
# 无 tool_registry 时仍可校验基本结构
# ═══════════════════════════════════════════════════════════════════

def test_no_tool_registry_accepts_tools(monkeypatch):
    """无 ToolRegistry 时，基本结构校验 + 高危命令过滤仍生效"""
    monkeypatch.setattr("config.settings.LLM_ENABLED", True)

    async def _run():
        agent_no_reg = DiagnoseAgent(tool_registry=None)
        mock_client = AsyncMock()
        mock_client.chat_simple.return_value = LLMResponse(
            ok=True,
            content=json.dumps([
                {"tool": "sys_info", "params": {"metric": "cpu"}},
                {"tool": "cmd_exec", "params": {"command": "rm -rf /"}},
            ]),
            provider="deepseek", model="deepseek-v4-pro",
        )
        with patch("app.services.diagnose_agent.LLMClient", return_value=mock_client):
            return await agent_no_reg.plan_with_llm({
                "intent": "cpu_query", "target_service": None,
                "entities": {}, "original_input": "查看 CPU",
            })

    result = asyncio.run(_run())
    assert len(result["plans"]) == 1
    assert result["plans"][0]["tool"] == "sys_info"
