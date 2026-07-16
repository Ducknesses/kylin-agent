"""ConfirmationStore —— medium 确认请求存储（Redis 持久化 + Lua 原子操作）

职责：
  - 创建/查询 PendingConfirmation
  - session_id + option_id 联合绑定
  - TTL 过期
  - 并发幂等（同一 session+option 只保留一个有效确认）

存储后端：Redis（通过 RedisStorage），服务重启后数据不丢失，多 worker 共享。

状态转换使用 Redis Lua 脚本保证原子性，消除 Read-Modify-Write 竞态。

Redis Key 格式：
  主键:   agent:confirmation:{session_id}:{option_id}
  引用键: agent:confirmation:{session_id}:{confirm_id}:ref  →  存储 option_id
"""
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Literal

from app.services.storage.redis_storage import RedisStorage

ConfirmationStatus = Literal["pending", "approving", "approved", "rejected", "expired", "consumed", "blocked", "failed"]
DecisionResult = Literal["claimed", "rejected", "not_found", "expired", "conflict"]

_KEY_PREFIX = "agent:confirmation"

# 过期标记后的保留时间（秒）
_EXPIRED_RETENTION_SECONDS = 60


@dataclass(frozen=True)
class ConfirmationDecisionResult:
    result: DecisionResult
    confirmation: "PendingConfirmation | None" = None


@dataclass
class PendingConfirmation:
    confirm_id: str
    session_id: str
    option_id: str
    trace_id: str
    status: ConfirmationStatus
    created_at: datetime
    expires_at: datetime

    def __post_init__(self) -> None:
        if self.status not in {"pending", "approving", "approved", "rejected", "expired", "consumed", "blocked", "failed"}:
            raise ValueError(f"非法 ConfirmationStatus: {self.status}")


def _copy_conf(c: PendingConfirmation) -> PendingConfirmation:
    return PendingConfirmation(
        confirm_id=c.confirm_id, session_id=c.session_id, option_id=c.option_id,
        trace_id=c.trace_id, status=c.status, created_at=c.created_at, expires_at=c.expires_at,
    )


# ── 序列化辅助 ────────────────────────────────────────────────────────

def _conf_to_dict(c: PendingConfirmation) -> dict[str, Any]:
    return {
        "confirm_id": c.confirm_id,
        "session_id": c.session_id,
        "option_id": c.option_id,
        "trace_id": c.trace_id,
        "status": c.status,
        "created_at": c.created_at.isoformat(),
        "expires_at": c.expires_at.isoformat(),
    }


def _dict_to_conf(d: dict[str, Any]) -> PendingConfirmation:
    return PendingConfirmation(
        confirm_id=d["confirm_id"],
        session_id=d["session_id"],
        option_id=d["option_id"],
        trace_id=d["trace_id"],
        status=d["status"],
        created_at=datetime.fromisoformat(d["created_at"]),
        expires_at=datetime.fromisoformat(d["expires_at"]),
    )


def _build_key(session_id: str, option_id: str) -> str:
    return f"{_KEY_PREFIX}:{session_id}:{option_id}"


def _build_ref_key(session_id: str, confirm_id: str) -> str:
    """引用键：confirm_id → option_id"""
    return f"{_KEY_PREFIX}:{session_id}:{confirm_id}:ref"


class ConfirmationStore:
    """medium 确认请求存储 —— Redis 持久化 + Lua 原子状态转换

    TTL 设计说明（30分钟）：
      Confirmation 是用户交互确认窗口，30 分钟内用户需做出 approve/reject 决策。
      超时后确认自动过期，防止僵尸确认堆积。
    """

    DEFAULT_TTL_SECONDS: int = 1800

    def __init__(
        self,
        ttl_seconds: int = DEFAULT_TTL_SECONDS,
        clock: Callable[[], datetime] | None = None,
        storage: RedisStorage | None = None,
    ) -> None:
        self._ttl = ttl_seconds
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._storage = storage or RedisStorage()

    def create_or_get(
        self, session_id: str, option_id: str, trace_id: str,
    ) -> tuple[PendingConfirmation, bool]:
        """创建或返回已有确认 —— 返回 (confirmation, created)"""
        redis_key = _build_key(session_id, option_id)
        now = self._clock()

        existing_raw = self._storage.get(redis_key)
        if existing_raw is not None:
            existing = _dict_to_conf(existing_raw)
            if now < existing.expires_at and existing.status == "pending":
                return existing, False

        confirm_id = f"cfm_{uuid.uuid4().hex[:8]}"
        expires = now + timedelta(seconds=self._ttl)
        conf = PendingConfirmation(
            confirm_id=confirm_id, session_id=session_id, option_id=option_id,
            trace_id=trace_id, status="pending",
            created_at=now, expires_at=expires,
        )
        conf_dict = _conf_to_dict(conf)
        self._storage.set(redis_key, conf_dict, ttl=self._ttl)
        # 建立引用键：confirm_id → option_id
        ref_key = _build_ref_key(session_id, confirm_id)
        self._storage.raw_set(ref_key, option_id, ttl=self._ttl)
        return conf, True

    def remove_if_pending(
        self, session_id: str, option_id: str, confirm_id: str,
    ) -> bool:
        """仅当记录匹配且 status=pending 时删除"""
        redis_key = _build_key(session_id, option_id)
        raw = self._storage.get(redis_key)
        if raw is None:
            return False
        conf = _dict_to_conf(raw)
        if conf.confirm_id == confirm_id and conf.status == "pending":
            ref_key = _build_ref_key(session_id, confirm_id)
            self._storage.delete(redis_key)
            self._storage.delete(ref_key)
            return True
        return False

    def get(self, session_id: str, confirm_id: str) -> PendingConfirmation | None:
        """按 session_id + confirm_id 查找确认（通过引用键）"""
        now = self._clock()
        ref_key = _build_ref_key(session_id, confirm_id)
        option_id = self._storage.raw_get(ref_key)
        if option_id is None:
            return None
        redis_key = _build_key(session_id, option_id)
        raw = self._storage.get(redis_key)
        if raw is None:
            return None
        conf = _dict_to_conf(raw)
        if now >= conf.expires_at:
            conf.status = "expired"
            self._storage.set(redis_key, _conf_to_dict(conf), ttl=_EXPIRED_RETENTION_SECONDS)
        return conf

    def get_by_option(self, session_id: str, option_id: str) -> PendingConfirmation | None:
        """按 session_id + option_id 查找确认"""
        redis_key = _build_key(session_id, option_id)
        now = self._clock()
        raw = self._storage.get(redis_key)
        if raw is None:
            return None
        conf = _dict_to_conf(raw)
        if now >= conf.expires_at:
            conf.status = "expired"
            self._storage.set(redis_key, _conf_to_dict(conf), ttl=_EXPIRED_RETENTION_SECONDS)
        return _copy_conf(conf)

    # ── Decision（Lua 原子操作）─────────────────────────────────

    def claim_approve(self, session_id: str, confirm_id: str) -> ConfirmationDecisionResult:
        """原子 pending→approving（Lua CAS）"""
        return self._atomic_transition(session_id, confirm_id, "pending", "approving")

    def reject(self, session_id: str, confirm_id: str) -> ConfirmationDecisionResult:
        """原子 pending→rejected（Lua CAS）"""
        result = self._atomic_transition(session_id, confirm_id, "pending", "rejected")
        if result.result == "ok":
            return ConfirmationDecisionResult(
                result="rejected", confirmation=result.confirmation,
            )
        return result

    def mark_consumed(self, session_id: str, confirm_id: str) -> bool:
        """approving → consumed（CAS）"""
        result = self._atomic_transition(session_id, confirm_id, "approving", "consumed")
        return result.result == "claimed"

    def mark_blocked(self, session_id: str, confirm_id: str) -> bool:
        """approving → blocked（CAS）"""
        result = self._atomic_transition(session_id, confirm_id, "approving", "blocked")
        return result.result == "claimed"

    def mark_failed(self, session_id: str, confirm_id: str) -> bool:
        """approving → failed（CAS）"""
        result = self._atomic_transition(session_id, confirm_id, "approving", "failed")
        return result.result == "claimed"

    # ── 原子转换核心 ────────────────────────────────────────────

    def _atomic_transition(
        self, session_id: str, confirm_id: str, expected: str, target: str,
    ) -> ConfirmationDecisionResult:
        """通过 ref key → primary key → WATCH/MULTI/EXEC CAS 完成原子状态转换"""
        ref_key = _build_ref_key(session_id, confirm_id)
        option_id = self._storage.raw_get(ref_key)
        if option_id is None:
            return ConfirmationDecisionResult(result="not_found")

        redis_key = _build_key(session_id, option_id)
        now = self._clock()

        # 先读取数据判断是否存在和状态（不可变字段读安全）
        raw = self._storage.get(redis_key)
        if raw is None:
            return ConfirmationDecisionResult(result="not_found")
        conf = _dict_to_conf(raw)

        # 过期检查（在 CAS 前判断，避免无效 CAS）
        if now >= conf.expires_at:
            # 标记过期
            def _mark_expired(data: dict[str, Any]) -> dict[str, Any]:
                data["status"] = "expired"
                return data
            self._storage.cas_update(redis_key, None, _mark_expired, ttl=_EXPIRED_RETENTION_SECONDS)
            return ConfirmationDecisionResult(result="expired")

        # 状态预检查（快速失败，避免无效 CAS）
        if conf.status != expected:
            return ConfirmationDecisionResult(result="conflict")

        ttl_to_use = self._storage.ttl(redis_key)
        if ttl_to_use <= 0:
            ttl_to_use = self._ttl

        def _update(data: dict[str, Any]) -> dict[str, Any] | None:
            if data["status"] != expected:
                return None
            data["status"] = target
            return data

        result = self._storage.cas_update(
            redis_key,
            expected_status=expected,  # cas_update 也做一次快速检查
            update_fn=_update,
            ttl=ttl_to_use,
        )
        if result is None:
            # cas_update 失败可能是并发修改 → conflict
            return ConfirmationDecisionResult(result="conflict")

        new_conf = _dict_to_conf(result)
        if target == "rejected":
            return ConfirmationDecisionResult(result="rejected", confirmation=_copy_conf(new_conf))
        return ConfirmationDecisionResult(result="claimed", confirmation=_copy_conf(new_conf))

    def cleanup_expired(self) -> int:
        """删除已过期记录"""
        now = self._clock()
        pattern = f"{_KEY_PREFIX}:*"
        removed = 0
        keys = self._storage.scan_keys(pattern)
        for key in keys:
            # 跳过引用键
            if key.endswith(":ref"):
                continue
            raw = self._storage.get(key)
            if raw is None:
                continue
            conf = _dict_to_conf(raw)
            if now >= conf.expires_at:
                ref_key = _build_ref_key(conf.session_id, conf.confirm_id)
                self._storage.delete(key)
                self._storage.delete(ref_key)
                removed += 1
        return removed
