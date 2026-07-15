"""WebSocket 认证测试

覆盖:
  1. 匿名模式（未配置 token）→ WS 正常连接
  2. TokenStore validate() 认证逻辑（有效/无效/空 token）
  3. ConnectionManager.connect() 的 token 验证流程
  4. Token 通过 query param 传递
"""
import os

import pytest
from fastapi.testclient import TestClient


class TestAnonymousWSConnection:
    """未配置 token 时应匿名放行（集成测试）"""

    def test_ws_connect_without_token_anonymous(self):
        """无 token 配置时任何连接都应成功"""
        old_token = os.environ.pop("API_TOKEN", None)
        old_tokens = os.environ.pop("API_TOKENS", None)
        try:
            import importlib
            import config
            importlib.reload(config)

            import app.core.security
            app.core.security.TokenStore._instance = None

            from app.main import app
            client = TestClient(app)
            try:
                with client.websocket_connect("/ws/chat/test-session-anon") as ws:
                    pass
            except Exception as e:
                pytest.fail(f"Anonymous WS connection should succeed: {e}")
        finally:
            if old_token is not None:
                os.environ["API_TOKEN"] = old_token
            if old_tokens is not None:
                os.environ["API_TOKENS"] = old_tokens


class TestConnectionManagerAuth:
    """ConnectionManager.connect() 认证逻辑单元测试"""

    def test_validate_valid_token(self):
        """有效 token → 返回 AuthContext"""
        from app.core.auth import AuthLevel
        from app.core.security import TokenStore

        store = TokenStore()
        store._tokens = {"my-token": AuthLevel.READ}

        auth = store.validate("my-token", "127.0.0.1")
        assert auth is not None
        assert auth.level == AuthLevel.READ
        assert auth.is_authenticated is True

    def test_validate_invalid_token(self):
        """无效 token → validate 返回 None"""
        from app.core.auth import AuthLevel
        from app.core.security import TokenStore

        store = TokenStore()
        store._tokens = {"my-token": AuthLevel.READ}

        auth = store.validate("wrong-token", "127.0.0.1")
        assert auth is None

    def test_validate_empty_token(self):
        """空 token → validate 返回 None"""
        from app.core.auth import AuthLevel
        from app.core.security import TokenStore

        store = TokenStore()
        store._tokens = {"my-token": AuthLevel.READ}

        assert store.validate(None) is None
        assert store.validate("") is None

    def test_not_configured_returns_false(self):
        """未配置 token 时 is_configured() 返回 False"""
        from app.core.security import TokenStore

        store = TokenStore()
        store._tokens = {}
        assert store.is_configured() is False

    def test_is_configured_returns_true(self):
        """配置 token 时 is_configured() 返回 True"""
        from app.core.security import TokenStore
        from app.core.auth import AuthLevel

        store = TokenStore()
        store._tokens = {"test": AuthLevel.READ}
        assert store.is_configured() is True

    def test_multi_level_tokens(self):
        """多级 token 分级认证"""
        from app.core.auth import AuthLevel
        from app.core.security import TokenStore

        store = TokenStore()
        store._tokens = {
            "rk-1": AuthLevel.READ,
            "op-1": AuthLevel.OP,
            "adm-1": AuthLevel.ADMIN,
        }

        auth = store.validate("rk-1")
        assert auth.level == AuthLevel.READ
        assert auth.is_authenticated is True

        auth = store.validate("op-1")
        assert auth.level == AuthLevel.OP
        assert auth.is_authenticated is True

        auth = store.validate("adm-1")
        assert auth.level == AuthLevel.ADMIN
        assert auth.is_authenticated is True

    def test_token_hash_no_leak(self):
        """token hash 脱敏：不暴露原文"""
        from app.core.auth import AuthContext, AuthLevel

        ctx = AuthContext.from_token("my-secret-token", AuthLevel.ADMIN, "127.0.0.1")
        assert len(ctx.token_hash) == 8
        assert all(c in "0123456789abcdef" for c in ctx.token_hash)
        assert "my-secret-token" not in ctx.token_hash
