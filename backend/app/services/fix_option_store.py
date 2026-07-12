"""FixOptionStore —— FixOption 后端状态存储服务

职责：
  - 保存 FixPlanner 生成的 FixOption，绑定 session_id + trace_id
  - 根据 session_id + option_id 回查原始选项
  - 支持状态流转、TTL 过期、防重复执行
  - 不调用 MCPClient、AgentHarness、SafetyGuard、LLMClient
  - 不执行系统命令

存储为进程内存，进程重启后数据丢失，多 worker 之间不共享。
后续可替换为 SQLite/Redis 后端。
"""

import logging
import threading
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Literal

from app.schemas.action import FixOption

logger = logging.getLogger(__name__)

# ── 状态定义 ──────────────────────────────────────────────────────────
# 状态机规则集中定义于此，所有 *_ALLOWED_FROM 决定合法转移。

FixOptionStatus = Literal[
    "pending",
    "confirm_required",
    "executing",
    "executed",
    "blocked",
    "failed",
    "expired",
]

# 允许转移到 executing 的状态（claim_for_execution）
_EXECUTABLE_FROM: set[FixOptionStatus] = {"pending", "confirm_required"}

# mark_executed 只允许从 executing
_EXECUTED_FROM: set[FixOptionStatus] = {"executing"}

# mark_failed 只允许从 executing
_FAILED_FROM: set[FixOptionStatus] = {"executing"}

# mark_blocked 允许从 pending / confirm_required
_BLOCKED_FROM: set[FixOptionStatus] = {"pending", "confirm_required"}

# mark_confirm_required 允许从 pending
_CONFIRM_REQUIRED_FROM: set[FixOptionStatus] = {"pending"}

# 已终态集合
_TERMINAL_STATUSES: set[FixOptionStatus] = {
    "executed", "blocked", "failed", "expired",
}


# ── 内部记录 ──────────────────────────────────────────────────────────

@dataclass
class StoredFixOption:
    """单条 FixOption 存储记录"""
    session_id: str
    trace_id: str
    option: FixOption          # 深拷贝后的 FixOption
    status: FixOptionStatus    # 当前状态
    created_at: datetime
    expires_at: datetime
    executed_at: datetime | None = None


# ═══════════════════════════════════════════════════════════════════════
# FixOptionStore
# ═══════════════════════════════════════════════════════════════════════

class FixOptionStore:
    """FixOption 后端状态存储服务

    使用方式：
        store = FixOptionStore(ttl_seconds=900)
        store.save_options(session_id, trace_id, options)
        stored = store.get_option(session_id, option_id)
    """

    def __init__(
        self,
        ttl_seconds: int = 900,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        """参数:
            ttl_seconds: 选项过期时间（秒），必须 > 0
            clock: 可注入时钟函数，默认 UTC 时间
        """
        if ttl_seconds <= 0:
            raise ValueError(f"ttl_seconds 必须 > 0，当前值: {ttl_seconds}")

        self._ttl = ttl_seconds
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._store: dict[tuple[str, str], StoredFixOption] = {}
        self._lock = threading.RLock()

    # ── 保存 ──────────────────────────────────────────────────────

    def save_options(
        self,
        session_id: str,
        trace_id: str,
        options: list[FixOption],
    ) -> list[str]:
        """保存一批 FixOption，返回已保存的 option_id 列表

        输入校验：
          - session_id / trace_id 非空
          - options 为 list 且非空

        重复 option_id（同一 session）会抛出 ValueError。
        """
        if not session_id or not isinstance(session_id, str) or not session_id.strip():
            raise ValueError("session_id 不能为空")
        if not trace_id or not isinstance(trace_id, str) or not trace_id.strip():
            raise ValueError("trace_id 不能为空")
        if not isinstance(options, list):
            raise ValueError("options 必须是 list")
        if not options:
            return []

        now = self._clock()
        expires_at = now + timedelta(seconds=self._ttl)
        saved_ids: list[str] = []

        with self._lock:
            for opt in options:
                key = (session_id, opt.option_id)
                existing = self._store.get(key)
                # 已过期的旧记录允许被覆盖（不抛重复异常）
                if existing is not None and now >= existing.expires_at:
                    existing = None
                if existing is not None and existing.status not in _TERMINAL_STATUSES:
                    raise ValueError(
                        f"option_id 重复: {opt.option_id} "
                        f"(session={session_id}, status={existing.status})"
                    )
                # 深拷贝防止外部修改
                copied = opt.model_copy(deep=True)
                self._store[key] = StoredFixOption(
                    session_id=session_id,
                    trace_id=trace_id,
                    option=copied,
                    status="pending",
                    created_at=now,
                    expires_at=expires_at,
                )
                saved_ids.append(opt.option_id)

        return saved_ids

    # ── 查询 ──────────────────────────────────────────────────────

    def get_option(
        self,
        session_id: str,
        option_id: str,
    ) -> StoredFixOption | None:
        """按 session_id + option_id 回查，过期自动标记为 expired

        返回独立副本，外部修改不影响内部存储。
        """
        key = (session_id, option_id)
        now = self._clock()

        with self._lock:
            stored = self._store.get(key)
            if stored is None:
                return None
            # 检查过期
            if now >= stored.expires_at:
                stored.status = "expired"
                self._store[key] = stored
                return deepcopy(stored)
            return deepcopy(stored)

    def list_options(self, session_id: str) -> list[StoredFixOption]:
        """列出指定 session 的所有 option，过期自动标记"""
        now = self._clock()
        results: list[StoredFixOption] = []

        with self._lock:
            for key, stored in self._store.items():
                if stored.session_id != session_id:
                    continue
                if now >= stored.expires_at:
                    stored.status = "expired"
                    self._store[key] = stored
                results.append(deepcopy(stored))

        return results

    # ── 状态流转 ──────────────────────────────────────────────────

    def mark_confirm_required(
        self,
        session_id: str,
        option_id: str,
    ) -> bool:
        """将 pending → confirm_required"""
        return self._transition(
            session_id, option_id, "confirm_required", _CONFIRM_REQUIRED_FROM,
        )

    def claim_for_execution(
        self,
        session_id: str,
        option_id: str,
    ) -> StoredFixOption | None:
        """领取执行权：pending → executing

        只有 pending 状态可领取；已过期拒绝领取。
        返回深拷贝或 None。
        """
        key = (session_id, option_id)
        now = self._clock()

        with self._lock:
            stored = self._store.get(key)
            if stored is None:
                return None
            if now >= stored.expires_at:
                stored.status = "expired"
                self._store[key] = stored
                return None
            if stored.status not in _EXECUTABLE_FROM:
                return None
            stored.status = "executing"
            self._store[key] = stored
            return deepcopy(stored)

    def mark_executed(
        self,
        session_id: str,
        option_id: str,
    ) -> bool:
        """executing → executed"""
        return self._transition(session_id, option_id, "executed", _EXECUTED_FROM)

    def mark_blocked(
        self,
        session_id: str,
        option_id: str,
    ) -> bool:
        """pending / confirm_required → blocked"""
        return self._transition(session_id, option_id, "blocked", _BLOCKED_FROM)

    def mark_failed(
        self,
        session_id: str,
        option_id: str,
    ) -> bool:
        """executing → failed"""
        return self._transition(session_id, option_id, "failed", _FAILED_FROM)

    # ── 清理 ──────────────────────────────────────────────────────

    def cleanup_expired(self) -> int:
        """删除已过期记录，返回删除数量"""
        now = self._clock()
        removed = 0
        with self._lock:
            expired_keys = [
                key for key, stored in self._store.items()
                if now >= stored.expires_at
            ]
            for key in expired_keys:
                del self._store[key]
                removed += 1
        return removed

    # ── 内部辅助 ──────────────────────────────────────────────────

    def _transition(
        self,
        session_id: str,
        option_id: str,
        target: FixOptionStatus,
        allowed_from: set[FixOptionStatus],
    ) -> bool:
        """通用状态转移：检查前置条件后更新状态"""
        key = (session_id, option_id)
        with self._lock:
            stored = self._store.get(key)
            if stored is None:
                return False
            if stored.status not in allowed_from:
                return False
            stored.status = target
            self._store[key] = stored
            return True
