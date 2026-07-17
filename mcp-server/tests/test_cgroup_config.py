"""cgroup 配置校验测试"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import (
    _get_positive_int_env,
    _get_non_negative_int_env,
    _get_non_negative_float_env,
)


class TestPositiveIntEnv:
    def test_valid_positive(self, monkeypatch):
        monkeypatch.setenv("TEST_POS", "100")
        assert _get_positive_int_env("TEST_POS", 50) == 100

    def test_default_when_empty(self, monkeypatch):
        monkeypatch.delenv("TEST_POS", raising=False)
        assert _get_positive_int_env("TEST_POS", 50) == 50

    def test_default_when_blank(self, monkeypatch):
        monkeypatch.setenv("TEST_POS", "")
        assert _get_positive_int_env("TEST_POS", 50) == 50

    def test_rejects_zero(self, monkeypatch):
        monkeypatch.setenv("TEST_POS", "0")
        with pytest.raises(ValueError, match="必须为正整数"):
            _get_positive_int_env("TEST_POS", 50)

    def test_rejects_negative(self, monkeypatch):
        monkeypatch.setenv("TEST_POS", "-1")
        with pytest.raises(ValueError, match="必须为正整数"):
            _get_positive_int_env("TEST_POS", 50)

    def test_rejects_non_numeric(self, monkeypatch):
        monkeypatch.setenv("TEST_POS", "abc")
        with pytest.raises(ValueError, match="不是有效整数"):
            _get_positive_int_env("TEST_POS", 50)

    def test_rejects_float_string(self, monkeypatch):
        monkeypatch.setenv("TEST_POS", "1.5")
        with pytest.raises(ValueError, match="不是有效整数"):
            _get_positive_int_env("TEST_POS", 50)

    def test_accepts_whitespace_trimmed(self, monkeypatch):
        """Python int() 本身接受前后空白，但 env 值通常不会带空白"""
        monkeypatch.setenv("TEST_POS", "  100  ")
        # int("  100  ") → 100，Python 标准行为接受
        assert _get_positive_int_env("TEST_POS", 50) == 100


class TestNonNegativeIntEnv:
    def test_allows_zero(self, monkeypatch):
        monkeypatch.setenv("TEST_NN", "0")
        assert _get_non_negative_int_env("TEST_NN", 10) == 0

    def test_rejects_negative(self, monkeypatch):
        monkeypatch.setenv("TEST_NN", "-1")
        with pytest.raises(ValueError, match="不能为负数"):
            _get_non_negative_int_env("TEST_NN", 10)

    def test_rejects_non_numeric(self, monkeypatch):
        monkeypatch.setenv("TEST_NN", "xyz")
        with pytest.raises(ValueError, match="不是有效整数"):
            _get_non_negative_int_env("TEST_NN", 10)


class TestNonNegativeFloatEnv:
    def test_allows_zero(self, monkeypatch):
        monkeypatch.setenv("TEST_FLT", "0")
        assert _get_non_negative_float_env("TEST_FLT", 1.0) == 0.0

    def test_allows_decimal(self, monkeypatch):
        monkeypatch.setenv("TEST_FLT", "3.5")
        assert _get_non_negative_float_env("TEST_FLT", 1.0) == 3.5

    def test_rejects_negative(self, monkeypatch):
        monkeypatch.setenv("TEST_FLT", "-0.5")
        with pytest.raises(ValueError, match="不能为负数"):
            _get_non_negative_float_env("TEST_FLT", 1.0)

    def test_default(self, monkeypatch):
        monkeypatch.delenv("TEST_FLT", raising=False)
        assert _get_non_negative_float_env("TEST_FLT", 1.0) == 1.0
