"""SafetyGuard —— 用户输入安全检查统一入口

职责：
  - 统一调用风险分级、Prompt 注入检测、高危模式匹配
  - 返回标准结构 {allowed, risk_level, reason, requires_confirm}
  - 不执行系统命令、不调用 LLM、不调用 MCP

当前阶段复用并扩展已有 security.py / prompt_guard.py / rbac.py 的检测能力。
"""
import logging
import re
from typing import Any, Dict

from app.core.prompt_guard import detect_injection
from app.core.security import risk_classify

logger = logging.getLogger(__name__)

# ── 本轮补充的高危模式（已有 security.py 未覆盖的破坏性命令） ─────────

_EXTRA_HIGH_RISK_PATTERNS: list[tuple[re.Pattern, str]] = [
    # curl ... | sh / curl ... | bash
    (re.compile(r"curl\b.*\|.*\b(bash|sh|/bin/sh|/bin/bash)\b", re.IGNORECASE),
     "禁止 curl 管道执行脚本"),
    # wget ... | sh / wget ... | bash
    (re.compile(r"wget\b.*\|.*\b(bash|sh|/bin/sh|/bin/bash)\b", re.IGNORECASE),
     "禁止 wget 管道执行脚本"),
    # dd of= 写入磁盘
    (re.compile(r"\bdd\b.*\bof=/dev/\w+", re.IGNORECASE),
     "禁止 dd 破坏性写入磁盘"),
    # 覆盖写入 /boot 目录
    (re.compile(r">\s*/boot/", re.IGNORECASE),
     "禁止写入 /boot 引导分区"),
    # systemd / auditd / mcp-server 服务破坏性操作
    (re.compile(r"\b(systemctl|service)\s+(stop|disable|mask)\s+(systemd|auditd|mcp-server)\b", re.IGNORECASE),
     "禁止破坏核心守护服务"),
    # 禁止关闭或清空审计规则（auditctl -s 只是查看状态，不拦截）
    (re.compile(r"\bauditctl\s+(-e\s*0|-D)\b", re.IGNORECASE),
     "禁止关闭或清空审计规则"),
    # 禁止停止、禁用、屏蔽 auditd 审计服务
    (re.compile(r"\b(systemctl|service)\s+(stop|disable|mask)\s+auditd\b", re.IGNORECASE),
     "禁止停止或禁用审计服务"),
    # chmod 777（安全模块原有规则仅覆盖 chmod -R 777，这里补全无 -R 的情况）
    (re.compile(r"\bchmod\s+777\b", re.IGNORECASE),
     "禁止 chmod 777 权限变更"),
    # sudo rm -rf（安全模块原有规则不匹配带 sudo 前缀的情况）
    (re.compile(r"\bsudo\s+rm\b", re.IGNORECASE),
     "禁止 sudo rm 高危删除操作"),
]

# ── 审计绕过 / 安全关闭注入关键词 ─────────────────────────────────────

_AUDIT_BYPASS_KEYWORDS: list[str] = [
    "忽略规则",
    "绕过安全限制",
    "不要记录日志",
    "不要写审计",
    "关闭审计",
    "ignore previous instructions",
    "bypass safety",
    "disable logging",
    "do not log",
    "no audit",
    "disable audit",
    "turn off logging",
    "绕过审计",
    "跳过安全检查",
    "停用审计",
]


class SafetyGuard:
    """用户输入安全检查统一入口"""

    def analyze_user_input(self, content: str) -> dict[str, Any]:
        """分析用户输入的安全性

        参数:
            content: 用户原始输入字符串

        返回:
            {
                "allowed": bool,          # 是否允许继续处理
                "risk_level": str,        # "low" | "medium" | "high"
                "reason": str,            # 判定原因（面向前端，不暴露内部细节）
                "requires_confirm": bool  # 是否需要二次确认
            }
        """
        # 空输入 / 纯空白
        stripped = content.strip() if content else ""
        if not stripped:
            return {
                "allowed": False,
                "risk_level": "high",
                "reason": "输入为空",
                "requires_confirm": False,
            }

        # 1. 归一化后做补充高危模式匹配
        normalized = stripped.lower()
        for pattern, reason in _EXTRA_HIGH_RISK_PATTERNS:
            if pattern.search(normalized):
                logger.warning(f"[SafetyGuard] 补充高危拦截: {reason}")
                return {
                    "allowed": False,
                    "risk_level": "high",
                    "reason": reason,
                    "requires_confirm": False,
                }

        # 2. 审计绕过 / 安全关闭关键词检测（直接判定为高危）
        for kw in _AUDIT_BYPASS_KEYWORDS:
            if kw.lower() in normalized:
                logger.warning(f"[SafetyGuard] 审计绕过检测: {kw}")
                return {
                    "allowed": False,
                    "risk_level": "high",
                    "reason": "检测到试图绕过安全审计的输入",
                    "requires_confirm": False,
                }

        # 3. 调已有 Prompt Injection 检测
        injection = detect_injection(stripped)
        if injection["detected"]:
            logger.warning(f"[SafetyGuard] Prompt Injection: {injection['reason']}")
            return {
                "allowed": False,
                "risk_level": "high",
                "reason": f"输入安全检测未通过: {injection['reason']}",
                "requires_confirm": False,
            }

        # 4. 调已有 risk_classify 做完整风险分级
        risk = risk_classify(stripped)

        # 高危：不允许
        if risk["action"] == "reject":
            logger.warning(f"[SafetyGuard] 高危拦截: {risk['reason']}")
            return {
                "allowed": False,
                "risk_level": risk["level"],
                "reason": risk["reason"],
                "requires_confirm": False,
            }

        # 中危：需二次确认
        if risk["action"] == "confirm":
            logger.info(f"[SafetyGuard] 中危需确认: {risk['reason']}")
            return {
                "allowed": True,
                "risk_level": risk["level"],
                "reason": risk["reason"],
                "requires_confirm": True,
            }

        # 低危：直接放行
        return {
            "allowed": True,
            "risk_level": "low",
            "reason": "未发现高危输入",
            "requires_confirm": False,
        }
