"""ConfirmationStore —— medium 确认请求存储（Redis 持久化）

职责：
  - 创建/查询 PendingConfirmation
  - session_id + option_id 联合绑定
  - TTL 过期
  - 并发幂等（同一 session+option 只保留一个有效确认）

存储后端：Redis（通过 RedisStorage），服务重启后数据不丢失，多 worker 共享。

Redis Key 格式：
  主键: agent:confirmation:{session_id}:{option_id}
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


class ConfirmationStore:
    """medium 确认请求存储 —— Redis 持久化

    key = (session_id, option_id)，同一 session+option 只保留一个有效确认。
    """

    # 默认 TTL：30 分钟（按生产化要求）
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
        """创建或返回已有确认 —— 返回 (confirmation, created)

        created=True 表示本次新建，created=False 表示复用已有。
        """
        redis_key = _build_key(session_id, option_id)
        now = self._clock()

        # 检查是否已存在有效确认
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
        self._storage.set(redis_key, _conf_to_dict(conf), ttl=self._ttl)
        return conf, True

    def remove_if_pending(
        self, session_id: str, option_id: str, confirm_id: str,
    ) -> bool:
        """仅当记录匹配且 status=pending 时删除，用于补偿"""
        redis_key = _build_key(session_id, option_id)
        raw = self._storage.get(redis_key)
        if raw is None:
            return False
        conf = _dict_to_conf(raw)
        if conf.confirm_id == confirm_id and conf.status == "pending":
            self._storage.delete(redis_key)
            return True
        return False

    def get(self, session_id: str, confirm_id: str) -> PendingConfirmation | None:
        """按 session_id + confirm_id 查找确认"""
        now = self._clock()
        # 扫描该 session 下所有 confirmation
        pattern = f"{_KEY_PREFIX}:{session_id}:*"
        keys = self._storage.scan_keys(pattern)
        for key in keys:
            raw = self._storage.get(key)
            if raw is None:
                continue
            conf = _dict_to_conf(raw)
            if conf.confirm_id == confirm_id:
                if now >= conf.expires_at:
                    conf.status = "expired"
                    self._storage.set(key, _conf_to_dict(conf), ttl=_EXPIRED_RETENTION_SECONDS)
                return conf
        return None

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

    # ── Decision ──────────────────────────────────────────────────

    def claim_approve(self, session_id: str, confirm_id: str) -> ConfirmationDecisionResult:
        """原子 pending→approving"""
        now = self._clock()
        pattern = f"{_KEY_PREFIX}:{session_id}:*"
        keys = self._storage.scan_keys(pattern)
        for key in keys:
            raw = self._storage.get(key)
            if raw is None:
                continue
            conf = _dict_to_conf(raw)
            if conf.confirm_id == confirm_id:
                if now >= conf.expires_at:
                    conf.status = "expired"
                    self._storage.set(key, _conf_to_dict(conf), ttl=_EXPIRED_RETENTION_SECONDS)
                    return ConfirmationDecisionResult(result="expired")
                if conf.status != "pending":
                    return ConfirmationDecisionResult(result="conflict")
                conf.status = "approving"
                self._storage.set(key, _conf_to_dict(conf), ttl=self._ttl)
                return ConfirmationDecisionResult(result="claimed", confirmation=_copy_conf(conf))
        return ConfirmationDecisionResult(result="not_found")

    def reject(self, session_id: str, confirm_id: str) -> ConfirmationDecisionResult:
        """原子 pending→rejected"""
        now = self._clock()
        pattern = f"{_KEY_PREFIX}:{session_id}:*"
        keys = self._storage.scan_keys(pattern)
        for key in keys:
            raw = self._storage.get(key)
            if raw is None:
                continue
            conf = _dict_to_conf(raw)
            if conf.confirm_id == confirm_id:
                if now >= conf.expires_at:
                    conf.status = "expired"
                    self._storage.set(key, _conf_to_dict(conf), ttl=_EXPIRED_RETENTION_SECONDS)
                    return ConfirmationDecisionResult(result="expired")
                if conf.status != "pending":
                    return ConfirmationDecisionResult(result="conflict")
                conf.status = "rejected"
                self._storage.set(key, _conf_to_dict(conf), ttl=self._ttl)
                return ConfirmationDecisionResult(result="rejected", confirmation=_copy_conf(conf))
        return ConfirmationDecisionResult(result="not_found")

    def mark_consumed(self, session_id: str, confirm_id: str) -> bool:
        """approving → consumed"""
        return self._transition(session_id, confirm_id, "consumed", {"approving"})

    def mark_blocked(self, session_id: str, confirm_id: str) -> bool:
        """approving → blocked"""
        return self._transition(session_id, confirm_id, "blocked", {"approving"})

    def mark_failed(self, session_id: str, confirm_id: str) -> bool:
        """approving → failed"""
        return self._transition(session_id, confirm_id, "failed", {"approving"})

    def _transition(self, session_id: str, confirm_id: str, target: ConfirmationStatus, allowed: set[str]) -> bool:
        """通用状态转移"""
        pattern = f"{_KEY_PREFIX}:{session_id}:*"
        keys = self._storage.scan_keys(pattern)
        for key in keys:
            raw = self._storage.get(key)
            if raw is None:
                continue
            conf = _dict_to_conf(raw)
            if conf.confirm_id == confirm_id:
                if conf.status not in allowed:
                    return False
                conf.status = target
                remaining = self._storage.ttl(key)
                ttl = remaining if (remaining is not None and remaining > 0) else self._ttl
                self._storage.set(key, _conf_to_dict(conf), ttl=ttl)
                return True
        return False

    def cleanup_expired(self) -> int:
        """删除已过期记录，返回删除数量"""
        now = self._clock()
        pattern = f"{_KEY_PREFIX}:*"
        removed = 0
        keys = self._storage.scan_keys(pattern)
        for key in keys:
            raw = self._storage.get(key)
            if raw is None:
                continue
            conf = _dict_to_conf(raw)
            if now >= conf.expires_at:
                self._storage.delete(key)
                removed += 1
        return removed
