"""ReporterAgent 单元测试

覆盖：
  1. cpu_query 成功报告
  2. memory_query 成功报告
  3. service_status_query 成功报告
  4. log_query 成功报告
  5. root_cause_analysis 完整报告
  6. MCP 失败报告
  7. observations 为空
  8. observation 结构异常
  9. blocked 结果
  10. 敏感信息过滤
  11. 不调用外部执行
  12. unknown intent
"""

import re

import pytest

from app.services.reporter_agent import ReporterAgent, sanitize_text


@pytest.fixture
def agent() -> ReporterAgent:
    return ReporterAgent()


# ═══════════════════════════════════════════════════════════════════
# 成功报告
# ═══════════════════════════════════════════════════════════════════

class TestCpuReport:
    """cpu_query 报告测试"""

    def test_cpu_snapshot_structure(self, agent):
        """兼容 cpu_percent_snapshot 结构"""
        report = agent.generate(
            {"intent": "cpu_query"},
            [{"tool": "sys_info", "params": {"metric": "cpu"}, "ok": True,
              "result": {"cpu": {"cpu_count": 4, "cpu_percent_snapshot": 12.5}}}],
        )
        assert "CPU 使用率" in report
        assert "12.5" in report
        assert "4" in report

    def test_cpu_flat_structure(self, agent):
        """兼容 cpu_percent 扁平结构"""
        report = agent.generate(
            {"intent": "cpu_query"},
            [{"tool": "sys_info", "params": {"metric": "cpu"}, "ok": True,
              "result": {"cpu_percent": 23.5, "load_avg": [0.5, 0.3, 0.2], "cores": 4}}],
        )
        assert "CPU 使用率" in report
        assert "23.5" in report

    def test_cpu_has_sections(self, agent):
        """报告包含四个章节"""
        report = agent.generate(
            {"intent": "cpu_query"},
            [{"tool": "sys_info", "params": {"metric": "cpu"}, "ok": True,
              "result": {"cpu_percent": 50}}],
        )
        for section in ["现象", "证据", "判断", "建议"]:
            assert section in report


class TestMemoryReport:
    """memory_query 报告测试"""

    def test_memory_nested(self, agent):
        report = agent.generate(
            {"intent": "memory_query"},
            [{"tool": "sys_info", "params": {"metric": "memory"}, "ok": True,
              "result": {"memory": {"percent": 45.0, "total_gb": 7.8, "used_gb": 3.5}}}],
        )
        assert "内存" in report
        assert "45.0" in report
        assert "7.8" in report


class TestServiceStatusReport:
    """service_status_query 报告测试"""

    def test_service_active(self, agent):
        report = agent.generate(
            {"intent": "service_status_query", "target_service": "nginx"},
            [{"tool": "service_mgr", "params": {"action": "status", "service": "nginx"},
              "ok": True,
              "result": {"service": "nginx.service", "is_active": True,
                         "parsed": {"active_state": "active (running)"},
                         "exit_code": 0,
                         "output": "● nginx.service - mock"}}],
        )
        assert "nginx" in report
        assert "运行中" in report or "active" in report.lower()


class TestLogReport:
    """log_query 报告测试"""

    def test_log_with_errors(self, agent):
        report = agent.generate(
            {"intent": "log_query", "target_service": "nginx"},
            [{"tool": "log_reader", "params": {"type": "journalctl", "service": "nginx"},
              "ok": True,
              "result": {"service": "nginx", "lines": 3,
                         "logs": ["error: timeout", "warn: slow", "info: ok"]}}],
        )
        assert "nginx" in report
        # 不应无限输出所有日志
        assert len(report) < 3000


class TestRootCauseReport:
    """root_cause_analysis 报告测试"""

    def test_full_root_cause(self, agent):
        observations = [
            {"tool": "log_reader", "params": {"type": "journalctl", "service": "nginx"},
             "ok": True,
             "result": {"logs": ["error: upstream timed out", "error: 502 Bad Gateway", "info: ok"]}},
            {"tool": "sys_info", "params": {"metric": "memory"},
             "ok": True,
             "result": {"memory": {"percent": 85.0}}},
            {"tool": "service_mgr", "params": {"action": "status", "service": "nginx"},
             "ok": True,
             "result": {"service": "nginx.service", "is_active": True,
                        "parsed": {"active_state": "active (running)"}}},
        ]
        report = agent.generate(
            {"intent": "root_cause_analysis", "target_service": "nginx"},
            observations,
            "分析 nginx 访问慢",
        )
        assert "nginx" in report
        assert "日志" in report or "log" in report.lower()
        assert "内存" in report or "memory" in report.lower()
        assert "服务" in report or "service" in report.lower()
        assert "判断" in report
        assert "建议" in report

    def test_root_cause_partial_failure(self, agent):
        """log 成功但内存失败 → 报告中说明该方向无法确认"""
        observations = [
            {"tool": "log_reader", "params": {}, "ok": True,
             "result": {"logs": ["error: timeout"]}},
            {"tool": "sys_info", "params": {"metric": "memory"}, "ok": False,
             "result": None, "error": "timeout"},
            {"tool": "service_mgr", "params": {}, "ok": True,
             "result": {"service": "nginx", "is_active": True}},
        ]
        report = agent.generate(
            {"intent": "root_cause_analysis", "target_service": "nginx"},
            observations,
        )
        assert "无法确认" in report or "失败" in report


# ═══════════════════════════════════════════════════════════════════
# 失败/异常处理
# ═══════════════════════════════════════════════════════════════════

class TestFailureHandling:
    """失败处理测试"""

    def test_mcp_failure_not_pretend_success(self, agent):
        """MCP 失败报告不假装成功"""
        report = agent.generate(
            {"intent": "cpu_query"},
            [{"tool": "sys_info", "params": {"metric": "cpu"},
              "ok": False, "result": None, "error": "MCP 超时"}],
        )
        assert "失败" in report or "无法确认" in report
        assert "成功" not in report

    def test_empty_observations_no_crash(self, agent):
        """空 observations 不崩溃"""
        report = agent.generate({"intent": "cpu_query"}, [])
        assert "诊断报告" in report
        assert "无法确认" in report or "没有可用的" in report

    def test_none_observations(self, agent):
        """None observations 不崩溃"""
        report = agent.generate({"intent": "cpu_query"}, None)
        assert "诊断报告" in report

    def test_malformed_observation(self, agent):
        """结构异常的 observation 不崩溃"""
        report = agent.generate(
            {"intent": "cpu_query"},
            [{"not_ok": True}],
        )
        assert "诊断报告" in report

    def test_mixed_success_failure(self, agent):
        """部分成功部分失败"""
        report = agent.generate(
            {"intent": "root_cause_analysis", "target_service": "nginx"},
            [
                {"tool": "log_reader", "params": {}, "ok": True,
                 "result": {"logs": ["error: timeout"]}},
                {"tool": "sys_info", "params": {}, "ok": False,
                 "result": None, "error": "connection refused"},
            ],
        )
        assert "无法确认" in report


class TestBlockedResult:
    """blocked 结果测试"""

    def test_blocked_command(self, agent):
        report = agent.generate(
            {"intent": "command_execute"},
            [{"tool": "cmd_exec", "params": {"command": "rm -rf /"},
              "ok": False,
              "result": {"blocked": True, "reason": "高危命令被拦截"}}],
        )
        assert "拦截" in report or "blocked" in report.lower()
        # 不假装成功
        assert "成功" not in report


# ═══════════════════════════════════════════════════════════════════
# 敏感信息过滤
# ═══════════════════════════════════════════════════════════════════

class TestSanitize:
    """sanitize_text 函数测试"""

    def test_authorization_filtered(self):
        text = "Authorization: Bearer sk-1234567890abcdefghij"
        cleaned = sanitize_text(text)
        assert "sk-1234567890abcdefghij" not in cleaned
        assert "[TOKEN]" in cleaned

    def test_bearer_token_filtered(self):
        text = '{"headers": {"Authorization": "Bearer abc123xyz"}}'
        cleaned = sanitize_text(text)
        assert "abc123xyz" not in cleaned

    def test_api_key_filtered(self):
        text = "DEEPSEEK_API_KEY=sk-verysecret"
        cleaned = sanitize_text(text)
        assert "sk-verysecret" not in cleaned
        assert "FILTERED" in cleaned

    def test_password_filtered(self):
        text = "password=admin123"
        cleaned = sanitize_text(text)
        assert "admin123" not in cleaned

    def test_token_json_filtered(self):
        text = '{"token": "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0"}'
        cleaned = sanitize_text(text)
        assert "eyJhbGci" not in cleaned

    def test_report_never_contains_sensitive(self, agent):
        """完整报告生成后不含敏感信息"""
        report = agent.generate(
            {"intent": "cpu_query"},
            [{"tool": "sys_info", "params": {"metric": "cpu"}, "ok": True,
              "result": {"cpu_percent": 50,
                         "_sensitive": "Authorization: Bearer sk-secret123",
                         "token": "abc123"}}],
        )
        assert "sk-secret123" not in report
        assert "abc123" not in report

    def test_normal_text_unchanged(self):
        text = "CPU 使用率 50%，内存使用率 45%"
        assert sanitize_text(text) == text


# ═══════════════════════════════════════════════════════════════════
# unknown / 空意图
# ═══════════════════════════════════════════════════════════════════

class TestUnknownIntent:
    """unknown 意图测试"""

    def test_unknown_intent(self, agent):
        report = agent.generate({"intent": "unknown"}, [])
        assert "无法识别" in report or "unknown" in report.lower()

    def test_missing_intent_key(self, agent):
        report = agent.generate({}, [{"tool": "sys_info", "params": {}, "ok": True,
                                       "result": {"cpu_percent": 10}}])
        assert "诊断报告" in report


# ═══════════════════════════════════════════════════════════════════
# 禁止项：不调用外部执行
# ═══════════════════════════════════════════════════════════════════

class TestNoExternalCalls:
    """确认 ReporterAgent 不调用外部执行"""

    def test_no_call_tool_in_source(self):
        import inspect
        import app.services.reporter_agent as ra
        src = inspect.getsource(ra)
        assert "call_tool" not in src

    def test_no_run_tool_in_source(self):
        import inspect
        import app.services.reporter_agent as ra
        src = inspect.getsource(ra)
        assert "run_tool" not in src

    def test_no_subprocess_import(self):
        import app.services.reporter_agent as ra
        src = str(ra.__dict__)
        assert "subprocess" not in src
        assert "os.system" not in src

    def test_no_mcp_agent_harness_strings(self):
        """模块中不含 MCPClient/AgentHarness 精确字符串（测试版的防误判 docstring 检查）"""
        import inspect
        import app.services.reporter_agent as ra
        # 只检查源代码，不检查 __dict__（docstring 会污染）
        src = inspect.getsource(ra)
        # 确保没有导入 MCPClient 或 AgentHarness
        assert "from app.mcp.client import MCPClient" not in src
        assert "from app.services.agent_harness import AgentHarness" not in src
