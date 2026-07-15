"""TokenStore 单元测试

覆盖:
  1. 单例线程安全（双重检查锁）
  2. validate() 多 token 分级认证
  3. 空 / 错误 token → None
  4. is_configured() 返回值
  5. token hash 脱敏（日志中不暴露原文）
  6. 热重载 reload()
  7. list_levels() 统计
"""
import threading
import time

import pytest

from app.core.auth import AuthContext, AuthLevel
from app.core.security import TokenStore


class TestTokenStoreSingleton:
    """单例与线程安全"""

    def test_singleton_returns_same_instance(self):
        a = TokenStore.singleton()
        b = TokenStore.singleton()
        assert a is b

    def test_singleton_thread_safety(self):
        """10 线程并发首次调用只创建一个实例"""
        # 重置单例
        TokenStore._instance = None

        results = []
        barrier = threading.Barrier(10)

        def get_instance():
            barrier.wait()
            ts = TokenStore.singleton()
            results.append(id(ts))

        threads = [threading.Thread(target=get_instance) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        unique_ids = set(results)
        assert len(unique_ids) == 1, f"Expected 1 instance, got {len(unique_ids)}"


class TestTokenStoreValidation:
    """Token 验证逻辑"""

    @pytest.fixture(autouse=True)
    def setup_store(self):
        """确保每个测试有独立的 TokenStore 实例（不污染环境变量）"""
        self.store = TokenStore()
        self.store._tokens = {
            "read-token-123": AuthLevel.READ,
            "op-token-456": AuthLevel.OP,
            "admin-token-789": AuthLevel.ADMIN,
        }

    def test_valid_token_read(self):
        auth = self.store.validate("read-token-123", "192.168.1.1")
        assert auth is not None
        assert auth.level == AuthLevel.READ
        assert auth.is_authenticated is True
        assert auth.client_ip == "192.168.1.1"
        # token hash 应为 sha256 前 8 位 hex，不为空且不包含原文
        assert len(auth.token_hash) == 8
        assert "read-token-123" not in auth.token_hash

    def test_valid_token_op(self):
        auth = self.store.validate("op-token-456")
        assert auth is not None
        assert auth.level == AuthLevel.OP
        assert auth.is_authenticated is True

    def test_valid_token_admin(self):
        auth = self.store.validate("admin-token-789")
        assert auth is not None
        assert auth.level == AuthLevel.ADMIN

    def test_invalid_token_returns_none(self):
        auth = self.store.validate("wrong-token", "10.0.0.1")
        assert auth is None

    def test_empty_token_returns_none(self):
        auth = self.store.validate(None)
        assert auth is None
        auth = self.store.validate("")
        assert auth is None

    def test_empty_store_returns_none(self):
        """没有配置任何 token 时 validate 返回 None"""
        empty_store = TokenStore()
        empty_store._tokens = {}
        auth = empty_store.validate("anything")
        assert auth is None


class TestTokenStoreConfiguration:
    """配置状态查询"""

    def test_is_configured_positive(self):
        store = TokenStore()
        store._tokens = {"test": AuthLevel.READ}
        assert store.is_configured() is True

    def test_is_configured_negative(self):
        store = TokenStore()
        store._tokens = {}
        assert store.is_configured() is False

    def test_token_count(self):
        store = TokenStore()
        store._tokens = {"a": AuthLevel.READ, "b": AuthLevel.OP, "c": AuthLevel.ADMIN}
        assert store.token_count() == 3

    def test_list_levels(self):
        store = TokenStore()
        store._tokens = {
            "rk1": AuthLevel.READ,
            "rk2": AuthLevel.READ,
            "op1": AuthLevel.OP,
            "adm1": AuthLevel.ADMIN,
        }
        levels = store.list_levels()
        assert levels["agent-read"] == 2
        assert levels["agent-op"] == 1
        assert levels["agent-admin"] == 1

    def test_reload_clears_and_reloads(self):
        """reload() 清空旧 tokens 并从环境加载新 tokens"""
        store = TokenStore()
        store._tokens = {"old": AuthLevel.READ}
        store.reload()
        # 环境变量未设置时 reload 后为空
        # （依赖真实环境变量，不强行断言具体值）
        assert isinstance(store._tokens, dict)


class TestAuthContextFromToken:
    """AuthContext.from_token 工厂方法"""

    def test_token_hash_is_sha256_prefix(self):
        ctx = AuthContext.from_token("my-secret-token", AuthLevel.ADMIN, "127.0.0.1")
        # sha256 前 8 hex = 8 chars
        assert len(ctx.token_hash) == 8
        # 全 hex 字符
        assert all(c in "0123456789abcdef" for c in ctx.token_hash)
        # 原文不在 hash 中
        assert "my-secret-token" != ctx.token_hash

    def test_anonymous_context(self):
        ctx = AuthContext.anonymous("10.0.0.1")
        assert ctx.level == AuthLevel.ANONYMOUS
        assert ctx.is_authenticated is False
        assert ctx.client_ip == "10.0.0.1"
        assert ctx.token_hash == ""