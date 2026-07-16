"""ResourceLimiter —— cgroups v2 资源限制抽象

职责：
  - 检测 cgroups v2 支持和可用控制器
  - 创建任务级 cgroup（每次执行独立）
  - 写入 CPU/Memory/IO/PIDs 限制
  - 将进程 PID 加入 cgroup
  - 终止 cgroup 内残留进程
  - 清理 cgroup 目录（os.rmdir，不递归删除）

设计决策：
  - 使用 cgroups v2（当前环境已是 v2，v1 已被内核标记为 deprecated）
  - 直接操作 cgroup 文件系统（不依赖第三方库、不 shell=True）
  - cgroup 创建失败时 fail-closed（不继续无限制执行）
  - 每次命令执行使用独立 cgroup（UUID 生成，无路径注入）
  - 单元测试使用临时目录模拟 cgroup 文件系统
  - 真实 cgroupfs 是内核虚拟文件系统，清理时必须使用 os.rmdir
    而非 shutil.rmtree（不能递归删除控制器伪文件）
"""
import logging
import os
import uuid

from config import config

logger = logging.getLogger("mcp.resource_limiter")

# cgroups v2 根路径
_CGROUP_ROOT = "/sys/fs/cgroup"
# 子 cgroup 父目录名（避免直接污染根 cgroup）
_MCP_NAMESPACE = "mcp-server"


class ResourceLimitError(Exception):
    """资源限制失败异常 —— 触发 fail-closed"""


def _sanitize_name(name: str) -> str:
    """校验 cgroup 名称安全：禁止 / .. 空字符 路径分隔符，限制长度"""
    if not name or not name.strip():
        raise ResourceLimitError("cgroup 名称不能为空")
    if "/" in name or ".." in name or "\x00" in name:
        raise ResourceLimitError(f"cgroup 名称包含非法字符: {name}")
    if len(name) > 128:
        raise ResourceLimitError(f"cgroup 名称过长 (>{128}): len={len(name)}")
    return name


class ResourceLimiter:
    """资源限制抽象基类

    生产环境使用 CgroupV2Limiter（真实 cgroups 文件操作）。
    单元测试注入 FakeResourceLimiter（临时目录模拟）。
    """

    def setup(self, task_id: str) -> bool:
        """创建任务级 cgroup 并写入限制。

        返回 True 表示 cgroup 已就绪，False 表示不支持（CGROUP_ENABLED=false）。
        失败时抛出 ResourceLimitError（fail-closed）。
        """
        raise NotImplementedError

    def attach(self, pid: int) -> None:
        """将进程 PID 加入 cgroup"""
        raise NotImplementedError

    def kill_all(self) -> None:
        """终止 cgroup 内所有残留进程（SIGKILL）"""
        raise NotImplementedError

    def cleanup(self) -> None:
        """清理 cgroup 目录。清理失败只写日志，不抛异常。"""
        raise NotImplementedError


class CgroupV2Limiter(ResourceLimiter):
    """cgroups v2 资源限制器 —— 真实 /sys/fs/cgroup 文件操作

    不依赖第三方库、不使用 shell=True、不调用 cgcreate/cgexec/systemd-run。
    """

    def __init__(self) -> None:
        self._cgroup_path: str | None = None
        self._task_id: str = ""

    # ── setup ────────────────────────────────────────────────────

    def setup(self, task_id: str) -> bool:
        """创建任务级 cgroup 并写入 CPU/Memory/IO/PIDs 限制。

        cgroup 路径: /sys/fs/cgroup/mcp-server/task-{task_id}
        """
        if not config.CGROUP_ENABLED:
            logger.info("[Cgroup] 未启用，跳过资源限制")
            return False

        self._task_id = _sanitize_name(task_id)

        # 检查 cgroups v2 支持
        if not self._check_v2_support():
            raise ResourceLimitError(
                "cgroups v2 不可用：系统不支持或未挂载。"
                "请确认 cgroup2fs 已挂载且 controllers 可用。"
            )

        # 确保命名空间目录存在
        ns_path = os.path.join(_CGROUP_ROOT, _MCP_NAMESPACE)
        self._ensure_dir(ns_path)

        # 创建任务级子 cgroup
        cgroup_dir = f"task-{self._task_id}"
        self._cgroup_path = os.path.join(ns_path, cgroup_dir)
        try:
            os.mkdir(self._cgroup_path)
            logger.info("[Cgroup] 已创建: %s", self._cgroup_path)
        except PermissionError:
            raise ResourceLimitError(
                "无权限创建 cgroup：当前用户没有 cgroup 写权限。"
                "请配置 systemd Delegate=yes 或 root 启用 subtree_control。"
            )
        except FileExistsError:
            # 并发同名 cgroup 极罕见（UUID 碰撞），视为失败
            raise ResourceLimitError(f"cgroup 目录已存在: {self._cgroup_path}")

        # 写入资源限制
        try:
            self._write_limits()
        except Exception:
            # 写入限制失败 → 清理已创建目录，fail-closed
            self._rmdir_safe(self._cgroup_path)
            self._cgroup_path = None
            raise

        logger.info(
            "[Cgroup] 限制已配置: task_id=%s cpu=%s mem=%s pids=%s",
            self._task_id,
            f"{config.CGROUP_CPU_QUOTA}/{config.CGROUP_CPU_PERIOD}",
            f"{config.CGROUP_MEMORY_MAX}",
            config.CGROUP_PIDS_MAX,
        )
        return True

    # ── attach ───────────────────────────────────────────────────

    def attach(self, pid: int) -> None:
        """将进程 PID 写入 cgroup.procs，使其受 cgroup 资源限制"""
        if self._cgroup_path is None:
            return
        procs_file = os.path.join(self._cgroup_path, "cgroup.procs")
        try:
            with open(procs_file, "w") as f:
                f.write(str(pid))
            logger.debug("[Cgroup] PID %d 已加入 %s", pid, self._cgroup_path)
        except OSError as e:
            raise ResourceLimitError(
                f"无法将进程加入 cgroup: PID={pid}, error={e}"
            )

    # ── kill_all ─────────────────────────────────────────────────

    def kill_all(self) -> None:
        """终止 cgroup 内所有残留进程

        读取 cgroup.procs 中所有 PID，逐一发送 SIGKILL。
        如果 cgroup 已不存在（已清理），静默跳过。
        """
        if self._cgroup_path is None:
            return
        procs_file = os.path.join(self._cgroup_path, "cgroup.procs")
        if not os.path.exists(procs_file):
            return
        try:
            with open(procs_file, "r") as f:
                pids = [line.strip() for line in f if line.strip()]
        except OSError:
            return

        import signal
        for pid_str in pids:
            try:
                pid = int(pid_str)
                os.kill(pid, signal.SIGKILL)
                logger.debug("[Cgroup] 已终止残留 PID: %d", pid)
            except (ValueError, ProcessLookupError, PermissionError):
                pass  # 进程已退出或无权限

    # ── cleanup ──────────────────────────────────────────────────

    def cleanup(self) -> None:
        """清理 cgroup 目录。

        真实 cgroupfs 清理流程：
        1. kill_all 终止残留进程
        2. 短暂等待进程退出
        3. 确认 cgroup.procs 为空（或不存在）
        4. os.rmdir 删除任务 cgroup 目录（不递归删除，不能删控制器伪文件）
        5. 不删除父级 mcp-server cgroup

        清理失败只写日志，不抛异常（不覆盖原始执行结果）。
        """
        if self._cgroup_path is None:
            return
        try:
            self.kill_all()
            # 等待进程退出
            import time
            time.sleep(0.1)

            # 再次检查并清理残留 PID
            procs_file = os.path.join(self._cgroup_path, "cgroup.procs")
            pids_left = False
            if os.path.exists(procs_file):
                try:
                    with open(procs_file, "r") as f:
                        remaining = [l.strip() for l in f if l.strip()]
                    pids_left = len(remaining) > 0
                except OSError:
                    pass

            if pids_left:
                logger.warning(
                    "[Cgroup] 清理时仍有残留 PID，跳过 rmdir: %s", self._cgroup_path
                )
            else:
                # 真实 cgroupfs：使用 os.rmdir（不递归删除控制器伪文件）
                self._rmdir_safe(self._cgroup_path)
                logger.info("[Cgroup] 已清理: %s", self._cgroup_path)
        except Exception as e:
            logger.warning("[Cgroup] 清理失败（非致命）: %s, error=%s", self._cgroup_path, e)
        finally:
            self._cgroup_path = None

    # ── 内部辅助 ──────────────────────────────────────────────────

    @staticmethod
    def _check_v2_support() -> bool:
        """检测 cgroups v2 支持"""
        if not os.path.exists(_CGROUP_ROOT):
            return False
        try:
            with open("/proc/self/mounts", "r") as f:
                for line in f:
                    if line.startswith("cgroup2 "):
                        return True
        except OSError:
            pass
        return False

    @staticmethod
    def _ensure_dir(path: str) -> None:
        """确保目录存在（不存在则创建），权限不足时抛 ResourceLimitError"""
        if os.path.exists(path):
            return
        try:
            os.mkdir(path)
        except PermissionError:
            raise ResourceLimitError(
                f"无权限创建 cgroup 命名空间目录: {path}"
            )

    def _write_limits(self) -> None:
        """写入 CPU / Memory / Swap / PIDs / IO 限制到 cgroup 控制文件"""
        assert self._cgroup_path is not None

        # CPU: cpu.max = "$QUOTA $PERIOD"
        cpu_file = os.path.join(self._cgroup_path, "cpu.max")
        cpu_val = f"{config.CGROUP_CPU_QUOTA} {config.CGROUP_CPU_PERIOD}"
        self._write_file(cpu_file, cpu_val)

        # Memory: memory.max
        mem_file = os.path.join(self._cgroup_path, "memory.max")
        self._write_file(mem_file, str(config.CGROUP_MEMORY_MAX))

        # Memory Swap: memory.swap.max (0 = 禁用 swap)
        swap_file = os.path.join(self._cgroup_path, "memory.swap.max")
        self._write_file(swap_file, str(config.CGROUP_MEMORY_SWAP_MAX))

        # PIDs: pids.max（防 fork bomb）
        pids_file = os.path.join(self._cgroup_path, "pids.max")
        self._write_file(pids_file, str(config.CGROUP_PIDS_MAX))

        # IO: io.max（可选，配置为空则跳过）
        if config.CGROUP_IO_MAX:
            _validate_io_max(config.CGROUP_IO_MAX)
            io_file = os.path.join(self._cgroup_path, "io.max")
            self._write_file(io_file, config.CGROUP_IO_MAX)

    @staticmethod
    def _write_file(path: str, content: str) -> None:
        """写入 cgroup 控制文件，失败时抛 ResourceLimitError"""
        try:
            with open(path, "w") as f:
                f.write(content)
        except OSError as e:
            raise ResourceLimitError(
                f"无法写入 cgroup 文件: {path}, error={e}"
            )

    @staticmethod
    def _rmdir_safe(path: str) -> None:
        """安全删除目录（os.rmdir，仅删除空目录）。

        真实 cgroupfs 是内核虚拟文件系统，不能用 shutil.rmtree 递归删除。
        必须确保目录为空（所有 PID 已迁出/终止）后再 rmdir。
        """
        try:
            os.rmdir(path)
        except FileNotFoundError:
            pass
        except OSError as e:
            logger.warning("[Cgroup] rmdir 失败: %s, error=%s", path, e)


# ── IO 格式校验 ────────────────────────────────────────────────────

def _validate_io_max(value: str) -> None:
    """校验 io.max 格式，拒绝非法值（路径注入、多行注入、非法设备号等）。

    合法格式: "MAJOR:MINOR rbps=N wiops=N" 或 "MAJOR:MINOR riops=N wbps=N"
    示例: "8:0 rbps=10485760 wiops=100"
    """
    if not value or not value.strip():
        return
    v = value.strip()
    # 拒绝多行注入
    if "\n" in v:
        raise ResourceLimitError(f"io.max 包含换行符，拒绝: {v!r}")
    # 拒绝 shell 元字符（优先检查，因路径检查会匹配 `/`）
    if any(c in v for c in (";", "&", "|", "$", "`")):
        raise ResourceLimitError(f"io.max 包含 shell 元字符，拒绝: {v!r}")
    # 拒绝路径遍历
    if ".." in v:
        raise ResourceLimitError(f"io.max 包含路径字符，拒绝: {v!r}")
    # 格式校验: "MAJOR:MINOR KEY=VALUE..."
    parts = v.split()
    # 第一段必须是 MAJOR:MINOR
    dev = parts[0].split(":")
    if len(dev) != 2:
        raise ResourceLimitError(f"io.max 设备格式无效，需要 MAJOR:MINOR: {v!r}")
    try:
        int(dev[0])
        int(dev[1])
    except ValueError:
        raise ResourceLimitError(f"io.max 设备号非数字: {v!r}")
    # 后续段必须是 KEY=VALUE，且值必须是非负整数或 "max"
    for p in parts[1:]:
        if "=" not in p:
            raise ResourceLimitError(f"io.max 限制字段格式无效: {v!r}")
        k, val = p.split("=", 1)
        if k not in ("rbps", "wbps", "riops", "wiops"):
            raise ResourceLimitError(f"io.max 未知限制字段: {k!r}")
        if val != "max":
            try:
                n = int(val)
                if n < 0:
                    raise ResourceLimitError(f"io.max 限制值不能为负数: {k}={val!r}")
            except ValueError:
                raise ResourceLimitError(f"io.max 限制值非数字: {k}={val!r}")
