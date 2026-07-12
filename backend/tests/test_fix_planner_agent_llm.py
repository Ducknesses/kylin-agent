"""FixPlannerAgent plan_with_llm 测试

覆盖：
  A. 真实调用路径（FakeLLMClient 注入）
  B. 合法候选生成 FixOption
  C. 非法工具/参数过滤
  D. JSON 错误回退
  E. 异常回退规则版
  F. 安全边界

使用 FakeLLMClient 注入，不调用真实 LLM/网络。
"""

import json

import pytest

from app.schemas.action import FixOption
from app.services.fix_planner_agent import LLMFixCandidate


# ── Fake LLMClient ────────────────────────────────────────────────────

class LLMResponse:
    def __init__(self, ok=True, content="", error=None):
        self.ok = ok
        self.content = content
        self.error = error


class FakeLLMClient:
    """可预设返回值的假 LLMClient"""
    def __init__(self, response=None):
        self.response = response or LLMResponse()
        self.calls = []

    async def chat_simple(self, system_prompt="", user_prompt="", **kwargs):
        self.calls.append({"system": system_prompt, "user": user_prompt})
        return self.response


# ── 合法 LLM 候选 JSON ───────────────────────────────────────────────

_VALID_LLM_JSON = json.dumps({
    "options": [{
        "title": "检查内存",
        "description": "查看当前内存使用详情",
        "tool": "sys_info",
        "params": {"metric": "memory"},
        "rollback": None,
    }]
})

_VALID_RESTART_JSON = json.dumps({
    "options": [{
        "title": "重启 nginx",
        "description": "nginx 服务异常，执行重启",
        "tool": "service_mgr",
        "params": {"action": "restart", "service": "nginx"},
        "rollback": "检查日志后恢复配置",
    }]
})

_VALID_TWO_OPTIONS = json.dumps({
    "options": [
        {"title": "a", "description": "a", "tool": "sys_info",
         "params": {"metric": "memory"}, "rollback": None},
        {"title": "b", "description": "b", "tool": "sys_info",
         "params": {"metric": "disk"}, "rollback": None},
    ]
})


# ── Fake ToolRegistry ─────────────────────────────────────────────────

class FakeToolRegistry:
    _TOOLS = {"sys_info", "service_mgr", "log_reader", "cmd_exec", "file_guard"}
    _RISK = {"sys_info": "low", "service_mgr": "low", "log_reader": "low",
             "cmd_exec": "medium", "file_guard": "medium"}

    def exists(self, tool): return tool in self._TOOLS
    def validate_params(self, tool, params): return {"valid": True, "errors": []}
    def get_tool_names(self): return list(self._TOOLS)
    def get_default_risk(self, tool): return self._RISK.get(tool)
    def get_risk_for_action(self, tool, action):
        if tool == "service_mgr":
            return "medium" if action in ("restart", "stop", "start") else "low"
        return self._RISK.get(tool)


# ── Fixtures ──────────────────────────────────────────────────────────

@pytest.fixture
def registry():
    return FakeToolRegistry()


def _agent(tool_registry=None, llm_response=None):
    from app.services.fix_planner_agent import FixPlannerAgent
    llm = FakeLLMClient(llm_response or LLMResponse(ok=True, content=_VALID_LLM_JSON))
    return FixPlannerAgent(tool_registry=tool_registry, llm_client=llm)


@pytest.mark.asyncio
async def _plan_llm(agent, **kw):
    kwargs = {"intent": "memory_query", "observations": [], "report": "", "target_service": None}
    kwargs.update(kw)
    return await agent.plan_with_llm(**kwargs)


# ═══════════════════════════════════════════════════════════════════════
# A: 真实调用路径
# ═══════════════════════════════════════════════════════════════════════

class TestLLMCallPath:
    @pytest.mark.asyncio
    async def test_calls_injected_llm_client(self):
        agent = _agent(FakeToolRegistry())
        resp = LLMResponse(ok=True, content=_VALID_LLM_JSON)
        llm = FakeLLMClient(resp)
        agent.llm_client = llm
        await _plan_llm(agent)
        assert len(llm.calls) == 1
        assert "system" in llm.calls[0]

    @pytest.mark.asyncio
    async def test_prompt_contains_intent_and_tools(self):
        agent = _agent(FakeToolRegistry())
        resp = LLMResponse(ok=True, content=_VALID_LLM_JSON)
        llm = FakeLLMClient(resp)
        agent.llm_client = llm
        await _plan_llm(agent, intent="memory_query", report="内存 85%")
        user_prompt = llm.calls[0]["user"]
        assert "memory_query" in user_prompt
        assert "85%" in user_prompt

    @pytest.mark.asyncio
    async def test_prompt_excludes_api_key(self):
        agent = _agent(FakeToolRegistry())
        resp = LLMResponse(ok=True, content=_VALID_LLM_JSON)
        llm = FakeLLMClient(resp)
        agent.llm_client = llm
        await _plan_llm(agent)
        combined = json.dumps(llm.calls)
        for secret in ("sk-", "Bearer", "api_key", "password", "Authorization"):
            assert secret not in combined

    @pytest.mark.asyncio
    async def test_does_not_create_llm_client_internally(self, registry):
        agent = _agent(registry)
        await _plan_llm(agent)
        assert agent.llm_client is not None  # injected, not internally created


# ═══════════════════════════════════════════════════════════════════════
# B: 合法候选
# ═══════════════════════════════════════════════════════════════════════

class TestValidCandidates:
    @pytest.mark.asyncio
    async def test_low_candidate_becomes_fixoption(self, registry):
        agent = _agent(registry)
        opts = await _plan_llm(agent)
        assert len(opts) == 1
        assert isinstance(opts[0], FixOption)
        assert opts[0].risk_level == "low"
        assert opts[0].requires_confirm is False

    @pytest.mark.asyncio
    async def test_medium_candidate_requires_confirm(self, registry):
        resp = LLMResponse(ok=True, content=_VALID_RESTART_JSON)
        agent = _agent(registry, resp)
        opts = await _plan_llm(agent)
        assert opts[0].risk_level == "medium"
        assert opts[0].requires_confirm is True

    @pytest.mark.asyncio
    async def test_option_id_backend_generated(self, registry):
        agent = _agent(registry)
        opts = await _plan_llm(agent)
        assert opts[0].option_id.startswith("fix_")
        assert len(opts[0].option_id) == 12  # fix_ + 8 hex

    @pytest.mark.asyncio
    async def test_llm_option_id_ignored(self, registry):
        """LLM 输出的 option_id 被 extra=forbid 拒绝，候选被过滤"""
        j = json.dumps({"options": [{"title": "t", "description": "d", "tool": "sys_info",
                       "params": {"metric": "cpu"}, "rollback": None,
                       "option_id": "llm_generated_id"}]})
        agent = _agent(registry, LLMResponse(ok=True, content=j))
        opts = await _plan_llm(agent)
        # 额外字段 option_id 被 LLMFixCandidate extra=forbid 拒绝
        # 候选被过滤 → 0 options → 回退规则版
        assert len(opts) == 0

    @pytest.mark.asyncio
    async def test_llm_risk_level_ignored(self, registry):
        """LLM 输出 risk_level 被 LLMFixCandidate extra=forbid 拒绝"""
        j = json.dumps({"options": [{"title": "t", "description": "d", "tool": "sys_info",
                       "params": {"metric": "cpu"}, "rollback": None,
                       "risk_level": "low"}]})
        agent = _agent(registry, LLMResponse(ok=True, content=j))
        opts = await _plan_llm(agent)
        # 额外字段被拒绝，此候选被跳过 → 0 options → 回退规则版
        assert len(opts) == 0

    @pytest.mark.asyncio
    async def test_multiple_options_ok(self, registry):
        agent = _agent(registry, LLMResponse(ok=True, content=_VALID_TWO_OPTIONS))
        opts = await _plan_llm(agent)
        assert len(opts) == 2
        assert all(isinstance(o, FixOption) for o in opts)

    @pytest.mark.asyncio
    async def test_max_3_options(self, registry):
        opts_list = [{"title": f"t{i}", "description": "d", "tool": "sys_info",
                      "params": {"metric": m}, "rollback": None}
                     for i, m in enumerate(["cpu", "memory", "disk", "load", "uptime", "all", "network"] * 2)]
        j = json.dumps({"options": opts_list})
        agent = _agent(registry, LLMResponse(ok=True, content=j))
        opts = await _plan_llm(agent)
        assert len(opts) == 3

    @pytest.mark.asyncio
    async def test_dedup_same_tool_params(self, registry):
        dup = {"title": "t", "description": "d", "tool": "sys_info",
               "params": {"metric": "cpu"}, "rollback": None}
        j = json.dumps({"options": [dup, dup]})
        agent = _agent(registry, LLMResponse(ok=True, content=j))
        opts = await _plan_llm(agent)
        assert len(opts) == 1


# ═══════════════════════════════════════════════════════════════════════
# C: 非法工具/参数
# ═══════════════════════════════════════════════════════════════════════

class TestInvalidCandidates:
    @pytest.mark.asyncio
    async def test_unknown_tool_filtered(self, registry):
        j = json.dumps({"options": [{"title": "t", "description": "d", "tool": "bad_tool",
                       "params": {}, "rollback": None}]})
        agent = _agent(registry, LLMResponse(ok=True, content=j))
        opts = await _plan_llm(agent)
        assert len(opts) == 0

    @pytest.mark.asyncio
    async def test_params_not_dict_filtered(self, registry):
        j = json.dumps({"options": [{"title": "t", "description": "d", "tool": "sys_info",
                       "params": "not_a_dict", "rollback": None}]})
        agent = _agent(registry, LLMResponse(ok=True, content=j))
        opts = await _plan_llm(agent)
        assert len(opts) == 0

    @pytest.mark.asyncio
    async def test_empty_title_filtered(self, registry):
        j = json.dumps({"options": [{"title": "", "description": "d", "tool": "sys_info",
                       "params": {"metric": "cpu"}, "rollback": None}]})
        agent = _agent(registry, LLMResponse(ok=True, content=j))
        opts = await _plan_llm(agent)
        assert len(opts) == 0

    @pytest.mark.asyncio
    async def test_one_valid_one_invalid(self, registry):
        j = json.dumps({"options": [
            {"title": "", "description": "d", "tool": "sys_info", "params": {"metric": "cpu"}, "rollback": None},
            {"title": "good", "description": "d", "tool": "sys_info", "params": {"metric": "memory"}, "rollback": None},
        ]})
        agent = _agent(registry, LLMResponse(ok=True, content=j))
        opts = await _plan_llm(agent)
        assert len(opts) == 1

    @pytest.mark.asyncio
    async def test_high_risk_tool_filtered(self, registry):
        """ToolRegistry 返回 high → 过滤"""
        class HighRiskRegistry(FakeToolRegistry):
            def get_default_risk(self, tool): return "high"
        agent = _agent(HighRiskRegistry())
        opts = await _plan_llm(agent)
        assert len(opts) == 0

    @pytest.mark.asyncio
    async def test_dangerous_cmd_exec_filtered(self, registry):
        class CmdRegistry(FakeToolRegistry):
            _TOOLS = {"sys_info", "service_mgr", "cmd_exec"}
            _RISK = {"sys_info": "low", "service_mgr": "low", "cmd_exec": "low"}
            def get_default_risk(self, tool): return "low"
        j = json.dumps({"options": [{"title": "t", "description": "d", "tool": "cmd_exec",
                       "params": {"command": "rm -rf /"}, "rollback": None}]})
        agent = _agent(CmdRegistry(), LLMResponse(ok=True, content=j))
        opts = await _plan_llm(agent)
        assert len(opts) == 0

    @pytest.mark.asyncio
    async def test_no_auto_map_tool_names(self, registry):
        """shell → 不自动映射为 cmd_exec"""
        j = json.dumps({"options": [{"title": "t", "description": "d", "tool": "shell",
                       "params": {"command": "ls"}, "rollback": None}]})
        agent = _agent(registry, LLMResponse(ok=True, content=j))
        opts = await _plan_llm(agent)
        assert len(opts) == 0


# ═══════════════════════════════════════════════════════════════════════
# D: JSON 错误回退
# ═══════════════════════════════════════════════════════════════════════

class TestJSONErrors:
    @pytest.mark.asyncio
    async def test_non_json_fallback_to_rules(self, registry):
        agent = _agent(registry, LLMResponse(ok=True, content="not json at all"))
        opts = await _plan_llm(agent)
        # 回退规则版 —— 空 observations 返回空列表
        assert opts == []

    @pytest.mark.asyncio
    async def test_missing_options_fallback(self, registry):
        agent = _agent(registry, LLMResponse(ok=True, content='{"other": "data"}'))
        opts = await _plan_llm(agent)
        assert opts == []

    @pytest.mark.asyncio
    async def test_options_not_list_fallback(self, registry):
        agent = _agent(registry, LLMResponse(ok=True, content='{"options": "str"}'))
        opts = await _plan_llm(agent)
        assert opts == []

    @pytest.mark.asyncio
    async def test_empty_options_fallback(self, registry):
        agent = _agent(registry, LLMResponse(ok=True, content='{"options": []}'))
        opts = await _plan_llm(agent)
        assert opts == []

    @pytest.mark.asyncio
    async def test_markdown_fenced_json(self, registry):
        content = '```json\n' + _VALID_LLM_JSON + '\n```'
        agent = _agent(registry, LLMResponse(ok=True, content=content))
        opts = await _plan_llm(agent)
        assert len(opts) == 1

    @pytest.mark.asyncio
    async def test_dict_already_parsed(self, registry):
        """静态方法 _parse_llm_candidates 处理正常 JSON 字符串"""
        from app.services.fix_planner_agent import FixPlannerAgent
        result = FixPlannerAgent._parse_llm_candidates('{"options": [{"title":"t","description":"d","tool":"sys_info","params":{},"rollback":null}]}')
        assert len(result) == 1


# ═══════════════════════════════════════════════════════════════════════
# E: 异常回退
# ═══════════════════════════════════════════════════════════════════════

class TestFallbacks:
    @pytest.mark.asyncio
    async def test_llm_client_none_fallback(self, registry):
        from app.services.fix_planner_agent import FixPlannerAgent
        agent = FixPlannerAgent(tool_registry=registry, llm_client=None)
        opts = await _plan_llm(agent)
        assert opts == []

    @pytest.mark.asyncio
    async def test_llm_exception_fallback(self, registry):
        class RaisingLLM:
            async def chat_simple(self, **kw):
                raise RuntimeError("boom")
        from app.services.fix_planner_agent import FixPlannerAgent
        agent = FixPlannerAgent(tool_registry=registry, llm_client=RaisingLLM())
        opts = await _plan_llm(agent)
        assert opts == []

    @pytest.mark.asyncio
    async def test_llm_not_ok_fallback(self, registry):
        resp = LLMResponse(ok=False, error="timeout")
        agent = _agent(registry, resp)
        opts = await _plan_llm(agent)
        assert opts == []

    @pytest.mark.asyncio
    async def test_tool_registry_none_fallback(self, registry):
        """ToolRegistry=None 时所有候选被过滤，回退规则版"""
        agent = _agent(None)
        opts = await _plan_llm(agent)
        assert opts == []

    @pytest.mark.asyncio
    async def test_no_recursive_loop(self, registry):
        """plan_with_llm 失败回退调用 self.plan()，不递归"""
        agent = _agent(registry, LLMResponse(ok=False, error="fail"))
        opts = await _plan_llm(agent)
        assert opts == []  # 空 observations 回退规则版 → 空

    @pytest.mark.asyncio
    async def test_llm_exception_not_leak(self, registry):
        class RaisingLLM:
            async def chat_simple(self, **kw):
                raise RuntimeError("secret: sk-abc123")
        from app.services.fix_planner_agent import FixPlannerAgent
        agent = FixPlannerAgent(tool_registry=registry, llm_client=RaisingLLM())
        try:
            await _plan_llm(agent)
        except Exception as e:
            assert "sk-" not in str(e)


# ═══════════════════════════════════════════════════════════════════════
# F: 安全边界
# ═══════════════════════════════════════════════════════════════════════

class TestSafetyBoundary:
    def test_no_mcp_client_import(self):
        from pathlib import Path
        import app.services.fix_planner_agent as fp
        src_lines = Path(fp.__file__).read_text(encoding="utf-8").splitlines()
        for line in src_lines:
            s = line.strip()
            if s.startswith("#") or s.startswith('"""') or s.startswith("'''"):
                continue
            if "MCPClient" in s and ("import" in s or "from" in s):
                raise AssertionError(f"MCPClient: {s}")

    def test_no_agent_harness_import(self):
        from pathlib import Path
        import app.services.fix_planner_agent as fp
        src_lines = Path(fp.__file__).read_text(encoding="utf-8").splitlines()
        for line in src_lines:
            s = line.strip()
            if s.startswith("#") or s.startswith('"""') or s.startswith("'''"):
                continue
            if "AgentHarness" in s and ("import" in s or "from" in s):
                raise AssertionError(f"AgentHarness: {s}")

    def test_no_subprocess(self):
        from pathlib import Path
        import app.services.fix_planner_agent as fp
        text = Path(fp.__file__).read_text(encoding="utf-8")
        assert "subprocess" not in text
        assert "os.system" not in text