"""认证上下文：解耦 Token 验证与权限判定

AuthContext 是认证层与授权层之间的数据契约。
- TokenStore 产出一个 AuthContext
- RBAC 消费 AuthContext 进行权限判定
- 中间件/依赖注入通过 request.state.auth 传递

扩展路径：
- 当前：静态 token 字符串比较
- 未来：JWT decode → AuthContext(token=sub, level=from_claims, exp=..., scopes=...)
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class AuthLevel(str, Enum):
    """权限等级（与 rbac.py Permission 对齐，按枚举定义顺序比较高低）"""
    ANONYMOUS = "agent-anonymous"  # 无 token / 无效 token  (0)
    READ = "agent-read"            # 只读                       (1)
    OP = "agent-op"                # 操作                       (2)
    ADMIN = "agent-admin"          # 管理                       (3)

    @staticmethod
    def from_string(raw: str, default: Optional["AuthLevel"] = None) -> "AuthLevel":
        """大小写不敏感转换，未知值回退到 default

        支持两种格式（大小写不敏感）：
          - value 格式：如 "agent-admin" → ADMIN
          - name 格式： 如 "ADMIN"       → ADMIN
        """
        raw_lower = raw.strip().lower()
        for level in AuthLevel:
            # 同时匹配 value（如 "agent-admin"）和 name（如 "ADMIN"）
            if level.value.lower() == raw_lower or raw_lower == level.name.lower():
                return level
        if default is not None:
            return default
        import logging
        logger = logging.getLogger(__name__)
        logger.warning(f"[Auth] 未识别的权限等级: '{raw}'，回退到 ANONYMOUS")
        return AuthLevel.ANONYMOUS

    # ── 自定义比较（按枚举定义顺序，不是按字符串值） ──

    def _order(self) -> int:
        """返回枚举成员的定义顺序索引（从缓存的 _AUTH_LEVEL_ORDER 字典 O(1) 查找）"""
        return _AUTH_LEVEL_ORDER[self]

    def __gt__(self, other: "AuthLevel") -> bool:
        if not isinstance(other, AuthLevel):
            return NotImplemented
        return _AUTH_LEVEL_ORDER[self] > _AUTH_LEVEL_ORDER[other]

    def __ge__(self, other: "AuthLevel") -> bool:
        if not isinstance(other, AuthLevel):
            return NotImplemented
        return _AUTH_LEVEL_ORDER[self] >= _AUTH_LEVEL_ORDER[other]

    def __lt__(self, other: "AuthLevel") -> bool:
        if not isinstance(other, AuthLevel):
            return NotImplemented
        return _AUTH_LEVEL_ORDER[self] < _AUTH_LEVEL_ORDER[other]

    def __le__(self, other: "AuthLevel") -> bool:
        if not isinstance(other, AuthLevel):
            return NotImplemented
        return _AUTH_LEVEL_ORDER[self] <= _AUTH_LEVEL_ORDER[other]


# 缓存枚举成员 → 排序索引映射（模块级，避免每次比较都创建新 list）
_AUTH_LEVEL_ORDER: dict["AuthLevel", int] = {member: idx for idx, member in enumerate(AuthLevel)}


@dataclass
class AuthContext:
    """认证后的用户/客户端上下文"""
    level: AuthLevel = AuthLevel.ANONYMOUS
    token_hash: str = ""       # token 的 sha256 前 8 位（用于审计，不存原文）
    client_ip: str = ""
    extra: dict = field(default_factory=dict)  # JWT claims / 自定义元数据

    @property
    def is_authenticated(self) -> bool:
        return self.level != AuthLevel.ANONYMOUS

    @classmethod
    def anonymous(cls, client_ip: str = "") -> "AuthContext":
        return cls(level=AuthLevel.ANONYMOUS, client_ip=client_ip)

    @classmethod
    def from_token(cls, token: str, level: AuthLevel, client_ip: str = "") -> "AuthContext":
        import hashlib
        token_hash = hashlib.sha256(token.encode()).hexdigest()[:8]
        return cls(level=level, token_hash=token_hash, client_ip=client_ip)
