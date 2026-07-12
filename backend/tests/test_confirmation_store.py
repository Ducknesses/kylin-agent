"""ConfirmationStore 测试"""
import pytest

from app.services.confirmation_store import ConfirmationStore


class TestConfirmationStore:
    def test_create_returns_confirm_and_created_true(self):
        store = ConfirmationStore()
        c, created = store.create_or_get("s1", "fix_a1b2c3d4", "t1")
        assert c.confirm_id.startswith("cfm_")
        assert created is True

    def test_repeat_returns_same_and_created_false(self):
        store = ConfirmationStore()
        c1, cr1 = store.create_or_get("s1", "fix_a1b2c3d4", "t1")
        c2, cr2 = store.create_or_get("s1", "fix_a1b2c3d4", "t2")
        assert c1.confirm_id == c2.confirm_id
        assert cr1 is True
        assert cr2 is False

    def test_wrong_session_returns_none(self):
        store = ConfirmationStore()
        c, _ = store.create_or_get("s1", "fix_a1b2c3d4", "t1")
        assert store.get("s2", c.confirm_id) is None

    def test_get_by_confirm_id(self):
        store = ConfirmationStore()
        c, _ = store.create_or_get("s1", "fix_11111111", "t1")
        assert store.get("s1", c.confirm_id) is not None

    def test_get_by_option(self):
        store = ConfirmationStore()
        store.create_or_get("s1", "fix_22222222", "t1")
        assert store.get_by_option("s1", "fix_22222222") is not None

    def test_ttl_expired_no_longer_valid(self):
        from datetime import datetime, timedelta, timezone
        t0 = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        store = ConfirmationStore(ttl_seconds=60)
        store._clock = lambda: t0
        c, _ = store.create_or_get("s1", "fix_a1b2c3d4", "t1")
        store._clock = lambda: t0 + timedelta(seconds=61)
        assert store.get("s1", c.confirm_id) is not None

    def test_cleanup_removes_expired(self):
        from datetime import datetime, timedelta, timezone
        t0 = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        store = ConfirmationStore(ttl_seconds=60)
        store._clock = lambda: t0
        store.create_or_get("s1", "fix_a1b2c3d4", "t1")
        store._clock = lambda: t0 + timedelta(seconds=120)
        assert store.cleanup_expired() == 1

    def test_confirm_id_format(self):
        store = ConfirmationStore()
        c, _ = store.create_or_get("s1", "fix_a1b2c3d4", "t1")
        import re
        assert re.fullmatch(r"cfm_[0-9a-f]{8}", c.confirm_id)

    def test_remove_if_pending_success(self):
        store = ConfirmationStore()
        c, _ = store.create_or_get("s1", "fix_a1b2c3d4", "t1")
        assert store.remove_if_pending("s1", "fix_a1b2c3d4", c.confirm_id) is True
        assert store.get_by_option("s1", "fix_a1b2c3d4") is None

    def test_remove_if_pending_wrong_confirm_id(self):
        store = ConfirmationStore()
        c, _ = store.create_or_get("s1", "fix_a1b2c3d4", "t1")
        assert store.remove_if_pending("s1", "fix_a1b2c3d4", "wrong_id") is False

    def test_status_runtime_validation(self):
        with pytest.raises(ValueError, match="非法 ConfirmationStatus"):
            from app.services.confirmation_store import PendingConfirmation
            from datetime import datetime, timezone
            PendingConfirmation(
                confirm_id="cfm_11111111", session_id="s", option_id="fix_11111111",
                trace_id="t", status="invalid",  # type: ignore[arg-type]
                created_at=datetime.now(timezone.utc),
                expires_at=datetime.now(timezone.utc),
            )

    def test_concurrent_same_confirm_id_and_only_one_created(self):
        import threading
        store = ConfirmationStore()
        results = []

        def _create():
            results.append(store.create_or_get("s1", "fix_a1b2c3d4", "t1"))
        t1 = threading.Thread(target=_create)
        t2 = threading.Thread(target=_create)
        t1.start(); t2.start()
        t1.join(); t2.join()
        assert results[0][0].confirm_id == results[1][0].confirm_id
        # 只有一个 created=True
        assert sum(1 for _, cr in results if cr) == 1
