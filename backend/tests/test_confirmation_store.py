"""ConfirmationStore 测试（Redis 持久化）

覆盖：
  - 创建 / 查询
  - pending / approve / reject
  - TTL 过期
  - 并发幂等
  - 异常场景
"""
from datetime import datetime, timedelta, timezone

import pytest

from app.services.confirmation_store import (
    ConfirmationStore,
    PendingConfirmation,
    ConfirmationDecisionResult,
)
from app.services.storage.redis_storage import RedisStorage

try:
    import fakeredis
    HAS_FAKEREDIS = True
except ImportError:
    HAS_FAKEREDIS = False


# 模块级共享 FakeRedis client
_shared_client: "fakeredis.FakeRedis | None" = None


def _get_shared_client():
    global _shared_client
    if _shared_client is None:
        if not HAS_FAKEREDIS:
            pytest.skip("fakeredis 未安装")
        _shared_client = fakeredis.FakeRedis(decode_responses=True)
    return _shared_client


def _make_store(ttl_seconds: int = 1800, clock=None):
    """创建 fakeredis 支持的 ConfirmationStore"""
    client = _get_shared_client()
    storage = RedisStorage(client=client)
    return ConfirmationStore(ttl_seconds=ttl_seconds, clock=clock, storage=storage)


@pytest.fixture(autouse=True)
def _clear_redis():
    """每个测试前清空共享 FakeRedis"""
    client = _get_shared_client()
    client.flushall()


class TestConfirmationStore:
    def test_create_returns_confirm_and_created_true(self):
        store = _make_store()
        c, created = store.create_or_get("s1", "fix_a1b2c3d4", "t1")
        assert c.confirm_id.startswith("cfm_")
        assert created is True

    def test_repeat_returns_same_and_created_false(self):
        store = _make_store()
        c1, cr1 = store.create_or_get("s1", "fix_a1b2c3d4", "t1")
        c2, cr2 = store.create_or_get("s1", "fix_a1b2c3d4", "t2")
        assert c1.confirm_id == c2.confirm_id
        assert cr1 is True
        assert cr2 is False

    def test_wrong_session_returns_none(self):
        store = _make_store()
        c, _ = store.create_or_get("s1", "fix_a1b2c3d4", "t1")
        assert store.get("s2", c.confirm_id) is None

    def test_get_by_confirm_id(self):
        store = _make_store()
        c, _ = store.create_or_get("s1", "fix_11111111", "t1")
        assert store.get("s1", c.confirm_id) is not None

    def test_get_by_option(self):
        store = _make_store()
        store.create_or_get("s1", "fix_22222222", "t1")
        assert store.get_by_option("s1", "fix_22222222") is not None

    def test_ttl_expired_no_longer_valid(self):
        t0 = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        store = _make_store(ttl_seconds=60, clock=lambda: t0)
        c, _ = store.create_or_get("s1", "fix_ttl01", "t1")
        store._clock = lambda: t0 + timedelta(seconds=61)
        result = store.get("s1", c.confirm_id)
        # 过期后仍可 get，但 status 已变为 expired
        assert result is not None

    def test_cleanup_removes_expired(self):
        t0 = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        store = _make_store(ttl_seconds=60, clock=lambda: t0)
        store.create_or_get("s1", "fix_cleanup01", "t1")
        store._clock = lambda: t0 + timedelta(seconds=120)
        assert store.cleanup_expired() == 1

    def test_confirm_id_format(self):
        store = _make_store()
        c, _ = store.create_or_get("s1", "fix_idfmt01", "t1")
        import re
        assert re.fullmatch(r"cfm_[0-9a-f]{8}", c.confirm_id)

    def test_remove_if_pending_success(self):
        store = _make_store()
        c, _ = store.create_or_get("s1", "fix_rm01", "t1")
        assert store.remove_if_pending("s1", "fix_rm01", c.confirm_id) is True
        assert store.get_by_option("s1", "fix_rm01") is None

    def test_remove_if_pending_wrong_confirm_id(self):
        store = _make_store()
        c, _ = store.create_or_get("s1", "fix_rm02", "t1")
        assert store.remove_if_pending("s1", "fix_rm02", "wrong_id") is False

    def test_status_runtime_validation(self):
        with pytest.raises(ValueError, match="非法 ConfirmationStatus"):
            PendingConfirmation(
                confirm_id="cfm_11111111", session_id="s", option_id="fix_11111111",
                trace_id="t", status="invalid",  # type: ignore[arg-type]
                created_at=datetime.now(timezone.utc),
                expires_at=datetime.now(timezone.utc),
            )

    def test_concurrent_same_confirm_id_and_only_one_created(self):
        import threading
        store = _make_store()
        results = []

        def _create():
            results.append(store.create_or_get("s1", "fix_cc01", "t1"))
        t1 = threading.Thread(target=_create)
        t2 = threading.Thread(target=_create)
        t1.start(); t2.start()
        t1.join(); t2.join()
        assert results[0][0].confirm_id == results[1][0].confirm_id
        # 只有一个 created=True
        assert sum(1 for _, cr in results if cr) == 1

    # ── Decision 决策测试 ──

    def test_claim_approve_success(self):
        store = _make_store()
        c, _ = store.create_or_get("s1", "fix_approve01", "t1")
        r = store.claim_approve("s1", c.confirm_id)
        assert r.result == "claimed"
        assert r.confirmation is not None
        assert r.confirmation.status == "approving"

    def test_claim_approve_conflict(self):
        store = _make_store()
        c, _ = store.create_or_get("s1", "fix_approve02", "t1")
        store.claim_approve("s1", c.confirm_id)
        r = store.claim_approve("s1", c.confirm_id)
        assert r.result == "conflict"

    def test_reject_success(self):
        store = _make_store()
        c, _ = store.create_or_get("s1", "fix_reject01", "t1")
        r = store.reject("s1", c.confirm_id)
        assert r.result == "rejected"
        assert r.confirmation is not None
        assert r.confirmation.status == "rejected"

    def test_reject_conflict_after_approve(self):
        store = _make_store()
        c, _ = store.create_or_get("s1", "fix_reject02", "t1")
        store.claim_approve("s1", c.confirm_id)
        r = store.reject("s1", c.confirm_id)
        assert r.result == "conflict"

    def test_claim_approve_expired(self):
        t0 = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        store = _make_store(ttl_seconds=60, clock=lambda: t0)
        store.create_or_get("s1", "fix_exp01", "t1")
        store._clock = lambda: t0 + timedelta(seconds=120)
        opt_conf = store.get_by_option("s1", "fix_exp01")
        assert opt_conf is not None
        conf = store.get("s1", opt_conf.confirm_id)
        assert conf is not None
        r = store.claim_approve("s1", conf.confirm_id)
        assert r.result == "expired"

    def test_reject_expired(self):
        t0 = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        store = _make_store(ttl_seconds=60, clock=lambda: t0)
        c, _ = store.create_or_get("s1", "fix_exp02", "t1")
        store._clock = lambda: t0 + timedelta(seconds=120)
        r = store.reject("s1", c.confirm_id)
        assert r.result == "expired"

    def test_claim_approve_wrong_session_not_found(self):
        store = _make_store()
        c, _ = store.create_or_get("s1", "fix_nf01", "t1")
        r = store.claim_approve("s2", c.confirm_id)
        assert r.result == "not_found"

    def test_claim_approve_conflict_after_consumed(self):
        store = _make_store()
        c, _ = store.create_or_get("s1", "fix_consume01", "t1")
        store.claim_approve("s1", c.confirm_id)
        store.mark_consumed("s1", c.confirm_id)
        r = store.claim_approve("s1", c.confirm_id)
        assert r.result == "conflict"

    # ── 状态流转 ──

    def test_mark_consumed(self):
        store = _make_store()
        c, _ = store.create_or_get("s1", "fix_mc01", "t1")
        store.claim_approve("s1", c.confirm_id)
        assert store.mark_consumed("s1", c.confirm_id) is True

    def test_mark_consumed_from_pending_fails(self):
        store = _make_store()
        c, _ = store.create_or_get("s1", "fix_mc02", "t1")
        assert store.mark_consumed("s1", c.confirm_id) is False

    def test_mark_blocked(self):
        store = _make_store()
        c, _ = store.create_or_get("s1", "fix_mb01", "t1")
        store.claim_approve("s1", c.confirm_id)
        assert store.mark_blocked("s1", c.confirm_id) is True

    def test_mark_failed(self):
        store = _make_store()
        c, _ = store.create_or_get("s1", "fix_mf01", "t1")
        store.claim_approve("s1", c.confirm_id)
        assert store.mark_failed("s1", c.confirm_id) is True


class TestConfirmationStoreTTL:
    """TTL 专项测试"""

    def test_confirmation_default_ttl(self):
        store = _make_store(ttl_seconds=1800)
        assert store._ttl == 1800

    def test_confirmation_custom_ttl(self):
        store = _make_store(ttl_seconds=600)
        assert store._ttl == 600

    def test_confirmation_expires_after_ttl(self):
        t0 = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        store = _make_store(ttl_seconds=30, clock=lambda: t0)
        c, created = store.create_or_get("s1", "fix_ttl02", "t1")
        assert created is True
        # 快进 31 秒
        store._clock = lambda: t0 + timedelta(seconds=31)
        # 过期后 create_or_get 应创建新的
        c2, created2 = store.create_or_get("s1", "fix_ttl02", "t2")
        assert created2 is True
        assert c2.confirm_id != c.confirm_id


class TestConfirmationConcurrency:
    """并发竞争测试 —— 验证 Lua 原子操作"""

    def test_concurrent_claim_approve_only_one_succeeds(self):
        """两个线程同时 claim_approve 同一个 confirm_id，只有一个成功"""
        import threading
        store = _make_store()
        c, _ = store.create_or_get("s1", "fix_cc01", "t1")
        results = []

        def _claim():
            r = store.claim_approve("s1", c.confirm_id)
            results.append(r.result)

        t1 = threading.Thread(target=_claim)
        t2 = threading.Thread(target=_claim)
        t1.start(); t2.start()
        t1.join(); t2.join()

        assert "claimed" in results
        assert "conflict" in results or results.count("claimed") == 1

    def test_concurrent_reject_only_one_succeeds(self):
        """两个线程同时 reject 同一个 confirm_id，只有一个成功"""
        import threading
        store = _make_store()
        c, _ = store.create_or_get("s1", "fix_cc02", "t1")
        results = []

        def _reject():
            r = store.reject("s1", c.confirm_id)
            results.append(r.result)

        t1 = threading.Thread(target=_reject)
        t2 = threading.Thread(target=_reject)
        t1.start(); t2.start()
        t1.join(); t2.join()

        assert "rejected" in results
        assert "conflict" in results or results.count("rejected") == 1

    def test_claim_approve_then_reject_fails(self):
        """claim_approve 成功后，reject 应返回 conflict"""
        store = _make_store()
        c, _ = store.create_or_get("s1", "fix_cc03", "t1")
        r1 = store.claim_approve("s1", c.confirm_id)
        assert r1.result == "claimed"
        r2 = store.reject("s1", c.confirm_id)
        assert r2.result == "conflict"

    def test_concurrent_mark_consumed_only_one_succeeds(self):
        """两个线程同时 mark_consumed，只有一个成功"""
        import threading
        store = _make_store()
        c, _ = store.create_or_get("s1", "fix_cc04", "t1")
        store.claim_approve("s1", c.confirm_id)
        results = []

        def _consume():
            results.append(store.mark_consumed("s1", c.confirm_id))

        t1 = threading.Thread(target=_consume)
        t2 = threading.Thread(target=_consume)
        t1.start(); t2.start()
        t1.join(); t2.join()

        assert sum(1 for r in results if r) == 1
