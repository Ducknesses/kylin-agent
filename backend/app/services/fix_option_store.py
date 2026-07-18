"""FixOptionStore —— FixOption 后端状态存储服务（Redis 持久化）

职责：
  - 保存 FixPlanner 生成的 FixOption，绑定 session_id + trace_id
  - 根据 session_id + option_id 回查原始选项
  - 支持状态流转、TTL 过期、防重复执行
  - 不调用 MCPClient、AgentHarness、SafetyGuard、LLMClient
  - 不执行系统命令

存储后端：Redis（通过 RedisStorage），服务重启后数据不丢失，多 worker 共享。

Redis Key 格式：agent:fix_option:{session_id}:{option_id}
"""
import logging
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Literal

from app.schemas.action import FixOption
from app.services.storage.redis_storage import RedisStorage

logger = logging.getLogger(__name__)

# ── 状态定义 ──────────────────────────────────────────────────────────
# 状态机规则集中定义于此，所有 *_ALLOWED_FROM 决定合法转移。

FixOptionStatus = Literal[
    "pending",
    "confirm_required",
    "executing",
    "executed",
    "rolling_back",
    "rolled_back",
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

# claim_for_rollback 只允许从 executed
_ROLLING_BACK_FROM: set[FixOptionStatus] = {"executed"}

# mark_rolled_back 只允许从 rolling_back
_ROLLED_BACK_FROM: set[FixOptionStatus] = {"rolling_back"}

# 已终态集合
_TERMINAL_STATUSES: set[FixOptionStatus] = {
    "executed", "rolled_back", "blocked", "failed", "expired",
}

# Redis Key 前缀
_KEY_PREFIX = "agent:fix_option"

# 过期标记后的保留时间（秒）
_EXPIRED_RETENTION_SECONDS = 60


# ── 内部记录 ──────────────────────────────────────────────────────────

@dataclass
class StoredFixOption:
    """单条 FixOption 存储记录"""
    session_id: str
    trace_id: str
    option: FixOption          # FixOption 对象
    status: FixOptionStatus    # 当前状态
    created_at: datetime
    expires_at: datetime
    executed_at: datetime | None = None
    pre_snapshot: dict[str, Any] | None = None  # 操作前状态快照 {tool, params}


# ── 序列化辅助 ────────────────────────────────────────────────────────

def _stored_to_dict(s: StoredFixOption) -> dict[str, Any]:
    """将 StoredFixOption 序列化为可 JSON 存储的 dict"""
    return {
        "session_id": s.session_id,
        "trace_id": s.trace_id,
        "option": s.option.model_dump(mode="json"),
        "status": s.status,
        "created_at": s.created_at.isoformat(),
        "expires_at": s.expires_at.isoformat(),
        "executed_at": s.executed_at.isoformat() if s.executed_at else None,
        "pre_snapshot": s.pre_snapshot,
    }


def _dict_to_stored(d: dict[str, Any]) -> StoredFixOption:
    """从 dict 反序列化 StoredFixOption"""
    return StoredFixOption(
        session_id=d["session_id"],
        trace_id=d["trace_id"],
        option=FixOption(**d["option"]),
        status=d["status"],
        created_at=datetime.fromisoformat(d["created_at"]),
        expires_at=datetime.fromisoformat(d["expires_at"]),
        executed_at=datetime.fromisoformat(d["executed_at"]) if d.get("executed_at") else None,
        pre_snapshot=d.get("pre_snapshot"),
    )


def _build_key(session_id: str, option_id: str) -> str:
    return f"{_KEY_PREFIX}:{session_id}:{option_id}"


# ═══════════════════════════════════════════════════════════════════════
# FixOptionStore（Redis 后端）
# ═══════════════════════════════════════════════════════════════════════

class FixOptionStore:
    """FixOption 后端状态存储服务（Redis 持久化）

    使用方式：
        store = FixOptionStore(ttl_seconds=86400)  # 默认 24 小时
        store.save_options(session_id, trace_id, options)
        stored = store.get_option(session_id, option_id)
    """

    # 默认 TTL：24 小时（按生产化要求）
    # 设计理由：FixOption 包含完整的修复计划数据，需要在服务重启后跨进程恢复。
    # 24 小时窗口覆盖一个完整运维工作日，过期自动释放 Redis 内存。
    DEFAULT_TTL_SECONDS: int = 86400

    def __init__(
        self,
        ttl_seconds: int = DEFAULT_TTL_SECONDS,
        clock: Callable[[], datetime] | None = None,
        storage: RedisStorage | None = None,
    ) -> None:
        """参数:
            ttl_seconds: 选项过期时间（秒），必须 > 0，默认 86400（24h）
            clock: 可注入时钟函数，默认 UTC 时间
            storage: 可注入 RedisStorage（测试用），默认创建真实连接
        """
        if ttl_seconds <= 0:
            raise ValueError(f"ttl_seconds 必须 > 0，当前值: {ttl_seconds}")

        self._ttl = ttl_seconds
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._storage = storage or RedisStorage()

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

        for opt in options:
            redis_key = _build_key(session_id, opt.option_id)

            # 检查重复：先读取已有记录
            existing_raw = self._storage.get(redis_key)
            if existing_raw is not None:
                existing = _dict_to_stored(existing_raw)
                # 已过期的旧记录允许被覆盖（不抛重复异常）
                if now < existing.expires_at and existing.status not in _TERMINAL_STATUSES:
                    raise ValueError(
                        f"option_id 重复: {opt.option_id} "
                        f"(session={session_id}, status={existing.status})"
                    )

            # 深拷贝防止外部修改
            copied = opt.model_copy(deep=True)
            stored = StoredFixOption(
                session_id=session_id,
                trace_id=trace_id,
                option=copied,
                status="pending",
                created_at=now,
                expires_at=expires_at,
            )

            self._storage.set(redis_key, _stored_to_dict(stored), ttl=self._ttl)
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
        redis_key = _build_key(session_id, option_id)
        now = self._clock()

        raw = self._storage.get(redis_key)
        if raw is None:
            return None

        stored = _dict_to_stored(raw)

        # 检查过期
        if now >= stored.expires_at:
            stored.status = "expired"
            # 更新 Redis 中的状态（保留剩余 TTL 或设置较短 TTL）
            remaining_ttl = self._storage.ttl(redis_key)
            if remaining_ttl is not None and remaining_ttl > 0:
                self._storage.set(redis_key, _stored_to_dict(stored), ttl=remaining_ttl)
            else:
                self._storage.set(redis_key, _stored_to_dict(stored), ttl=_EXPIRED_RETENTION_SECONDS)

        return deepcopy(stored)

    def list_options(self, session_id: str) -> list[StoredFixOption]:
        """列出指定 session 的所有 option，过期自动标记"""
        now = self._clock()
        pattern = f"{_KEY_PREFIX}:{session_id}:*"
        results: list[StoredFixOption] = []

        keys = self._storage.scan_keys(pattern)
        for key in keys:
            raw = self._storage.get(key)
            if raw is None:
                continue
            stored = _dict_to_stored(raw)

            if now >= stored.expires_at:
                stored.status = "expired"
                remaining_ttl = self._storage.ttl(key)
                ttl = remaining_ttl if (remaining_ttl is not None and remaining_ttl > 0) else 60
                self._storage.set(key, _stored_to_dict(stored), ttl=ttl)

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
        """领取执行权：pending/confirm_required → executing（Lua 原子操作）

        只有可执行状态可领取；已过期拒绝领取。
        两个并发 worker 同时 claim 时，只有一个成功。
        返回深拷贝或 None。
        """
        return self._atomic_claim(session_id, option_id)

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

    # ── 回滚状态流转 ────────────────────────────────────────────

    def claim_for_rollback(
        self,
        session_id: str,
        option_id: str,
    ) -> StoredFixOption | None:
        """原子领取回滚权：executed → rolling_back（Lua CAS）

        与 claim_for_execution 逻辑同等：
          - 只有 executed 状态可领取回滚权
          - 两个并发 worker 同时 claim 时，只有一个成功
          - 返回深拷贝或 None
        """
        redis_key = _build_key(session_id, option_id)

        ttl = self._storage.ttl(redis_key)
        if ttl <= 0:
            ttl = self._ttl

        def _update(data: dict[str, Any]) -> dict[str, Any] | None:
            now = self._clock()
            stored = _dict_to_stored(data)
            # TTL 检查
            if now >= stored.expires_at:
                return None
            # 状态检查：仅 executed 可进入 rolling_back
            if stored.status not in {"executed"}:
                return None
            data["status"] = "rolling_back"
            return data

        result = self._storage.cas_update(
            redis_key,
            expected_status=None,
            update_fn=_update,
            ttl=ttl,
        )
        if result is None:
            return None
        stored = _dict_to_stored(result)
        if stored.status == "rolling_back":
            return deepcopy(stored)
        return None

    def mark_rolled_back(
        self,
        session_id: str,
        option_id: str,
    ) -> bool:
        """rolling_back → rolled_back"""
        return self._transition(session_id, option_id, "rolled_back", _ROLLED_BACK_FROM)

    # ── 快照 ────────────────────────────────────────────────────

    def set_pre_snapshot(
        self,
        session_id: str,
        option_id: str,
        snapshot: dict[str, Any],
    ) -> bool:
        """写入操作前状态快照（仅 executing 状态可写）"""
        redis_key = _build_key(session_id, option_id)

        ttl = self._storage.ttl(redis_key)
        if ttl <= 0:
            ttl = self._ttl

        def _update(data: dict[str, Any]) -> dict[str, Any] | None:
            if data.get("status") != "executing":
                return None
            data["pre_snapshot"] = snapshot
            return data

        result = self._storage.cas_update(
            redis_key,
            expected_status=None,
            update_fn=_update,
            ttl=ttl,
        )
        return result is not None

    # ── 清理 ──────────────────────────────────────────────────────

    def cleanup_expired(self) -> int:
        """删除已过期记录，返回删除数量

        注意：Redis 的 TTL 会自动删除过期 key，此方法处理
        expires_at 已过期但 Redis 尚未清理的 key。
        """
        now = self._clock()
        pattern = f"{_KEY_PREFIX}:*"
        removed = 0

        keys = self._storage.scan_keys(pattern)
        for key in keys:
            raw = self._storage.get(key)
            if raw is None:
                continue
            stored = _dict_to_stored(raw)
            if now >= stored.expires_at:
                self._storage.delete(key)
                removed += 1

        return removed

    # ── 内部辅助 ──────────────────────────────────────────────────

    def _atomic_claim(
        self, session_id: str, option_id: str,
    ) -> StoredFixOption | None:
        """原子 claim：WATCH/MULTI/EXEC 完成 pending/confirm_required → executing"""
        redis_key = _build_key(session_id, option_id)

        ttl = self._storage.ttl(redis_key)
        if ttl <= 0:
            ttl = self._ttl

        def _update(data: dict[str, Any]) -> dict[str, Any] | None:
            now = self._clock()
            stored = _dict_to_stored(data)
            # TTL 检查
            if now >= stored.expires_at and stored.status in {"pending", "confirm_required"}:
                data["status"] = "expired"
                return data
            # 状态检查
            if stored.status not in _EXECUTABLE_FROM:
                return None  # 拒绝更新
            data["status"] = "executing"
            return data

        # cas_update 检查当前 status 在 _EXECUTABLE_FROM 中
        result = self._storage.cas_update(
            redis_key,
            expected_status=None,  # 由 update_fn 自行判断
            update_fn=_update,
            ttl=ttl,
        )
        if result is None:
            return None
        stored = _dict_to_stored(result)
        if stored.status == "executing":
            return deepcopy(stored)
        return None

    def _transition(
        self,
        session_id: str,
        option_id: str,
        target: FixOptionStatus,
        allowed_from: set[FixOptionStatus],
    ) -> bool:
        """原子状态转移（WATCH/MULTI/EXEC CAS）

        pending/confirm_required 在 TTL 过期后阻止转换；
        executing 即使过期也允许 mark_executed/failed 完成收口。
        """
        redis_key = _build_key(session_id, option_id)

        ttl = self._storage.ttl(redis_key)
        if ttl <= 0:
            ttl = self._ttl

        def _update(data: dict[str, Any]) -> dict[str, Any] | None:
            now = self._clock()
            stored = _dict_to_stored(data)
            # TTL 检查：仅 pending/confirm_required 在过期时阻止
            if now >= stored.expires_at and stored.status in {"pending", "confirm_required"}:
                data["status"] = "expired"
                return data
            # 状态检查
            if stored.status not in allowed_from:
                return None  # 拒绝更新
            data["status"] = target
            return data

        result = self._storage.cas_update(
            redis_key,
            expected_status=None,
            update_fn=_update,
            ttl=ttl,
        )
        if result is None:
            return False
        return result.get("status") == target
