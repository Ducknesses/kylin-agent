"""IntentAgent —— 规则版意图识别（含 LLM 增强路径 + fallback）

职责：
  - 基于关键词/模式匹配识别用户输入意图（规则版 fallback）
  - 可选接入 LLM 进行意图识别（detect_with_llm）
  - 提取服务名、端口号等实体
  - 不调用 MCPClient、不执行命令
  - 不做安全裁决——安全裁决归 SafetyGuard
  - LLM 输出必须通过 schema 校验，非法时 fallback 规则版
"""

import json
import logging
import re
from typing import Any

from app.services.llm_client import LLMClient

logger = logging.getLogger(__name__)


# ── 允许的 intent 范围 ─────────────────────────────────────────────────
_VALID_INTENTS: set[str] = {
    "cpu_query", "memory_query", "disk_query", "load_query",
    "network_query", "service_status_query", "log_query",
    "root_cause_analysis", "command_execute", "unknown",
}


# ── 服务名白名单 ──────────────────────────────────────────────────────
# 从用户输入中可识别的服务名列表
_KNOWN_SERVICES: list[str] = [
    "nginx", "httpd", "apache2",
    "redis", "redis-server",
    "mysql", "mysqld", "mariadb",
    "postgresql",
    "sshd", "ssh",
    "docker",
    "cron", "crond",
    "rsyslog",
    "fail2ban",
    "iptables", "firewalld",
    "auditd",
]


def _extract_service(text: str) -> str | None:
    """从文本中提取服务名，按最长匹配优先"""
    lower = text.lower()
    best: str | None = None
    best_len = 0
    for svc in _KNOWN_SERVICES:
        if svc in lower and len(svc) > best_len:
            best = svc
            best_len = len(svc)
    return best


def _extract_port(text: str) -> int | None:
    """从文本中提取端口号"""
    m = re.search(r"端口\s*(\d+)|port\s*(\d+)|:(\d{2,5})\b", text, re.IGNORECASE)
    if m:
        port_str = m.group(1) or m.group(2) or m.group(3)
        try:
            port = int(port_str)
            if 1 <= port <= 65535:
                return port
        except (ValueError, TypeError):
            pass
    return None


# ── 高危命令模式（用于标记 command_execute 中的危险命令） ────────────
_HIGH_RISK_COMMAND_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"\brm\s+-rf\b", re.IGNORECASE), "禁止递归删除"),
    (re.compile(r"\bmkfs\.", re.IGNORECASE), "禁止格式化"),
    (re.compile(r"\bchmod\s+777\b", re.IGNORECASE), "禁止 chmod 777"),
    (re.compile(r"\bdd\s+if=.*of=/dev/", re.IGNORECASE), "禁止 dd 写入磁盘"),
    (re.compile(r"curl\b.*\|.*\b(bash|sh)\b", re.IGNORECASE), "禁止 curl 管道执行"),
    (re.compile(r"wget\b.*\|.*\b(bash|sh)\b", re.IGNORECASE), "禁止 wget 管道执行"),
    (re.compile(r">\s*/etc/", re.IGNORECASE), "禁止写入 /etc"),
    (re.compile(r">\s*/boot/", re.IGNORECASE), "禁止写入 /boot"),
]


# ── 意图关键词 ────────────────────────────────────────────────────────
# 按优先级排列：先匹配更具体的模式，再匹配通用关键词
# 每个规则为 (intent_name, pattern_or_keywords, is_regex)

_INTENT_RULES: list[tuple[str, list[str], bool]] = [
    # root_cause_analysis 优先（含多个关键词组合）
    ("root_cause_analysis", [
        "分析", "根因", "为什么", "原因",
        "502", "超时", "访问慢", "响应慢",
        "upstream timed out", "connection refused",
    ], False),
    # log_query
    ("log_query", [
        "日志", "log", "journalctl", "error log",
    ], False),
    # service_status_query
    ("service_status_query", [
        "服务状态", "查看.*服务", "服务.*运行",
    ], False),
    # 补充：明确 service_mgr 模式 —— systemctl status xxx
    ("service_status_query", [
        r"systemctl\s+status\s+\w+",
        r"service\s+\w+\s+status",
        r"查看\s*(nginx|redis|mysql|sshd|docker|apache|cron|auditd)",
        r"(nginx|redis|mysql|sshd)(的|服务)?(状态|运行|是否|怎样)",
    ], True),
    # cpu_query
    ("cpu_query", [
        "cpu", "CPU", "处理器", "中央处理器",
    ], False),
    # memory_query
    ("memory_query", [
        "内存", "memory", "mem", "free",
    ], False),
    # disk_query
    ("disk_query", [
        "磁盘", "硬盘", "disk", "df ",
    ], False),
    # load_query
    ("load_query", [
        "负载", "load", "uptime",
    ], False),
    # network_query
    ("network_query", [
        "网络", "端口", "连接", "net", "network",
        "listen", "监听",
    ], False),
    # command_execute — 匹配明显是系统命令的输入（以已知命令开头）
    ("command_execute", [
        r"^(df |ps |free |uptime|whoami|uname |systemctl |journalctl |ip |ss |netstat |ifconfig |lscpu |lsblk )",
        r"^(rm |mkfs|chmod |dd |curl |wget )",
    ], True),
]


class IntentAgent:
    """规则版意图识别 —— 基于关键词/模式匹配

    使用方式：
        agent = IntentAgent()
        result = agent.detect("查看 CPU 使用率")
        # → {"intent": "cpu_query", "target_service": None, ...}

        # LLM 增强路径（自动 fallback 规则版）：
        result = await agent.detect_with_llm("查看 CPU 使用率")
    """

    def detect(self, content: str) -> dict[str, Any]:
        """识别用户输入的意图

        参数:
            content: 用户自然语言输入

        返回:
            {
                "intent": str,           # 意图名
                "target_service": str|None,  # 识别到的服务名
                "confidence": float,     # 置信度 0.0~1.0
                "entities": dict,        # 提取的实体（port, source, command 等）
                "original_input": str,
            }
        """
        # 防御：空输入或非字符串
        if not isinstance(content, str) or not content.strip():
            return {
                "intent": "unknown",
                "target_service": None,
                "confidence": 0.0,
                "entities": {},
                "original_input": str(content) if content else "",
            }

        text = content.strip()
        entities: dict[str, Any] = {}

        # 提取实体
        svc = _extract_service(text)
        port = _extract_port(text)
        if svc:
            entities["service"] = svc
        if port:
            entities["port"] = port

        # ── journalctl 命令 → 优先归为 log_query（发生在 command_execute 之前）──
        if text.startswith("journalctl") or text.startswith("journalctl "):
            return {
                "intent": "log_query",
                "target_service": svc,
                "confidence": 0.9,
                "entities": entities,
                "original_input": text,
            }

        # ── 先检查是否为明显的系统命令 ──
        for pattern in [
            re.compile(r"^(df |ps |free |uptime|whoami|uname )"),
            re.compile(r"^(systemctl |ip |ss |netstat |ifconfig |lscpu |lsblk )"),
        ]:
            if pattern.match(text):
                return {
                    "intent": "command_execute",
                    "target_service": svc,
                    "confidence": 0.95,
                    "entities": {**entities, "command": text},
                    "original_input": text,
                }

        # ── 检查高危命令模式 ──
        for pat, reason in _HIGH_RISK_COMMAND_PATTERNS:
            if pat.search(text):
                return {
                    "intent": "command_execute",
                    "target_service": svc,
                    "confidence": 1.0,
                    "entities": {
                        **entities,
                        "command": text,
                        "high_risk": True,
                        "risk_reason": reason,
                    },
                    "original_input": text,
                }

        # ── 如果包含 systemctl/service 模式且不是纯命令 → service_status_query ──
        if re.search(r"systemctl|service\s+\w+", text, re.IGNORECASE):
            # 检查是否为含服务名的状态查询
            if svc:
                return {
                    "intent": "service_status_query",
                    "target_service": svc,
                    "confidence": 0.85,
                    "entities": entities,
                    "original_input": text,
                }

        # ── 按关键词匹配意图 ──
        lower = text.lower()

        # root_cause_analysis：至少命中 2 个关键词才算
        rca_hits = sum(
            1 for kw in ["分析", "根因", "为什么", "原因", "502", "超时",
                         "访问慢", "响应慢", "upstream timed out", "connection refused"]
            if kw in lower
        )
        if rca_hits >= 2:
            return {
                "intent": "root_cause_analysis",
                "target_service": svc,
                "confidence": min(0.6 + rca_hits * 0.1, 1.0),
                "entities": entities,
                "original_input": text,
            }

        # log_query
        if any(kw in lower for kw in ["日志", "log", "journalctl", "error log"]):
            return {
                "intent": "log_query",
                "target_service": svc,
                "confidence": 0.85 if svc else 0.6,
                "entities": entities,
                "original_input": text,
            }

        # service_status_query：服务名 + 状态/查看等关键词
        svc_status_keywords = ["状态", "查看", "运行", "是否", "怎样", "status", "is-active", "is-enabled"]
        if svc and any(kw in lower for kw in svc_status_keywords):
            return {
                "intent": "service_status_query",
                "target_service": svc,
                "confidence": 0.8,
                "entities": entities,
                "original_input": text,
            }

        # cpu_query
        if any(kw in lower for kw in ["cpu", "处理器"]):
            return {
                "intent": "cpu_query",
                "target_service": svc,
                "confidence": 0.9,
                "entities": entities,
                "original_input": text,
            }

        # memory_query
        if any(kw in lower for kw in ["内存", "memory", "mem", "free"]):
            return {
                "intent": "memory_query",
                "target_service": svc,
                "confidence": 0.9,
                "entities": entities,
                "original_input": text,
            }

        # disk_query
        if any(kw in lower for kw in ["磁盘", "硬盘", "disk"]):
            return {
                "intent": "disk_query",
                "target_service": svc,
                "confidence": 0.9,
                "entities": entities,
                "original_input": text,
            }

        # load_query
        if any(kw in lower for kw in ["负载", "load", "uptime"]):
            return {
                "intent": "load_query",
                "target_service": svc,
                "confidence": 0.9,
                "entities": entities,
                "original_input": text,
            }

        # network_query
        if any(kw in lower for kw in ["网络", "端口", "连接", "net", "network", "listen", "监听"]):
            return {
                "intent": "network_query",
                "target_service": svc,
                "confidence": 0.85,
                "entities": entities,
                "original_input": text,
            }

        # 兜底：unknown
        return {
            "intent": "unknown",
            "target_service": svc,
            "confidence": 0.0,
            "entities": entities,
            "original_input": text,
        }

    # ── LLM 增强路径 ──────────────────────────────────────────────────

    async def detect_with_llm(self, content: str) -> dict[str, Any]:
        """使用 LLM 进行意图识别，失败时 fallback 到规则版 detect()

        参数:
            content: 用户自然语言输入

        返回:
            与 detect() 相同结构；LLM 失败/非法时自动 fallback
        """
        # ── 防御：空输入直接走规则版 ──
        if not isinstance(content, str) or not content.strip():
            return self.detect(content)

        # ── 检查 LLM 是否启用 ──
        from config import settings
        if not settings.LLM_ENABLED:
            logger.debug("[IntentAgent] LLM 未启用，使用规则版")
            return self.detect(content)

        # ── 调用 LLM ──
        try:
            client = LLMClient()

            system_prompt = (
                "你是一个意图分类器。根据用户输入，判断运维意图。\n"
                "只输出 JSON，不输出任何解释文字。\n"
                "JSON 字段：intent（意图名）、target_service（目标服务名或 null）、"
                "confidence（置信度 0.0~1.0）、entities（提取的实体对象，可空）。\n"
                f"intent 必须在以下范围内：{', '.join(sorted(_VALID_INTENTS))}\n"
                "target_service 必须是系统服务名（如 nginx、redis、mysql 等），或 null。"
            )
            user_prompt = f"用户输入：{content}"

            resp = await client.chat_simple(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
            )

            if not resp.ok:
                logger.info(f"[IntentAgent] LLM 调用失败，fallback 规则版: {resp.error}")
                return self.detect(content)

            # ── 解析 JSON ──
            llm_result = self._parse_llm_intent_json(resp.content)

            # ── Schema 校验 ──
            validated = self._validate_llm_intent(llm_result, content)
            if validated is not None:
                logger.info(f"[IntentAgent] LLM 意图识别成功: {validated['intent']}")
                return validated

            # ── 校验失败，fallback ──
            logger.warning("[IntentAgent] LLM 输出校验失败，fallback 规则版")
            return self.detect(content)

        except Exception as e:
            logger.warning(f"[IntentAgent] LLM 路径异常，fallback 规则版: {e}")
            return self.detect(content)

    @staticmethod
    def _parse_llm_intent_json(raw: str) -> dict[str, Any]:
        """从 LLM 原始输出中提取 JSON 对象"""
        text = raw.strip()
        # 去掉 markdown 代码块
        if text.startswith("```"):
            text = text.strip("`").strip()
            if text.lower().startswith("json"):
                text = text[4:].strip()
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            # 尝试提取第一个 JSON 对象
            m = re.search(r'\{[^{}]*\}', text)
            if m:
                try:
                    return json.loads(m.group())
                except json.JSONDecodeError:
                    pass
            return {}

    @staticmethod
    def _validate_llm_intent(llm_result: dict, original_input: str) -> dict | None:
        """校验 LLM 意图识别结果

        返回校验通过后的标准化 dict，失败返回 None。
        """
        if not isinstance(llm_result, dict):
            return None

        intent = llm_result.get("intent", "")
        if not intent or intent not in _VALID_INTENTS:
            return None

        # confidence 校验：0.0 ~ 1.0
        confidence = llm_result.get("confidence", 0.0)
        try:
            confidence = float(confidence)
        except (ValueError, TypeError):
            return None
        if not (0.0 <= confidence <= 1.0):
            return None

        # target_service 校验
        target_service = llm_result.get("target_service")
        if target_service is not None:
            if not isinstance(target_service, str) or not target_service.strip():
                target_service = None
            elif target_service.lower() not in _KNOWN_SERVICES:
                # LLM 返回了不在白名单的服务名，信任但标记
                logger.debug(f"[IntentAgent] LLM 返回非白名单服务: {target_service}")

        # entities 校验
        entities = llm_result.get("entities", {})
        if not isinstance(entities, dict):
            entities = {}

        return {
            "intent": intent,
            "target_service": target_service,
            "confidence": confidence,
            "entities": entities,
            "original_input": original_input,
        }
