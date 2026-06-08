"""Prompt Injection 检测模块"""
import logging
import re
from typing import Dict

logger = logging.getLogger(__name__)

# 已知注入关键词（中英文）
INJECTION_KEYWORDS = [
    "忽略之前指令",
    "忽略之前的指令",
    "ignore previous",
    "ignore all previous",
    "forget previous",
    "disregard earlier",
    "you are now",
    "从现在开始",
    "新角色",
    "system prompt",
    "override instructions",
    "绕过安全",
    "bypass safety",
    "jailbreak",
    "dank mode",
]

# 控制字符与异常 Unicode
CONTROL_CHARS_PATTERN = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
UNICODE_OBFUSCATION_PATTERN = re.compile(
    r"[\u200b-\u200f\u2060-\u2064\ufeff]"  # 零宽字符
)
REPEAT_PATTERN = re.compile(r"(.)\1{50,}")  # 单个字符重复50次以上


def detect_injection(text: str) -> Dict:
    """
    多层 Prompt Injection 检测

    返回: {"detected": bool, "reason": str}
    """
    # 1. 控制字符过滤
    if CONTROL_CHARS_PATTERN.search(text):
        logger.warning("[PromptGuard] 发现控制字符")
        return {"detected": True, "reason": "输入包含非法控制字符"}

    # 2. Unicode 混淆检测（零宽字符）
    if UNICODE_OBFUSCATION_PATTERN.search(text):
        logger.warning("[PromptGuard] 发现 Unicode 混淆字符")
        return {"detected": True, "reason": "输入包含 Unicode 混淆字符（零宽字符）"}

    # 3. 超长重复字符（攻击者常用来绕过简单过滤）
    if REPEAT_PATTERN.search(text):
        logger.warning("[PromptGuard] 发现异常重复字符")
        return {"detected": True, "reason": "输入包含异常重复字符（可能用于绕过过滤）"}

    # 4. 注入关键词检测
    lower = text.lower()
    for kw in INJECTION_KEYWORDS:
        if kw.lower() in lower:
            logger.warning(f"[PromptGuard] 发现注入关键词: {kw}")
            return {"detected": True, "reason": f"检测到 Prompt Injection 关键词: {kw}"}

    # 5. 语义边界检测：是否包含覆盖系统指令的句式
    # 简单规则：出现 "你是" / "you are" + "忽略" / "ignore" 组合
    if ("你是" in text or "you are" in lower) and ("忽略" in text or "ignore" in lower):
        logger.warning("[PromptGuard] 发现语义边界越界")
        return {"detected": True, "reason": "检测到试图覆盖系统指令的语义模式"}

    return {"detected": False, "reason": "未检测到注入攻击"}


def sanitize_input(text: str) -> str:
    """
    输入净化：去除控制字符和零宽字符
    """
    text = CONTROL_CHARS_PATTERN.sub("", text)
    text = UNICODE_OBFUSCATION_PATTERN.sub("", text)
    return text.strip()
