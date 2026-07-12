"""FixOption Schema 专项测试

覆盖：
  - 合法 FixOption 创建
  - risk_level 枚举约束
  - 非空字符串校验
  - params 类型校验
  - rollback 类型校验
  - 额外字段行为
  - 序列化字段完整性
  - ActionExecuteRequest 校验
  - ActionExecuteResponse 校验

不调用真实 LLM、MCP、FastAPI、数据库，不依赖网络。
"""

import pytest
from pydantic import ValidationError

from app.schemas.action import (
    ActionExecuteRequest,
    ActionExecuteResponse,
    FixOption,
    RiskLevel,
)

# ── 固定合法参数 ──────────────────────────────────────────────────────

_VALID_KWARGS = {
    "option_id": "fix_001",
    "title": "重启 nginx 服务",
    "description": "执行 systemctl restart nginx 以恢复 Web 服务",
    "risk_level": "medium",
    "tool": "service_mgr",
    "params": {"action": "restart", "service": "nginx"},
    "requires_confirm": True,
    "rollback": "systemctl start nginx（如已停止）",
}


def _make(**overrides) -> FixOption:
    kwargs = {**_VALID_KWARGS, **overrides}
    return FixOption(**kwargs)


# ═══════════════════════════════════════════════════════════════════════
# FixOption 正常创建
# ═══════════════════════════════════════════════════════════════════════

class TestValidFixOption:
    def test_valid_option_created(self):
        opt = _make()
        assert opt.option_id == "fix_001"
        assert opt.risk_level == "medium"
        assert opt.requires_confirm is True

    def test_rollback_defaults_to_none(self):
        opt = _make(rollback=None)
        assert opt.rollback is None
        kwargs = {k: v for k, v in _VALID_KWARGS.items() if k != "rollback"}
        opt2 = FixOption(**kwargs)
        assert opt2.rollback is None

    def test_rollback_as_string(self):
        opt = _make(rollback="systemctl start nginx")
        assert opt.rollback == "systemctl start nginx"

    def test_requires_confirm_bool(self):
        assert _make(requires_confirm=True).requires_confirm is True
        assert _make(requires_confirm=False).requires_confirm is False

    def test_serialization_fields_complete(self):
        d = _make().model_dump()
        expected = {"option_id", "title", "description", "risk_level", "tool", "params", "requires_confirm", "rollback"}
        assert set(d.keys()) == expected


class TestRiskLevel:
    @pytest.mark.parametrize("level", ["low", "medium", "high"])
    def test_valid_risk_levels(self, level):
        assert _make(risk_level=level).risk_level == level

    def test_invalid_risk_level_rejected(self):
        with pytest.raises(ValidationError):
            _make(risk_level="critical")

    def test_empty_risk_level_rejected(self):
        with pytest.raises(ValidationError):
            _make(risk_level="")

    def test_random_string_rejected(self):
        with pytest.raises(ValidationError):
            _make(risk_level="not-a-level")


class TestNonEmptyStrings:
    def test_option_id_empty_rejected(self):
        with pytest.raises(ValidationError): _make(option_id="")
    def test_option_id_whitespace_rejected(self):
        with pytest.raises(ValidationError): _make(option_id="   ")
    def test_title_empty_rejected(self):
        with pytest.raises(ValidationError): _make(title="")
    def test_title_whitespace_rejected(self):
        with pytest.raises(ValidationError): _make(title="\t\n  ")
    def test_description_empty_rejected(self):
        with pytest.raises(ValidationError): _make(description="")
    def test_description_whitespace_rejected(self):
        with pytest.raises(ValidationError): _make(description="  \n ")
    def test_tool_empty_rejected(self):
        with pytest.raises(ValidationError): _make(tool="")
    def test_tool_whitespace_rejected(self):
        with pytest.raises(ValidationError): _make(tool="  \t  ")
    def test_option_id_with_leading_trailing_spaces_stripped(self):
        assert _make(option_id="  fix_123  ").option_id == "fix_123"
    def test_all_fields_strip_consistently(self):
        opt = FixOption(option_id="  opt_1  ", title="  重启  ", description="  描述  ",
                        risk_level="low", tool="  sys_info  ", params={"metric": "cpu"}, requires_confirm=False)
        assert opt.option_id == "opt_1" and opt.title == "重启" and opt.description == "描述" and opt.tool == "sys_info"


class TestParamsType:
    def test_params_dict_valid(self):
        assert _make(params={"action": "status"}).params == {"action": "status"}
    def test_params_list_rejected(self):
        with pytest.raises(ValidationError): _make(params=["action"])
    def test_params_string_rejected(self):
        with pytest.raises(ValidationError): _make(params="action=restart")
    def test_params_none_rejected(self):
        with pytest.raises(ValidationError): _make(params=None)
    def test_params_empty_dict_valid(self):
        assert _make(params={}).params == {}


class TestRollbackType:
    def test_rollback_int_rejected(self):
        with pytest.raises(ValidationError): _make(rollback=123)
    def test_rollback_bool_rejected(self):
        with pytest.raises(ValidationError): _make(rollback=True)
    def test_rollback_list_rejected(self):
        with pytest.raises(ValidationError): _make(rollback=["cmd1"])


class TestExtraFields:
    @pytest.mark.parametrize("extra_key, extra_value", [
        ("unknown_field", "v"), ("extra", 42), ("foo", "bar"),
    ])
    def test_extra_field_rejected(self, extra_key, extra_value):
        payload = {**_VALID_KWARGS, extra_key: extra_value}
        with pytest.raises(ValidationError):
            FixOption.model_validate(payload)

    def test_model_dump_only_has_8_fields(self):
        d = FixOption(**_VALID_KWARGS).model_dump()
        assert set(d.keys()) == {"option_id", "title", "description", "risk_level", "tool", "params", "requires_confirm", "rollback"}


# ═══════════════════════════════════════════════════════════════════════
# Action API Schema
# ═══════════════════════════════════════════════════════════════════════

class TestActionExecuteRequest:
    def test_valid_request(self):
        req = ActionExecuteRequest(session_id="s1", option_id="fix_a1b2c3d4")
        assert req.session_id == "s1" and req.option_id == "fix_a1b2c3d4"

    def test_empty_session_id_rejected(self):
        with pytest.raises(ValidationError): ActionExecuteRequest(session_id="", option_id="fix_a1b2c3d4")
    def test_empty_option_id_rejected(self):
        with pytest.raises(ValidationError): ActionExecuteRequest(session_id="s1", option_id="")
    def test_option_id_no_fix_prefix_rejected(self):
        with pytest.raises(ValidationError): ActionExecuteRequest(session_id="s1", option_id="abc12345")
    def test_option_id_wrong_hex_length_rejected(self):
        with pytest.raises(ValidationError): ActionExecuteRequest(session_id="s1", option_id="fix_abc")
    def test_option_id_uppercase_hex_rejected(self):
        with pytest.raises(ValidationError): ActionExecuteRequest(session_id="s1", option_id="fix_ABC12345")
    def test_extra_tool_field_rejected(self):
        with pytest.raises(ValidationError): ActionExecuteRequest(session_id="s1", option_id="fix_deadbeef", tool="x")
    def test_extra_params_field_rejected(self):
        with pytest.raises(ValidationError): ActionExecuteRequest(session_id="s1", option_id="fix_deadbeef", params={})
    def test_extra_risk_level_rejected(self):
        with pytest.raises(ValidationError): ActionExecuteRequest(session_id="s1", option_id="fix_deadbeef", risk_level="low")
    def test_extra_confirm_field_rejected(self):
        with pytest.raises(ValidationError): ActionExecuteRequest(session_id="s1", option_id="fix_deadbeef", confirm=True)
    def test_whitespace_stripped(self):
        req = ActionExecuteRequest(session_id="  s1  ", option_id="  fix_a1b2c3d4  ")
        assert req.session_id == "s1" and req.option_id == "fix_a1b2c3d4"


class TestActionExecuteResponse:
    def test_ready_response(self):
        resp = ActionExecuteResponse(option_id="fix_a1b2c3d4", session_id="s1", trace_id="t1", status="ready", risk_level="low", message="就绪", requires_confirm=False)
        assert resp.status == "ready"
    def test_confirm_required_response(self):
        resp = ActionExecuteResponse(option_id="fix_a1b2c3d4", session_id="s1", trace_id="t1", status="confirm_required", risk_level="medium", message="需确认", requires_confirm=True, confirm_id="cfm_a1b2c3d4")
        assert resp.requires_confirm is True
    def test_blocked_response(self):
        resp = ActionExecuteResponse(option_id="fix_a1b2c3d4", session_id="s1", trace_id="t1", status="blocked", risk_level="high", message="已阻断", requires_confirm=False)
        assert resp.status == "blocked"
    def test_invalid_status_rejected(self):
        with pytest.raises(ValidationError): ActionExecuteResponse(option_id="fix_a1b2c3d4", session_id="s1", trace_id="t1", status="executing", risk_level="low", message="x", requires_confirm=False)


class TestResponseCrossValidation:
    def test_confirm_required_without_confirm_id_rejected(self):
        with pytest.raises(ValidationError):
            ActionExecuteResponse(option_id="fix_a1b2c3d4", session_id="s1", trace_id="t1",
                                  status="confirm_required", risk_level="medium",
                                  message="x", requires_confirm=True, confirm_id=None)

    def test_confirm_id_malformed_rejected(self):
        with pytest.raises(ValidationError):
            ActionExecuteResponse(option_id="fix_a1b2c3d4", session_id="s1", trace_id="t1",
                                  status="confirm_required", risk_level="medium",
                                  message="x", requires_confirm=True, confirm_id="bad")

    def test_executed_with_confirm_id_rejected(self):
        with pytest.raises(ValidationError):
            ActionExecuteResponse(option_id="fix_a1b2c3d4", session_id="s1", trace_id="t1",
                                  status="executed", risk_level="low",
                                  message="x", requires_confirm=False, confirm_id="cfm_11111111")

    def test_blocked_with_confirm_id_rejected(self):
        with pytest.raises(ValidationError):
            ActionExecuteResponse(option_id="fix_a1b2c3d4", session_id="s1", trace_id="t1",
                                  status="blocked", risk_level="high",
                                  message="x", requires_confirm=False, confirm_id="cfm_11111111")

    def test_confirm_required_with_confirm_id_ok(self):
        resp = ActionExecuteResponse(option_id="fix_a1b2c3d4", session_id="s1", trace_id="t1",
                                     status="confirm_required", risk_level="medium",
                                     message="x", requires_confirm=True, confirm_id="cfm_a1b2c3d4")
        assert resp.confirm_id == "cfm_a1b2c3d4"

    def test_executed_without_confirm_id_ok(self):
        resp = ActionExecuteResponse(option_id="fix_a1b2c3d4", session_id="s1", trace_id="t1",
                                     status="executed", risk_level="low",
                                     message="x", requires_confirm=False, confirm_id=None)
        assert resp.confirm_id is None
