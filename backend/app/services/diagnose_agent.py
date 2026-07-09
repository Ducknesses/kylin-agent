"""DiagnoseAgent —— 规则版诊断规划（含 LLM 增强路径 + fallback）

职责：
  - 根据意图识别结果生成工具调用计划
  - 只规划，不执行——不调用执行客户端、不调用工具调用外壳
  - 可选接入 ToolRegistry 做参数结构自检
  - command_execute 的高危命令不生成计划
  - LLM 可生成候选 tool_calls，但必须通过 ToolRegistry 校验
"""

import json
import logging
import re
from typing import Any

from app.services.llm_client import LLMClient

logger = logging.getLogger(__name__)


# ── 高危命令检测模式（与 IntentAgent 保持一致） ──────────────────────
_HIGH_RISK_PATTERNS: list[re.Pattern] = [
    re.compile(r"\brm\s+-rf\b", re.IGNORECASE),
    re.compile(r"\bmkfs\.", re.IGNORECASE),
    re.compile(r"\bchmod\s+777\b", re.IGNORECASE),
    re.compile(r"\bdd\s+if=.*of=/dev/", re.IGNORECASE),
    re.compile(r"curl\b.*\|.*\b(bash|sh)\b", re.IGNORECASE),
    re.compile(r"wget\b.*\|.*\b(bash|sh)\b", re.IGNORECASE),
    re.compile(r">\s*/etc/", re.IGNORECASE),
    re.compile(r">\s*/boot/", re.IGNORECASE),
]

# ── 只读命令白名单（可安全生成 cmd_exec 计划） ──────────────────────
_READONLY_COMMANDS: set[str] = {
    "df -h", "free -m", "uptime", "whoami", "uname -a",
    "ps aux", "systemctl status nginx", "journalctl -u nginx -n 50",
    "ip a", "ss -tlnp", "netstat -tlnp",
}


def _is_dangerous_command(command: str) -> bool:
    """检查命令是否匹配高危模式"""
    return any(p.search(command) for p in _HIGH_RISK_PATTERNS)


class DiagnoseAgent:
    """规则版诊断规划 —— 根据意图生成工具调用计划

    使用方式：
        agent = DiagnoseAgent(tool_registry=registry)  # tool_registry 可选
        plans = agent.plan(intent_result)
        # → [{"tool": "sys_info", "params": {"metric": "cpu"}}]
    """

    def __init__(self, tool_registry: Any = None) -> None:
        self.tool_registry = tool_registry

    def plan(self, intent_result: dict) -> dict[str, Any]:
        intent = intent_result.get("intent", "unknown")
        target_service = intent_result.get("target_service")
        entities = intent_result.get("entities", {})
        original_input = intent_result.get("original_input", "")

        if intent == "cpu_query":
            plans = [{"tool": "sys_info", "params": {"metric": "cpu"}}]
            reason = "查询 CPU 使用率"
        elif intent == "memory_query":
            plans = [{"tool": "sys_info", "params": {"metric": "memory"}}]
            reason = "查询内存使用情况"
        elif intent == "disk_query":
            plans = [{"tool": "sys_info", "params": {"metric": "disk"}}]
            reason = "查询磁盘使用情况"
        elif intent == "load_query":
            plans = [{"tool": "sys_info", "params": {"metric": "load"}}]
            reason = "查询系统负载"
        elif intent == "network_query":
            plans = self._plan_network(entities)
            reason = "查询网络状态"
        elif intent == "service_status_query":
            plans, reason = self._plan_service_status(target_service)
        elif intent == "log_query":
            plans, reason = self._plan_log(target_service, entities)
        elif intent == "root_cause_analysis":
            plans, reason = self._plan_root_cause(target_service)
        elif intent == "command_execute":
            plans, reason = self._plan_command(entities, original_input)
        else:
            plans, reason = self._plan_fallback(target_service, entities, original_input)

        validation_errors: list[str] = []
        if self.tool_registry is not None:
            for plan_item in plans:
                result = self.tool_registry.validate_params(
                    plan_item["tool"], plan_item["params"]
                )
                if not result["valid"]:
                    validation_errors.extend(result["errors"])
            if validation_errors:
                plans = []
                reason = f"工具计划参数校验失败: {'; '.join(validation_errors)}"

        return {"plans": plans, "reason": reason, "validation_errors": validation_errors}

    def _plan_network(self, entities: dict) -> list[dict]:
        port = entities.get("port")
        if port is not None:
            return [{"tool": "net_monitor", "params": {"metric": "listen", "port": port}}]
        return [{"tool": "net_monitor", "params": {"metric": "all"}}]

    @staticmethod
    def _plan_service_status(target_service: str | None) -> tuple[list[dict], str]:
        if not target_service:
            return [], "无法确定目标服务名称，跳过服务状态查询"
        return (
            [{"tool": "service_mgr", "params": {"action": "status", "service": target_service}}],
            f"查询 {target_service} 服务状态",
        )

    @staticmethod
    def _plan_fallback(target_service: str | None, entities: dict, original_input: str) -> tuple[list[dict], str]:
        if not target_service:
            return [], "无法识别意图，无法生成工具计划"
        lower = (entities.get("command", original_input) or "").lower()
        action = entities.get("action")
        if action == "restart" or any(kw in lower for kw in ["重启", "restart"]):
            return ([{"tool": "service_mgr", "params": {"action": "restart", "service": target_service}}], f"重启 {target_service}")
        if action == "start" or any(kw in lower for kw in ["启动", "start "]):
            return ([{"tool": "service_mgr", "params": {"action": "start", "service": target_service}}], f"启动 {target_service}")
        if action == "stop" or any(kw in lower for kw in ["停止", "stop "]):
            return ([{"tool": "service_mgr", "params": {"action": "stop", "service": target_service}}], f"停止 {target_service}")
        return [], "无法识别意图，无法生成工具计划"

    @staticmethod
    def _plan_log(target_service: str | None, entities: dict) -> tuple[list[dict], str]:
        if target_service:
            return ([{"tool": "log_reader", "params": {"type": "journalctl", "service": target_service, "lines": 100}}], f"查询 {target_service} 日志")
        source = entities.get("source")
        if source:
            return ([{"tool": "log_reader", "params": {"source": source, "lines": 100}}], f"查询日志源 {source}")
        return [], "无法确定日志来源（缺少服务名或日志源）"

    @staticmethod
    def _plan_root_cause(target_service: str | None) -> tuple[list[dict], str]:
        svc = target_service or "nginx"
        plans = [
            {"tool": "log_reader", "params": {"type": "journalctl", "service": svc, "lines": 100}},
            {"tool": "sys_info", "params": {"metric": "memory"}},
            {"tool": "service_mgr", "params": {"action": "status", "service": svc}},
        ]
        return plans, f"根因分析 {svc}：日志→内存→服务状态"

    @staticmethod
    def _plan_command(entities: dict, original_input: str) -> tuple[list[dict], str]:
        command = entities.get("command", original_input).strip()
        if entities.get("high_risk") or _is_dangerous_command(command):
            return [], f"高危命令已标记，不生成执行计划: {command[:60]}"
        if command in _READONLY_COMMANDS:
            return ([{"tool": "cmd_exec", "params": {"command": command}}], f"执行只读命令: {command}")
        if any(command.startswith(prefix) for prefix in [
            "df ", "free ", "ps ", "uptime", "whoami", "uname ",
            "systemctl status ", "journalctl ", "ip ", "ss ", "netstat ",
            "lscpu", "lsblk",
        ]):
            return ([{"tool": "cmd_exec", "params": {"command": command}}], f"执行系统命令: {command}")
        return [], f"命令不在只读白名单中，不生成执行计划: {command[:60]}"

    # ── LLM 增强路径 ──────────────────────────────────────────────────

    async def plan_with_llm(self, intent_result: dict) -> dict[str, Any]:
        from config import settings
        if not settings.LLM_ENABLED:
            logger.debug("[DiagnoseAgent] LLM 未启用，使用规则版")
            return self.plan(intent_result)

        intent = intent_result.get("intent", "unknown")
        if intent == "command_execute" and intent_result.get("entities", {}).get("high_risk"):
            logger.debug("[DiagnoseAgent] 高危命令，不交 LLM 规划")
            return self.plan(intent_result)

        try:
            client = LLMClient()
            tool_names = self._get_tool_names()
            system_prompt = (
                "你是一个诊断规划器。根据意图识别结果，生成需要调用的工具列表。\n"
                "只输出 JSON 数组，不输出任何解释文字。\n"
                "每项包含 tool（工具名）和 params（参数字典）。\n"
                f"可用工具：{', '.join(tool_names)}\n"
                "工具参数说明：\n"
                "- sys_info: metric (cpu/memory/disk/load/network/uptime/all)\n"
                "- service_mgr: action (status/start/stop/restart), service (服务名)\n"
                "- log_reader: type (journalctl), service (服务名), lines (行数 1-500)\n"
                "- net_monitor: metric (connections/traffic/interfaces/routes/dns/listen/all), port (可选)\n"
                "- cmd_exec: command (命令字符串，限只读命令)\n"
                "禁止生成 rm、mkfs、chmod 777、dd、curl pipe 等危险命令。"
            )
            user_prompt = json.dumps(intent_result, ensure_ascii=False)

            resp = await client.chat_simple(system_prompt=system_prompt, user_prompt=user_prompt)
            if not resp.ok:
                logger.info(f"[DiagnoseAgent] LLM 调用失败，fallback 规则版: {resp.error}")
                return self.plan(intent_result)

            llm_plans = self._parse_llm_plan_json(resp.content)
            validated_plans, validation_errors = self._validate_llm_plans(llm_plans)

            if validation_errors:
                logger.warning(f"[DiagnoseAgent] LLM 计划校验失败: {'; '.join(validation_errors[:3])}")
            if not validated_plans:
                logger.warning("[DiagnoseAgent] LLM 未生成有效计划，fallback 规则版")
                return self.plan(intent_result)

            logger.info(f"[DiagnoseAgent] LLM 生成 {len(validated_plans)} 个有效计划")
            return {
                "plans": validated_plans,
                "reason": f"LLM 生成 {len(validated_plans)} 个诊断步骤",
                "validation_errors": validation_errors,
            }
        except Exception as e:
            logger.warning(f"[DiagnoseAgent] LLM 路径异常，fallback 规则版: {e}")
            return self.plan(intent_result)

    @staticmethod
    def _parse_llm_plan_json(raw: str) -> list[dict]:
        text = raw.strip()
        if text.startswith("```"):
            text = text.strip("`").strip()
            if text.lower().startswith("json"):
                text = text[4:].strip()
        try:
            result = json.loads(text)
            if isinstance(result, list):
                return result
            if isinstance(result, dict) and "plans" in result:
                return result["plans"]
            return []
        except json.JSONDecodeError:
            m = re.search(r'\[.*\]', text, re.DOTALL)
            if m:
                try:
                    return json.loads(m.group())
                except json.JSONDecodeError:
                    pass
            return []

    def _validate_llm_plans(self, llm_plans: list) -> tuple[list[dict], list[str]]:
        if not isinstance(llm_plans, list):
            return [], ["LLM 输出不是 JSON 数组"]
        validated: list[dict] = []
        errors: list[str] = []
        for i, item in enumerate(llm_plans):
            if not isinstance(item, dict):
                errors.append(f"第 {i+1} 项不是对象")
                continue
            tool = item.get("tool", "")
            params = item.get("params", {})
            if not isinstance(tool, str) or not tool:
                errors.append(f"第 {i+1} 项缺少 tool 字段")
                continue
            if not isinstance(params, dict):
                errors.append(f"第 {i+1} 项 params 不是字典")
                continue
            if self.tool_registry is not None:
                if not self.tool_registry.exists(tool):
                    errors.append(f"工具 {tool} 不在 ToolRegistry 中")
                    continue
                validation = self.tool_registry.validate_params(tool, params)
                if not validation["valid"]:
                    errors.append(f"工具 {tool} 参数校验失败: {'; '.join(validation['errors'])}")
                    continue
            if tool == "cmd_exec":
                command = params.get("command", "")
                if _is_dangerous_command(command):
                    errors.append(f"LLM 生成高危命令，已过滤: {command[:60]}")
                    continue
            validated.append({"tool": tool, "params": params})
        return validated, errors

    def _get_tool_names(self) -> list[str]:
        if self.tool_registry is not None:
            return self.tool_registry.get_tool_names()
        return ["sys_info", "service_mgr", "log_reader", "net_monitor", "cmd_exec"]
