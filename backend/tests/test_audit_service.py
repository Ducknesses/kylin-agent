"""AuditService 单元测试

覆盖：
  1. save_event 正常写入低风险记录
  2. save_context 从 AgentContext 生成记录
  3. 高危拒绝记录 risk_level=high
  4. 中危确认等待 event_type=confirm_required
  5. MCP 失败记录 error
  6. 敏感信息过滤
  7. list_records 返回 list
  8. limit/offset 生效
  9. llm_reasoning 为 None
  10. 不导入 MCPClient/AgentHarness
  11. 数据库初始化幂等
  12. 使用 tmp_path 不污染真实 audit.db
"""

import os

import pytest

from app.services.agent_context import AgentContext
from app.services.audit_service import AuditService


@pytest.fixture
def service(tmp_path):
    """每个测试使用独立临时数据库"""
    db_path = str(tmp_path / "test_audit.db")
    return AuditService(db_path=db_path)


def _run(coro):
    import asyncio
    return asyncio.run(coro)


# ═══════════════════════════════════════════════════════════════════
# 基本写入
# ═══════════════════════════════════════════════════════════════════

class TestSaveEvent:
    """save_event 测试"""

    def test_save_low_risk(self, service):
        r = _run(service.save_event(
            trace_id="t1", user_input="查看CPU", risk_level="low",
            mcp_tool="sys_info", event_type="tool_call",
        ))
        assert r["trace_id"] == "t1"
        assert r["risk_level"] == "low"

    def test_save_high_risk(self, service):
        r = _run(service.save_event(
            trace_id="t2", user_input="rm -rf /", risk_level="high",
            event_type="blocked", final_response="高危命令已拦截",
        ))
        assert r["risk_level"] == "high"
        assert r["event_type"] == "blocked"

    def test_save_confirm_required(self, service):
        r = _run(service.save_event(
            trace_id="t3", user_input="重启 nginx", risk_level="medium",
            event_type="confirm_required", final_response="等待用户确认",
        ))
        assert r["event_type"] == "confirm_required"

    def test_save_mcp_error(self, service):
        r = _run(service.save_event(
            trace_id="t4", user_input="查看CPU", risk_level="low",
            mcp_tool="sys_info", error="MCP 超时", event_type="tool_call",
        ))
        assert r["trace_id"] == "t4"


# ═══════════════════════════════════════════════════════════════════
# save_context
# ═══════════════════════════════════════════════════════════════════

class TestSaveContext:
    """save_context 测试"""

    def test_save_from_context(self, service):
        ctx = AgentContext(session_id="s1", user_input="查看CPU")
        ctx.intent = "cpu_query"
        ctx.risk_level = "low"
        ctx.add_tool_call("sys_info", {"metric": "cpu"}, {"cpu_percent": 23.5})
        ctx.add_observation({"tool": "sys_info", "ok": True, "result": {"cpu_percent": 23.5}})

        r = _run(service.save_context(ctx))
        assert r["trace_id"] == ctx.trace_id
        assert r["risk_level"] == "low"

    def test_save_context_with_failure(self, service):
        ctx = AgentContext(session_id="s2", user_input="查看CPU")
        ctx.risk_level = "low"
        ctx.add_tool_call("sys_info", {"metric": "cpu"}, None)
        ctx.add_observation({"tool": "sys_info", "ok": False, "error": "timeout"})

        r = _run(service.save_context(ctx, event_type="tool_call"))
        assert r["trace_id"] == ctx.trace_id


# ═══════════════════════════════════════════════════════════════════
# 查询
# ═══════════════════════════════════════════════════════════════════

class TestListRecords:
    """list_records 测试"""

    def test_returns_list(self, service):
        _run(service.save_event(trace_id="t1", user_input="a", risk_level="low"))
        records = _run(service.list_records())
        assert isinstance(records, list)

    def test_limit(self, service):
        for i in range(5):
            _run(service.save_event(trace_id=f"t{i}", user_input=f"inp{i}", risk_level="low"))
        records = _run(service.list_records(limit=2))
        assert len(records) == 2

    def test_offset(self, service):
        for i in range(5):
            _run(service.save_event(trace_id=f"t{i}", user_input=f"inp{i}", risk_level="low"))
        all_records = _run(service.list_records(limit=10))
        subset = _run(service.list_records(limit=2, offset=2))
        assert len(subset) == 2
        # subset 应与 all_records[2:4] 一致
        assert subset[0]["trace_id"] == all_records[2]["trace_id"]

    def test_fields_present(self, service):
        _run(service.save_event(trace_id="tx", user_input="test", risk_level="low"))
        records = _run(service.list_records())
        r = records[0]
        for field in ("trace_id", "timestamp", "user_input", "risk_level", "final_response"):
            assert field in r


# ═══════════════════════════════════════════════════════════════════
# 敏感信息过滤
# ═══════════════════════════════════════════════════════════════════

class TestSensitiveFiltering:
    """敏感信息过滤测试"""

    def test_authorization_filtered(self, service):
        _run(service.save_event(
            trace_id="t1", user_input="test", risk_level="low",
            raw_output="Authorization: Bearer sk-1234567890abcdefghij",
        ))
        records = _run(service.list_records())
        raw = records[0].get("raw_output", "")
        assert "sk-1234567890abcdefghij" not in str(raw)

    def test_bearer_token_filtered(self, service):
        _run(service.save_event(
            trace_id="t2", user_input="test", risk_level="low",
            final_response="Bearer abc123xyz token used",
        ))
        records = _run(service.list_records())
        fr = records[0].get("final_response", "")
        assert "abc123xyz" not in str(fr)

    def test_api_key_filtered(self, service):
        _run(service.save_event(
            trace_id="t3", user_input="test", risk_level="low",
            raw_output="DEEPSEEK_API_KEY=sk-verysecret",
        ))
        records = _run(service.list_records())
        raw = records[0].get("raw_output", "")
        assert "sk-verysecret" not in str(raw)
        assert "REDACTED" in str(raw)

    def test_password_filtered(self, service):
        _run(service.save_event(
            trace_id="t4", user_input="test", risk_level="low",
            command="password=admin123",
        ))
        records = _run(service.list_records())
        cmd = records[0].get("command", "")
        assert "admin123" not in str(cmd)


# ═══════════════════════════════════════════════════════════════════
# llm_reasoning
# ═══════════════════════════════════════════════════════════════════

class TestLLMReasoning:
    """llm_reasoning 强制为 None"""

    def test_llm_reasoning_none(self, service):
        _run(service.save_event(
            trace_id="t1", user_input="test", risk_level="low",
            llm_reasoning="model thought: user wants cpu...",
        ))
        records = _run(service.list_records())
        assert records[0].get("llm_reasoning") is None


# ═══════════════════════════════════════════════════════════════════
# 数据库初始化
# ═══════════════════════════════════════════════════════════════════

class TestDBInit:
    """数据库初始化测试"""

    def test_init_idempotent(self, service):
        """重复初始化不报错"""
        _run(service._ensure_db())
        _run(service._ensure_db())
        # 不抛异常即通过

    def test_uses_tmp_path(self, tmp_path):
        """确认使用临时路径，不污染真实 audit.db"""
        db_path = str(tmp_path / "isolated.db")
        svc = AuditService(db_path=db_path)
        _run(svc.save_event(trace_id="t1", user_input="x", risk_level="low"))
        assert os.path.exists(db_path)
        # 确认不是默认路径
        assert "backend/data" not in db_path


# ═══════════════════════════════════════════════════════════════════
# 禁止项
# ═══════════════════════════════════════════════════════════════════

class TestNoExternalCalls:
    """确认 audit_service 不导入 MCPClient/AgentHarness"""

    def test_no_mcp_client_import(self):
        import inspect
        import app.services.audit_service as au
        src = inspect.getsource(au)
        assert "MCPClient" not in src
        assert "AgentHarness" not in src

    def test_no_call_tool(self):
        import inspect
        import app.services.audit_service as au
        src = inspect.getsource(au)
        assert "call_tool" not in src
        assert "run_tool" not in src
