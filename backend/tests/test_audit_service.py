"""AuditService 单元测试

覆盖：
  1. save_event 正常写入低风险记录
  2. save_context 从 AgentContext 生成记录
  3. 高危拒绝记录 risk_level=high
  4. 中危确认等待 event_type=confirm_required
  5. MCP 失败记录 error
  6. 敏感信息过滤
  7. list_records 返回 (records, total) 元组
  8. limit/offset 生效
  9. llm_reasoning 为 None
  10. 不导入 MCPClient/AgentHarness
  11. 数据库初始化幂等
  12. 使用 tmp_path 不污染真实 audit.db
  13. 多维筛选（risk_level / action_type / user / role）
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


def _records(service, **kwargs):
    """辅助：调用 list_records 并只返回 records 列表"""
    records, _ = _run(service.list_records(**kwargs))
    return records


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
    """list_records 测试 —— 返回 (records, total) 元组"""

    def test_returns_tuple(self, service):
        _run(service.save_event(trace_id="t1", user_input="a", risk_level="low"))
        records, total = _run(service.list_records())
        assert isinstance(records, list)
        assert isinstance(total, int)
        assert total >= 1

    def test_total_matches_count(self, service):
        for i in range(5):
            _run(service.save_event(trace_id=f"t{i}", user_input=f"inp{i}", risk_level="low"))
        records, total = _run(service.list_records())
        assert len(records) == 5
        assert total == 5

    def test_limit(self, service):
        for i in range(5):
            _run(service.save_event(trace_id=f"t{i}", user_input=f"inp{i}", risk_level="low"))
        records, total = _run(service.list_records(limit=2))
        assert len(records) == 2
        assert total == 5  # total 仍为全部匹配数

    def test_offset(self, service):
        for i in range(5):
            _run(service.save_event(trace_id=f"t{i}", user_input=f"inp{i}", risk_level="low"))
        all_records, _ = _run(service.list_records(limit=10))
        subset, _ = _run(service.list_records(limit=2, offset=2))
        assert len(subset) == 2
        # subset 应与 all_records[2:4] 一致
        assert subset[0]["trace_id"] == all_records[2]["trace_id"]

    def test_fields_present(self, service):
        _run(service.save_event(trace_id="tx", user_input="test", risk_level="low"))
        records, _ = _run(service.list_records())
        r = records[0]
        for field in ("trace_id", "timestamp", "user_input", "risk_level", "final_response"):
            assert field in r

    def test_filter_by_risk_level(self, service):
        _run(service.save_event(trace_id="t1", user_input="a", risk_level="low", event_type="tool_call"))
        _run(service.save_event(trace_id="t2", user_input="b", risk_level="high", event_type="blocked"))
        _run(service.save_event(trace_id="t3", user_input="c", risk_level="low", event_type="chat"))

        records, total = _run(service.list_records(risk_level="high"))
        assert total == 1
        assert records[0]["risk_level"] == "high"

    def test_filter_by_action_type(self, service):
        _run(service.save_event(trace_id="t1", user_input="a", risk_level="low", event_type="tool_call"))
        _run(service.save_event(trace_id="t2", user_input="b", risk_level="low", event_type="chat"))

        records, total = _run(service.list_records(action_type="chat"))
        assert total == 1
        assert records[0]["event_type"] == "chat"

    def test_filter_by_user(self, service):
        _run(service.save_event(trace_id="t1", user_input="查看CPU使用率", risk_level="low"))
        _run(service.save_event(trace_id="t2", user_input="查看内存状态", risk_level="low"))

        records, total = _run(service.list_records(user="CPU"))
        assert total == 1
        assert "CPU" in records[0]["user_input"]

    def test_filter_by_role(self, service):
        _run(service.save_event(trace_id="t1", user_input="a", intent="admin_op", risk_level="low"))
        _run(service.save_event(trace_id="t2", user_input="b", intent="read_query", risk_level="low"))

        records, total = _run(service.list_records(role="admin"))
        assert total == 1
        assert "admin" in records[0]["intent"]


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
        records = _records(service)
        raw = records[0].get("raw_output", "")
        assert "sk-1234567890abcdefghij" not in str(raw)

    def test_bearer_token_filtered(self, service):
        _run(service.save_event(
            trace_id="t2", user_input="test", risk_level="low",
            final_response="Bearer abc123xyz token used",
        ))
        records = _records(service)
        fr = records[0].get("final_response", "")
        assert "abc123xyz" not in str(fr)

    def test_api_key_filtered(self, service):
        _run(service.save_event(
            trace_id="t3", user_input="test", risk_level="low",
            raw_output="DEEPSEEK_API_KEY=sk-verysecret",
        ))
        records = _records(service)
        raw = records[0].get("raw_output", "")
        assert "sk-verysecret" not in str(raw)
        assert "REDACTED" in str(raw)

    def test_password_filtered(self, service):
        _run(service.save_event(
            trace_id="t4", user_input="test", risk_level="low",
            command="password=admin123",
        ))
        records = _records(service)
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
        records = _records(service)
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


# ═══════════════════════════════════════════════════════════════════
# BLOCKER #1: 迁移测试
# ═══════════════════════════════════════════════════════════════════

class TestMigration:
    """扩展字段迁移测试"""

    def test_extended_columns_exist_after_init(self, service):
        """初始化后 PRAGMA table_info 确认扩展字段存在"""
        import aiosqlite
        _run(service._ensure_db())
        cols = _run(_pragma_columns(service.db_path, "audit_chain"))
        col_names = {c["name"] for c in cols}
        for ext in ("session_id", "params", "error", "event_type"):
            assert ext in col_names, f"缺少扩展字段: {ext}"

    def test_extended_columns_written(self, service):
        """save_event 后扩展字段真实写入"""
        _run(service.save_event(
            trace_id="t-ext", user_input="test", risk_level="low",
            session_id="s1", params={"metric": "cpu"}, error="boom",
            event_type="tool_call",
        ))
        records, _ = _run(service.list_records())
        r = records[0]
        assert r.get("session_id") == "s1"
        assert r.get("params") is not None
        assert "metric" in (r.get("params") or "")
        assert r.get("error") == "boom"
        assert r.get("event_type") == "tool_call"

    def test_migration_idempotent(self, service):
        """重复 _ensure_db 不报错"""
        _run(service._ensure_db())
        _run(service._ensure_db())


async def _pragma_columns(db_path, table):
    import aiosqlite
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(f"PRAGMA table_info({table})") as cur:
            return [dict(r) for r in await cur.fetchall()]


# ═══════════════════════════════════════════════════════════════════
# MAJOR #3: 扩展敏感模式测试
# ═══════════════════════════════════════════════════════════════════

class TestExtendedSensitive:
    """扩展敏感信息过滤测试"""

    def test_token_filtered(self, service):
        _run(service.save_event(trace_id="t1", user_input="t", risk_level="low",
                                command="token=abc123"))
        records = _records(service)
        assert "abc123" not in str(records[0].get("command", ""))

    def test_access_token_filtered(self, service):
        _run(service.save_event(trace_id="t2", user_input="t", risk_level="low",
                                raw_output="access_token=abc123"))
        records = _records(service)
        assert "abc123" not in str(records[0].get("raw_output", ""))

    def test_secret_key_filtered(self, service):
        _run(service.save_event(trace_id="t3", user_input="t", risk_level="low",
                                error="secret_key=abc123"))
        records = _records(service)
        assert "abc123" not in str(records[0].get("error", ""))

    def test_jwt_filtered(self, service):
        _run(service.save_event(trace_id="t4", user_input="t", risk_level="low",
                                final_response="eyJabc.def.ghi token leaked"))
        records = _records(service)
        assert "eyJabc.def.ghi" not in str(records[0].get("final_response", ""))

    def test_llm_reasoning_still_none(self, service):
        _run(service.save_event(trace_id="t5", user_input="t", risk_level="low",
                                llm_reasoning="secret_key=abc123"))
        records = _records(service)
        assert records[0].get("llm_reasoning") is None


# ═══════════════════════════════════════════════════════════════════
# PR20: Authorization Bearer Token 脱敏专项测试
# ═══════════════════════════════════════════════════════════════════

class TestAuthorizationSanitize:
    """Authorization Bearer Token 脱敏 —— PR20 安全修复"""

    def test_bearer_token_standard(self, service):
        """标准 Authorization: Bearer <token> 格式被脱敏"""
        _run(service.save_event(
            trace_id="t-auth1", user_input="test", risk_level="low",
            raw_output="Authorization: Bearer sk-test123456",
        ))
        records = _records(service)
        raw = records[0].get("raw_output", "")
        assert "sk-test" not in raw, f"Token leaked: {raw}"
        assert "[REDACTED]" in raw, f"Not replaced: {raw}"

    def test_bearer_token_lowercase(self, service):
        """大小写兼容：authorization: bearer 也脱敏"""
        _run(service.save_event(
            trace_id="t-auth2", user_input="test", risk_level="low",
            final_response="authorization: bearer abc123456",
        ))
        records = _records(service)
        fr = records[0].get("final_response", "")
        assert "abc123" not in fr, f"Token leaked in lowercase: {fr}"

    def test_whitespace_variants(self, service):
        """空格兼容：:Bearer / : Bearer / :  Bearer 均匹配"""
        cases = [
            "Authorization:Bearer tok1",
            "Authorization: Bearer tok2",
            "Authorization:  Bearer tok3",
        ]
        for case in cases:
            _run(service.save_event(
                trace_id="t-auth3", user_input="test", risk_level="low",
                command=case,
            ))
            records = _records(service)
            cmd = records[0].get("command", "")
            assert "tok" not in cmd.split("[")[-1], f"Whitespace variant leaked: {cmd}"

    def test_other_patterns_unaffected(self, service):
        """其他脱敏规则不受影响：api_key / password / token / secret"""
        _run(service.save_event(
            trace_id="t-auth4", user_input="test", risk_level="low",
            raw_output="api_key=mykey password=mypw token=mytok secret=mysec",
        ))
        records = _records(service)
        raw = records[0].get("raw_output", "")
        assert "mykey" not in raw
        assert "mypw" not in raw
        assert "mytok" not in raw
        assert "mysec" not in raw