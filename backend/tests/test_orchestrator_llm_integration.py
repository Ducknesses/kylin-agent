"""Orchestrator LLM 集成测试 —— 全部 mock，不依赖真实 API Key

覆盖：
  1. LLM_ENABLED=true → 调用 *_with_llm 方法
  2. LLM_ENABLED=false → 调用规则版方法
  3. tool_call 帧 params 已脱敏
  4. 安全模块未新增 LLM 依赖
"""

import asyncio

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from app.services.orchestrator import Orchestrator
from app.services.agent_context import AgentContext


# ── Fake 依赖 ─────────────────────────────────────────────────────────

class FakeSafetyGuard:
    def analyze_user_input(self, content):
        return {"allowed": True, "risk_level": "low", "reason": "", "requires_confirm": False}

    def analyze_tool_call(self, tool, params, role="viewer"):
        return {"allowed": True, "risk_level": "low", "reason": "", "requires_confirm": False}


class FakeToolRegistry:
    def exists(self, tool_name):
        return True

    def validate_params(self, tool_name, params):
        return {"valid": True, "errors": []}

    def get_tool_names(self):
        return ["sys_info", "service_mgr", "log_reader"]


class FakeMCPClient:
    async def call_tool(self, tool_name, arguments=None):
        return {"ok": True, "result": {"cpu_percent": 23.5}}


class FakeAuditService:
    async def save_context(self, ctx, event_type="chat"):
        pass


# ── 辅助 ───────────────────────────────────────────────────────────────

def _run(async_gen):
    """将 async generator 收集为 list"""
    async def _collect():
        return [item async for item in async_gen]
    return asyncio.run(_collect())


# ═══════════════════════════════════════════════════════════════════
# LLM_ENABLED=true → *_with_llm
# ═══════════════════════════════════════════════════════════════════

def test_llm_enabled_calls_with_llm_methods(monkeypatch):
    """LLM_ENABLED=true 时，Orchestrator 调用 *_with_llm 方法"""
    monkeypatch.setattr("config.settings.LLM_ENABLED", True)

    sg = FakeSafetyGuard()
    tr = FakeToolRegistry()
    mc = FakeMCPClient()
    orch = Orchestrator(sg, tr, mc, audit_service=FakeAuditService())

    # mock agents
    orch.intent_agent.detect_with_llm = AsyncMock(return_value={
        "intent": "cpu_query", "target_service": None, "confidence": 0.9,
        "entities": {}, "original_input": "查看 CPU",
    })
    orch.diagnose_agent.plan_with_llm = AsyncMock(return_value={
        "plans": [{"tool": "sys_info", "params": {"metric": "cpu"}}],
        "reason": "LLM plan", "validation_errors": [],
    })
    orch.reporter_agent.generate_with_llm = AsyncMock(return_value="## LLM 报告")

    frames = _run(orch.handle_chat("s1", "查看 CPU 使用率"))

    orch.intent_agent.detect_with_llm.assert_called_once()
    orch.diagnose_agent.plan_with_llm.assert_called_once()
    orch.reporter_agent.generate_with_llm.assert_called_once()

    # done 帧在最后
    assert frames[-1]["type"] == "done"
    assert frames[-1]["final_response"] == "## LLM 报告"


# ═══════════════════════════════════════════════════════════════════
# LLM_ENABLED=false → 规则版
# ═══════════════════════════════════════════════════════════════════

def test_llm_disabled_calls_rule_methods(monkeypatch):
    """LLM_ENABLED=false 时，Orchestrator 调用规则版方法"""
    monkeypatch.setattr("config.settings.LLM_ENABLED", False)

    sg = FakeSafetyGuard()
    tr = FakeToolRegistry()
    mc = FakeMCPClient()
    orch = Orchestrator(sg, tr, mc, audit_service=FakeAuditService())

    # spy on rule methods
    orch.intent_agent.detect = MagicMock(wraps=orch.intent_agent.detect)
    orch.diagnose_agent.plan = MagicMock(wraps=orch.diagnose_agent.plan)
    orch.reporter_agent.generate = MagicMock(wraps=orch.reporter_agent.generate)

    frames = _run(orch.handle_chat("s1", "查看 CPU 使用率"))

    orch.intent_agent.detect.assert_called_once()
    orch.diagnose_agent.plan.assert_called_once()
    orch.reporter_agent.generate.assert_called_once()

    assert frames[-1]["type"] == "done"


# ═══════════════════════════════════════════════════════════════════
# tool_call params 脱敏
# ═══════════════════════════════════════════════════════════════════

def test_tool_call_params_sanitized(monkeypatch):
    """tool_call 帧中的 params 已脱敏"""
    monkeypatch.setattr("config.settings.LLM_ENABLED", False)

    sg = FakeSafetyGuard()
    tr = FakeToolRegistry()
    mc = FakeMCPClient()
    orch = Orchestrator(sg, tr, mc, audit_service=FakeAuditService())

    orch.intent_agent.detect = MagicMock(return_value={
        "intent": "cpu_query", "target_service": None, "confidence": 0.9,
    })
    orch.diagnose_agent.plan = MagicMock(return_value={
        "plans": [{"tool": "sys_info", "params": {"password": "abc123", "config": "token=abc123"}}],
        "reason": "test", "validation_errors": [],
    })
    orch.reporter_agent.generate = MagicMock(return_value="# test")

    frames = _run(orch.handle_chat("s1", "test"))

    tool_call_frames = [f for f in frames if f["type"] == "tool_call"]
    assert len(tool_call_frames) == 1
    params_str = str(tool_call_frames[0]["params"])
    assert "abc123" not in params_str
    assert "[REDACTED]" in params_str


# ═══════════════════════════════════════════════════════════════════
# 安全模块未新增 LLM 依赖
# ═══════════════════════════════════════════════════════════════════

def test_safety_modules_no_llm_import():
    """SafetyGuard / AgentHarness / ToolRegistry 未导入 LLM"""
    import os
    files = [
        "backend/app/services/safety_guard.py",
        "backend/app/services/agent_harness.py",
        "backend/app/services/tool_registry.py",
    ]
    for f in files:
        with open(f) as fh:
            content = fh.read()
        assert "LLMClient" not in content, f"{f} 不应导入 LLMClient"
        assert "from app.services.llm_client" not in content, f"{f} 不应导入 llm_client"


# ═══════════════════════════════════════════════════════════════════
# provider 默认值
# ═══════════════════════════════════════════════════════════════════

def test_local_llm_provider_default():
    """LOCAL_LLM_PROVIDER 默认为 local_openai_compatible"""
    import os
    # 清除环境变量确保使用默认值
    old = os.environ.pop("LOCAL_LLM_PROVIDER", None)
    try:
        from config import Settings
        s = Settings()
        assert s.LOCAL_LLM_PROVIDER == "local_openai_compatible"
    finally:
        if old is not None:
            os.environ["LOCAL_LLM_PROVIDER"] = old


def test_llm_client_accepts_openai_compatible_alias(monkeypatch):
    """openai_compatible 仍可作为兼容别名"""
    monkeypatch.setattr("config.settings.LLM_PROVIDER", "openai_compatible")
    monkeypatch.setattr("config.settings.LLM_ENABLED", True)
    from app.services.llm_client import LLMClient
    client = LLMClient()
    # 应降级为 local_openai_compatible 处理
    assert client.provider_name in ("local_openai_compatible", "openai_compatible")


# ═══════════════════════════════════════════════════════════════════
# USE_REAL_LLM deprecated — 确认新业务路径不读取
# ═══════════════════════════════════════════════════════════════════

def test_use_real_llm_not_read_by_orchestrator():
    """Orchestrator / LLMClient / Agent 不应读取 USE_REAL_LLM"""
    files = [
        "backend/app/services/orchestrator.py",
        "backend/app/services/llm_client.py",
        "backend/app/services/intent_agent.py",
        "backend/app/services/diagnose_agent.py",
        "backend/app/services/reporter_agent.py",
    ]
    for f in files:
        with open(f) as fh:
            content = fh.read()
        assert "USE_REAL_LLM" not in content, f"{f} 不应读取 USE_REAL_LLM"


def test_use_real_llm_still_in_config_for_compat():
    """USE_REAL_LLM 在 config.py 中保留，但有 deprecated 标记"""
    with open("backend/config.py") as f:
        content = f.read()
    assert "USE_REAL_LLM" in content  # 仍存在
    assert "Deprecated" in content     # 但已标记 deprecated
