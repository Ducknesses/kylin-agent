"""ResourceLimiter / CgroupV2Limiter 单元测试

使用临时目录模拟 /sys/fs/cgroup 文件系统（FakeCgroupFilesystem）。
不依赖真实 cgroups 权限、设备号、用户 ID。
"""
import os
import tempfile
import uuid

import pytest

# 确保 mcp-server 在 Python path
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import config
from resource_limiter import (
    CgroupV2Limiter,
    ResourceLimitError,
    ResourceLimiter,
    _sanitize_name,
)


# ── FakeCgroupFilesystem Fixture ─────────────────────────────────────

@pytest.fixture
def fake_cgroup_root(monkeypatch):
    """创建临时 cgroup 文件系统，monkeypatch _CGROUP_ROOT 指向临时目录"""
    with tempfile.TemporaryDirectory(prefix="fake-cgroup-") as tmpdir:
        # 创建 cgroup v2 基础结构
        ns_dir = os.path.join(tmpdir, "mcp-server")
        os.makedirs(ns_dir, exist_ok=True)

        # patch CgroupV2Limiter 使用的根路径
        monkeypatch.setattr("resource_limiter._CGROUP_ROOT", tmpdir)
        monkeypatch.setattr("resource_limiter._MCP_NAMESPACE", "mcp-server")

        # 模拟 cgroup v2 挂载检测
        def _fake_check():
            return True
        monkeypatch.setattr(CgroupV2Limiter, "_check_v2_support", staticmethod(_fake_check))

        yield tmpdir


@pytest.fixture
def limiter_with_cgroup(fake_cgroup_root, monkeypatch):
    """创建已启用 cgroup 的 CgroupV2Limiter"""
    monkeypatch.setattr(config, "CGROUP_ENABLED", True)
    return CgroupV2Limiter()


# ═══════════════════════════════════════════════════════════════════════
# cgroup 名称安全
# ═══════════════════════════════════════════════════════════════════════

class TestCgroupName:
    """cgroup 名称生成与安全"""

    def test_sanitize_valid_name(self):
        assert _sanitize_name("abc123") == "abc123"

    def test_sanitize_rejects_slash(self):
        with pytest.raises(ResourceLimitError, match="非法字符"):
            _sanitize_name("a/b")

    def test_sanitize_rejects_dotdot(self):
        with pytest.raises(ResourceLimitError, match="非法字符"):
            _sanitize_name("..")

    def test_sanitize_rejects_empty(self):
        with pytest.raises(ResourceLimitError, match="不能为空"):
            _sanitize_name("")

    def test_sanitize_rejects_too_long(self):
        with pytest.raises(ResourceLimitError, match="过长"):
            _sanitize_name("a" * 200)

    def test_unique_per_call(self):
        """每次调用生成不同的 UUID"""
        id1 = uuid.uuid4().hex[:12]
        id2 = uuid.uuid4().hex[:12]
        assert id1 != id2


# ═══════════════════════════════════════════════════════════════════════
# cgroup setup
# ═══════════════════════════════════════════════════════════════════════

class TestCgroupSetup:
    """cgroup 创建和资源限制写入"""

    def test_setup_creates_directory(self, limiter_with_cgroup, fake_cgroup_root):
        task_id = uuid.uuid4().hex[:12]
        limiter_with_cgroup.setup(task_id)
        cgroup_dir = os.path.join(fake_cgroup_root, "mcp-server", f"task-{task_id}")
        assert os.path.isdir(cgroup_dir)

    def test_setup_disabled_returns_false(self, monkeypatch):
        monkeypatch.setattr(config, "CGROUP_ENABLED", False)
        limiter = CgroupV2Limiter()
        assert limiter.setup("test123") is False

    def test_cpu_limit_written(self, limiter_with_cgroup, fake_cgroup_root, monkeypatch):
        monkeypatch.setattr(config, "CGROUP_CPU_QUOTA", "30000")
        monkeypatch.setattr(config, "CGROUP_CPU_PERIOD", "100000")
        task_id = uuid.uuid4().hex[:12]
        limiter_with_cgroup.setup(task_id)
        cpu_file = os.path.join(fake_cgroup_root, "mcp-server", f"task-{task_id}", "cpu.max")
        with open(cpu_file) as f:
            assert f.read().strip() == "30000 100000"

    def test_memory_limit_written(self, limiter_with_cgroup, fake_cgroup_root, monkeypatch):
        monkeypatch.setattr(config, "CGROUP_MEMORY_MAX", "134217728")  # 128MB
        task_id = uuid.uuid4().hex[:12]
        limiter_with_cgroup.setup(task_id)
        mem_file = os.path.join(fake_cgroup_root, "mcp-server", f"task-{task_id}", "memory.max")
        with open(mem_file) as f:
            assert f.read().strip() == "134217728"

    def test_swap_limit_written(self, limiter_with_cgroup, fake_cgroup_root, monkeypatch):
        monkeypatch.setattr(config, "CGROUP_MEMORY_SWAP_MAX", "0")
        task_id = uuid.uuid4().hex[:12]
        limiter_with_cgroup.setup(task_id)
        swap_file = os.path.join(fake_cgroup_root, "mcp-server", f"task-{task_id}", "memory.swap.max")
        with open(swap_file) as f:
            assert f.read().strip() == "0"

    def test_pids_limit_written(self, limiter_with_cgroup, fake_cgroup_root, monkeypatch):
        monkeypatch.setattr(config, "CGROUP_PIDS_MAX", "32")
        task_id = uuid.uuid4().hex[:12]
        limiter_with_cgroup.setup(task_id)
        pids_file = os.path.join(fake_cgroup_root, "mcp-server", f"task-{task_id}", "pids.max")
        with open(pids_file) as f:
            assert f.read().strip() == "32"

    def test_io_limit_written_when_configured(self, limiter_with_cgroup, fake_cgroup_root, monkeypatch):
        monkeypatch.setattr(config, "CGROUP_IO_MAX", "8:0 rbps=10485760")
        task_id = uuid.uuid4().hex[:12]
        limiter_with_cgroup.setup(task_id)
        io_file = os.path.join(fake_cgroup_root, "mcp-server", f"task-{task_id}", "io.max")
        with open(io_file) as f:
            assert "rbps=10485760" in f.read()

    def test_io_limit_skipped_when_empty(self, limiter_with_cgroup, fake_cgroup_root, monkeypatch):
        monkeypatch.setattr(config, "CGROUP_IO_MAX", "")
        task_id = uuid.uuid4().hex[:12]
        limiter_with_cgroup.setup(task_id)
        io_file = os.path.join(fake_cgroup_root, "mcp-server", f"task-{task_id}", "io.max")
        assert not os.path.exists(io_file)


# ═══════════════════════════════════════════════════════════════════════
# PID 加入与进程终止
# ═══════════════════════════════════════════════════════════════════════

class TestProcessAttach:
    """进程加入 cgroup"""

    def test_attach_writes_pid(self, limiter_with_cgroup, fake_cgroup_root):
        task_id = uuid.uuid4().hex[:12]
        limiter_with_cgroup.setup(task_id)
        # 使用当前进程 PID 测试写入
        limiter_with_cgroup.attach(os.getpid())
        procs_file = os.path.join(fake_cgroup_root, "mcp-server", f"task-{task_id}", "cgroup.procs")
        with open(procs_file) as f:
            content = f.read()
            assert str(os.getpid()) in content

    def test_kill_all_clears_pids(self, limiter_with_cgroup, fake_cgroup_root):
        """kill_all 读取并处理 cgroup.procs 内容，不应抛异常"""
        task_id = uuid.uuid4().hex[:12]
        limiter_with_cgroup.setup(task_id)
        # 写入一个假 PID（已不存在的进程）
        procs_file = os.path.join(fake_cgroup_root, "mcp-server", f"task-{task_id}", "cgroup.procs")
        with open(procs_file, "w") as f:
            f.write("999999\n")
        # kill_all 应该静默处理不存在的 PID
        limiter_with_cgroup.kill_all()  # 不应抛异常


# ═══════════════════════════════════════════════════════════════════════
# cleanup
# ═══════════════════════════════════════════════════════════════════════

class TestCleanup:
    """cgroup 清理"""

    def test_cleanup_removes_directory(self, limiter_with_cgroup, fake_cgroup_root):
        """cleanup 使用 os.rmdir 删除空 cgroup 目录"""
        task_id = uuid.uuid4().hex[:12]
        limiter_with_cgroup.setup(task_id)
        cgroup_dir = os.path.join(fake_cgroup_root, "mcp-server", f"task-{task_id}")
        assert os.path.isdir(cgroup_dir)
        # 模拟真实 cgroupfs：需先删除控制器伪文件，使目录为空
        for fname in os.listdir(cgroup_dir):
            os.remove(os.path.join(cgroup_dir, fname))
        # 确保 cgroup.procs 不存在（cleanup 检查）
        limiter_with_cgroup.cleanup()
        assert not os.path.exists(cgroup_dir)

    def test_cleanup_idempotent(self, limiter_with_cgroup, fake_cgroup_root):
        """重复清理不应抛异常"""
        task_id = uuid.uuid4().hex[:12]
        limiter_with_cgroup.setup(task_id)
        for fname in os.listdir(os.path.join(fake_cgroup_root, "mcp-server", f"task-{task_id}")):
            os.remove(os.path.join(fake_cgroup_root, "mcp-server", f"task-{task_id}", fname))
        limiter_with_cgroup.cleanup()
        limiter_with_cgroup.cleanup()  # 第二次清理：不抛异常

    def test_cleanup_no_shutil_rmtree(self, limiter_with_cgroup, fake_cgroup_root):
        """verify cleanup 不使用 shutil.rmtree"""
        import shutil
        import unittest.mock
        task_id = uuid.uuid4().hex[:12]
        limiter_with_cgroup.setup(task_id)
        cgroup_dir = os.path.join(fake_cgroup_root, "mcp-server", f"task-{task_id}")
        for fname in os.listdir(cgroup_dir):
            os.remove(os.path.join(cgroup_dir, fname))
        with unittest.mock.patch("shutil.rmtree") as mock_rmtree:
            limiter_with_cgroup.cleanup()
            mock_rmtree.assert_not_called()
        assert not os.path.exists(cgroup_dir)

    def test_cleanup_skips_when_pids_left(self, limiter_with_cgroup, fake_cgroup_root):
        """cgroup.procs 非空时跳过 rmdir"""
        task_id = uuid.uuid4().hex[:12]
        limiter_with_cgroup.setup(task_id)
        cgroup_dir = os.path.join(fake_cgroup_root, "mcp-server", f"task-{task_id}")
        # 写入一个假 PID，但保留控制器文件
        with open(os.path.join(cgroup_dir, "cgroup.procs"), "w") as f:
            f.write("1\n")
        limiter_with_cgroup.cleanup()
        # 目录应该还在（有残留 PID）
        assert os.path.isdir(cgroup_dir)

    def test_cleanup_does_not_remove_parent(self, limiter_with_cgroup, fake_cgroup_root):
        """cleanup 不删除父级 mcp-server cgroup"""
        task_id = uuid.uuid4().hex[:12]
        limiter_with_cgroup.setup(task_id)
        cgroup_dir = os.path.join(fake_cgroup_root, "mcp-server", f"task-{task_id}")
        for fname in os.listdir(cgroup_dir):
            os.remove(os.path.join(cgroup_dir, fname))
        limiter_with_cgroup.cleanup()
        # 父目录应仍存在
        assert os.path.isdir(os.path.join(fake_cgroup_root, "mcp-server"))


# ═══════════════════════════════════════════════════════════════════════
# fail-closed
# ═══════════════════════════════════════════════════════════════════════

class TestFailClosed:
    """fail-closed 策略"""

    def test_setup_fails_when_no_permission(self, monkeypatch):
        """无权限时 setup 应抛 ResourceLimitError（fail-closed）"""
        monkeypatch.setattr(config, "CGROUP_ENABLED", True)
        limiter = CgroupV2Limiter()
        monkeypatch.setattr(CgroupV2Limiter, "_check_v2_support", staticmethod(lambda: True))
        # 指向存在的但无法创建子目录的路径
        # 模拟 os.mkdir 抛 PermissionError
        def _fake_mkdir(path):
            raise PermissionError("Permission denied")
        monkeypatch.setattr(os, "mkdir", _fake_mkdir)
        monkeypatch.setattr("resource_limiter._CGROUP_ROOT", "/tmp")
        with pytest.raises(ResourceLimitError, match="无权限"):
            limiter.setup("fail-test")

    def test_disabled_does_not_fail(self, monkeypatch):
        """CGROUP_ENABLED=false 时不抛异常，返回 False"""
        monkeypatch.setattr(config, "CGROUP_ENABLED", False)
        limiter = CgroupV2Limiter()
        result = limiter.setup("test123")
        assert result is False

    def test_no_silent_fallback(self, monkeypatch):
        """CGROUP_ENABLED=true 但环境不支持时应 fail-closed（抛异常）"""
        monkeypatch.setattr(config, "CGROUP_ENABLED", True)
        limiter = CgroupV2Limiter()
        # 模拟 cgroups v2 不可用
        monkeypatch.setattr(CgroupV2Limiter, "_check_v2_support", staticmethod(lambda: False))
        with pytest.raises(ResourceLimitError, match="cgroups v2 不可用"):
            limiter.setup("test123")


# ═══════════════════════════════════════════════════════════════════════
# 并发隔离
# ═══════════════════════════════════════════════════════════════════════

class TestConcurrency:
    """并发任务隔离"""

    def test_concurrent_tasks_separate_cgroups(self, limiter_with_cgroup, fake_cgroup_root):
        """两个任务使用不同 cgroup"""
        task1 = uuid.uuid4().hex[:12]
        task2 = uuid.uuid4().hex[:12]
        limiter_with_cgroup.setup(task1)
        # 创建新的 limiter 实例（模拟并发）
        limiter2 = CgroupV2Limiter()
        limiter2.setup(task2)
        cg1 = os.path.join(fake_cgroup_root, "mcp-server", f"task-{task1}")
        cg2 = os.path.join(fake_cgroup_root, "mcp-server", f"task-{task2}")
        assert cg1 != cg2
        assert os.path.isdir(cg1)
        assert os.path.isdir(cg2)
        limiter_with_cgroup.cleanup()
        limiter2.cleanup()


# ═══════════════════════════════════════════════════════════════════════
# 敏感错误脱敏
# ═══════════════════════════════════════════════════════════════════════

class TestErrorSanitization:
    """敏感信息不出现在错误消息中"""

    def test_error_does_not_expose_system_paths_directly(self, monkeypatch):
        """错误消息中不包含 /sys/fs/cgroup 等系统路径（由 ResourceLimitError 控制）"""
        monkeypatch.setattr(config, "CGROUP_ENABLED", True)
        limiter = CgroupV2Limiter()
        monkeypatch.setattr(CgroupV2Limiter, "_check_v2_support", staticmethod(lambda: False))
        with pytest.raises(ResourceLimitError) as exc:
            limiter.setup("test123")
        # 确认不泄露完整系统路径
        assert "/sys/fs/cgroup" not in str(exc.value)


# ═══════════════════════════════════════════════════════════════════════
# IO 格式校验
# ═══════════════════════════════════════════════════════════════════════

class TestIOValidation:
    """IO 限制格式校验"""

    def test_valid_io_max(self):
        """合法 io.max 不抛异常"""
        from resource_limiter import _validate_io_max
        _validate_io_max("8:0 rbps=10485760 wiops=100")
        _validate_io_max("8:0 wbps=5242880")

    def test_invalid_device_format(self):
        """非法设备格式应拒绝"""
        from resource_limiter import _validate_io_max
        bad_values = [
            "abc:0 rbps=1000",
            "8:abc rbps=1000",
            "8:0:1 rbps=1000",
            "8 rbps=1000",
        ]
        for v in bad_values:
            with pytest.raises(ResourceLimitError):
                _validate_io_max(v)

    def test_invalid_rate_values(self):
        """非法速率值应拒绝"""
        from resource_limiter import _validate_io_max
        bad_values = [
            "8:0 rbps=abc",
            "8:0 rbps=-1",
            "8:0 unknown=1000",
        ]
        for v in bad_values:
            with pytest.raises(ResourceLimitError):
                _validate_io_max(v)

    def test_reject_multiline(self):
        """多行注入应拒绝"""
        from resource_limiter import _validate_io_max
        with pytest.raises(ResourceLimitError, match="换行"):
            _validate_io_max("8:0 rbps=1000\nmemory.max 999")

    def test_reject_path_traversal(self):
        """路径遍历应拒绝"""
        from resource_limiter import _validate_io_max
        for v in ["1:0 rbps=1000..", "2:0 ..riops=100"]:
            with pytest.raises(ResourceLimitError):
                _validate_io_max(v)

    def test_reject_shell_chars(self):
        """Shell 元字符应拒绝"""
        from resource_limiter import _validate_io_max
        for v in ["8:0 rbps=1;rm", "8:0 rbps=100&exit", "8:0 riops=10|cat"]:
            with pytest.raises(ResourceLimitError):
                _validate_io_max(v)

    def test_empty_io_passes(self):
        """空 IO 配置不报错"""
        from resource_limiter import _validate_io_max
        _validate_io_max("")  # 不应抛异常
        _validate_io_max("   ")  # 不应抛异常
