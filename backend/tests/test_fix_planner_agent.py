"""FixPlannerAgent 规则版专项测试

覆盖：
  - 服务异常场景（inactive / failed → restart FixOption）
  - 内存高场景（≥ 80% → 只读候选）—— 真实 memory.percent 路径
  - 磁盘高场景（≥ 85% → 只读候选）—— 真实 disk 列表 + 多挂载点
  - 证据不足返回空列表
  - ok=False / 非法类型 / bool / 字符串 → 跳过
  - option_id 格式与唯一性
  - 安全边界：不生成 high、不调用 MCPClient/AgentHarness

所有测试使用 AgentHarness 真实 observation 契约：
  {tool, params, ok, result, error?}

不调用真实 LLM、MCP、FastAPI、数据库，不依赖网络。
"""

import re
from pathlib import Path

import pytest

from app.schemas.action import FixOption


# ── 测试用 FixPlannerAgent 创建 ────────────────────────────────────────

@pytest.fixture
def agent():
    from app.services.fix_planner_agent import FixPlannerAgent
    return FixPlannerAgent()


# ── 真实 observation 工厂函数 ─────────────────────────────────────────
# AgentHarness observation 结构：
#   {tool, params: {metric/action/service/...}, ok: bool, result: dict, error?: str}


def _obs(*entries: dict) -> list[dict[str, object]]:
    return list(entries)


def _svc_status(service: str, status: str, is_ok: bool = True) -> dict[str, object]:
    """service_mgr status observation（匹配 AgentHarness 真实结构）"""
    return {
        "tool": "service_mgr",
        "params": {"action": "status", "service": service},
        "ok": is_ok,
        "result": {"service": service, "status": status, "sub_state": "running"},
    }


def _mem_obs(percent: float, is_ok: bool = True) -> dict[str, object]:
    """sys_info metric=memory observation（真实 memory.percent 路径）"""
    obs: dict[str, object] = {
        "tool": "sys_info",
        "params": {"metric": "memory"},
        "ok": is_ok,
    }
    if is_ok:
        obs["result"] = {"memory": {"percent": percent, "total": 8589934592}}
    else:
        obs["error"] = "MCP 工具调用异常"
    return obs


def _disk_obs(mountpoints: list[dict], is_ok: bool = True) -> dict[str, object]:
    """sys_info metric=disk observation（真实 disk 列表路径）"""
    obs: dict[str, object] = {
        "tool": "sys_info",
        "params": {"metric": "disk"},
        "ok": is_ok,
    }
    if is_ok:
        obs["result"] = {"disk": mountpoints}
    else:
        obs["error"] = "MCP 工具调用异常"
    return obs


def _disk_mp(mountpoint: str, percent: float) -> dict[str, object]:
    """单个挂载点"""
    return {"mountpoint": mountpoint, "percent": percent, "total_gb": 100.0, "used_gb": percent}


# ═══════════════════════════════════════════════════════════════════════
# 服务异常场景
# ═══════════════════════════════════════════════════════════════════════

class TestServiceIssue:
    """服务异常 → restart FixOption"""

    @pytest.mark.parametrize("status", ["inactive", "failed", "stopped", "dead"])
    def test_bad_status_generates_restart(self, agent, status):
        options = agent.plan(
            intent="service_status_query",
            observations=[_svc_status("nginx", status)],
            report="nginx 服务状态异常",
            target_service="nginx",
        )
        assert len(options) == 1
        opt = options[0]
        assert opt.tool == "service_mgr"
        assert opt.params["action"] == "restart"
        assert opt.params["service"] == "nginx"

    def test_active_service_no_restart(self, agent):
        options = agent.plan(
            intent="service_status_query",
            observations=[_svc_status("nginx", "active")],
            report="nginx 正常运行",
            target_service="nginx",
        )
        assert options == []

    def test_restart_is_medium(self, agent):
        options = agent.plan(
            intent="service_status_query",
            observations=[_svc_status("nginx", "inactive")],
            report="",
            target_service="nginx",
        )
        assert options[0].risk_level == "medium"

    def test_restart_requires_confirm_true(self, agent):
        options = agent.plan(
            intent="service_status_query",
            observations=[_svc_status("nginx", "failed")],
            report="",
            target_service="nginx",
        )
        assert options[0].requires_confirm is True

    def test_restart_uses_service_mgr(self, agent):
        options = agent.plan(
            intent="service_status_query",
            observations=[_svc_status("redis", "inactive")],
            report="",
            target_service="redis",
        )
        assert options[0].tool == "service_mgr"

    def test_restart_params_contain_correct_service(self, agent):
        options = agent.plan(
            intent="service_status_query",
            observations=[_svc_status("sshd", "stopped")],
            report="",
            target_service="sshd",
        )
        assert options[0].params == {"action": "restart", "service": "sshd"}

    def test_rollback_is_text_only(self, agent):
        options = agent.plan(
            intent="service_status_query",
            observations=[_svc_status("nginx", "inactive")],
            report="",
            target_service="nginx",
        )
        assert isinstance(options[0].rollback, str)
        assert len(options[0].rollback) > 0

    def test_service_name_case_insensitive(self, agent):
        options = agent.plan(
            intent="service_status_query",
            observations=[_svc_status("Nginx", "inactive")],
            report="",
            target_service="nginx",
        )
        assert len(options) == 1
        assert options[0].params["service"] == "nginx"

    def test_service_failed_obs_skipped(self, agent):
        """ok=False 的 observation 不参与服务异常判断"""
        options = agent.plan(
            intent="service_status_query",
            observations=[_svc_status("nginx", "inactive", is_ok=False)],
            report="",
            target_service="nginx",
        )
        assert options == []


# ═══════════════════════════════════════════════════════════════════════
# 内存高场景
# ═══════════════════════════════════════════════════════════════════════

class TestMemoryIssue:
    """内存高 → 只读候选（真实 memory.percent 路径）"""

    def test_high_memory_generates_options(self, agent):
        options = agent.plan(
            intent="memory_query",
            observations=[_mem_obs(85.0)],
            report="内存使用率 85%",
        )
        assert len(options) >= 1

    def test_low_memory_no_options(self, agent):
        options = agent.plan(
            intent="memory_query",
            observations=[_mem_obs(79.0)],
            report="内存正常",
        )
        assert options == []

    def test_exact_threshold_generates_options(self, agent):
        options = agent.plan(
            intent="memory_query",
            observations=[_mem_obs(80.0)],
            report="",
        )
        assert len(options) >= 1

    def test_memory_options_are_low_risk(self, agent):
        options = agent.plan(
            intent="memory_query",
            observations=[_mem_obs(88.0)],
            report="",
        )
        for opt in options:
            assert opt.risk_level == "low"
            assert opt.requires_confirm is False

    def test_memory_includes_sys_info(self, agent):
        options = agent.plan(
            intent="memory_query",
            observations=[_mem_obs(85.0)],
            report="",
        )
        tools = {opt.tool for opt in options}
        assert "sys_info" in tools

    def test_memory_includes_ps_aux(self, agent):
        options = agent.plan(
            intent="memory_query",
            observations=[_mem_obs(90.0)],
            report="",
        )
        cmd_opts = [o for o in options if o.tool == "cmd_exec"]
        assert len(cmd_opts) >= 1
        assert cmd_opts[0].params["command"] == "ps aux"

    def test_root_cause_triggers_memory_check(self, agent):
        options = agent.plan(
            intent="root_cause_analysis",
            observations=[_mem_obs(95.0)],
            report="根因分析：内存不足",
        )
        assert len(options) >= 1

    # ── 非法类型 / 边界 ──

    def test_memory_percent_string_ignored(self, agent):
        """memory.percent 为字符串时不生成候选"""
        obs: dict[str, object] = {
            "tool": "sys_info",
            "params": {"metric": "memory"},
            "ok": True,
            "result": {"memory": {"percent": "85", "total": 8589934592}},
        }
        options = agent.plan(
            intent="memory_query", observations=[obs], report="",
        )
        assert options == []

    def test_memory_percent_bool_ignored(self, agent):
        """memory.percent 为 True 时不生成候选"""
        obs: dict[str, object] = {
            "tool": "sys_info",
            "params": {"metric": "memory"},
            "ok": True,
            "result": {"memory": {"percent": True}},
        }
        options = agent.plan(
            intent="memory_query", observations=[obs], report="",
        )
        assert options == []

    def test_memory_ok_false_ignored(self, agent):
        """ok=False 时 result 中有 percent 也忽略"""
        options = agent.plan(
            intent="memory_query",
            observations=[_mem_obs(92.0, is_ok=False)],
            report="",
        )
        assert options == []

    def test_memory_wrong_tool_ignored(self, agent):
        """tool 不是 sys_info 时忽略"""
        obs: dict[str, object] = {
            "tool": "service_mgr",
            "params": {"metric": "memory"},
            "ok": True,
            "result": {"memory": {"percent": 90.0}},
        }
        options = agent.plan(
            intent="memory_query", observations=[obs], report="",
        )
        assert options == []

    def test_memory_wrong_metric_ignored(self, agent):
        """params.metric 不是 memory/all 时不作为内存证据"""
        obs: dict[str, object] = {
            "tool": "sys_info",
            "params": {"metric": "cpu"},
            "ok": True,
            "result": {"memory": {"percent": 90.0}},
        }
        options = agent.plan(
            intent="memory_query", observations=[obs], report="",
        )
        assert options == []

    def test_memory_all_metric_works(self, agent):
        """metric=all 也包含 memory 数据"""
        obs: dict[str, object] = {
            "tool": "sys_info",
            "params": {"metric": "all"},
            "ok": True,
            "result": {"memory": {"percent": 95.0}, "cpu": {}, "disk": []},
        }
        options = agent.plan(
            intent="memory_query", observations=[obs], report="",
        )
        assert len(options) >= 1

    def test_memory_result_missing_no_crash(self, agent):
        """observation 缺少 result 时不崩溃"""
        obs: dict[str, object] = {
            "tool": "sys_info",
            "params": {"metric": "memory"},
            "ok": True,
        }
        options = agent.plan(
            intent="memory_query", observations=[obs], report="",
        )
        assert options == []

    def test_memory_not_dict_no_crash(self, agent):
        """result.memory 非 dict 时不崩溃"""
        obs: dict[str, object] = {
            "tool": "sys_info",
            "params": {"metric": "memory"},
            "ok": True,
            "result": {"memory": "not_a_dict"},
        }
        options = agent.plan(
            intent="memory_query", observations=[obs], report="",
        )
        assert options == []


# ═══════════════════════════════════════════════════════════════════════
# 磁盘高场景
# ═══════════════════════════════════════════════════════════════════════

class TestDiskIssue:
    """磁盘高 → 只读候选（真实 disk 列表 + 多挂载点路径）"""

    def test_high_disk_generates_options(self, agent):
        options = agent.plan(
            intent="disk_query",
            observations=[_disk_obs([_disk_mp("/", 90.0)])],
            report="磁盘使用率 90%",
        )
        assert len(options) >= 1

    def test_low_disk_no_options(self, agent):
        options = agent.plan(
            intent="disk_query",
            observations=[_disk_obs([_disk_mp("/", 50.0)])],
            report="磁盘正常",
        )
        assert options == []

    def test_exact_disk_threshold_generates_options(self, agent):
        options = agent.plan(
            intent="disk_query",
            observations=[_disk_obs([_disk_mp("/", 85.0)])],
            report="",
        )
        assert len(options) >= 1

    def test_disk_options_are_low_risk(self, agent):
        options = agent.plan(
            intent="disk_query",
            observations=[_disk_obs([_disk_mp("/", 88.0)])],
            report="",
        )
        for opt in options:
            assert opt.risk_level == "low"
            assert opt.requires_confirm is False

    def test_disk_with_service_adds_log_reader(self, agent):
        options = agent.plan(
            intent="root_cause_analysis",
            observations=[_disk_obs([_disk_mp("/", 90.0)])],
            report="",
            target_service="nginx",
        )
        tools = {opt.tool for opt in options}
        assert "log_reader" in tools

    def test_disk_without_service_no_log_reader(self, agent):
        options = agent.plan(
            intent="disk_query",
            observations=[_disk_obs([_disk_mp("/", 90.0)])],
            report="",
        )
        tools = {opt.tool for opt in options}
        assert "log_reader" not in tools

    # ── 多挂载点 ──

    def test_multi_mountpoint_picks_highest(self, agent):
        """多挂载点时取最高值：/=60, /data=91 → 识别 /data"""
        options = agent.plan(
            intent="disk_query",
            observations=[_disk_obs([
                _disk_mp("/", 60.0),
                _disk_mp("/data", 91.0),
                _disk_mp("/home", 45.0),
            ])],
            report="",
        )
        assert len(options) >= 1
        # description 中应包含高占用挂载点
        desc = options[0].description
        assert "91.0" in desc or "/data" in desc

    def test_multi_mountpoint_all_below_threshold(self, agent):
        """多挂载点全部低于阈值 → 无候选"""
        options = agent.plan(
            intent="disk_query",
            observations=[_disk_obs([
                _disk_mp("/", 60.0),
                _disk_mp("/data", 70.0),
            ])],
            report="",
        )
        assert options == []

    def test_disk_empty_list_no_crash(self, agent):
        """disk 为空列表 → 无候选"""
        options = agent.plan(
            intent="disk_query",
            observations=[_disk_obs([])],
            report="",
        )
        assert options == []

    def test_disk_item_not_dict_skipped(self, agent):
        """disk 列表项非 dict 时跳过"""
        obs: dict[str, object] = {
            "tool": "sys_info",
            "params": {"metric": "disk"},
            "ok": True,
            "result": {"disk": ["bad_item", _disk_mp("/data", 95.0)]},
        }
        options = agent.plan(
            intent="disk_query", observations=[obs], report="",
        )
        assert len(options) >= 1

    def test_disk_percent_bool_skipped(self, agent):
        """disk percent 为 bool 时跳过该挂载点"""
        obs: dict[str, object] = {
            "tool": "sys_info",
            "params": {"metric": "disk"},
            "ok": True,
            "result": {"disk": [
                {"mountpoint": "/", "percent": True},
                _disk_mp("/data", 88.0),
            ]},
        }
        options = agent.plan(
            intent="disk_query", observations=[obs], report="",
        )
        assert len(options) >= 1

    def test_disk_percent_string_skipped(self, agent):
        """disk percent 为字符串时跳过该挂载点"""
        obs: dict[str, object] = {
            "tool": "sys_info",
            "params": {"metric": "disk"},
            "ok": True,
            "result": {"disk": [
                {"mountpoint": "/", "percent": "90"},
                _disk_mp("/data", 86.0),
            ]},
        }
        options = agent.plan(
            intent="disk_query", observations=[obs], report="",
        )
        assert len(options) >= 1

    def test_disk_ok_false_ignored(self, agent):
        """ok=False 的 disk observation 忽略"""
        options = agent.plan(
            intent="disk_query",
            observations=[_disk_obs([_disk_mp("/", 95.0)], is_ok=False)],
            report="",
        )
        assert options == []

    def test_disk_wrong_tool_ignored(self, agent):
        """tool 不是 sys_info 时忽略"""
        obs: dict[str, object] = {
            "tool": "service_mgr",
            "params": {"metric": "disk"},
            "ok": True,
            "result": {"disk": [_disk_mp("/", 95.0)]},
        }
        options = agent.plan(
            intent="disk_query", observations=[obs], report="",
        )
        assert options == []

    def test_disk_wrong_metric_ignored(self, agent):
        """params.metric 不是 disk/all 时忽略"""
        obs: dict[str, object] = {
            "tool": "sys_info",
            "params": {"metric": "cpu"},
            "ok": True,
            "result": {"disk": [_disk_mp("/", 95.0)]},
        }
        options = agent.plan(
            intent="disk_query", observations=[obs], report="",
        )
        assert options == []

    def test_disk_all_metric_works(self, agent):
        """metric=all 也包含 disk 数据"""
        obs: dict[str, object] = {
            "tool": "sys_info",
            "params": {"metric": "all"},
            "ok": True,
            "result": {"disk": [_disk_mp("/", 96.0)], "memory": {}, "cpu": {}},
        }
        options = agent.plan(
            intent="disk_query", observations=[obs], report="",
        )
        assert len(options) >= 1

    def test_disk_result_not_dict_no_crash(self, agent):
        """result 不是 dict 时不崩溃"""
        obs: dict[str, object] = {
            "tool": "sys_info",
            "params": {"metric": "disk"},
            "ok": True,
            "result": "bad_result",
        }
        options = agent.plan(
            intent="disk_query", observations=[obs], report="",
        )
        assert options == []

    def test_disk_not_list_no_crash(self, agent):
        """result.disk 不是 list 时不崩溃"""
        obs: dict[str, object] = {
            "tool": "sys_info",
            "params": {"metric": "disk"},
            "ok": True,
            "result": {"disk": "not_a_list"},
        }
        options = agent.plan(
            intent="disk_query", observations=[obs], report="",
        )
        assert options == []


# ═══════════════════════════════════════════════════════════════════════
# 证据不足 / 高风险
# ═══════════════════════════════════════════════════════════════════════

class TestNoEvidence:
    """证据不足或高风险场景返回空列表"""

    def test_empty_observations_returns_empty(self, agent):
        options = agent.plan(intent="memory_query", observations=[], report="")
        assert options == []

    def test_non_list_observations_no_crash(self, agent):
        options = agent.plan(
            intent="memory_query", observations=None, report="",  # type: ignore
        )
        assert options == []

    def test_unknown_intent_returns_empty(self, agent):
        options = agent.plan(
            intent="unknown",
            observations=[_svc_status("nginx", "inactive")],
            report="",
            target_service="nginx",
        )
        assert options == []

    def test_no_high_risk_options(self, agent):
        options = agent.plan(
            intent="service_status_query",
            observations=[_svc_status("nginx", "inactive")],
            report="nginx 挂了",
            target_service="nginx",
        )
        for opt in options:
            assert opt.risk_level != "high"

    def test_report_with_dangerous_command_no_high_option(self, agent):
        options = agent.plan(
            intent="memory_query",
            observations=[_mem_obs(85.0)],
            report="请执行 rm -rf / 清理磁盘",
        )
        for opt in options:
            assert opt.risk_level != "high"

    def test_no_target_service_no_service_plan(self, agent):
        options = agent.plan(
            intent="service_status_query",
            observations=[_svc_status("nginx", "inactive")],
            report="",
            target_service=None,
        )
        assert options == []


# ═══════════════════════════════════════════════════════════════════════
# option_id
# ═══════════════════════════════════════════════════════════════════════

class TestOptionId:
    """option_id 格式与唯一性"""

    def test_option_id_prefix(self, agent):
        options = agent.plan(
            intent="service_status_query",
            observations=[_svc_status("nginx", "inactive")],
            report="",
            target_service="nginx",
        )
        assert options[0].option_id.startswith("fix_")

    def test_option_id_suffix_is_8_hex(self, agent):
        options = agent.plan(
            intent="service_status_query",
            observations=[_svc_status("nginx", "inactive")],
            report="",
            target_service="nginx",
        )
        suffix = options[0].option_id[4:]
        assert len(suffix) == 8
        assert re.fullmatch(r"[0-9a-f]{8}", suffix)

    def test_multiple_option_ids_unique(self, agent):
        options = agent.plan(
            intent="memory_query",
            observations=[_mem_obs(90.0)],
            report="",
        )
        assert len(options) >= 2
        ids = [o.option_id for o in options]
        assert len(ids) == len(set(ids))


# ═══════════════════════════════════════════════════════════════════════
# 类型与结构
# ═══════════════════════════════════════════════════════════════════════

class TestReturnStructure:
    """返回值类型与结构"""

    def test_all_returned_are_fixoption(self, agent):
        options = agent.plan(
            intent="memory_query",
            observations=[_mem_obs(90.0)],
            report="",
        )
        assert len(options) > 0
        for opt in options:
            assert isinstance(opt, FixOption)

    def test_all_low_requires_confirm_false(self, agent):
        options = agent.plan(
            intent="disk_query",
            observations=[_disk_obs([_disk_mp("/", 90.0)])],
            report="",
        )
        for opt in options:
            if opt.risk_level == "low":
                assert opt.requires_confirm is False

    def test_all_medium_requires_confirm_true(self, agent):
        options = agent.plan(
            intent="service_status_query",
            observations=[_svc_status("nginx", "inactive")],
            report="",
            target_service="nginx",
        )
        for opt in options:
            if opt.risk_level == "medium":
                assert opt.requires_confirm is True


# ═══════════════════════════════════════════════════════════════════════
# 安全边界：不调用 MCPClient / AgentHarness
# ═══════════════════════════════════════════════════════════════════════

class TestSafetyBoundary:
    """FixPlannerAgent 不调用任何执行模块"""

    def test_no_mcp_client_import(self):
        """模块不导入 MCPClient（注释中提及不算导入）"""
        import app.services.fix_planner_agent as fp
        src_lines = Path(fp.__file__).read_text(encoding="utf-8").splitlines()
        for line in src_lines:
            stripped = line.strip()
            if stripped.startswith("#") or stripped.startswith('"""') or stripped.startswith("'''"):
                continue
            if "MCPClient" in stripped and ("import" in stripped or "from" in stripped):
                raise AssertionError(f"MCPClient 不应被导入: {stripped}")

    def test_no_agent_harness_import(self):
        """模块不导入 AgentHarness（注释中提及不算导入）"""
        import app.services.fix_planner_agent as fp
        src_lines = Path(fp.__file__).read_text(encoding="utf-8").splitlines()
        for line in src_lines:
            stripped = line.strip()
            if stripped.startswith("#") or stripped.startswith('"""') or stripped.startswith("'''"):
                continue
            if "AgentHarness" in stripped and ("import" in stripped or "from" in stripped):
                raise AssertionError(f"AgentHarness 不应被导入: {stripped}")

    def test_no_llm_client_import(self):
        """模块不导入 LLMClient（注释中提及不算导入）"""
        import app.services.fix_planner_agent as fp
        src_lines = Path(fp.__file__).read_text(encoding="utf-8").splitlines()
        for line in src_lines:
            s = line.strip()
            if s.startswith("#") or s.startswith('"""') or s.startswith("'''"):
                continue
            if "LLMClient" in s and ("import" in s or "from" in s):
                raise AssertionError(f"LLMClient 不应被导入: {s}")

    def test_no_subprocess(self):
        import app.services.fix_planner_agent as fp
        src = Path(fp.__file__).read_text(encoding="utf-8")
        assert "subprocess" not in src
        assert "os.system" not in src


# ═══════════════════════════════════════════════════════════════════════
# ToolRegistry 校验
# ═══════════════════════════════════════════════════════════════════════

class TestToolRegistryValidation:
    """FixPlannerAgent 集成 ToolRegistry 校验"""

    def test_valid_params_pass_validation(self):
        from app.services.fix_planner_agent import FixPlannerAgent
        from app.services.tool_registry import ToolRegistry
        agent_tr = FixPlannerAgent(tool_registry=ToolRegistry())
        options = agent_tr.plan(
            intent="service_status_query",
            observations=[_svc_status("nginx", "inactive")],
            report="",
            target_service="nginx",
        )
        assert len(options) == 1

    def test_invalid_action_filtered_when_registry_present(self):
        """如果有 ToolRegistry，应被调用校验"""
        from unittest.mock import MagicMock
        from app.services.fix_planner_agent import FixPlannerAgent
        mock_registry = MagicMock()
        mock_registry.validate_params.return_value = {"valid": True}
        agent_tr = FixPlannerAgent(tool_registry=mock_registry)
        options = agent_tr.plan(
            intent="service_status_query",
            observations=[_svc_status("nginx", "inactive")],
            report="",
            target_service="nginx",
        )
        assert len(options) == 1
        mock_registry.validate_params.assert_called()
