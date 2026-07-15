"""AuthLevel 枚举单元测试

覆盖:
  1. 比较运算（按枚举顺序）
  2. from_string 正常转换（value / name 两种格式）
  3. from_string 大小写不敏感
  4. from_string 未知值 → ANONYMOUS
  5. from_string 显式 default 参数
  6. _order() 与 _AUTH_LEVEL_ORDER 缓存一致性
"""
import pytest

from app.core.auth import AuthLevel


class TestAuthLevelComparison:
    """AuthLevel 比较运算测试"""

    def test_admin_greater_than_all(self):
        assert AuthLevel.ADMIN > AuthLevel.OP
        assert AuthLevel.ADMIN > AuthLevel.READ
        assert AuthLevel.ADMIN > AuthLevel.ANONYMOUS
        assert AuthLevel.ADMIN >= AuthLevel.ADMIN

    def test_op_greater_than_read_and_anonymous(self):
        assert AuthLevel.OP > AuthLevel.READ
        assert AuthLevel.OP > AuthLevel.ANONYMOUS
        assert AuthLevel.OP >= AuthLevel.OP

    def test_read_greater_than_anonymous(self):
        assert AuthLevel.READ > AuthLevel.ANONYMOUS
        assert AuthLevel.READ >= AuthLevel.READ

    def test_anonymous_lowest(self):
        assert AuthLevel.ANONYMOUS < AuthLevel.READ
        assert AuthLevel.ANONYMOUS < AuthLevel.OP
        assert AuthLevel.ANONYMOUS < AuthLevel.ADMIN
        assert AuthLevel.ANONYMOUS <= AuthLevel.ANONYMOUS

    def test_comparison_with_string_uses_str_comparison(self):
        """与普通字符串比较走 str 基类比较（因为 AuthLevel 继承自 str）"""
        # AuthLevel(str, Enum) 同时拥有 str 和 Enum 的比较语义
        # 当另一方不是 AuthLevel 时，NotImplemented 触发 Python 回退到 str.__gt__
        # 所以此类比较结果是 str 比较结果，不抛异常
        result = AuthLevel.ADMIN > "read"
        assert isinstance(result, bool)


class TestFromStringNormal:
    """from_string 正常值转换"""

    def test_by_value(self):
        assert AuthLevel.from_string("agent-anonymous") == AuthLevel.ANONYMOUS
        assert AuthLevel.from_string("agent-read") == AuthLevel.READ
        assert AuthLevel.from_string("agent-op") == AuthLevel.OP
        assert AuthLevel.from_string("agent-admin") == AuthLevel.ADMIN

    def test_by_name(self):
        assert AuthLevel.from_string("ANONYMOUS") == AuthLevel.ANONYMOUS
        assert AuthLevel.from_string("READ") == AuthLevel.READ
        assert AuthLevel.from_string("OP") == AuthLevel.OP
        assert AuthLevel.from_string("ADMIN") == AuthLevel.ADMIN

    def test_case_insensitive(self):
        # value 格式大小写不敏感
        assert AuthLevel.from_string("Agent-Admin") == AuthLevel.ADMIN
        assert AuthLevel.from_string("AGENT-OP") == AuthLevel.OP
        # name 格式大小写不敏感
        assert AuthLevel.from_string("admin") == AuthLevel.ADMIN
        assert AuthLevel.from_string("read") == AuthLevel.READ
        assert AuthLevel.from_string("Anonymous") == AuthLevel.ANONYMOUS

    def test_strip_whitespace(self):
        assert AuthLevel.from_string("  agent-admin  ") == AuthLevel.ADMIN
        assert AuthLevel.from_string(" READ\n") == AuthLevel.READ


class TestFromStringUnknown:
    """from_string 未知值处理"""

    def test_unknown_without_default_returns_anonymous(self):
        result = AuthLevel.from_string("superadmin")
        assert result == AuthLevel.ANONYMOUS
        result = AuthLevel.from_string("nonexistent")
        assert result == AuthLevel.ANONYMOUS
        result = AuthLevel.from_string("")
        assert result == AuthLevel.ANONYMOUS

    def test_unknown_with_explicit_default(self):
        result = AuthLevel.from_string("unknown", default=AuthLevel.READ)
        assert result == AuthLevel.READ
        result = AuthLevel.from_string("", default=AuthLevel.OP)
        assert result == AuthLevel.OP


class TestAuthLevelOrderCache:
    """_AUTH_LEVEL_ORDER 缓存一致性"""

    def test_cache_indices(self):
        from app.core import auth
        assert auth._AUTH_LEVEL_ORDER[AuthLevel.ANONYMOUS] == 0
        assert auth._AUTH_LEVEL_ORDER[AuthLevel.READ] == 1
        assert auth._AUTH_LEVEL_ORDER[AuthLevel.OP] == 2
        assert auth._AUTH_LEVEL_ORDER[AuthLevel.ADMIN] == 3

    def test_order_method_uses_cache(self):
        assert AuthLevel.ANONYMOUS._order() == 0
        assert AuthLevel.READ._order() == 1
        assert AuthLevel.OP._order() == 2
        assert AuthLevel.ADMIN._order() == 3