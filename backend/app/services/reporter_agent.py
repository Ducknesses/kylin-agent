"""ReporterAgent —— 规则版诊断报告生成（含 LLM 增强路径 + fallback）

职责：
  - 将 observations 转成用户可读的 Markdown 诊断报告
  - 可选接入 LLM 生成自然语言报告（generate_with_llm）
  - 不调用 MCPClient、不调用 AgentHarness、不执行系统命令
  - 工具没有返回的数据不编造
  - MCP 失败时明确说明"无法确认"
  - 输出前做敏感信息过滤
  - LLM 失败/超时/无 key/输出为空时 fallback 到规则版
"""

import json
import logging
import re
from typing import Any

from app.services.llm_client import LLMClient

logger = logging.getLogger(__name__)


# ── 敏感信息过滤 ──────────────────────────────────────────────────────
# 对输出报告做最后清洗，确保敏感信息不泄露

_SENSITIVE_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"sk-[a-zA-Z0-9]{20,}", re.IGNORECASE), "[API_KEY]"),
    (re.compile(r"Bearer\s+[a-zA-Z0-9\-_\.]+", re.IGNORECASE), "Bearer [TOKEN]"),
    (re.compile(r'Authorization:\s*Bearer\s+[^\s"\']+', re.IGNORECASE), "Authorization: Bearer [TOKEN]"),
    (re.compile(r'(?i)deepseek_api_key[=:]\s*[^\s"\']+'), "DEEPSEEK_API_KEY=[FILTERED]"),
    (re.compile(r'(?i)api_key[=:]\s*[^\s"\']+'), "api_key=[FILTERED]"),
    (re.compile(r'(?i)password[=:]\s*[^\s"\']+'), "password=[FILTERED]"),
    (re.compile(r'(?i)secret[=:]\s*[^\s"\']+'), "secret=[FILTERED]"),
    (re.compile(r'(?i)"token"\s*:\s*"[^"]{8,}"'), '"token":"[FILTERED]"'),
]


def sanitize_text(text: str) -> str:
    """过滤文本中的敏感信息

    不改变文本结构，只替换敏感值。
    """
    for pattern, replacement in _SENSITIVE_PATTERNS:
        text = pattern.sub(replacement, text)
    return text


# ── 辅助函数 ──────────────────────────────────────────────────────────

def _safe_get(d: Any, *keys: str, default: Any = None) -> Any:
    """安全地从嵌套字典中取值，任意层为 None 或非 dict 时返回 default"""
    for key in keys:
        if not isinstance(d, dict):
            return default
        d = d.get(key)
        if d is None:
            return default
    return d


def _extract_obs_results(observations: list[dict]) -> list[dict]:
    """从 observations 列表中提取有效的工具调用结果

    每条 observation 结构: {tool, params, ok, result?, error?}
    """
    results = []
    for obs in observations:
        if not isinstance(obs, dict):
            continue
        results.append({
            "tool": obs.get("tool", "unknown"),
            "params": obs.get("params", {}),
            "ok": obs.get("ok", False),
            "result": obs.get("result"),
            "error": obs.get("error"),
        })
    return results


def _truncate(text: str, max_len: int = 200) -> str:
    """截断过长的文本"""
    if len(text) <= max_len:
        return text
    return text[:max_len] + "..."


# ── ReporterAgent ──────────────────────────────────────────────────────

class ReporterAgent:
    """规则版诊断报告生成器

    使用方式：
        agent = ReporterAgent()
        report = agent.generate(intent_result, observations, user_input)
    """

    def generate(
        self,
        intent_result: dict,
        observations: list[dict],
        user_input: str = "",
        knowledge_result: dict | None = None,
    ) -> str:
        """根据意图和观测结果生成诊断报告

        参数:
            intent_result: IntentAgent.detect() 的返回结果
            observations: AgentContext.observations 列表
            user_input: 用户原始输入
            knowledge_result: 可选，知识库匹配结果

        返回:
            Markdown 格式诊断报告字符串
        """
        intent = intent_result.get("intent", "unknown") if isinstance(intent_result, dict) else "unknown"
        obs_list = _extract_obs_results(observations if isinstance(observations, list) else [])

        # unknown intent 优先处理——不能让空 observations 分支覆盖
        if intent == "unknown":
            report = self._report_unknown(user_input)
            return sanitize_text(self._append_knowledge_reference(report, knowledge_result))

        # 防御：observations 为空
        if not obs_list:
            report = self._empty_report(user_input)
            return sanitize_text(self._append_knowledge_reference(report, knowledge_result))

        # 按 intent 分发
        if intent == "cpu_query":
            report = self._report_cpu(obs_list, user_input)
        elif intent == "memory_query":
            report = self._report_memory(obs_list, user_input)
        elif intent == "disk_query":
            report = self._report_disk(obs_list, user_input)
        elif intent == "load_query":
            report = self._report_load(obs_list, user_input)
        elif intent == "network_query":
            report = self._report_network(obs_list, user_input)
        elif intent == "service_status_query":
            report = self._report_service_status(obs_list, user_input, intent_result)
        elif intent == "log_query":
            report = self._report_log(obs_list, user_input)
        elif intent == "root_cause_analysis":
            report = self._report_root_cause(obs_list, user_input, intent_result)
        elif intent == "command_execute":
            report = self._report_command(obs_list, user_input)
        else:
            report = self._report_unknown(user_input)

        return sanitize_text(self._append_knowledge_reference(report, knowledge_result))

    # ── 知识依据 ──────────────────────────────────────────────────────

    @staticmethod
    def _append_knowledge_reference(report: str, knowledge_result: dict | None) -> str:
        """如果知识库有匹配结果，在报告末尾追加「知识依据」章节。"""
        if not knowledge_result or not knowledge_result.get("matched"):
            return report
        items = knowledge_result.get("items", [])
        if not items:
            return report

        lines = ["", "### 知识依据", ""]
        for i, item in enumerate(items, 1):
            title = item.get("title", "未命名")
            item_type = item.get("type", "")
            solution = item.get("solution", "")
            confidence = item.get("confidence", 0.0)
            type_label = {"faq": "常见问题", "fault_pattern": "故障模式", "solution": "解决方案"}.get(item_type, item_type)
            lines.append(f"{i}. **[{type_label}] {title}**（匹配度: {confidence:.0%}）")
            if solution:
                lines.append(f"   {solution}")
            lines.append("")
        return report.rstrip() + "\n" + "\n".join(lines)

    # ── 空报告 ────────────────────────────────────────────────────────

    @staticmethod
    def _empty_report(user_input: str = "") -> str:
        query = f"「{user_input}」" if user_input else "当前请求"
        return (
            f"## 诊断报告\n\n"
            f"### 现象\n{query} 尚无可用观测结果。\n\n"
            f"### 证据\n当前没有可用的工具观测结果，无法确认系统状态。\n\n"
            f"### 判断\n无法做出有效判断。\n\n"
            f"### 建议\n1. 确认 MCP Server 是否正常运行\n2. 确认工具调用权限是否正确配置\n"
        )

    @staticmethod
    def _report_unknown(user_input: str = "") -> str:
        query = f"「{user_input}」" if user_input else "当前请求"
        return (
            f"## 诊断报告\n\n"
            f"### 现象\n{query} 无法识别明确的运维意图。\n\n"
            f"### 证据\n暂无匹配的观测数据。\n\n"
            f"### 判断\n无法确定具体运维目标，建议提供更具体的信息。\n\n"
            f"### 建议\n1. 提供具体的服务名称（例如 nginx、redis）\n"
            f"2. 说明要查询的指标（CPU、内存、磁盘、日志等）\n3. 描述具体运维场景\n"
        )

    # ── CPU 报告 ───────────────────────────────────────────────────────

    def _report_cpu(self, obs_list: list[dict], user_input: str) -> str:
        obs = obs_list[0]
        result = obs.get("result", {}) if obs.get("ok") else {}
        if not obs.get("ok"):
            return self._failed_report("CPU 使用率", obs.get("error", "未知错误"))

        cpu = _safe_get(result, "cpu", default={})
        cpu_pct = _safe_get(cpu, "cpu_percent_snapshot") or _safe_get(result, "cpu_percent")
        cores = _safe_get(cpu, "cpu_count") or _safe_get(result, "cores")
        load_avg = _safe_get(cpu, "load_avg") or _safe_get(result, "load_avg")

        lines = [
            "## 诊断报告",
            "",
            "### 现象",
            f"查询 {user_input or 'CPU 使用率'}。",
            "",
            "### 证据",
        ]
        if cpu_pct is not None:
            lines.append(f"- CPU 使用率：{cpu_pct}%")
        if cores is not None:
            lines.append(f"- CPU 核心数：{cores}")
        if load_avg:
            avg_str = ", ".join(str(round(v, 2)) for v in load_avg) if isinstance(load_avg, list) else str(load_avg)
            lines.append(f"- 系统负载均值：{avg_str}")
        if cpu_pct is None and cores is None:
            lines.append("- CPU 数据格式不完整，无法提取具体数值")

        lines.extend([
            "",
            "### 判断",
            self._cpu_judgment(cpu_pct),
            "",
            "### 建议",
            "1. 如使用率持续偏高，考虑扩容或优化应用",
            "2. 使用 `top` 或 `htop` 查看占用最高的进程",
            "3. 可进一步查询系统负载和内存使用情况",
        ])
        return "\n".join(lines)

    @staticmethod
    def _cpu_judgment(cpu_pct: float | None) -> str:
        if cpu_pct is None:
            return "当前 CPU 数据不可用，无法评估负载水平。"
        if cpu_pct < 50:
            return f"当前 CPU 使用率为 {cpu_pct}%，处于正常范围。"
        if cpu_pct < 80:
            return f"当前 CPU 使用率为 {cpu_pct}%，负载偏高，建议关注。"
        return f"当前 CPU 使用率为 {cpu_pct}%，处于高负载状态，需要尽快排查。"

    # ── 内存报告 ──────────────────────────────────────────────────────

    def _report_memory(self, obs_list: list[dict], user_input: str) -> str:
        obs = obs_list[0]
        if not obs.get("ok"):
            return self._failed_report("内存使用情况", obs.get("error", "未知错误"))

        result = obs.get("result", {})
        mem = _safe_get(result, "memory", default={})
        pct = _safe_get(mem, "percent") or _safe_get(result, "percent")
        total_gb = _safe_get(mem, "total_gb") or _safe_get(result, "total_gb")
        used_gb = _safe_get(mem, "used_gb") or _safe_get(result, "used_gb")

        lines = [
            "## 诊断报告",
            "",
            "### 现象",
            f"查询 {user_input or '内存使用情况'}。",
            "",
            "### 证据",
        ]
        if pct is not None:
            lines.append(f"- 内存使用率：{pct}%")
            if total_gb is not None:
                lines.append(f"  - 总内存：{total_gb} GB")
            if used_gb is not None:
                lines.append(f"  - 已用内存：{used_gb} GB")
        if pct is None:
            lines.append("- 内存数据不完整，无法提取具体数值")

        judgment = "内存数据不可用。" if pct is None else (
            f"内存使用率为 {pct}%，{'偏高，建议关注' if pct > 80 else '处于正常范围'}。"
        )
        lines.extend([
            "",
            "### 判断",
            judgment,
            "",
            "### 建议",
            "1. 使用 `free -m` 查看详细内存分配",
            "2. 检查是否有内存泄漏的进程",
            "3. 如内存持续偏高，考虑增加物理内存或优化应用",
        ])
        return "\n".join(lines)

    # ── 磁盘报告 ──────────────────────────────────────────────────────

    def _report_disk(self, obs_list: list[dict], user_input: str) -> str:
        obs = obs_list[0]
        if not obs.get("ok"):
            return self._failed_report("磁盘使用情况", obs.get("error", "未知错误"))

        result = obs.get("result", {})
        disk_list = _safe_get(result, "disk", default=[])
        pct = _safe_get(result, "percent")
        total_gb = _safe_get(result, "total_gb")
        used_gb = _safe_get(result, "used_gb")

        # disk 可能是列表（mock 结构）
        if isinstance(disk_list, list) and disk_list:
            disk_item = disk_list[0] if isinstance(disk_list[0], dict) else {}
            pct = pct or _safe_get(disk_item, "percent")

        lines = [
            "## 诊断报告",
            "",
            "### 现象",
            f"查询 {user_input or '磁盘使用情况'}。",
            "",
            "### 证据",
        ]
        if pct is not None:
            lines.append(f"- 磁盘使用率：{pct}%")
        if total_gb is not None:
            lines.append(f"  - 总容量：{total_gb} GB")
        if used_gb is not None:
            lines.append(f"  - 已用：{used_gb} GB")
        if pct is None:
            lines.append("- 磁盘数据不完整，无法提取具体数值")

        judgment = "磁盘数据不可用。" if pct is None else (
            f"磁盘使用率为 {pct}%，{'偏高，需要关注' if pct > 85 else '处于正常范围'}。"
        )
        lines.extend([
            "",
            "### 判断",
            judgment,
            "",
            "### 建议",
            "1. 使用 `df -h` 查看各分区详细使用情况",
            "2. 清理旧日志和临时文件",
            "3. 如需扩容，提前规划磁盘空间",
        ])
        return "\n".join(lines)

    # ── 负载报告 ──────────────────────────────────────────────────────

    def _report_load(self, obs_list: list[dict], user_input: str) -> str:
        obs = obs_list[0]
        if not obs.get("ok"):
            return self._failed_report("系统负载", obs.get("error", "未知错误"))

        result = obs.get("result", {})
        load = _safe_get(result, "load", default={})
        load_avg = _safe_get(load, "load_avg") or _safe_get(result, "load_avg")
        uptime_sec = _safe_get(_safe_get(result, "uptime", default={}), "uptime_seconds")

        lines = [
            "## 诊断报告",
            "",
            "### 现象",
            f"查询 {user_input or '系统负载'}。",
            "",
            "### 证据",
        ]
        if load_avg:
            if isinstance(load_avg, list):
                lines.append(f"- 系统负载（1/5/15 分钟）：{', '.join(str(round(v, 2)) for v in load_avg)}")
            else:
                lines.append(f"- 系统负载：{load_avg}")
        if uptime_sec is not None:
            days = int(uptime_sec) // 86400
            hours = (int(uptime_sec) % 86400) // 3600
            lines.append(f"- 运行时间：{days} 天 {hours} 小时")
        if not load_avg and uptime_sec is None:
            lines.append("- 负载数据不完整，无法提取具体数值")

        lines.extend([
            "",
            "### 判断",
            "负载数据已获取，请结合 CPU 使用率和进程列表综合评估。" if load_avg else "负载数据不可用。",
            "",
            "### 建议",
            "1. 如负载持续偏高，考虑扩容或优化应用",
            "2. 使用 `ps aux --sort=-%cpu` 查找高 CPU 进程",
        ])
        return "\n".join(lines)

    # ── 网络报告 ──────────────────────────────────────────────────────

    def _report_network(self, obs_list: list[dict], user_input: str) -> str:
        obs = obs_list[0]
        if not obs.get("ok"):
            return self._failed_report("网络状态", obs.get("error", "未知错误"))

        result = obs.get("result", {})
        metric = obs.get("params", {}).get("metric", "all")
        lines = [
            "## 诊断报告",
            "",
            "### 现象",
            f"查询 {user_input or '网络状态'}（指标：{metric}）。",
            "",
            "### 证据",
        ]

        # connection 列表
        connections = _safe_get(result, "connections", default=[])
        if isinstance(connections, list) and connections:
            cnt = len(connections)
            lines.append(f"- 活跃连接数：{cnt}")
            for conn in connections[:3]:
                proto = conn.get("proto", "?")
                local = conn.get("local", conn.get("local_address", "?"))
                state = conn.get("state", "")
                lines.append(f"  - {proto} {local} {state}")

        # interfaces
        interfaces = _safe_get(result, "interfaces", default=[])
        if isinstance(interfaces, list) and interfaces:
            lines.append(f"- 网络接口数：{len(interfaces)}")

        # traffic
        traffic = _safe_get(result, "traffic", default={})
        if traffic:
            rx = _safe_get(traffic, "bytes_recv")
            tx = _safe_get(traffic, "bytes_sent")
            if rx is not None:
                lines.append(f"- 接收流量：{rx} bytes")
            if tx is not None:
                lines.append(f"- 发送流量：{tx} bytes")

        # rx_kbps / tx_kbps（扁平结构）
        rx_kbps = _safe_get(result, "rx_kbps")
        tx_kbps = _safe_get(result, "tx_kbps")
        if rx_kbps is not None:
            lines.append(f"- 入站速率：{rx_kbps} kbps")
        if tx_kbps is not None:
            lines.append(f"- 出站速率：{tx_kbps} kbps")

        if not connections and not interfaces and not traffic and rx_kbps is None:
            lines.append("- 网络数据不完整，无法提取具体统计")

        lines.extend([
            "",
            "### 判断",
            "网络状态数据已获取，请结合具体指标分析。" if connections or traffic else "网络数据不可用。",
            "",
            "### 建议",
            "1. 如发现异常连接，使用 `ss -tlnp` 查看详情",
            "2. 检查防火墙规则是否正常",
            "3. 关注异常流量模式",
        ])
        return "\n".join(lines)

    # ── 服务状态报告 ──────────────────────────────────────────────────

    def _report_service_status(self, obs_list: list[dict], user_input: str, intent_result: dict) -> str:
        obs = obs_list[0]
        svc = intent_result.get("target_service", "?") if isinstance(intent_result, dict) else "?"
        if not obs.get("ok"):
            return self._failed_report(f"{svc} 服务状态", obs.get("error", "未知错误"))

        result = obs.get("result", {})
        is_active = result.get("is_active")
        active_state = _safe_get(result, "parsed", "active_state") or result.get("active_state")
        exit_code = result.get("exit_code")
        output = result.get("output", "")
        service_name = result.get("service", svc)

        lines = [
            "## 诊断报告",
            "",
            "### 现象",
            f"查询 {service_name} 服务状态。",
            "",
            "### 证据",
        ]
        lines.append(f"- 服务名称：{service_name}")
        if is_active is not None:
            lines.append(f"- 运行状态：{'运行中' if is_active else '未运行'}")
        if active_state:
            lines.append(f"- 详细状态：{active_state}")
        if exit_code is not None:
            lines.append(f"- 退出码：{exit_code}")
        if output:
            lines.append(f"- 输出摘要：{_truncate(str(output), 200)}")

        if is_active:
            judgment = f"{service_name} 服务当前正常运行。"
        elif is_active is False:
            judgment = f"{service_name} 服务当前未运行，可能是异常的主要原因。"
        else:
            judgment = f"{service_name} 服务状态无法确认。"
        lines.extend([
            "",
            "### 判断",
            judgment,
            "",
            "### 建议",
            f"1. 如需重启 {service_name}，请通过安全确认流程操作",
            "2. 使用 `journalctl -u " + service_name.replace('.service', '') + " -n 50` 查看最近日志",
            "3. 检查服务配置文件是否正确",
        ])
        return "\n".join(lines)

    # ── 日志报告 ──────────────────────────────────────────────────────

    def _report_log(self, obs_list: list[dict], user_input: str) -> str:
        obs = obs_list[0]
        if not obs.get("ok"):
            return self._failed_report("日志查询", obs.get("error", "未知错误"))

        result = obs.get("result", {})
        logs = result.get("logs", [])
        service = obs.get("params", {}).get("service", result.get("source", result.get("service", "?")))
        lines_count = result.get("lines", len(logs) if isinstance(logs, list) else 0)

        key_logs = self._extract_key_logs(logs) if isinstance(logs, list) else []

        lines = [
            "## 诊断报告",
            "",
            "### 现象",
            f"查询 {service} 日志{'：' + user_input if user_input else ''}",
            "",
            "### 证据",
            f"- 日志来源：{service}",
            f"- 返回行数：{lines_count}",
        ]

        if key_logs:
            lines.append("- 关键日志条目：")
            for log in key_logs[:3]:
                lines.append(f"  - {_truncate(str(log), 150)}")
        elif logs:
            lines.append("- 未发现明显的错误或告警日志")
        else:
            lines.append("- 未返回日志条目")

        lines.extend([
            "",
            "### 判断",
            self._log_judgment(key_logs),
            "",
            "### 建议",
            "1. 如发现错误，重点关注时间点和频率",
            "2. 扩大日志查询范围（增加 lines 或调整时间范围）",
            "3. 结合服务状态和系统资源综合分析",
        ])
        return "\n".join(lines)

    @staticmethod
    def _extract_key_logs(logs: list) -> list[str]:
        """提取关键日志（优先 error/warn/failed/timeout/502/refused/denied）"""
        keywords = ["error", "warn", "failed", "timeout", "502", "refused", "denied", "critical", "fatal"]
        key = [l for l in logs if isinstance(l, str) and any(k in l.lower() for k in keywords)]
        return key

    @staticmethod
    def _log_judgment(key_logs: list[str]) -> str:
        if not key_logs:
            return "未发现明显的错误或异常日志。"
        return f"发现 {len(key_logs)} 条关键日志，可能存在服务异常或性能问题。"

    # ── 根因分析报告（重点场景） ──────────────────────────────────────

    def _report_root_cause(self, obs_list: list[dict], user_input: str, intent_result: dict) -> str:
        svc = intent_result.get("target_service", "服务") if isinstance(intent_result, dict) else "服务"

        # 按工具名分类
        log_obs = next((o for o in obs_list if o["tool"] == "log_reader"), None)
        mem_obs = next((o for o in obs_list if o["tool"] == "sys_info"), None)
        svc_obs = next((o for o in obs_list if o["tool"] == "service_mgr"), None)

        log_ok = log_obs["ok"] if log_obs else False
        mem_ok = mem_obs["ok"] if mem_obs else False
        svc_ok = svc_obs["ok"] if svc_obs else False

        lines = [
            "## 诊断报告",
            "",
            "### 现象",
            f"正在分析 {svc} 服务异常原因。{'用户描述：' + user_input if user_input else ''}",
            "",
            "### 证据",
            "",
        ]

        # ── 日志证据 ──
        lines.append("**日志证据：**")
        if log_obs and log_ok:
            log_result = log_obs.get("result", {})
            logs = log_result.get("logs", [])
            key_logs = self._extract_key_logs(logs) if isinstance(logs, list) else []
            if key_logs:
                for log in key_logs[:3]:
                    lines.append(f"- {_truncate(str(log), 150)}")
            else:
                lines.append("- 日志中未发现明显错误")
        else:
            error_msg = log_obs.get("error", "无日志数据") if log_obs else "未执行日志查询"
            lines.append(f"- 无法确认：{error_msg}")

        # ── 内存证据 ──
        lines.append("")
        lines.append("**内存证据：**")
        if mem_obs and mem_ok:
            mem_result = mem_obs.get("result", {})
            mem = _safe_get(mem_result, "memory", default={})
            mem_pct = _safe_get(mem, "percent") or _safe_get(mem_result, "percent")
            if mem_pct is not None:
                lines.append(f"- 内存使用率：{mem_pct}%")
            else:
                lines.append("- 内存数据不完整")
        else:
            lines.append("- 无法确认：内存查询失败或未执行")

        # ── 服务状态证据 ──
        lines.append("")
        lines.append("**服务状态证据：**")
        if svc_obs and svc_ok:
            svc_result = svc_obs.get("result", {})
            is_active = svc_result.get("is_active")
            active_state = _safe_get(svc_result, "parsed", "active_state")
            if is_active is not None:
                lines.append(f"- {svc} 运行状态：{'运行中' if is_active else '未运行'}")
            if active_state:
                lines.append(f"- 详细状态：{active_state}")
        else:
            error_msg = svc_obs.get("error", "未执行服务状态查询") if svc_obs else "未执行服务状态查询"
            lines.append(f"- 无法确认：{error_msg}")

        # ── 综合判断 ──
        lines.extend(["", "### 判断", ""])
        judgments = self._root_cause_judgments(log_obs, log_ok, mem_obs, mem_ok, svc_obs, svc_ok, svc)
        lines.extend(judgments)

        # ── 建议 ──
        lines.extend([
            "",
            "### 建议",
            "1. 查看更近时间范围的日志（缩小 since 参数）",
            "2. 检查服务配置文件是否正确",
            f"3. 如需重启 {svc}，必须通过安全确认流程操作",
            "4. 检查内存占用较高的进程（`ps aux --sort=-%mem`）",
            "5. 如频繁出现，考虑增加资源或优化应用",
        ])
        return "\n".join(lines)

    def _root_cause_judgments(
        self,
        log_obs: dict | None, log_ok: bool,
        mem_obs: dict | None, mem_ok: bool,
        svc_obs: dict | None, svc_ok: bool,
        svc: str,
    ) -> list[str]:
        """生成根因分析的判断列表"""
        judgments = []

        # 服务状态判断
        if svc_obs and svc_ok:
            svc_result = svc_obs.get("result", {})
            is_active = svc_result.get("is_active")
            if is_active is False:
                judgments.append(f"- {svc} 服务状态异常（未运行），这是最可能的主要原因。")
        elif svc_obs and not svc_ok:
            judgments.append("- 服务状态查询失败，该项无法确认。")

        # 日志判断
        if log_obs and log_ok:
            log_result = log_obs.get("result", {})
            logs = log_result.get("logs", [])
            key_logs = self._extract_key_logs(logs) if isinstance(logs, list) else []
            if key_logs:
                judgments.append("- 日志中存在错误或超时记录，可能与后端响应慢或连接异常有关。")
        elif log_obs and not log_ok:
            judgments.append("- 日志查询失败，该项无法确认。")

        # 内存判断
        if mem_obs and mem_ok:
            mem_result = mem_obs.get("result", {})
            mem = _safe_get(mem_result, "memory", default={})
            mem_pct = _safe_get(mem, "percent") or _safe_get(mem_result, "percent")
            if mem_pct is not None and mem_pct > 80:
                judgments.append(f"- 内存使用率偏高（{mem_pct}%），可能存在资源压力。")
        elif mem_obs and not mem_ok:
            judgments.append("- 内存查询失败，该项无法确认。")

        if not judgments:
            judgments.append("- 当前证据不足以确定根因，建议扩大诊断范围。")

        return judgments

    # ── 命令执行报告 ──────────────────────────────────────────────────

    def _report_command(self, obs_list: list[dict], user_input: str) -> str:
        obs = obs_list[0]

        # blocked 结果
        result = obs.get("result", {})
        if isinstance(result, dict) and result.get("blocked"):
            return (
                f"## 诊断报告\n\n"
                f"### 现象\n命令 `{user_input or obs.get('params', {}).get('command', '?')}` 已被安全策略拦截。\n\n"
                f"### 证据\n- 命令被拦截：{result.get('reason', '安全策略拒绝')}\n\n"
                f"### 判断\n该命令不在允许的执行范围内，未执行。\n\n"
                f"### 建议\n1. 确认命令是否为合法运维操作\n2. 如需执行，联系管理员评估风险\n"
            )

        if not obs.get("ok"):
            cmd = obs.get("params", {}).get("command", user_input or "?")
            return self._failed_report(f"命令执行：{cmd}", obs.get("error", "未知错误"))

        result = obs.get("result", {})
        stdout = result.get("stdout", result.get("output", ""))
        stderr = result.get("stderr", "")
        exit_code = result.get("returncode", result.get("exit_code"))

        lines = [
            "## 诊断报告",
            "",
            "### 现象",
            f"执行命令：`{obs.get('params', {}).get('command', user_input)}`",
            "",
            "### 证据",
        ]
        if stdout:
            lines.append(f"- 标准输出：{_truncate(str(stdout), 300)}")
        if stderr:
            lines.append(f"- 标准错误：{_truncate(str(stderr), 200)}")
        if exit_code is not None:
            lines.append(f"- 退出码：{exit_code}")

        success = exit_code == 0 if exit_code is not None else True
        lines.extend([
            "",
            "### 判断",
            "命令执行成功。" if success else f"命令执行异常（退出码：{exit_code}）。",
            "",
            "### 建议",
            "1. 确认命令输出是否符合预期",
            "2. 如有异常退出码，检查命令语法和参数",
        ])
        return "\n".join(lines)

    # ── 通用失败报告 ──────────────────────────────────────────────────

    @staticmethod
    def _failed_report(context: str, error: str = "未知错误") -> str:
        return (
            f"## 诊断报告\n\n"
            f"### 现象\n{context} 查询未完成。\n\n"
            f"### 证据\n- 工具调用失败：{error}\n- 无法获取有效数据\n\n"
            f"### 判断\n该项无法确认，工具调用失败。\n\n"
            f"### 建议\n1. 检查 MCP Server 是否正常运行\n2. 确认工具参数是否正确\n3. 稍后重试\n"
        )

    # ── LLM 增强路径 ──────────────────────────────────────────────────

    async def generate_with_llm(
        self,
        intent_result: dict,
        observations: list[dict],
        user_input: str = "",
        knowledge_result: dict | None = None,
    ) -> str:
        """使用 LLM 生成诊断报告，失败时 fallback 到规则版 generate()

        LLM 只根据已验证的工具结果生成报告：
          - 不能编造工具没有返回的数据
          - 不能输出执行命令
          - 不能输出未经验证的系统状态
          - 不能输出敏感信息
          - 输出为空时 fallback

        返回:
            Markdown 格式诊断报告字符串（已脱敏）
        """
        from config import settings
        if not settings.LLM_ENABLED:
            logger.debug("[ReporterAgent] LLM 未启用，使用规则版")
            return self.generate(intent_result, observations, user_input, knowledge_result)

        obs_summary = self._summarize_observations(observations)
        if not obs_summary:
            logger.debug("[ReporterAgent] 无可用的 observations，使用规则版")
            return self.generate(intent_result, observations, user_input, knowledge_result)

        try:
            client = LLMClient()

            intent_name = intent_result.get("intent", "unknown") if isinstance(intent_result, dict) else "unknown"
            target_service = intent_result.get("target_service", "") if isinstance(intent_result, dict) else ""

            system_prompt = (
                "你是一个系统运维诊断报告生成器。\n"
                "只基于提供的观测数据生成报告，不得编造任何未在数据中出现的信息。\n"
                "输出格式固定为 Markdown，包含四级标题：\n"
                "## 诊断报告\n"
                "### 现象\n（描述用户问题和观察到的现象）\n"
                "### 证据\n（列出所有已验证的工具观测结果，只包含数据中实际存在的值）\n"
                "### 判断\n（基于证据的专业判断，不可编造）\n"
                "### 建议\n（具体可操作的建议，不含危险命令）\n\n"
                "重要规则：\n"
                "- 只基于提供的观测数据，不可编造。\n"
                "- 不可输出 rm、mkfs、chmod 777、dd、curl pipe 等危险命令。\n"
                "- 不可输出 API Key、密码、token 等敏感信息。\n"
                "- 不可输出未在数据中出现过的数值（CPU%、内存% 等）。\n"
                "- 数据不足时明确说明「无法确认」，不要猜测。"
            )
            user_prompt = (
                f"意图：{intent_name}\n"
                f"目标服务：{target_service or '无'}\n"
                f"用户输入：{user_input}\n"
                f"观测数据：{obs_summary}\n"
            )

            resp = await client.chat_simple(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
            )

            if not resp.ok:
                logger.info(f"[ReporterAgent] LLM 调用失败，fallback 规则版: {resp.error}")
                return self.generate(intent_result, observations, user_input, knowledge_result)

            content = resp.content.strip()
            if not content:
                logger.warning("[ReporterAgent] LLM 返回空内容，fallback 规则版")
                return self.generate(intent_result, observations, user_input, knowledge_result)

            if self._contains_dangerous_content(content):
                logger.warning("[ReporterAgent] LLM 输出包含危险内容，fallback 规则版")
                return self.generate(intent_result, observations, user_input, knowledge_result)

            return sanitize_text(self._append_knowledge_reference(content, knowledge_result))

        except Exception as e:
            logger.warning(f"[ReporterAgent] LLM 路径异常，fallback 规则版: {e}")
            return self.generate(intent_result, observations, user_input, knowledge_result)

    @staticmethod
    def _summarize_observations(observations: list[dict]) -> str:
        """将 observations 列表摘要为 LLM 可读的文本"""
        if not isinstance(observations, list) or not observations:
            return ""

        parts: list[str] = []
        for i, obs in enumerate(observations):
            if not isinstance(obs, dict):
                continue
            tool = obs.get("tool", f"unknown_{i}")
            ok = obs.get("ok", False)
            if not ok:
                parts.append(f"[{tool}] 调用失败: {obs.get('error', '未知错误')}")
                continue
            result = obs.get("result", {})
            if result:
                result_str = json.dumps(result, ensure_ascii=False, default=str)
                if len(result_str) > 800:
                    result_str = result_str[:800] + "..."
                parts.append(f"[{tool}] {result_str}")
            else:
                parts.append(f"[{tool}] 无返回数据")

        return "\n".join(parts)

    @staticmethod
    def _contains_dangerous_content(text: str) -> bool:
        """检查 LLM 输出是否包含危险命令或模式"""
        dangerous_patterns = [
            r"\brm\s+-rf\b",
            r"\bmkfs\.\w+",
            r"\bchmod\s+777\b",
            r"\bdd\s+if=.*of=/dev/",
            r"curl\b.*\|.*\b(bash|sh)\b",
            r"wget\b.*\|.*\b(bash|sh)\b",
        ]
        for pattern in dangerous_patterns:
            if re.search(pattern, text, re.IGNORECASE):
                return True
        return False
