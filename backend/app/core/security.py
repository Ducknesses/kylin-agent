"""安全护栏核心：输入过滤、风险分级"""
import logging
import re
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
