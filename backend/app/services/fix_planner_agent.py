"""FixPlannerAgent —— 规则版 + LLM 版一键修复方案规划器

职责：
  - 根据诊断结果（intent + observations + report）生成结构化 FixOption 列表
  - 只规划，不执行——不调用 MCPClient、AgentHarness、系统命令
  - 不生成 risk_level="high" 的 FixOption
  - medium 选项必须 requires_confirm=True
  - rollback 仅作为人工说明文本，不自动执行
  - plan_with_llm 调用真实 LLMClient，失败自动回退规则版
"""

import json
import logging
import uuid
from typing import Any

from pydantic import BaseModel, validator

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


# ── LLM 候选中间模型 ────────────────────────────────────────────────
# LLM 不生成 option_id/risk_level/requires_confirm，后端统一补全。

class LLMFixCandidate(BaseModel):
    """LLM 返回的单个修复候选（未经后端校验）"""
    title: str
    description: str
    tool: str
    params: dict[str, object]
    rollback: str | None = None

    class Config:
        extra = "forbid"

    @validator("title", "description", "tool")
    def _not_blank(cls, v: str) -> str:
        stripped = v.strip()
        if not stripped:
            raise ValueError("不能为空或仅包含空白字符")
        return stripped


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

    def __init__(self, tool_registry: Any = None, llm_client: Any = None) -> None:
        """参数:
            tool_registry: 可选 ToolRegistry，提供后会对每项 FixOption 做参数校验
            llm_client: 可选 LLMClient，为 None 时 plan_with_llm 回退规则版
        """
        self.tool_registry = tool_registry
        self.llm_client = llm_client

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
        if self.tool_registry is None:
            if options:
                logger.warning(
                    "FixPlannerAgent 未注入 ToolRegistry，已拒绝生成规则修复选项"
                )
            return []

        return self._validate_options_with_registry(options)

    def _validate_options_with_registry(
        self,
        options: list[FixOption],
    ) -> list[FixOption]:
        """通过 ToolRegistry 校验并校正 FixOption。

        ToolRegistry 负责工具存在性、参数合法性和 action 静态风险。
        没有 action 字段的候选保留 Planner 已确定的风险等级。
        """

        if self.tool_registry is None:
            logger.warning(
                "FixPlannerAgent 未注入 ToolRegistry，"
                "已拒绝生成修复选项"
            )
            return []

        validated: list[FixOption] = []

        for opt in options:
            if not self.tool_registry.exists(opt.tool):
                logger.warning(
                    "修复候选使用未知工具: tool=%s",
                    opt.tool,
                )
                continue

            validation = self.tool_registry.validate_params(
                opt.tool,
                opt.params,
            )
            if not validation["valid"]:
                logger.warning(
                    "修复候选未通过 ToolRegistry 校验: "
                    "tool=%s, errors=%s",
                    opt.tool,
                    validation["errors"],
                )
                continue

            final_risk = opt.risk_level

            # 只有具备 action 参数的工具，才使用 Registry 的 action 风险覆盖。
            action = opt.params.get("action")
            if isinstance(action, str):
                registry_risk = self.tool_registry.get_risk_for_action(
                    opt.tool,
                    action,
                )

                if registry_risk is None:
                    logger.warning(
                        "修复候选无法确定 action 风险: tool=%s",
                        opt.tool,
                    )
                    continue

                final_risk = registry_risk

            # Planner 不生成高风险自动修复候选。
            if final_risk == "high":
                logger.warning(
                    "修复候选风险过高，已拒绝生成: tool=%s",
                    opt.tool,
                )
                continue

            validated.append(
                opt.copy(
                    update={
                        "risk_level": final_risk,
                        "requires_confirm": final_risk == "medium",
                    }
                )
            )

        return validated
    # ── 服务异常 ────────────────────────────────────────────────────

    def _plan_service_issue(
        self,
        intent: str,
        observations: list[dict[str, object]],
        target_service: str | None,
    ) -> list[FixOption]:
        """服务异常场景：inactive / failed / stopped → 生成 restart 候选"""
        # 仅当意图暗示服务相关且有目标服务名时触发
        # 支持 raw_intent 和旧版 intent（向后兼容）
        service_intents = {"service_status_query", "root_cause_analysis", "log_query"}
        actual_intent = intent
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

    # ── LLM 增强路径 ──────────────────────────────────────────────────

    async def plan_with_llm(
        self,
        intent: str,
        observations: list[dict[str, object]],
        report: str,
        target_service: str | None = None,
    ) -> list[FixOption]:
        """LLM 生成修复候选，经校验后转为 FixOption。失败回退规则版。"""
        if self.llm_client is None:
            logger.info("[FixPlanner] llm_client=None，回退规则版")
            return self.plan(intent, observations, report, target_service)
        if intent == "command_execute":
            return self.plan(intent, observations, report, target_service)

        try:
            prompt = self._build_llm_prompt(intent, observations, report, target_service)
            resp = await self.llm_client.chat_simple(
                system_prompt=prompt["system"], user_prompt=prompt["user"],
            )
            if not resp.ok:
                logger.info("[FixPlanner] LLM 调用失败: %s", resp.error)
                return self.plan(intent, observations, report, target_service)

            candidates = self._parse_llm_candidates(resp.content)
            if not candidates:
                logger.info("[FixPlanner] LLM 未生成有效候选，回退规则版")
                return self.plan(intent, observations, report, target_service)

            options = self._build_fix_options(candidates)
            if not options:
                logger.info("[FixPlanner] 所有 LLM 候选被过滤，回退规则版")
                return self.plan(intent, observations, report, target_service)

            logger.info("[FixPlanner] LLM 生成 %d 个 FixOption", len(options))
            return options
        except Exception:
            logger.exception("[FixPlanner] LLM 路径异常，回退规则版")
            return self.plan(intent, observations, report, target_service)

    def _build_llm_prompt(
        self, intent: str, observations: list[dict[str, object]],
        report: str, target_service: str | None,
    ) -> dict[str, str]:
        tool_descs = self._describe_allowed_tools()
        system = (
            "你是运维修复方案规划器。根据诊断结果生成修复候选。\n"
            "只输出 JSON 对象，不输出解释/Markdown/思维过程。\n"
            '格式: {"options": [{"title":"...","description":"...","tool":"...","params":{...},"rollback":"..."}]}\n'
            "规则: 1.只用给定工具 2.不生成高风险操作(rm -rf/mkfs/dd/shutdown/curl|sh等) 3.最多3个候选 4.rollback仅作说明文本\n"
            f"可用工具: {tool_descs}\n"
            "禁止生成 option_id/risk_level/requires_confirm\n"
        )
        obs_summary = []
        for obs in observations:
            if isinstance(obs, dict):
                obs_summary.append({"tool": obs.get("tool"), "ok": obs.get("ok"),
                                    "has_result": obs.get("result") is not None})
        user = json.dumps({
            "intent": intent, "target_service": target_service,
            "observations_summary": obs_summary,
            "report": report[:2000],
        }, ensure_ascii=False, default=str)
        return {"system": system, "user": user}

    def _describe_allowed_tools(self) -> str:
        if self.tool_registry is not None:
            return ", ".join(self.tool_registry.get_tool_names())
        return "sys_info, service_mgr, log_reader, net_monitor, cmd_exec"

    @staticmethod
    def _parse_llm_candidates(raw: str) -> list[dict]:
        text = raw.strip()
        # 严格成对 Markdown fenced code block 解析
        if text.startswith("```"):
            if not text.endswith("```"):
                return []                   # 未闭合 fence，直接拒绝
            inner = text[3:-3].strip()      # 去掉开头和结尾 ```
            if "\n" in inner:
                first_line, _, body = inner.partition("\n")
                if first_line.strip().lower() == "json":
                    text = body.strip()     # 有语言标记 → 使用 body
                else:
                    text = inner            # 无语言标记 → 使用全部
            else:
                text = inner                # 单行 fence 内容
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            return []
        if isinstance(data, dict) and "options" in data:
            opts = data["options"]
            if isinstance(opts, list):
                return [o for o in opts if isinstance(o, dict)]
        return []

    def _build_fix_options(self, candidates: list[dict]) -> list[FixOption]:
        seen: set[tuple[str, str]] = set()
        results: list[FixOption] = []
        _MAX = 3
        for item in candidates:
            if len(results) >= _MAX:
                break
            try:
                candidate = LLMFixCandidate(**item)
            except Exception:
                continue
            if self.tool_registry is None:
                logger.warning("FixPlannerAgent 未注入 ToolRegistry，已拒绝生成可执行修复选项")
                return []
            if not self.tool_registry.exists(candidate.tool):
                continue
            validation = self.tool_registry.validate_params(candidate.tool, candidate.params)
            if not validation["valid"]:
                continue
            if candidate.tool == "cmd_exec":
                cmd = str(candidate.params.get("command", ""))
                if _is_dangerous_cmd(cmd):
                    continue
            action = candidate.params.get("action")
            risk = self.tool_registry.get_risk_for_action(candidate.tool, action) if action else None
            if risk is None:
                risk = self.tool_registry.get_default_risk(candidate.tool)
            if risk is None or risk == "high":
                continue
            params_key = json.dumps(candidate.params, sort_keys=True, default=str)
            if (candidate.tool, params_key) in seen:
                continue
            seen.add((candidate.tool, params_key))
            results.append(FixOption(
                option_id=f"fix_{uuid.uuid4().hex[:8]}",
                title=candidate.title, description=candidate.description,
                risk_level=risk,  # type: ignore[arg-type]
                tool=candidate.tool, params=candidate.params,
                requires_confirm=(risk == "medium"),
                rollback=candidate.rollback,
            ))
        return results


# ── 高危命令检测 ─────────────────────────────────────────────────────

_HIGH_RISK_CMD_PATTERNS = [
    "rm -rf", "rm -r", "mkfs.", "shutdown", "reboot", "halt",
    "passwd", "userdel", "groupdel",
    "iptables -F", "iptables --flush",
    "auditctl -D", "auditctl -e 0",
    "dd if=", "curl ", "| sh", "| bash",
    "bash -c", "sh -c", "python -c",
    "chmod 777", "> /etc/",
]


def _is_dangerous_cmd(command: str) -> bool:
    cmd_lower = command.lower()
    return any(p.lower() in cmd_lower for p in _HIGH_RISK_CMD_PATTERNS)
