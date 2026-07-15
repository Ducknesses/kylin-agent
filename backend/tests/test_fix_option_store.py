"""FixOptionStore 专项测试

覆盖：
  - 保存 / 回查 / 列表
  - session 隔离 / 重复拒绝 / 输入校验
  - TTL 过期 / 清理 / 时钟注入
  - 状态机全路径 / claim 防重复执行
  - 深拷贝隔离
  - 安全边界

使用可控 fake clock，不依赖真实时间等待、网络或数据库。
"""

from datetime import datetime, timedelta, timezone

import pytest

from app.schemas.action import FixOption
from app.services.fix_option_store import (
    FixOptionStore,
    FixOptionStatus,
    StoredFixOption,
)


# ── 固定合法 FixOption ────────────────────────────────────────────────

import re
_OPT_RE = re.compile(r"fix_[0-9a-f]{8}")
_counter = [0]

def _fix_option(option_id: str = "fix_001", **overrides) -> FixOption:
    if not _OPT_RE.fullmatch(option_id):
        _counter[0] += 1
        option_id = f"fix_{_counter[0]:08x}"
    kwargs = {
        "option_id": option_id,
        "title": "重启 nginx 服务",
        "description": "执行 systemctl restart nginx",
        "risk_level": "medium",
        "tool": "service_mgr",
        "params": {"action": "restart", "service": "nginx"},
        "requires_confirm": True,
        "rollback": "检查日志后恢复配置",
        **overrides,
    }
    return FixOption(**kwargs)


# ── 固定时钟 ──────────────────────────────────────────────────────────

def _fixed_clock(t: datetime):
    """返回一个始终返回固定时间的 clock"""
    return lambda: t


# ═══════════════════════════════════════════════════════════════════════
# 保存与查询
# ═══════════════════════════════════════════════════════════════════════

class TestSaveAndGet:
    """保存与回查"""

    def test_save_one_option(self):
        store = FixOptionStore()
        opt = _fix_option("fix_2bb225ce")
        ids = store.save_options("s1", "t1", [opt])
        assert ids == ["fix_2bb225ce"]
        stored = store.get_option("s1", "fix_2bb225ce")
        assert stored is not None
        assert stored.option.option_id == "fix_2bb225ce"
        assert isinstance(stored.option, FixOption)

    def test_save_multiple_options(self):
        store = FixOptionStore()
        opts = [_fix_option("fix_0cc175b9"), _fix_option("fix_92eb5ffe"), _fix_option("fix_4a8a08f0")]
        ids = store.save_options("s1", "t1", opts)
        assert ids == ["fix_0cc175b9", "fix_92eb5ffe", "fix_4a8a08f0"]

    def test_get_returns_correct_option(self):
        store = FixOptionStore()
        store.save_options("s1", "t1", [_fix_option("fix_9dd4e461", title="X")])
        stored = store.get_option("s1", "fix_9dd4e461")
        assert stored is not None
        assert stored.option.title == "X"

    def test_get_unknown_returns_none(self):
        store = FixOptionStore()
        assert store.get_option("s1", "nonexistent") is None

    def test_list_options_per_session(self):
        store = FixOptionStore()
        store.save_options("s1", "t1", [_fix_option("fix_0cc175b9"), _fix_option("fix_92eb5ffe")])
        store.save_options("s2", "t2", [_fix_option("fix_4a8a08f0")])
        s1_list = store.list_options("s1")
        s2_list = store.list_options("s2")
        assert len(s1_list) == 2
        assert len(s2_list) == 1
        assert {s.option.option_id for s in s1_list} == {"fix_0cc175b9", "fix_92eb5ffe"}

    def test_list_unknown_session_returns_empty(self):
        store = FixOptionStore()
        assert store.list_options("nonexistent") == []

    def test_empty_options_returns_empty_list(self):
        store = FixOptionStore()
        ids = store.save_options("s1", "t1", [])
        assert ids == []


# ═══════════════════════════════════════════════════════════════════════
# 隔离
# ═══════════════════════════════════════════════════════════════════════

class TestIsolation:
    """session 隔离与重复保护"""

    def test_different_session_same_option_id_independent(self):
        store = FixOptionStore()
        store.save_options("s1", "t1", [_fix_option("fix_0e9f1e8e")])
        store.save_options("s2", "t2", [_fix_option("fix_0e9f1e8e")])
        assert store.get_option("s1", "fix_0e9f1e8e") is not None
        assert store.get_option("s2", "fix_0e9f1e8e") is not None

    def test_same_session_duplicate_option_id_rejected(self):
        store = FixOptionStore()
        store.save_options("s1", "t1", [_fix_option("fix_0e9f1e8e")])
        with pytest.raises(ValueError, match="option_id 重复"):
            store.save_options("s1", "t2", [_fix_option("fix_0e9f1e8e")])

    def test_duplicate_after_expired_allowed(self):
        """已过期（终态）的 option 可以被同名覆盖"""
        t0 = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        store = FixOptionStore(ttl_seconds=60, clock=_fixed_clock(t0))
        store.save_options("s1", "t1", [_fix_option("fix_0e9f1e8e")])
        # 快进到过期
        store._clock = _fixed_clock(t0 + timedelta(seconds=120))
        store.save_options("s1", "t2", [_fix_option("fix_0e9f1e8e")])
        assert store.get_option("s1", "fix_0e9f1e8e") is not None


# ═══════════════════════════════════════════════════════════════════════
# 输入校验
# ═══════════════════════════════════════════════════════════════════════

class TestInputValidation:
    """输入校验"""

    def test_empty_session_id_rejected(self):
        store = FixOptionStore()
        with pytest.raises(ValueError, match="session_id"):
            store.save_options("", "t1", [_fix_option()])

    def test_whitespace_session_id_rejected(self):
        store = FixOptionStore()
        with pytest.raises(ValueError, match="session_id"):
            store.save_options("   ", "t1", [_fix_option()])

    def test_empty_trace_id_rejected(self):
        store = FixOptionStore()
        with pytest.raises(ValueError, match="trace_id"):
            store.save_options("s1", "", [_fix_option()])

    def test_options_not_list_rejected(self):
        store = FixOptionStore()
        with pytest.raises(ValueError, match="options 必须是 list"):
            store.save_options("s1", "t1", None)  # type: ignore

    def test_ttl_zero_rejected(self):
        with pytest.raises(ValueError, match="ttl_seconds"):
            FixOptionStore(ttl_seconds=0)

    def test_ttl_negative_rejected(self):
        with pytest.raises(ValueError, match="ttl_seconds"):
            FixOptionStore(ttl_seconds=-1)


# ═══════════════════════════════════════════════════════════════════════
# 深拷贝隔离
# ═══════════════════════════════════════════════════════════════════════

class TestDeepCopy:
    """外部修改不影响内部存储"""

    def test_save_then_modify_original_no_effect(self):
        store = FixOptionStore()
        opt = _fix_option("fix_2bb225ce", title="原始标题")
        store.save_options("s1", "t1", [opt])
        # 修改原对象
        opt.title = "被篡改的标题"  # type: ignore[attr-defined]
        stored = store.get_option("s1", "fix_2bb225ce")
        assert stored is not None
        assert stored.option.title == "原始标题"

    def test_get_returned_modify_no_effect(self):
        store = FixOptionStore()
        store.save_options("s1", "t1", [_fix_option("fix_2bb225ce")])
        stored1 = store.get_option("s1", "fix_2bb225ce")
        assert stored1 is not None
        stored1.option.title = "外部修改"  # type: ignore[attr-defined]
        stored2 = store.get_option("s1", "fix_2bb225ce")
        assert stored2 is not None
        assert stored2.option.title != stored1.option.title


# ═══════════════════════════════════════════════════════════════════════
# TTL 与过期
# ═══════════════════════════════════════════════════════════════════════

class TestTTL:
    """TTL 过期逻辑"""

    def test_not_expired_can_get(self):
        store = FixOptionStore(ttl_seconds=60)
        store.save_options("s1", "t1", [_fix_option("fix_2bb225ce")])
        stored = store.get_option("s1", "fix_2bb225ce")
        assert stored is not None
        assert stored.status == "pending"

    def test_expired_get_marks_expired(self):
        t0 = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        store = FixOptionStore(ttl_seconds=60, clock=_fixed_clock(t0))
        store.save_options("s1", "t1", [_fix_option("fix_2bb225ce")])
        # 快进 61 秒
        store._clock = _fixed_clock(t0 + timedelta(seconds=61))
        stored = store.get_option("s1", "fix_2bb225ce")
        assert stored is not None
        assert stored.status == "expired"

    def test_expired_cannot_claim(self):
        t0 = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        store = FixOptionStore(ttl_seconds=60, clock=_fixed_clock(t0))
        store.save_options("s1", "t1", [_fix_option("fix_2bb225ce")])
        store._clock = _fixed_clock(t0 + timedelta(seconds=61))
        claimed = store.claim_for_execution("s1", "fix_2bb225ce")
        assert claimed is None

    def test_cleanup_removes_expired(self):
        t0 = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        store = FixOptionStore(ttl_seconds=60, clock=_fixed_clock(t0))
        store.save_options("s1", "t1", [_fix_option("fix_0cc175b9"), _fix_option("fix_92eb5ffe")])
        # 未过期
        assert store.cleanup_expired() == 0
        # 过期
        store._clock = _fixed_clock(t0 + timedelta(seconds=120))
        assert store.cleanup_expired() == 2
        assert store.get_option("s1", "fix_0cc175b9") is None

    def test_cleanup_not_remove_unexpired(self):
        t0 = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        store = FixOptionStore(ttl_seconds=60, clock=_fixed_clock(t0))
        store.save_options("s1", "t1", [_fix_option("fix_0cc175b9")])
        store._clock = _fixed_clock(t0 + timedelta(seconds=30))
        assert store.cleanup_expired() == 0
        assert store.get_option("s1", "fix_0cc175b9") is not None


# ═══════════════════════════════════════════════════════════════════════
# 状态机
# ═══════════════════════════════════════════════════════════════════════

class TestStateMachine:
    """状态转移"""

    # ── claim ──

    def test_pending_can_claim(self):
        store = FixOptionStore()
        store.save_options("s1", "t1", [_fix_option("fix_2bb225ce")])
        claimed = store.claim_for_execution("s1", "fix_2bb225ce")
        assert claimed is not None
        assert claimed.status == "executing"
        # 确认状态已持久化
        assert store.get_option("s1", "fix_2bb225ce") is not None
        assert store.get_option("s1", "fix_2bb225ce").status == "executing"  # type: ignore[reportOptionalMemberAccess]

    def test_executing_cannot_claim_again(self):
        store = FixOptionStore()
        store.save_options("s1", "t1", [_fix_option("fix_2bb225ce")])
        store.claim_for_execution("s1", "fix_2bb225ce")
        claimed2 = store.claim_for_execution("s1", "fix_2bb225ce")
        assert claimed2 is None

    def test_executed_cannot_claim(self):
        store = FixOptionStore()
        store.save_options("s1", "t1", [_fix_option("fix_2bb225ce")])
        store.claim_for_execution("s1", "fix_2bb225ce")
        store.mark_executed("s1", "fix_2bb225ce")
        assert store.claim_for_execution("s1", "fix_2bb225ce") is None

    def test_blocked_cannot_claim(self):
        store = FixOptionStore()
        store.save_options("s1", "t1", [_fix_option("fix_2bb225ce")])
        store.mark_blocked("s1", "fix_2bb225ce")
        assert store.claim_for_execution("s1", "fix_2bb225ce") is None

    def test_failed_cannot_claim(self):
        store = FixOptionStore()
        store.save_options("s1", "t1", [_fix_option("fix_2bb225ce")])
        store.claim_for_execution("s1", "fix_2bb225ce")
        store.mark_failed("s1", "fix_2bb225ce")
        assert store.claim_for_execution("s1", "fix_2bb225ce") is None

    def test_confirm_required_can_claim(self):
        """confirm_required 可通过 claim 进入 executing（medium approve 需要）"""
        store = FixOptionStore()
        store.save_options("s1", "t1", [_fix_option("fix_2bb225ce")])
        store.mark_confirm_required("s1", "fix_2bb225ce")
        assert store.claim_for_execution("s1", "fix_2bb225ce") is not None

    # ── mark_executed ──

    def test_mark_executed_from_executing(self):
        store = FixOptionStore()
        store.save_options("s1", "t1", [_fix_option("fix_2bb225ce")])
        store.claim_for_execution("s1", "fix_2bb225ce")
        assert store.mark_executed("s1", "fix_2bb225ce") is True
        assert store.get_option("s1", "fix_2bb225ce") is not None
        assert store.get_option("s1", "fix_2bb225ce").status == "executed"  # type: ignore[reportOptionalMemberAccess]

    def test_mark_executed_from_pending_fails(self):
        store = FixOptionStore()
        store.save_options("s1", "t1", [_fix_option("fix_2bb225ce")])
        assert store.mark_executed("s1", "fix_2bb225ce") is False
        assert store.get_option("s1", "fix_2bb225ce") is not None
        assert store.get_option("s1", "fix_2bb225ce").status == "pending"  # type: ignore[reportOptionalMemberAccess]

    # ── mark_failed ──

    def test_mark_failed_from_executing(self):
        store = FixOptionStore()
        store.save_options("s1", "t1", [_fix_option("fix_2bb225ce")])
        store.claim_for_execution("s1", "fix_2bb225ce")
        assert store.mark_failed("s1", "fix_2bb225ce") is True
        assert store.get_option("s1", "fix_2bb225ce") is not None
        assert store.get_option("s1", "fix_2bb225ce").status == "failed"  # type: ignore[reportOptionalMemberAccess]

    def test_mark_failed_from_pending_fails(self):
        store = FixOptionStore()
        store.save_options("s1", "t1", [_fix_option("fix_2bb225ce")])
        assert store.mark_failed("s1", "fix_2bb225ce") is False

    # ── mark_blocked ──

    def test_mark_blocked_from_pending(self):
        store = FixOptionStore()
        store.save_options("s1", "t1", [_fix_option("fix_2bb225ce")])
        assert store.mark_blocked("s1", "fix_2bb225ce") is True
        assert store.get_option("s1", "fix_2bb225ce") is not None
        assert store.get_option("s1", "fix_2bb225ce").status == "blocked"  # type: ignore[reportOptionalMemberAccess]

    def test_mark_blocked_from_confirm_required(self):
        store = FixOptionStore()
        store.save_options("s1", "t1", [_fix_option("fix_2bb225ce")])
        store.mark_confirm_required("s1", "fix_2bb225ce")
        assert store.mark_blocked("s1", "fix_2bb225ce") is True
        assert store.get_option("s1", "fix_2bb225ce") is not None
        assert store.get_option("s1", "fix_2bb225ce").status == "blocked"  # type: ignore[reportOptionalMemberAccess]

    def test_mark_blocked_from_executing_fails(self):
        store = FixOptionStore()
        store.save_options("s1", "t1", [_fix_option("fix_2bb225ce")])
        store.claim_for_execution("s1", "fix_2bb225ce")
        assert store.mark_blocked("s1", "fix_2bb225ce") is False

    # ── mark_confirm_required ──

    def test_mark_confirm_required_from_pending(self):
        store = FixOptionStore()
        store.save_options("s1", "t1", [_fix_option("fix_2bb225ce")])
        assert store.mark_confirm_required("s1", "fix_2bb225ce") is True
        assert store.get_option("s1", "fix_2bb225ce") is not None
        assert store.get_option("s1", "fix_2bb225ce").status == "confirm_required"  # type: ignore[reportOptionalMemberAccess]

    # ── 未知 option ──

    def test_transition_unknown_option_returns_false(self):
        store = FixOptionStore()
        assert store.mark_executed("s1", "nope") is False
        assert store.mark_blocked("s1", "nope") is False
        assert store.mark_failed("s1", "nope") is False
        assert store.mark_confirm_required("s1", "nope") is False


# ═══════════════════════════════════════════════════════════════════════
# 安全边界
# ═══════════════════════════════════════════════════════════════════════

class TestSafetyBoundary:
    """FixOptionStore 不调用任何执行模块"""

    def test_no_mcp_client_import(self):
        """模块不导入 MCPClient（注释中提及不算导入）"""
        import app.services.fix_option_store as fos
        from pathlib import Path
        src_lines = Path(fos.__file__).read_text(encoding="utf-8").splitlines()
        for line in src_lines:
            s = line.strip()
            if s.startswith("#") or s.startswith('"""') or s.startswith("'''"):
                continue
            if "MCPClient" in s and ("import" in s or "from" in s):
                raise AssertionError(f"MCPClient 不应被导入: {s}")

    def test_no_agent_harness_import(self):
        """模块不导入 AgentHarness（注释中提及不算导入）"""
        import app.services.fix_option_store as fos
        from pathlib import Path
        src_lines = Path(fos.__file__).read_text(encoding="utf-8").splitlines()
        for line in src_lines:
            s = line.strip()
            if s.startswith("#") or s.startswith('"""') or s.startswith("'''"):
                continue
            if "AgentHarness" in s and ("import" in s or "from" in s):
                raise AssertionError(f"AgentHarness 不应被导入: {s}")

    def test_no_llm_client_import(self):
        """模块不导入 LLMClient（注释中提及不算导入）"""
        import app.services.fix_option_store as fos
        from pathlib import Path
        src_lines = Path(fos.__file__).read_text(encoding="utf-8").splitlines()
        for line in src_lines:
            s = line.strip()
            if s.startswith("#") or s.startswith('"""') or s.startswith("'''"):
                continue
            if "LLMClient" in s and ("import" in s or "from" in s):
                raise AssertionError(f"LLMClient 不应被导入: {s}")

    def test_no_subprocess(self):
        import app.services.fix_option_store as fos
        from pathlib import Path
        text = Path(fos.__file__).read_text(encoding="utf-8")
        assert "subprocess" not in text
        assert "os.system" not in text
