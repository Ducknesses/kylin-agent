"""ConfirmationStore —— medium 确认请求存储

职责：
  - 创建/查询 PendingConfirmation
  - session_id + option_id 联合绑定
  - TTL 过期
  - 并发幂等（同一 session+option 只保留一个有效确认）
"""

import threading
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Callable, Literal

ConfirmationStatus = Literal["pending", "approving", "approved", "rejected", "expired", "consumed", "blocked", "failed"]

DecisionResult = Literal["claimed", "rejected", "not_found", "expired", "conflict"]


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


class ConfirmationStore:
    """medium 确认请求存储 —— 进程内存

    key = (session_id, option_id)，同一 session+option 只保留一个有效确认。
    """

    def __init__(
        self,
        ttl_seconds: int = 300,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._ttl = ttl_seconds
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._store: dict[tuple[str, str], PendingConfirmation] = {}
        self._lock = threading.RLock()

    def create_or_get(
        self, session_id: str, option_id: str, trace_id: str,
    ) -> tuple[PendingConfirmation, bool]:
        """创建或返回已有确认 —— 返回 (confirmation, created)

        created=True 表示本次新建，created=False 表示复用已有。
        """
        key = (session_id, option_id)
        now = self._clock()

        with self._lock:
            existing = self._store.get(key)
            if existing is not None:
                if now < existing.expires_at and existing.status == "pending":
                    return existing, False
            confirm_id = f"cfm_{uuid.uuid4().hex[:8]}"
            expires = now + timedelta(seconds=self._ttl)
            conf = PendingConfirmation(
                confirm_id=confirm_id, session_id=session_id, option_id=option_id,
                trace_id=trace_id, status="pending",
                created_at=now, expires_at=expires,
            )
            self._store[key] = conf
            return conf, True

    def remove_if_pending(
        self, session_id: str, option_id: str, confirm_id: str,
    ) -> bool:
        """仅当记录匹配且 status=pending 时删除，用于补偿"""
        key = (session_id, option_id)
        with self._lock:
            conf = self._store.get(key)
            if conf is not None and conf.confirm_id == confirm_id and conf.status == "pending":
                del self._store[key]
                return True
            return False

    def get(self, session_id: str, confirm_id: str) -> PendingConfirmation | None:
        now = self._clock()
        with self._lock:
            for key, conf in self._store.items():
                if conf.session_id == session_id and conf.confirm_id == confirm_id:
                    if now >= conf.expires_at:
                        conf.status = "expired"
                        self._store[key] = conf
                    return conf
            return None

    def get_by_option(self, session_id: str, option_id: str) -> PendingConfirmation | None:
        key = (session_id, option_id)
        now = self._clock()
        with self._lock:
            conf = self._store.get(key)
            if conf is None:
                return None
            if now >= conf.expires_at:
                conf.status = "expired"
                self._store[key] = conf
            return _copy_conf(conf)

    # ── Decision ──────────────────────────────────────────────────

    def claim_approve(self, session_id: str, confirm_id: str) -> ConfirmationDecisionResult:
        """原子 pending→approving"""
        now = self._clock()
        with self._lock:
            for key, conf in self._store.items():
                if conf.session_id == session_id and conf.confirm_id == confirm_id:
                    if now >= conf.expires_at:
                        conf.status = "expired"
                        self._store[key] = conf
                        return ConfirmationDecisionResult(result="expired")
                    if conf.status != "pending":
                        return ConfirmationDecisionResult(result="conflict")
                    conf.status = "approving"
                    self._store[key] = conf
                    return ConfirmationDecisionResult(result="claimed", confirmation=_copy_conf(conf))
            return ConfirmationDecisionResult(result="not_found")

    def reject(self, session_id: str, confirm_id: str) -> ConfirmationDecisionResult:
        """原子 pending→rejected"""
        now = self._clock()
        with self._lock:
            for key, conf in self._store.items():
                if conf.session_id == session_id and conf.confirm_id == confirm_id:
                    if now >= conf.expires_at:
                        conf.status = "expired"
                        self._store[key] = conf
                        return ConfirmationDecisionResult(result="expired")
                    if conf.status != "pending":
                        return ConfirmationDecisionResult(result="conflict")
                    conf.status = "rejected"
                    self._store[key] = conf
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
        with self._lock:
            for key, conf in self._store.items():
                if conf.session_id == session_id and conf.confirm_id == confirm_id:
                    if conf.status not in allowed:
                        return False
                    conf.status = target
                    self._store[key] = conf
                    return True
            return False

    def cleanup_expired(self) -> int:
        now = self._clock()
        removed = 0
        with self._lock:
            expired = [k for k, c in self._store.items() if now >= c.expires_at]
            for k in expired:
                del self._store[k]
                removed += 1
        return removed
