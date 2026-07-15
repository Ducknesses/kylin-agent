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
        """大小写不敏感转换，未知值回退到 default"""
        raw_lower = raw.strip().lower()
        for level in AuthLevel:
            if level.value.lower() == raw_lower or raw_lower == level.name.lower():
                return level
        return default or AuthLevel.READ

    # ── 自定义比较（按枚举定义顺序，不是按字符串值） ──

    def _order(self) -> int:
        """返回枚举成员的定义顺序索引"""
        return list(AuthLevel).index(self)

    def __gt__(self, other: "AuthLevel") -> bool:
        if not isinstance(other, AuthLevel):
            return NotImplemented
        return self._order() > other._order()

    def __ge__(self, other: "AuthLevel") -> bool:
        if not isinstance(other, AuthLevel):
            return NotImplemented
        return self._order() >= other._order()

    def __lt__(self, other: "AuthLevel") -> bool:
        if not isinstance(other, AuthLevel):
            return NotImplemented
        return self._order() < other._order()

    def __le__(self, other: "AuthLevel") -> bool:
        if not isinstance(other, AuthLevel):
            return NotImplemented
        return self._order() <= other._order()


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