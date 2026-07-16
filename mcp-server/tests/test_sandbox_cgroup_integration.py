"""sandbox.execute() + CgroupV2Limiter 集成测试

验证调用顺序、fail-closed、进程组终止、cleanup 不被覆盖。
使用 monkeypatch 注入 Fake 控制调用顺序，不整体 Mock 全部业务逻辑。
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import config
from sandbox import execute, _kill_process_group
from resource_limiter import CgroupV2Limiter, ResourceLimitError


# ── 辅助 ──────────────────────────────────────────────────────────

class CallRecorder:
    """记录 sandbox 内部调用顺序"""
    def __init__(self):
        self.calls = []

    def record(self, name: str):
        self.calls.append(name)


# ═══════════════════════════════════════════════════════════════════════
# CGROUP_ENABLED=false 保持原有路径
# ═══════════════════════════════════════════════════════════════════════

class TestCgroupDisabled:
    """CGROUP_ENABLED=false 时行为不变"""

    def test_whitelist_block_before_cgroup(self, monkeypatch):
        """白名单外命令在 cgroup 前拦截"""
        monkeypatch.setattr(config, "CGROUP_ENABLED", False)
        recorder = CallRecorder()
        # patch CgroupV2Limiter.setup 确保不被调用
        def fake_setup(self, tid):
            recorder.record("setup")
            return False
        monkeypatch.setattr(CgroupV2Limiter, "setup", fake_setup)
        result = execute("rm -rf /", timeout=5, user="agent-read")
        assert result["blocked"] is True
        assert "setup" not in recorder.calls

    def test_danger_pattern_blocked_before_cgroup(self, monkeypatch):
        """危险字符在 cgroup 前拦截"""
        monkeypatch.setattr(config, "CGROUP_ENABLED", False)
        recorder = CallRecorder()
        def fake_setup(self, tid):
            recorder.record("setup")
            return False
        monkeypatch.setattr(CgroupV2Limiter, "setup", fake_setup)
        # 使用白名单中的模式但添加危险字符
        result = execute("df -h; rm -rf /", timeout=5, user="agent-read")
        assert result["blocked"] is True
        assert "setup" not in recorder.calls


# ═══════════════════════════════════════════════════════════════════════
# fail-closed
# ═══════════════════════════════════════════════════════════════════════

class TestCgroupFailClosed:
    """cgroup 失败时 fail-closed"""

    def test_setup_failure_returns_blocked(self, monkeypatch):
        """setup 失败时返回 blocked=True，不创建 subprocess"""
        monkeypatch.setattr(config, "CGROUP_ENABLED", True)
        def fake_setup(self, tid):
            raise ResourceLimitError("模拟 cgroup 创建失败")
        monkeypatch.setattr(CgroupV2Limiter, "setup", fake_setup)
        result = execute("df -h", timeout=5, user="agent-read")
        assert result["blocked"] is True
        assert "cgroup" in result["stderr"].lower() or "资源" in result["stderr"]

    def test_attach_failure_kills_process_group(self, monkeypatch):
        """attach 失败时调用进程组终止"""
        monkeypatch.setattr(config, "CGROUP_ENABLED", True)
        attach_failed = []

        def fake_setup(self, tid):
            return True

        def fake_attach(self, pid):
            attach_failed.append(True)
            raise ResourceLimitError("模拟 attach 失败")

        kill_called = []
        def fake_killpg(pid, sig):
            kill_called.append((pid, sig))

        monkeypatch.setattr(CgroupV2Limiter, "setup", fake_setup)
        monkeypatch.setattr(CgroupV2Limiter, "attach", fake_attach)
        monkeypatch.setattr("os.killpg", fake_killpg)

        result = execute("df -h", timeout=5, user="agent-read")
        assert result["blocked"] is True
        assert len(attach_failed) > 0
        assert len(kill_called) > 0

    def test_attach_failure_does_not_communicate(self, monkeypatch):
        """attach 失败后不调用 communicate"""
        monkeypatch.setattr(config, "CGROUP_ENABLED", True)
        comm_called = []

        class FakePopen:
            pid = 12345
            def communicate(self, timeout=None):
                comm_called.append(True)
                return ("", "")

        monkeypatch.setattr("subprocess.Popen", lambda *a, **kw: FakePopen())

        def fake_setup(self, tid):
            return True
        def fake_attach(self, pid):
            raise ResourceLimitError("模拟 attach 失败")
        monkeypatch.setattr(CgroupV2Limiter, "setup", fake_setup)
        monkeypatch.setattr(CgroupV2Limiter, "attach", fake_attach)
        monkeypatch.setattr(CgroupV2Limiter, "cleanup", lambda self: None)

        result = execute("df -h", timeout=5, user="agent-read")
        assert result["blocked"] is True
        assert len(comm_called) == 0


# ═══════════════════════════════════════════════════════════════════════
# 调用顺序
# ═══════════════════════════════════════════════════════════════════════

class TestCallOrder:
    """验证调用顺序：validate → setup → Popen → attach → communicate → cleanup"""

    def test_call_order_with_cgroup(self, monkeypatch):
        """CGROUP_ENABLED=true 时的调用顺序"""
        monkeypatch.setattr(config, "CGROUP_ENABLED", True)
        recorder = CallRecorder()

        def fake_setup(self, tid):
            recorder.record("setup")
            return True

        def fake_attach(self, pid):
            recorder.record("attach")

        def fake_cleanup(self):
            recorder.record("cleanup")

        class FakePopen:
            pid = 12345
            returncode = 0
            def communicate(self, timeout=None):
                recorder.record("communicate")
                return ("output", "")

        monkeypatch.setattr("subprocess.Popen", lambda *a, **kw: FakePopen())
        monkeypatch.setattr(CgroupV2Limiter, "setup", fake_setup)
        monkeypatch.setattr(CgroupV2Limiter, "attach", fake_attach)
        monkeypatch.setattr(CgroupV2Limiter, "cleanup", fake_cleanup)

        result = execute("df -h", timeout=5, user="agent-read")
        assert result.get("blocked") is None  # 未拦截
        # 验证顺序
        setup_idx = recorder.calls.index("setup")
        attach_idx = recorder.calls.index("attach")
        comm_idx = recorder.calls.index("communicate")
        cleanup_idx = recorder.calls.index("cleanup")
        assert setup_idx < attach_idx < comm_idx
        assert comm_idx < cleanup_idx


# ═══════════════════════════════════════════════════════════════════════
# cleanup 和异常处理
# ═══════════════════════════════════════════════════════════════════════

class TestCleanupAndErrors:
    """cleanup 在各种异常下仍执行"""

    def test_cleanup_called_on_normal_exit(self, monkeypatch):
        """正常退出后 cleanup 被调用"""
        monkeypatch.setattr(config, "CGROUP_ENABLED", True)
        cleanup_called = []
        def fake_setup(self, tid):
            return True
        def fake_attach(self, pid):
            pass
        def fake_cleanup(self):
            cleanup_called.append(True)

        class FakePopen:
            pid = 12345
            returncode = 0
            def communicate(self, timeout=None):
                return ("out", "")

        monkeypatch.setattr("subprocess.Popen", lambda *a, **kw: FakePopen())
        monkeypatch.setattr(CgroupV2Limiter, "setup", fake_setup)
        monkeypatch.setattr(CgroupV2Limiter, "attach", fake_attach)
        monkeypatch.setattr(CgroupV2Limiter, "cleanup", fake_cleanup)

        execute("df -h", timeout=5, user="agent-read")
        assert len(cleanup_called) > 0

    def test_cleanup_called_on_timeout(self, monkeypatch):
        """timeout 后 cleanup 被调用"""
        monkeypatch.setattr(config, "CGROUP_ENABLED", True)
        cleanup_called = []
        def fake_setup(self, tid):
            return True
        def fake_attach(self, pid):
            pass
        def fake_cleanup(self):
            cleanup_called.append(True)

        class FakePopen:
            pid = 12345
            stdout = None
            def communicate(self, timeout=None):
                raise __import__("subprocess").TimeoutExpired("cmd", timeout)

        monkeypatch.setattr("subprocess.Popen", lambda *a, **kw: FakePopen())
        monkeypatch.setattr(CgroupV2Limiter, "setup", fake_setup)
        monkeypatch.setattr(CgroupV2Limiter, "attach", fake_attach)
        monkeypatch.setattr(CgroupV2Limiter, "cleanup", fake_cleanup)
        monkeypatch.setattr("sandbox._kill_process_group", lambda p: None)
        monkeypatch.setattr("sandbox._stdout_before_kill", lambda p: "")

        result = execute("df -h", timeout=5, user="agent-read")
        assert result["blocked"] is True
        assert len(cleanup_called) > 0

    def test_cleanup_exception_does_not_override_result(self, monkeypatch):
        """cleanup 异常不覆盖原始执行结果"""
        monkeypatch.setattr(config, "CGROUP_ENABLED", True)
        def fake_setup(self, tid):
            return True
        def fake_attach(self, pid):
            pass
        def fake_cleanup(self):
            raise RuntimeError("cleanup 模拟失败")

        class FakePopen:
            pid = 12345
            returncode = 0
            def communicate(self, timeout=None):
                return ("output", "")

        monkeypatch.setattr("subprocess.Popen", lambda *a, **kw: FakePopen())
        monkeypatch.setattr(CgroupV2Limiter, "setup", fake_setup)
        monkeypatch.setattr(CgroupV2Limiter, "attach", fake_attach)
        monkeypatch.setattr(CgroupV2Limiter, "cleanup", fake_cleanup)

        result = execute("df -h", timeout=5, user="agent-read")
        # cleanup 异常不应覆盖结果
        assert result["stdout"] == "output"


# ═══════════════════════════════════════════════════════════════════════
# shell=False / start_new_session
# ═══════════════════════════════════════════════════════════════════════

class TestSubprocessFlags:
    """验证 subprocess 安全参数"""

    def test_popen_uses_start_new_session(self, monkeypatch):
        """Popen 必须使用 start_new_session=True"""
        monkeypatch.setattr(config, "CGROUP_ENABLED", False)
        popen_kwargs = {}

        def fake_popen(*args, **kwargs):
            popen_kwargs.update(kwargs)
            class Fake:
                pid = 12345
                returncode = 0
                def communicate(self, timeout=None):
                    return ("", "")
            return Fake()

        monkeypatch.setattr("subprocess.Popen", fake_popen)
        execute("df -h", timeout=5, user="agent-read")
        assert popen_kwargs.get("start_new_session") is True
        assert popen_kwargs.get("shell") is False

    def test_blocked_result_has_expected_fields(self, monkeypatch):
        """blocked 结果字段不变"""
        monkeypatch.setattr(config, "CGROUP_ENABLED", False)
        result = execute("rm -rf /", timeout=5, user="agent-read")
        assert result["blocked"] is True
        assert "stdout" in result
        assert "stderr" in result
        assert "returncode" in result
        assert "execution_time" in result
        # 不过度检查 stderr 内容（可能因环境不同）
