"""DiagnoseAgent —— 规则版诊断规划

职责：
  - 根据意图识别结果生成工具调用计划
  - 只规划，不执行——不调用执行客户端、不调用工具调用外壳
  - 可选接入 ToolRegistry 做参数结构自检
  - command_execute 的高危命令不生成计划
"""

import re
from typing import Any


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
        """
        参数:
            tool_registry: 可选 ToolRegistry 实例，用于参数结构自检
        """
        self.tool_registry = tool_registry

    def plan(self, intent_result: dict) -> dict[str, Any]:
        """根据意图结果生成工具调用计划

        返回:
            {
                "plans": [{"tool": str, "params": dict}, ...],
                "reason": str | None,
                "validation_errors": list[str],
            }
            plans 为空列表表示无法生成有效计划。
        """
        intent = intent_result.get("intent", "unknown")
        target_service = intent_result.get("target_service")
        entities = intent_result.get("entities", {})
        original_input = intent_result.get("original_input", "")

        # 分发到各 intent handler
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
        else:  # unknown
            plans = []
            reason = "无法识别意图，无法生成工具计划"

        # ── 可选：ToolRegistry 参数自检 ──
        validation_errors: list[str] = []
        if self.tool_registry is not None:
            for plan_item in plans:
                result = self.tool_registry.validate_params(
                    plan_item["tool"], plan_item["params"]
                )
                if not result["valid"]:
                    validation_errors.extend(result["errors"])

            # 参数校验失败时清空计划，不输出错误工具计划
            if validation_errors:
                plans = []
                reason = f"工具计划参数校验失败: {'; '.join(validation_errors)}"

        return {
            "plans": plans,
            "reason": reason,
            "validation_errors": validation_errors,
        }

    # ── 各 intent 的计划生成 ──────────────────────────────────────────

    def _plan_network(self, entities: dict) -> list[dict]:
        """network_query → net_monitor"""
        port = entities.get("port")
        if port is not None:
            return [{"tool": "net_monitor", "params": {"metric": "listen", "port": port}}]
        return [{"tool": "net_monitor", "params": {"metric": "all"}}]

    @staticmethod
    def _plan_service_status(target_service: str | None) -> tuple[list[dict], str]:
        """service_status_query → service_mgr status

        target_service 为空时返回空计划，不瞎填。
        """
        if not target_service:
            return [], "无法确定目标服务名称，跳过服务状态查询"
        return (
            [{"tool": "service_mgr", "params": {"action": "status", "service": target_service}}],
            f"查询 {target_service} 服务状态",
        )

    @staticmethod
    def _plan_log(target_service: str | None, entities: dict) -> tuple[list[dict], str]:
        """log_query → log_reader

        优先用 target_service 查 journalctl；
        否则尝试 entities.source，最后返回空计划。
        """
        if target_service:
            return (
                [{"tool": "log_reader", "params": {"type": "journalctl", "service": target_service, "lines": 100}}],
                f"查询 {target_service} 日志",
            )
        source = entities.get("source")
        if source:
            return (
                [{"tool": "log_reader", "params": {"source": source, "lines": 100}}],
                f"查询日志源 {source}",
            )
        return [], "无法确定日志来源（缺少服务名或日志源）"

    @staticmethod
    def _plan_root_cause(target_service: str | None) -> tuple[list[dict], str]:
        """root_cause_analysis → log_reader + sys_info(memory) + service_mgr(status)

        三个工具按顺序：先查日志、再查内存、最后查服务状态。
        target_service 为 None 时默认用 nginx（常见根因分析场景）。
        """
        svc = target_service or "nginx"
        plans = [
            {"tool": "log_reader", "params": {"type": "journalctl", "service": svc, "lines": 100}},
            {"tool": "sys_info", "params": {"metric": "memory"}},
            {"tool": "service_mgr", "params": {"action": "status", "service": svc}},
        ]
        return plans, f"根因分析 {svc}：日志→内存→服务状态"

    @staticmethod
    def _plan_command(entities: dict, original_input: str) -> tuple[list[dict], str]:
        """command_execute → cmd_exec（仅只读命令）"""
        command = entities.get("command", original_input).strip()

        # 高危命令：不生成计划
        if entities.get("high_risk") or _is_dangerous_command(command):
            return [], f"高危命令已标记，不生成执行计划: {command[:60]}"

        # 只读白名单命令
        if command in _READONLY_COMMANDS:
            return (
                [{"tool": "cmd_exec", "params": {"command": command}}],
                f"执行只读命令: {command}",
            )

        # 类似只读的命令（df -h 变体等）—— 也允许
        if any(command.startswith(prefix) for prefix in [
            "df ", "free ", "ps ", "uptime", "whoami", "uname ",
            "systemctl status ", "journalctl ", "ip ", "ss ", "netstat ",
            "lscpu", "lsblk",
        ]):
            return (
                [{"tool": "cmd_exec", "params": {"command": command}}],
                f"执行系统命令: {command}",
            )

        # 不在白名单也不在高危 → 保守处理，不生成计划
        return [], f"命令不在只读白名单中，不生成执行计划: {command[:60]}"
