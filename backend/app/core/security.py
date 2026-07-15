"""安全护栏核心：输入过滤、风险分级 + Token 认证"""
import logging
import re
import hashlib
import threading
from typing import Dict, Optional

from config import settings
from app.core.prompt_guard import detect_injection

logger = logging.getLogger(__name__)

# 高危命令黑名单（正则）
HIGH_RISK_PATTERNS = [
    r"rm\s+-rf\s+/.*",
    r"mkfs\.",
    r"dd\s+if=/dev/zero",
    r">\s*/etc/passwd",
    r">\s*/etc/shadow",
    r":\(\)\s*\{\s*:\|:\&\s*\};.*",  # fork bomb
    r"chmod\s+-R\s+777\s+/.*",
    r"mv\s+/.*\s+/dev/null",
]

# 中危关键词
MEDIUM_RISK_KEYWORDS = [
    "systemctl stop",
    "systemctl disable",
    "iptables -F",
    "useradd",
    "userdel",
    "passwd",
    "chmod 777",
    "chown -R",
    "kill -9",
]

# 编译正则，提升性能
_HIGH_RISK_COMPILED = [re.compile(p, re.IGNORECASE) for p in HIGH_RISK_PATTERNS]


def _check_length(text: str) -> Optional[Dict]:
    """检查输入长度"""
    if len(text) > settings.MAX_INPUT_LENGTH:
        return {
            "level": "high",
            "reason": f"输入过长({len(text)}>{settings.MAX_INPUT_LENGTH})",
            "action": "reject",
        }
    return None


def _check_blacklist(text: str) -> Optional[Dict]:
    """正则黑名单匹配"""
    for pattern in _HIGH_RISK_COMPILED:
        if pattern.search(text):
            return {
                "level": "high",
                "reason": f"匹配高危命令模式: {pattern.pattern[:40]}...",
                "action": "reject",
            }
    return None


def _check_keywords(text: str) -> Optional[Dict]:
    """关键词分级匹配"""
    lower = text.lower()
    for kw in MEDIUM_RISK_KEYWORDS:
        if kw in lower:
            return {
                "level": "medium",
                "reason": f"包含敏感关键词: {kw}",
                "action": "confirm",
            }
    return None


def risk_classify(user_input: str, skip_injection_check: bool = False) -> Dict:
    """
    三层安全检测：
    1. 长度检查
    2. 正则黑名单（高危直接拒）
    3. 关键词分级（中危需确认）
    4. Prompt Injection 语义检测（可通过 skip_injection_check=True 跳过，避免重复检测）

    返回: {"level": "high|medium|low", "reason": "...", "action": "reject|confirm|allow"}
    """
    # 1. 长度检查
    result = _check_length(user_input)
    if result:
        logger.warning(f"[安全] 输入过长拦截: {result['reason']}")
        return result

    # 2. 高危黑名单
    result = _check_blacklist(user_input)
    if result:
        logger.warning(f"[安全] 高危命令拦截: {result['reason']}")
        return result

    # 3. Prompt Injection 检测（如果调用方已自行检测可跳过）
    if not skip_injection_check:
        injection = detect_injection(user_input)
        if injection["detected"]:
            logger.warning(f"[安全] Prompt Injection 拦截: {injection['reason']}")
            return {
                "level": "high",
                "reason": injection["reason"],
                "action": "reject",
            }

    # 4. 中危关键词
    result = _check_keywords(user_input)
    if result:
        logger.info(f"[安全] 中危命令需确认: {result['reason']}")
        return result

    # 低危允许通过
    return {
        "level": "low",
        "reason": "通过安全检测",
        "action": "allow",
    }


# ── Token 认证模块 ────────────────────────────────────────────
# 当前：静态 token 字符串比较
# 未来：可替换为 JWT decode / Redis 查找 / OAuth2
# 环境变量格式（单 token 模式）：
#   API_TOKEN=my-secret         → 所有匹配均为 ADMIN
# 环境变量格式（多 token 分级模式）：
#   API_TOKENS=read:rk_xxx,op:op_yyy,admin:adm_zzz
# 二选一，API_TOKENS 优先。

from app.core.auth import AuthContext, AuthLevel  # noqa: E402


class TokenStore:
    """多 token 存储器 + 验证引擎

    特性：
    - 支持单 token (API_TOKEN) 和多 token 分级 (API_TOKENS)
    - 未配置时 is_configured() 返回 False → 匿名放行（向后兼容）
    - validate() 返回 AuthContext 或 None
    - token 原文不入日志，仅保留 sha256 前 8 位 hash
    """

    _instance: Optional["TokenStore"] = None
    _lock = threading.Lock()

    def __init__(self) -> None:
        self._tokens: dict[str, AuthLevel] = {}
        self._load_from_env()

    # ── 单例（避免反复解析环境变量） ──

    @classmethod
    def singleton(cls) -> "TokenStore":
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    # ── 环境变量加载 ──

    def _load_from_env(self) -> None:
        """从 .env 读取 token 配置"""
        # 优先读 API_TOKENS（分级）
        if settings.API_TOKENS:
            for entry in settings.API_TOKENS.split(","):
                entry = entry.strip()
                if ":" in entry:
                    level_str, tok = entry.split(":", 1)
                    level = AuthLevel.from_string(level_str.strip())
                    self._tokens[tok.strip()] = level
            logger.info(f"[TokenStore] 加载 {len(self._tokens)} 个分级 token")
            return

        # 回退到 API_TOKEN（单 token → ADMIN）
        if settings.API_TOKEN:
            self._tokens[settings.API_TOKEN] = AuthLevel.ADMIN
            logger.info("[TokenStore] 加载单 token (ADMIN)")

    def reload(self) -> None:
        """热重载 token 配置（供 /api/config 修改后调用）"""
        self._tokens.clear()
        self._load_from_env()

    # ── 查询 ──

    def is_configured(self) -> bool:
        """是否启用了 token 认证"""
        return len(self._tokens) > 0

    def validate(self, token: Optional[str], client_ip: str = "") -> Optional[AuthContext]:
        """验证 token，返回 AuthContext 或 None

        - token 为空 → None
        - token 匹配 → AuthContext
        - token 不匹配 → None
        """
        if not token:
            return None
        if not self._tokens:
            return None

        level = self._tokens.get(token)
        if level is None:
            logger.warning(
                f"[TokenStore] token 验证失败 "
                f"(hash={hashlib.sha256(token.encode()).hexdigest()[:8]})"
            )
            return None

        return AuthContext.from_token(token, level, client_ip)

    # ── 管理接口 ──

    def token_count(self) -> int:
        return len(self._tokens)

    def list_levels(self) -> dict[str, int]:
        """返回 level -> count 统计（不暴露 token 原文）"""
        result: dict[str, int] = {}
        for level in self._tokens.values():
            result[level.value] = result.get(level.value, 0) + 1
        return result