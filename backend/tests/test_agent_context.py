"""AgentContext 单元测试

覆盖：
  - trace_id 自动生成
  - 默认 role 为 viewer
  - tool_calls / observations 默认为空列表（无可变默认值污染）
  - add_tool_call / add_observation 追加逻辑
  - created_at / updated_at 时间戳
"""

import pytest

from app.services.agent_context import AgentContext


class TestAgentContextDefaults:
    """默认值测试"""

    def test_trace_id_auto_generated(self):
        """trace_id 应在实例化时自动生成"""
        ctx = AgentContext(session_id="s1", user_input="test")
        assert ctx.trace_id is not None
        assert len(ctx.trace_id) == 36  # UUID 标准格式

    def test_trace_id_unique_per_instance(self):
        """每次实例化应生成不同的 trace_id"""
        ctx1 = AgentContext(session_id="s1", user_input="a")
        ctx2 = AgentContext(session_id="s2", user_input="b")
        assert ctx1.trace_id != ctx2.trace_id

    def test_role_default_viewer(self):
        """role 默认应为 viewer（最小权限原则）"""
        ctx = AgentContext(session_id="s1", user_input="test")
        assert ctx.role == "viewer"

    def test_role_can_override(self):
        """role 可通过参数覆盖"""
        ctx = AgentContext(session_id="s1", user_input="test", role="admin")
        assert ctx.role == "admin"

    def test_tool_calls_default_empty(self):
        """tool_calls 默认为空列表"""
        ctx = AgentContext(session_id="s1", user_input="test")
        assert ctx.tool_calls == []
        assert isinstance(ctx.tool_calls, list)

    def test_observations_default_empty(self):
        """observations 默认为空列表"""
        ctx = AgentContext(session_id="s1", user_input="test")
        assert ctx.observations == []
        assert isinstance(ctx.observations, list)

    def test_intent_default_none(self):
        """intent 默认为 None"""
        ctx = AgentContext(session_id="s1", user_input="test")
        assert ctx.intent is None

    def test_risk_level_default_none(self):
        """risk_level 默认为 None"""
        ctx = AgentContext(session_id="s1", user_input="test")
        assert ctx.risk_level is None

    def test_final_response_default_none(self):
        """final_response 默认为 None"""
        ctx = AgentContext(session_id="s1", user_input="test")
        assert ctx.final_response is None

    def test_created_at_auto_generated(self):
        """created_at 应在实例化时自动生成"""
        ctx = AgentContext(session_id="s1", user_input="test")
        assert ctx.created_at is not None
        # ISO 8601 格式
        assert "T" in ctx.created_at

    def test_updated_at_default_none(self):
        """updated_at 默认为 None"""
        ctx = AgentContext(session_id="s1", user_input="test")
        assert ctx.updated_at is None


class TestAgentContextNoMutableDefaultPollution:
    """可变默认值污染测试 —— 确保不同实例不共享列表"""

    def test_tool_calls_independent(self):
        """不同实例的 tool_calls 互不影响"""
        ctx1 = AgentContext(session_id="s1", user_input="a")
        ctx2 = AgentContext(session_id="s2", user_input="b")

        ctx1.add_tool_call("sys_info", {"metric": "cpu"})
        assert len(ctx1.tool_calls) == 1
        assert len(ctx2.tool_calls) == 0  # ctx2 不受影响

    def test_observations_independent(self):
        """不同实例的 observations 互不影响"""
        ctx1 = AgentContext(session_id="s1", user_input="a")
        ctx2 = AgentContext(session_id="s2", user_input="b")

        ctx1.add_observation({"cpu": "23%"})
        assert len(ctx1.observations) == 1
        assert len(ctx2.observations) == 0

    def test_tool_calls_not_shared_class_var(self):
        """确保 tool_calls 不是类变量——多个实例默认值不指向同一对象"""
        ctx1 = AgentContext(session_id="s1", user_input="a")
        ctx2 = AgentContext(session_id="s2", user_input="b")
        assert ctx1.tool_calls is not ctx2.tool_calls

    def test_observations_not_shared_class_var(self):
        """确保 observations 不是类变量"""
        ctx1 = AgentContext(session_id="s1", user_input="a")
        ctx2 = AgentContext(session_id="s2", user_input="b")
        assert ctx1.observations is not ctx2.observations


class TestAgentContextMutators:
    """add_tool_call / add_observation 测试"""

    def test_add_tool_call_structure(self):
        """add_tool_call 应正确保存工具调用记录"""
        ctx = AgentContext(session_id="s1", user_input="test")
        ctx.add_tool_call("sys_info", {"metric": "cpu"}, {"cpu_percent": 23.5})

        assert len(ctx.tool_calls) == 1
        call = ctx.tool_calls[0]
        assert call["tool"] == "sys_info"
        assert call["params"] == {"metric": "cpu"}
        assert call["result"] == {"cpu_percent": 23.5}

    def test_add_tool_call_result_none(self):
        """add_tool_call 的 result 可以为 None"""
        ctx = AgentContext(session_id="s1", user_input="test")
        ctx.add_tool_call("cmd_exec", {"command": "whoami"})
        assert ctx.tool_calls[0]["result"] is None

    def test_add_tool_call_updates_updated_at(self):
        """add_tool_call 应刷新 updated_at"""
        ctx = AgentContext(session_id="s1", user_input="test")
        assert ctx.updated_at is None
        ctx.add_tool_call("sys_info", {"metric": "cpu"})
        assert ctx.updated_at is not None

    def test_add_observation_structure(self):
        """add_observation 应正确追加"""
        ctx = AgentContext(session_id="s1", user_input="test")
        ctx.add_observation({"status": "ok"})
        assert ctx.observations == [{"status": "ok"}]

    def test_add_observation_updates_updated_at(self):
        """add_observation 应刷新 updated_at"""
        ctx = AgentContext(session_id="s1", user_input="test")
        ctx.add_observation({"x": 1})
        assert ctx.updated_at is not None

    def test_multiple_calls_ordered(self):
        """多次调用保持顺序"""
        ctx = AgentContext(session_id="s1", user_input="test")
        ctx.add_tool_call("a", {})
        ctx.add_tool_call("b", {})
        ctx.add_observation({"n": 1})
        assert [c["tool"] for c in ctx.tool_calls] == ["a", "b"]
        assert len(ctx.observations) == 1
