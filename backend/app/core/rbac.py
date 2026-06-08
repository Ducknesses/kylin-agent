"""最小权限控制"""
import logging
import re
from typing import Dict, List

logger = logging.getLogger(__name__)


class Permission:
    """权限常量"""
    READ = "agent-read"
    OP = "agent-op"
    ADMIN = "agent-admin"


# 命令模板白名单：权限 -> 允许的正则模板
COMMAND_WHITELIST: Dict[str, List[str]] = {
    Permission.READ: [
        r"^df\s*-h.*",
        r"^ps\s+aux.*",
        r"^cat\s+/var/log/.*",
        r"^journalctl\s+.*",
        r"^free\s+-h.*",
        r"^top\s+-bn1.*",
        r"^uptime.*",
        r"^uname\s+-a.*",
        r"^ls\s+.*",
        r"^ss\s+-tlnp.*",
        r"^netstat\s+-tlnp.*",
        r"^ip\s+addr.*",
        r"^ping\s+-c\s+\d+.*",
    ],
    Permission.OP: [
        r"^systemctl\s+(start|restart|status)\s+\w+.*",
        r"^service\s+\w+\s+(start|restart|status).*",
    ],
    Permission.ADMIN: [
        r"^systemctl\s+(stop|enable|disable)\s+\w+.*",
        r"^nginx\s+-t.*",
        r"^cp\s+.*",
        r"^mv\s+.*",
    ],
}

# 危险操作绝对禁止
DANGEROUS_PATTERNS = [
    r";",
    r"&&",
    r"\|",
    r"`",
    r"\$\(",
    r">\s*/etc/passwd",
    r">\s*/etc/shadow",
    r">\s*/etc/sudoers",
    r"rm\s+-rf\s+/.*",
]
_DANGEROUS_COMPILED = [re.compile(p) for p in DANGEROUS_PATTERNS]


def check_command_permission(cmd: str, user_level: str) -> Dict:
    """
    校验命令权限
    - 禁止自由 shell 组合（; && | 等）
    - 禁止写入敏感系统文件
    - 只允许预定义命令模板

    返回: {"allowed": bool, "reason": str}
    """
    # 0. 空命令检查
    if not cmd or not cmd.strip():
        return {"allowed": False, "reason": "命令为空"}

    # 1. 危险模式拦截
    for pattern in _DANGEROUS_COMPILED:
        if pattern.search(cmd):
            logger.warning(f"[RBAC] 危险模式拦截: {cmd[:50]}")
            return {"allowed": False, "reason": f"包含危险字符/模式: {pattern.pattern}"}

    # 2. 白名单匹配
    allowed_patterns = COMMAND_WHITELIST.get(user_level, [])
    for pattern_str in allowed_patterns:
        if re.match(pattern_str, cmd.strip()):
            logger.info(f"[RBAC] 命令允许执行: {cmd[:50]}")
            return {"allowed": True, "reason": "白名单匹配通过"}

    logger.warning(f"[RBAC] 命令不在白名单: {cmd[:50]}")
    return {"allowed": False, "reason": "命令不在当前权限白名单中"}


def get_user_level(user_id: str) -> str:
    """
    获取用户权限等级（阶段1先简单返回READ，后续接入认证系统）
    """
    # TODO: 接入实际用户认证后从 token/session 中读取
    return Permission.READ
