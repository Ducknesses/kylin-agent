"""FixPlannerAgent —— 规则版一键修复方案规划器

职责：
  - 根据诊断结果（intent + observations + report）生成结构化 FixOption 列表
  - 只规划，不执行——不调用 MCPClient、AgentHarness、系统命令
  - 不生成 risk_level="high" 的 FixOption
  - medium 选项必须 requires_confirm=True
  - rollback 仅作为人工说明文本，不自动执行
"""

import logging
import uuid
from typing import Any

from app.schemas.action import FixOption

logger = logging.getLogger(__name__)

# ── 阈值 ──────────────────────────────────────────────────────────────

_MEMORY_HIGH_PERCENT = 80  # 内存使用率 ≥ 此值视为偏高
_DISK_HIGH_PERCENT = 85    # 磁盘使用率 ≥ 此值视为偏高

# ── 服务异常状态关键词 ───────────────────────────────────────────────
# 在 observation 的 result 中搜索这些关键词判定服务异常

_SERVICE_BAD_STATUSES: tuple[str, ...] = (
    "inactive", "failed", "stopped", "not running",
    "dead", "exited",
)


# ── 辅助函数 ──────────────────────────────────────────────────────────

def _is_number(val: object) -> bool:
    """判定是否为数值（排除 bool，因为 bool 是 int 的子类）"""
    return isinstance(val, (int, float)) and not isinstance(val, bool)


def _extract_memory_percent(observations: list[dict[str, object]]) -> float | None:
    """从 observations 中提取真实 memory.percent

    AgentHarness observation 结构：
      {tool:"sys_info", params:{metric:"memory"}, ok:True,
       result:{memory:{percent:45.0, total:...}}}

    仅处理 ok=True 且 tool=sys_info 且 metric 为 memory/all 的条目。
    """
    for obs in observations:
        if not isinstance(obs, dict):
            continue
        if obs.get("ok") is not True:
            continue
        if obs.get("tool") != "sys_info":
            continue
        params = obs.get("params")
        if not isinstance(params, dict):
            continue
        metric = params.get("metric", "")
        if metric not in ("memory", "all"):
            continue
        result = obs.get("result")
        if not isinstance(result, dict):
            continue
        memory = result.get("memory")
        if not isinstance(memory, dict):
            continue
        pct = memory.get("percent")
        if _is_number(pct):
            return float(pct)  # type: ignore[arg-type]
    return None


def _extract_disk_max(
    observations: list[dict[str, object]],
) -> tuple[str | None, float] | None:
    """从 observations 中提取磁盘挂载点最高使用率

    AgentHarness observation 结构：
      {tool:"sys_info", params:{metric:"disk"}, ok:True,
       result:{disk:[{mountpoint:"/", percent:62.0}, ...]}}

    遍历所有挂载点，返回 (最高占用挂载点, 百分比)。
    仅处理 ok=True 且 tool=sys_info 且 metric 为 disk/all 的条目。
    """
    best: tuple[str | None, float] | None = None
    for obs in observations:
        if not isinstance(obs, dict):
            continue
        if obs.get("ok") is not True:
            continue
        if obs.get("tool") != "sys_info":
            continue
        params = obs.get("params")
        if not isinstance(params, dict):
            continue
        metric = params.get("metric", "")
        if metric not in ("disk", "all"):
            continue
        result = obs.get("result")
        if not isinstance(result, dict):
            continue
        disks = result.get("disk")
        if not isinstance(disks, list):
            continue
        for item in disks:
            if not isinstance(item, dict):
                continue
            pct = item.get("percent")
            if not _is_number(pct):
                continue
            pct_f = float(pct)  # type: ignore[arg-type]
            if best is None or pct_f > best[1]:
                mountpoint = item.get("mountpoint")
                best = (str(mountpoint) if mountpoint else None, pct_f)
    return best


def _contains_status(
    observations: list[dict[str, object]],
    service: str | None,
) -> bool:
    """检查 observations 中是否包含指定服务的异常状态"""
    if not service:
        return False
    for obs in observations:
        if not isinstance(obs, dict):
            continue
        if obs.get("ok") is not True:
            continue
        result = obs.get("result")
        if not isinstance(result, dict):
            continue
        # 检测 result 中的 service 字段匹配
        res_service = str(result.get("service", "")).lower()
        status = str(result.get("status", "")).lower()
        if res_service != service.lower():
            continue
        for bad in _SERVICE_BAD_STATUSES:
            if bad in status:
                return True
    return False


# ═══════════════════════════════════════════════════════════════════════
# FixPlannerAgent
# ═══════════════════════════════════════════════════════════════════════

class FixPlannerAgent:
    """规则版一键修复方案规划器

    使用方式：
        agent = FixPlannerAgent(tool_registry=registry)  # tool_registry 可选
        options = agent.plan(
            intent="service_status_query",
            observations=[...],
            report="...",
            target_service="nginx",
        )
        # → [FixOption(...), ...]
    """

    def __init__(self, tool_registry: Any = None) -> None:
        """参数:
            tool_registry: 可选 ToolRegistry，提供后会对每项 FixOption 做参数校验
        """
        self.tool_registry = tool_registry

    # ── 主入口 ──────────────────────────────────────────────────────

    def plan(
        self,
        intent: str,
        observations: list[dict[str, object]],
        report: str,
        target_service: str | None = None,
    ) -> list[FixOption]:
        """根据诊断结果生成修复选项列表

        参数:
            intent: 意图类型（如 service_status_query / memory_query / disk_query 等）
            observations: AgentContext.observations 列表
            report: ReporterAgent 生成的诊断报告文本
            target_service: 目标服务名，可选

        返回:
            FixOption 列表，证据不足时返回空列表。
            不生成 risk_level="high" 的选项。
        """
        # 防御：observations 非预期结构
        if not isinstance(observations, list):
            logger.warning("[FixPlanner] observations 不是列表，返回空")
            return []

        # ── 按场景优先级依次检查 ──
        options: list[FixOption] = []

        # 1. 服务异常（优先级最高——可能有明确修复动作）
        service_opts = self._plan_service_issue(intent, observations, target_service)
        options.extend(service_opts)

        # 2. 内存高
        memory_opts = self._plan_memory_issue(intent, observations, report)
        options.extend(memory_opts)

        # 3. 磁盘高
        disk_opts = self._plan_disk_issue(intent, observations, report, target_service)
        options.extend(disk_opts)

        # ── 校验通过 ToolRegistry ──
        if self.tool_registry is not None and options:
            validated: list[FixOption] = []
            for opt in options:
                validation = self.tool_registry.validate_params(opt.tool, opt.params)
                if validation["valid"]:
                    validated.append(opt)
                else:
                    logger.warning(
                        "[FixPlanner] FixOption 参数校验失败: %s — %s",
                        opt.option_id, "; ".join(validation["errors"]),
                    )
            return validated

        return options

    # ── 服务异常 ────────────────────────────────────────────────────

    def _plan_service_issue(
        self,
        intent: str,
        observations: list[dict[str, object]],
        target_service: str | None,
    ) -> list[FixOption]:
        """服务异常场景：inactive / failed / stopped → 生成 restart 候选"""
        # 仅当意图暗示服务相关且有目标服务名时触发
        service_intents = {"service_status_query", "root_cause_analysis", "log_query"}
        if intent not in service_intents:
            return []
        if not target_service:
            return []
        if not _contains_status(observations, target_service):
            return []

        option_id = f"fix_{uuid.uuid4().hex[:8]}"
        return [FixOption(
            option_id=option_id,
            title=f"重启 {target_service} 服务",
            description=(
                f"检测到 {target_service} 服务状态异常，"
                "执行重启操作以尝试恢复服务。"
                "该操作会造成短暂服务中断，请确认后执行。"
            ),
            risk_level="medium",
            tool="service_mgr",
            params={"action": "restart", "service": target_service},
            requires_confirm=True,
            rollback=(
                f"如重启后异常，请检查 {target_service} 服务日志和配置，"
                "并恢复原配置后再次启动。"
            ),
        )]

    # ── 内存高 ──────────────────────────────────────────────────────

    def _plan_memory_issue(
        self,
        intent: str,
        observations: list[dict[str, object]],
        report: str,
    ) -> list[FixOption]:
        """内存高场景：≥ 80% → 生成只读诊断候选"""
        memory_intents = {"memory_query", "root_cause_analysis"}
        if intent not in memory_intents:
            return []
        pct = _extract_memory_percent(observations)
        if pct is None or pct < _MEMORY_HIGH_PERCENT:
            return []

        options: list[FixOption] = []

        # 只读：重新查询内存详情
        options.append(FixOption(
            option_id=f"fix_{uuid.uuid4().hex[:8]}",
            title="查看内存使用详情",
            description=f"当前内存使用率约为 {pct:.1f}%，建议查看详细内存信息以分析原因。",
            risk_level="low",
            tool="sys_info",
            params={"metric": "memory"},
            requires_confirm=False,
        ))

        # 只读：查看进程列表（cmd_exec 白名单命令 ps aux）
        options.append(FixOption(
            option_id=f"fix_{uuid.uuid4().hex[:8]}",
            title="查看进程列表",
            description="列出当前运行的进程，帮助定位高内存使用的进程。",
            risk_level="low",
            tool="cmd_exec",
            params={"command": "ps aux"},
            requires_confirm=False,
        ))

        return options

    # ── 磁盘高 ──────────────────────────────────────────────────────

    def _plan_disk_issue(
        self,
        intent: str,
        observations: list[dict[str, object]],
        report: str,
        target_service: str | None,
    ) -> list[FixOption]:
        """磁盘高场景：≥ 85% → 生成只读诊断候选"""
        disk_intents = {"disk_query", "root_cause_analysis"}
        if intent not in disk_intents:
            return []
        disk_result = _extract_disk_max(observations)
        if disk_result is None:
            return []
        mountpoint, pct = disk_result
        if pct < _DISK_HIGH_PERCENT:
            return []

        options: list[FixOption] = []

        # 只读：重新查询磁盘详情
        mp_desc = f"（挂载点 {mountpoint}）" if mountpoint else ""
        options.append(FixOption(
            option_id=f"fix_{uuid.uuid4().hex[:8]}",
            title="查看磁盘使用详情",
            description=f"当前磁盘使用率约为 {pct:.1f}%{mp_desc}，建议查看详细磁盘信息以分析原因。",
            risk_level="low",
            tool="sys_info",
            params={"metric": "disk"},
            requires_confirm=False,
        ))

        # 如果有目标服务，附加日志查看
        if target_service:
            options.append(FixOption(
                option_id=f"fix_{uuid.uuid4().hex[:8]}",
                title=f"查看 {target_service} 日志",
                description="检查服务日志中是否存在磁盘相关错误。",
                risk_level="low",
                tool="log_reader",
                params={"type": "journalctl", "service": target_service, "lines": 100},
                requires_confirm=False,
            ))

        return options
