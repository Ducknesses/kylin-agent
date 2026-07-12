"""FixOption Schema 专项测试

覆盖：
  - 合法 FixOption 创建
  - risk_level 枚举约束
  - 非空字符串校验
  - params 类型校验
  - rollback 类型校验
  - 额外字段行为
  - 序列化字段完整性

不调用真实 LLM、MCP、FastAPI、数据库，不依赖网络。
"""

import pytest
from pydantic import ValidationError

from app.schemas.action import FixOption

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
# 正常创建
# ═══════════════════════════════════════════════════════════════════════

class TestValidFixOption:
    """合法 FixOption 创建"""

    def test_valid_option_created(self):
        opt = _make()
        assert opt.option_id == "fix_001"
        assert opt.title == "重启 nginx 服务"
        assert opt.risk_level == "medium"
        assert opt.requires_confirm is True

    def test_rollback_defaults_to_none(self):
        """rollback 省略时默认为 None"""
        opt = _make(rollback=None)
        assert opt.rollback is None
        # 不传 rollback 也应默认为 None
        kwargs = {k: v for k, v in _VALID_KWARGS.items() if k != "rollback"}
        opt2 = FixOption(**kwargs)
        assert opt2.rollback is None

    def test_rollback_as_string(self):
        opt = _make(rollback="systemctl start nginx")
        assert opt.rollback == "systemctl start nginx"

    def test_requires_confirm_bool(self):
        opt_t = _make(requires_confirm=True)
        assert opt_t.requires_confirm is True
        opt_f = _make(requires_confirm=False)
        assert opt_f.requires_confirm is False

    def test_serialization_fields_complete(self):
        """序列化结果字段完整"""
        opt = _make()
        d = opt.model_dump()
        expected_fields = {
            "option_id", "title", "description", "risk_level",
            "tool", "params", "requires_confirm", "rollback",
        }
        assert set(d.keys()) == expected_fields


# ═══════════════════════════════════════════════════════════════════════
# risk_level 枚举
# ═══════════════════════════════════════════════════════════════════════

class TestRiskLevel:
    """risk_level Literal 约束"""

    @pytest.mark.parametrize("level", ["low", "medium", "high"])
    def test_valid_risk_levels(self, level):
        opt = _make(risk_level=level)
        assert opt.risk_level == level

    def test_invalid_risk_level_rejected(self):
        with pytest.raises(ValidationError):
            _make(risk_level="critical")

    def test_empty_risk_level_rejected(self):
        with pytest.raises(ValidationError):
            _make(risk_level="")

    def test_random_string_rejected(self):
        with pytest.raises(ValidationError):
            _make(risk_level="not-a-level")


# ═══════════════════════════════════════════════════════════════════════
# 非空字符串校验
# ═══════════════════════════════════════════════════════════════════════

class TestNonEmptyStrings:
    """option_id / title / description / tool 非空校验"""

    def test_option_id_empty_rejected(self):
        with pytest.raises(ValidationError):
            _make(option_id="")

    def test_option_id_whitespace_rejected(self):
        with pytest.raises(ValidationError):
            _make(option_id="   ")

    def test_title_empty_rejected(self):
        with pytest.raises(ValidationError):
            _make(title="")

    def test_title_whitespace_rejected(self):
        with pytest.raises(ValidationError):
            _make(title="\t\n  ")

    def test_description_empty_rejected(self):
        with pytest.raises(ValidationError):
            _make(description="")

    def test_description_whitespace_rejected(self):
        with pytest.raises(ValidationError):
            _make(description="  \n ")

    def test_tool_empty_rejected(self):
        with pytest.raises(ValidationError):
            _make(tool="")

    def test_tool_whitespace_rejected(self):
        with pytest.raises(ValidationError):
            _make(tool="  \t  ")

    def test_option_id_with_leading_trailing_spaces_stripped(self):
        """前后空格应被 strip 后保留内容"""
        opt = _make(option_id="  fix_123  ")
        assert opt.option_id == "fix_123"

    def test_all_fields_strip_consistently(self):
        """option_id / title / description / tool 的 strip 行为一致"""
        opt = FixOption(
            option_id="  opt_1  ",
            title="  重启  ",
            description="  描述  ",
            risk_level="low",
            tool="  sys_info  ",
            params={"metric": "cpu"},
            requires_confirm=False,
        )
        assert opt.option_id == "opt_1"
        assert opt.title == "重启"
        assert opt.description == "描述"
        assert opt.tool == "sys_info"


# ═══════════════════════════════════════════════════════════════════════
# params 类型校验
# ═══════════════════════════════════════════════════════════════════════

class TestParamsType:
    """params 必须为字典"""

    def test_params_dict_valid(self):
        opt = _make(params={"action": "status", "service": "nginx"})
        assert opt.params == {"action": "status", "service": "nginx"}

    def test_params_list_rejected(self):
        with pytest.raises(ValidationError):
            _make(params=["action", "status"])

    def test_params_string_rejected(self):
        with pytest.raises(ValidationError):
            _make(params="action=restart")

    def test_params_none_rejected(self):
        with pytest.raises(ValidationError):
            _make(params=None)

    def test_params_empty_dict_valid(self):
        """空字典是合法 dict"""
        opt = _make(params={})
        assert opt.params == {}


# ═══════════════════════════════════════════════════════════════════════
# rollback 类型校验
# ═══════════════════════════════════════════════════════════════════════

class TestRollbackType:
    """rollback 只能为 str 或 None"""

    def test_rollback_int_rejected(self):
        with pytest.raises(ValidationError):
            _make(rollback=123)

    def test_rollback_bool_rejected(self):
        with pytest.raises(ValidationError):
            _make(rollback=True)

    def test_rollback_list_rejected(self):
        with pytest.raises(ValidationError):
            _make(rollback=["cmd1", "cmd2"])


# ═══════════════════════════════════════════════════════════════════════
# 额外字段
# ═══════════════════════════════════════════════════════════════════════

class TestExtraFields:
    """额外字段行为 —— extra="forbid"，未知字段触发 ValidationError"""

    @pytest.mark.parametrize("extra_key, extra_value", [
        ("unknown_field", "should_be_rejected"),
        ("extra", 42),
        ("foo", "bar"),
    ])
    def test_extra_field_rejected(self, extra_key, extra_value):
        """未定义的额外字段必须触发 ValidationError"""
        payload = {**_VALID_KWARGS, extra_key: extra_value}
        with pytest.raises(ValidationError):
            FixOption.model_validate(payload)

    def test_model_dump_only_has_8_fields(self):
        """model_dump 只包含正式 8 个字段"""
        opt = FixOption(**_VALID_KWARGS)
        d = opt.model_dump()
        expected_fields = {
            "option_id", "title", "description", "risk_level",
            "tool", "params", "requires_confirm", "rollback",
        }
        assert set(d.keys()) == expected_fields
