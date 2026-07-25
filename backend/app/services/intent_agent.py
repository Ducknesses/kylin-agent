"""IntentAgent —— 规则版意图识别（含 LLM 增强路径 + fallback）

职责：
  - 基于关键词/模式匹配识别用户输入意图（规则版 fallback）
  - 可选接入 LLM 进行意图识别（detect_with_llm）
  - intent 字段为自然语言描述（方便用户审查），raw_intent 为分类标签（方便机器处理）
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


# ── 允许的 raw_intent（分类标签）范围 ──────────────────────────────────
_VALID_RAW_INTENTS: set[str] = {
    "cpu_query", "memory_query", "disk_query", "load_query",
    "network_query", "service_status_query", "log_query",
    "root_cause_analysis", "command_execute", "system_monitor_query",
    "unknown",
}


# ── 服务名白名单 ──────────────────────────────────────────────────────
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


# ── 高危命令模式 ──────────────────────────────────────────────────────
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


# ── 实体 → raw_intent 推断映射（用于 LLM 未返回 raw_intent 时） ──────
# 优先级从高到低排列
_ENTITY_TO_RAW_INTENT: list[tuple[list[str], str]] = [
    (["service", "action"], "service_status_query"),
    (["log", "logs"], "log_query"),
    (["command"], "command_execute"),
    (["cpu"], "cpu_query"),
    (["memory", "mem"], "memory_query"),
    (["disk", "storage"], "disk_query"),
    (["load", "load_average"], "load_query"),
    (["network", "port", "connections", "traffic"], "network_query"),
    (["resources"], "system_monitor_query"),  # 通用资源查询
]


def _infer_raw_intent_from_entities(entities: dict) -> str:
    """根据 entities 中的 key 推断 raw_intent 分类标签"""
    entity_keys = set(str(k).lower() for k in entities.keys())
    entity_values = set()
    for v in entities.values():
        if isinstance(v, str):
            entity_values.add(v.lower())

    for keys, raw_intent in _ENTITY_TO_RAW_INTENT:
        if any(k in entity_keys for k in keys):
            return raw_intent

    # 回退：检查是否有资源名称
    if "resources" in entity_keys:
        resources = entities.get("resources", [])
        if isinstance(resources, list) and resources:
            return "system_monitor_query"

    return "unknown"


class IntentAgent:
    """意图识别 —— 规则版 + LLM 增强路径

    intent 字段为自然语言描述（方便用户审查），raw_intent 为分类标签（方便下游机器处理）。

    使用方式：
        agent = IntentAgent()
        result = agent.detect("查看 CPU 使用率")
        # → {"intent": "查询CPU使用率", "raw_intent": "cpu_query", ...}

        result = await agent.detect_with_llm("查看 CPU 使用率")
        # → {"intent": "查询CPU使用率", "raw_intent": "cpu_query", ...}
    """

    # ── raw_intent → 默认中文描述（规则版使用） ──────────────────
    _INTENT_DISPLAY: dict[str, str] = {
        "cpu_query": "查询CPU使用率",
        "memory_query": "查询内存使用情况",
        "disk_query": "查询磁盘使用情况",
        "load_query": "查询系统负载",
        "network_query": "查询网络状态",
        "service_status_query": "查询服务状态",
        "log_query": "查询系统日志",
        "root_cause_analysis": "分析系统故障根因",
        "command_execute": "执行系统命令",
        "unknown": "未识别意图",
    }

    def detect(self, content: str) -> dict[str, Any]:
        """识别用户输入的意图（规则版）

        返回:
            {
                "intent": str,              # 自然语言描述（给用户看）
                "raw_intent": str,          # 分类标签（给机器用）
                "target_service": str|None,
                "confidence": float,
                "entities": dict,
                "original_input": str,
            }
        """
        if not isinstance(content, str) or not content.strip():
            return self._result(
                raw_intent="unknown", target_service=None, confidence=0.0,
                entities={}, original_input=str(content) if content else "",
            )

        text = content.strip()
        entities: dict[str, Any] = {}

        # 提取实体
        svc = _extract_service(text)
        port = _extract_port(text)
        if svc:
            entities["service"] = svc
        if port:
            entities["port"] = port

        # ── journalctl 命令 → 优先归为 log_query ──
        if text.startswith("journalctl") or text.startswith("journalctl "):
            return self._result(
                raw_intent="log_query", target_service=svc, confidence=0.9,
                entities=entities, original_input=text,
            )

        # ── 先检查是否为明显的系统命令 ──
        for pattern in [
            re.compile(r"^(df |ps |free |uptime|whoami|uname )"),
            re.compile(r"^(systemctl |ip |ss |netstat |ifconfig |lscpu |lsblk )"),
        ]:
            if pattern.match(text):
                entities["command"] = text
                return self._result(
                    raw_intent="command_execute", target_service=svc, confidence=0.95,
                    entities=entities, original_input=text,
                )

        # ── 检查高危命令模式 ──
        for pat, reason in _HIGH_RISK_COMMAND_PATTERNS:
            if pat.search(text):
                entities["command"] = text
                entities["high_risk"] = True
                entities["risk_reason"] = reason
                return self._result(
                    raw_intent="command_execute", target_service=svc, confidence=1.0,
                    entities=entities, original_input=text,
                )

        # ── systemctl/service 模式 → service_status_query ──
        if re.search(r"systemctl|service\s+\w+", text, re.IGNORECASE):
            if svc:
                return self._result(
                    raw_intent="service_status_query", target_service=svc, confidence=0.85,
                    entities=entities, original_input=text,
                )

        # ── 按关键词匹配 ──
        lower = text.lower()

        # root_cause_analysis：至少命中 2 个关键词
        rca_hits = sum(
            1 for kw in ["分析", "根因", "为什么", "原因", "502", "超时",
                         "访问慢", "响应慢", "upstream timed out", "connection refused"]
            if kw in lower
        )
        if rca_hits >= 2:
            return self._result(
                raw_intent="root_cause_analysis", target_service=svc,
                confidence=min(0.6 + rca_hits * 0.1, 1.0),
                entities=entities, original_input=text,
            )

        # log_query
        if any(kw in lower for kw in ["日志", "log", "journalctl", "error log"]):
            return self._result(
                raw_intent="log_query", target_service=svc,
                confidence=0.85 if svc else 0.6,
                entities=entities, original_input=text,
            )

        # service_status_query
        svc_status_keywords = ["状态", "查看", "运行", "是否", "怎样", "status", "is-active", "is-enabled"]
        if svc and any(kw in lower for kw in svc_status_keywords):
            return self._result(
                raw_intent="service_status_query", target_service=svc, confidence=0.8,
                entities=entities, original_input=text,
            )

        # cpu_query
        if any(kw in lower for kw in ["cpu", "处理器"]):
            return self._result(
                raw_intent="cpu_query", target_service=svc, confidence=0.9,
                entities=entities, original_input=text,
            )

        # memory_query
        if any(kw in lower for kw in ["内存", "memory", "mem", "free"]):
            return self._result(
                raw_intent="memory_query", target_service=svc, confidence=0.9,
                entities=entities, original_input=text,
            )

        # disk_query
        if any(kw in lower for kw in ["磁盘", "硬盘", "disk"]):
            return self._result(
                raw_intent="disk_query", target_service=svc, confidence=0.9,
                entities=entities, original_input=text,
            )

        # load_query
        if any(kw in lower for kw in ["负载", "load", "uptime"]):
            return self._result(
                raw_intent="load_query", target_service=svc, confidence=0.9,
                entities=entities, original_input=text,
            )

        # network_query
        if any(kw in lower for kw in ["网络", "端口", "连接", "net", "network", "listen", "监听"]):
            return self._result(
                raw_intent="network_query", target_service=svc, confidence=0.85,
                entities=entities, original_input=text,
            )

        # 兜底：unknown
        # 尝试根据实体推断 raw_intent
        raw = _infer_raw_intent_from_entities(entities) if entities else "unknown"
        return self._result(
            raw_intent=raw, target_service=svc, confidence=0.0,
            entities=entities, original_input=text,
        )

    def _result(
        self, raw_intent: str, target_service: str | None, confidence: float,
        entities: dict, original_input: str,
    ) -> dict[str, Any]:
        """构建标准化返回结构"""
        display = self._INTENT_DISPLAY.get(raw_intent, raw_intent)
        # 如果有服务名，追加到 intent 描述中
        if target_service and raw_intent in ("service_status_query", "log_query", "root_cause_analysis"):
            display = f"{display}（{target_service}）"
        return {
            "intent": display,
            "raw_intent": raw_intent,
            "target_service": target_service,
            "confidence": confidence,
            "entities": entities,
            "original_input": original_input,
        }

    # ── LLM 增强路径 ──────────────────────────────────────────────────

    async def detect_with_llm(self, content: str) -> dict[str, Any]:
        """使用 LLM 进行意图识别，失败时 fallback 到规则版 detect()"""
        if not isinstance(content, str) or not content.strip():
            return self.detect(content)

        from config import settings
        if not settings.LLM_ENABLED:
            logger.debug("[IntentAgent] LLM 未启用，使用规则版")
            return self.detect(content)

        try:
            client = LLMClient()

            raw_intents_str = ", ".join(sorted(_VALID_RAW_INTENTS))
            system_prompt = (
                "你是一个运维意图分类器。根据用户输入，判断用户想要做什么。\n"
                "只输出 JSON 对象，不输出任何解释文字或 markdown 代码块。\n\n"
                "JSON 字段说明：\n"
                '  - intent: 自然语言描述用户意图（如'
                '"查询CPU和内存使用率"、"重启nginx服务"、"知道redis为什么连接异常"等）'
                '，用于向用户展示\n'
                "  - raw_intent: 分类标签，必须在以下范围内："
                f"{raw_intents_str}\n"
                "    其中 cpu/memory/disk/load/network_query 分别对应查询相应系统资源；\n"
                "    service_status_query 对应查询服务状态；log_query 对应查询日志；\n"
                "    root_cause_analysis 对应故障分析；command_execute 对应执行命令；\n"
                "    unknown 对应无法明确分类的情况\n"
                "  - target_service: 目标服务名（如 nginx、redis、mysql 等）或 null\n"
                "  - confidence: 置信度 0.0~1.0\n"
                "  - entities: 提取的实体对象，包括 resources（资源类型列表，如 [\"cpu\", \"memory\"]）、\n"
                "    service（服务名）、port（端口号）、command（命令文本）、action（操作类型）等\n\n"
                "示例输出：\n"
                '{"intent": "查询CPU和内存使用率", "raw_intent": "system_monitor_query", "target_service": null, "confidence": 0.95, "entities": {"resources": ["cpu", "memory"]}}\n'
                '{"intent": "查看nginx服务是否正常运行", "raw_intent": "service_status_query", "target_service": "nginx", "confidence": 0.9, "entities": {"service": "nginx", "action": "status"}}\n'
            )
            user_prompt = f"用户输入：{content}"

            resp = await client.chat_simple(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
            )

            if not resp.ok:
                logger.info(f"[IntentAgent] LLM 调用失败，fallback 规则版: {resp.error}")
                return self.detect(content)

            llm_result = self._parse_llm_intent_json(resp.content)
            validated = self._validate_llm_intent(llm_result, content)
            if validated is not None:
                logger.info(
                    f"[IntentAgent] LLM 意图识别成功: raw_intent={validated['raw_intent']}, "
                    f"intent={validated['intent']}"
                )
                return validated

            logger.warning("[IntentAgent] LLM 输出校验失败，fallback 规则版")
            return self.detect(content)

        except Exception as e:
            logger.warning(f"[IntentAgent] LLM 路径异常，fallback 规则版: {e}")
            return self.detect(content)

    @staticmethod
    def _parse_llm_intent_json(raw: str) -> dict[str, Any]:
        """从 LLM 原始输出中提取 JSON 对象"""
        text = raw.strip()
        if text.startswith("```"):
            text = text.strip("`").strip()
            if text.lower().startswith("json"):
                text = text[4:].strip()
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            m = re.search(r'\{[^{}]*\}', text)
            if m:
                try:
                    return json.loads(m.group())
                except json.JSONDecodeError:
                    pass
            return {}

    @staticmethod
    def _validate_llm_intent(llm_result: dict, original_input: str) -> dict | None:
        """校验 LLM 意图识别结果"""
        if not isinstance(llm_result, dict):
            return None

        # intent: 必须是有效非空字符串（不再限枚举）
        intent = str(llm_result.get("intent", "")).strip()
        if not intent:
            return None

        # raw_intent: 分类标签，用于下游机器处理
        raw_intent = str(llm_result.get("raw_intent", "")).strip()
        if raw_intent not in _VALID_RAW_INTENTS:
            # 如果 LLM 返回了不在枚举中的 raw_intent，尝试从 entities 推断
            entities = llm_result.get("entities", {})
            if isinstance(entities, dict) and entities:
                raw_intent = _infer_raw_intent_from_entities(entities)
            if raw_intent not in _VALID_RAW_INTENTS:
                raw_intent = "unknown"

        # confidence: 0.0 ~ 1.0
        confidence = llm_result.get("confidence", 0.5)
        try:
            confidence = float(confidence)
        except (ValueError, TypeError):
            return None
        if not (0.0 <= confidence <= 1.0):
            return None

        # target_service
        target_service = llm_result.get("target_service")
        if target_service is not None:
            if not isinstance(target_service, str) or not target_service.strip():
                target_service = None
            elif target_service.lower() not in _KNOWN_SERVICES:
                logger.debug(f"[IntentAgent] LLM 返回非白名单服务: {target_service}")

        # entities
        entities = llm_result.get("entities", {})
        if not isinstance(entities, dict):
            entities = {}

        return {
            "intent": intent,
            "raw_intent": raw_intent,
            "target_service": target_service,
            "confidence": confidence,
            "entities": entities,
            "original_input": original_input,
        }