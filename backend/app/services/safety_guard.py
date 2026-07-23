"""SafetyGuard —— 用户输入安全检查统一入口

职责：
  - 统一调用风险分级、Prompt 注入检测、高危模式匹配
  - 返回标准结构 {allowed, risk_level, reason, requires_confirm}
  - 不执行系统命令、不调用 LLM、不调用 MCP

规则数据全部集中在 app.core.security_rules，本模块只保留判定逻辑。

返回契约（analyze_* 与所有 _check_* 都必须经 _result/_allow/_deny/_confirm
返回完整四字段 dict，不允许返回缺字段的半成品）：
  - allowed=True   → 放行
  - allowed=False  → 策略硬拒绝（任何角色都不放行）
  - allowed=None   → 待 RBAC 角色裁决（仅 medium，
                     由 analyze_tool_call 统一补全为 True/False）
  analyze_user_input / analyze_tool_call 对外返回时 allowed 必为 bool。
"""
import logging
from typing import Any

from app.core.prompt_guard import detect_injection
from app.core.security import risk_classify
from app.core.security_rules import (
    AUDIT_BYPASS_KEYWORDS,
    CMD_EXEC_RULES,
    EXTRA_HIGH_RISK_PATTERNS,
    INJECTION_CHARS_PATTERN,
    MEDIUM_NGINX_PATTERN,
    ROLE_CAN_MEDIUM,
    SENSITIVE_EXTENSIONS,
    SENSITIVE_PATHS,
    VALID_ROLES,
)

logger = logging.getLogger(__name__)


class SafetyGuard:
    """用户输入安全检查统一入口"""

    # ── 用户输入检查 ─────────────────────────────────────────────────

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
            return self._deny("low", "输入不能为空")

        # 1. 归一化后做补充高危模式匹配
        normalized = stripped.lower()
        for pattern, reason in EXTRA_HIGH_RISK_PATTERNS:
            if pattern.search(normalized):
                logger.warning(f"[SafetyGuard] 补充高危拦截: {reason}")
                return self._deny("high", reason)

        # 2. 审计绕过 / 安全关闭关键词检测（直接判定为高危）
        for kw in AUDIT_BYPASS_KEYWORDS:
            if kw.lower() in normalized:
                logger.warning(f"[SafetyGuard] 审计绕过检测: {kw}")
                return self._deny("high", "检测到试图绕过安全审计的输入")

        # 3. Prompt Injection 检测（优先于中风险，防止「忽略规则 + restart nginx」被误判为 medium）
        injection = detect_injection(stripped)
        if injection["detected"]:
            logger.warning(f"[SafetyGuard] Prompt Injection: {injection['reason']}")
            return self._deny("high", f"输入安全检测未通过: {injection['reason']}")

        # 4. 中风险 nginx 服务操作检测（Prompt 注入已排除，仅正常运维操作到此）
        if MEDIUM_NGINX_PATTERN.search(stripped):
            logger.info(f"[SafetyGuard] 中风险服务操作: {stripped[:60]}")
            return self._result(
                allowed=True,
                risk_level="medium",
                reason=f"该操作涉及服务变更，需要确认: {stripped[:50]}",
                requires_confirm=True,
            )

        # 5. 调已有 risk_classify 做完整风险分级（跳过注入检测，前面已做过）
        risk = risk_classify(stripped, skip_injection_check=True)

        # 高危：不允许
        if risk["action"] == "reject":
            logger.warning(f"[SafetyGuard] 高危拦截: {risk['reason']}")
            return self._deny(risk["level"], risk["reason"])

        # 中危：需二次确认
        if risk["action"] == "confirm":
            logger.info(f"[SafetyGuard] 中危需确认: {risk['reason']}")
            return self._result(
                allowed=True,
                risk_level=risk["level"],
                reason=risk["reason"],
                requires_confirm=True,
            )

        # 低危：直接放行
        return self._allow("low", "未发现高危输入")

    # ── 工具调用检查 ─────────────────────────────────────────────────

    def analyze_tool_call(
        self, tool: str, params: dict, role: str = "viewer"
    ) -> dict[str, Any]:
        """对 MCP 工具调用进行安全裁决

        参数:
            tool: 工具名（sys_info / log_reader / service_mgr / cmd_exec / file_guard 等）
            params: 工具参数
            role: 用户角色（viewer / operator / admin），未知按 viewer 处理

        返回:
            {allowed, risk_level, reason, requires_confirm}
            对外返回时 allowed 必为 bool；_check_* 返回的 allowed=None
            （待 RBAC 角色裁决）在此统一补全。
        """
        # 规范化 role，未知角色按 viewer
        role = role.lower() if role else "viewer"
        if role not in VALID_ROLES:
            role = "viewer"

        # 分发到具体工具检查
        if tool == "sys_info":
            result = self._check_sys_info(params)
        elif tool == "log_reader":
            result = self._check_log_reader(params)
        elif tool == "service_mgr":
            result = self._check_service_mgr(params)
        elif tool == "cmd_exec":
            result = self._check_cmd_exec(params)
        elif tool == "file_guard":
            result = self._check_file_guard(params, role)
        elif tool == "net_monitor":
            result = self._check_net_monitor(params)
        elif tool == "metrics_history":
            result = self._check_metrics_history(params)
        else:
            return self._deny("medium", "未知工具")

        # 应用 RBAC：high 永远拒绝
        if result["risk_level"] == "high":
            result["allowed"] = False
            result["requires_confirm"] = False
            return result

        # medium：策略硬拒绝（allowed=False）不因角色放行；
        # 待裁决（allowed=None）由角色补全最终判定
        if result["risk_level"] == "medium":
            if result["allowed"] is False:
                return result
            if role in ROLE_CAN_MEDIUM:
                result["allowed"] = True
                result["requires_confirm"] = True
                return result
            return self._deny(
                "medium",
                f"当前角色 ({role}) 无权执行中风险操作",
            )

        # low：直接放行
        result["allowed"] = True
        result["requires_confirm"] = False
        return result

    # ── 各工具检查逻辑 ──────────────────────────────────────────────

    def _check_sys_info(self, params: dict) -> dict[str, Any]:
        """sys_info 工具检查：仅允许合法 metric"""
        valid_metrics = {
            "cpu", "memory", "disk", "load",
            "network", "uptime", "all",
        }
        metric = params.get("metric", "all")
        if metric not in valid_metrics:
            return self._deny("medium", f"非法的 sys_info metric: {metric}")
        return self._allow("low", "sys_info 只读查询")

    def _check_log_reader(self, params: dict) -> dict[str, Any]:
        """log_reader 工具检查：限制路径、行数、防注入"""
        service = params.get("service", "")
        lines = params.get("lines", 50)
        # source / path / log_file 统一检查
        file_path = params.get("source") or params.get("path") or params.get("log_file") or ""

        # service 名中的命令注入检测
        if service and INJECTION_CHARS_PATTERN.search(service):
            return self._deny("high", "service 名称包含命令注入字符")

        # 路径在 /var/log 之外
        if file_path and not file_path.startswith("/var/log/"):
            return self._deny("high", f"日志路径超出允许范围: {file_path}")

        # 行数校验：1~500 合法，<=0 或 >500 或非数字均拒绝
        try:
            lines_int = int(str(lines))
        except (ValueError, TypeError):
            return self._deny("medium", "lines 参数格式非法")
        if lines_int < 1 or lines_int > 500:
            return self._deny("medium", f"请求日志行数不合法: {lines_int}")

        return self._allow("low", "log_reader 只读日志查询")

    def _check_service_mgr(self, params: dict) -> dict[str, Any]:
        """service_mgr 工具检查：按 action + 服务名分级"""
        action = (params.get("action") or "").strip().lower()
        service_name = (params.get("service") or params.get("name") or "").strip()

        # 服务名为空或含注入字符
        if not service_name or INJECTION_CHARS_PATTERN.search(service_name):
            return self._deny("high", "服务名称为空或包含命令注入字符")

        svc_lower = service_name.lower()

        # 高风险服务名（核心守护进程）
        high_risk_services = {
            "systemd", "systemd-logind", "systemd-journald",
            "auditd", "mcp-server", "network", "networkmanager",
            "dbus", "dbus-daemon", "polkit",
        }
        is_high_svc = svc_lower in high_risk_services

        # 只读 action
        read_actions = {"status", "is_active", "is-active", "is_enabled", "is-enabled"}
        # 变更 action（需确认）
        medium_actions = {"start", "stop", "restart", "reload"}

        # 高风险服务 + 变更/禁用 → high
        if is_high_svc and action in (medium_actions | {"disable"}):
            return self._deny("high", f"禁止对核心服务执行 {action}: {service_name}")

        # 只读 action → low（含高风险服务的只读查询）
        if action in read_actions:
            return self._allow("low", f"service_mgr 只读查询: {action} {service_name}")

        # 变更 action → medium（待 RBAC 角色裁决）
        if action in medium_actions:
            return self._confirm("medium", f"中风险服务操作: {action} {service_name}")

        # disable（非高风险服务）→ medium 拒绝
        if action == "disable":
            return self._deny("medium", f"禁止执行 disable 操作: {service_name}")

        # 非法 action
        return self._deny("medium", f"非法的 service_mgr action: {action}")

    def _check_cmd_exec(self, params: dict) -> dict[str, Any]:
        """cmd_exec 工具检查：高危拦截 → 白名单单表匹配（exact/prefix）→ 兜底拒绝"""
        command = (params.get("command") or "").strip()
        if not command:
            return self._deny("high", "命令为空")

        # 1. 高危检测（shell 注入、破坏性命令、安全绕过等）
        risk = self.analyze_user_input(command)
        if risk["risk_level"] == "high":
            return self._deny("high", f"高危命令: {risk['reason']}")

        normalized_cmd = command.lower().strip()

        # 2. 白名单单表匹配（表内顺序：exact low → exact medium → prefix low）
        for mode, pattern, level in CMD_EXEC_RULES:
            if mode == "exact":
                if normalized_cmd != pattern:
                    continue
                if level == "medium":
                    return self._confirm("medium", f"中危白名单命令: {command}")
                return self._allow("low", f"白名单命令: {command}")
            # prefix：只读诊断命令前缀，由 sandbox 做最终路径/字符校验
            if normalized_cmd.startswith(pattern) or normalized_cmd == pattern:
                return self._allow("low", f"只读诊断命令（sandbox 最终裁决）: {command[:60]}")

        # 3. 其余命令拒绝
        return self._deny("medium", f"命令不在白名单中: {command[:60]}")

    def _check_file_guard(self, params: dict, role: str) -> dict[str, Any]:
        """file_guard 工具检查：按 action + path + 后缀分级"""
        action = (params.get("action") or "").strip()
        path = (params.get("path") or "").strip()

        # 路径为空、路径穿越、命令注入
        if not path:
            return self._deny("high", "文件路径为空")
        if ".." in path:
            return self._deny("high", f"文件路径包含路径穿越: {path}")
        if INJECTION_CHARS_PATTERN.search(path):
            return self._deny("high", "文件路径包含命令注入字符")

        path_lower = path.lower()

        # 敏感路径
        for sensitive in SENSITIVE_PATHS:
            if path_lower.startswith(sensitive):
                return self._deny("high", f"禁止访问敏感路径: {path}")

        # .ssh 目录
        if "/.ssh/" in path_lower or path_lower.endswith("/.ssh"):
            return self._deny("high", f"禁止访问 SSH 密钥目录: {path}")

        # 敏感文件后缀（密钥/证书）
        for ext in SENSITIVE_EXTENSIONS:
            if path_lower.endswith(ext):
                return self._deny("high", f"禁止访问密钥/证书文件: {path}")

        # action 分级
        if action in ("check", "read"):
            if path_lower.startswith("/var/log/") or path_lower.startswith("/tmp/"):
                return self._allow("low", f"file_guard 只读操作: {path}")
            return self._deny("medium", f"file_guard 只读操作但路径不在允许范围内: {path}")

        if action == "write":
            if path_lower.startswith("/tmp/"):
                # viewer 不能写
                if role == "viewer":
                    return self._deny("medium", "viewer 无权执行文件写入操作")
                return self._confirm("medium", f"中风险文件写入: {path}")
            return self._deny("high", f"禁止写入路径: {path}")

        return self._deny("medium", f"非法的 file_guard action: {action}")

    def _check_net_monitor(self, params: dict) -> dict[str, Any]:
        """检查网络监控调用的动态安全边界。

        metric 的枚举合法性由 ToolRegistry 负责；
        这里只保留防御性类型检查和安全语义判断。
        """
        metric = params.get("metric", "all")

        if not isinstance(metric, str):
            return self._deny("medium", "net_monitor metric 类型非法")

        # 当前 Registry 中允许的 net_monitor 操作均为只读监控。
        return self._allow("low", "net_monitor 只读网络监控")

    def _check_metrics_history(self, params: dict) -> dict[str, Any]:
        """检查 metrics_history 调用的安全边界。

        metrics_history 是只读历史指标查询工具，所有参数都是可选的查询条件。
        参数枚举与约束合法性由 ToolRegistry 负责，这里只做防御性类型检查。
        """
        # limit 参数做防御性类型校验
        limit = params.get("limit")
        if limit is not None:
            try:
                limit_int = int(str(limit))
            except (ValueError, TypeError):
                return self._deny("medium", "metrics_history limit 参数格式非法")
            if limit_int < 1 or limit_int > 10000:
                return self._deny("medium", f"metrics_history limit 超出允许范围: {limit_int}")

        # metrics 参数如果是字符串，仅允许逗号分隔的合法指标名
        valid_metric_names = {"cpu", "memory", "disk", "network", "all"}
        metric_val = params.get("metrics")
        if metric_val is not None:
            if isinstance(metric_val, str):
                parts = [p.strip() for p in metric_val.split(",") if p.strip()]
                for p in parts:
                    if p not in valid_metric_names:
                        return self._deny("medium", f"metrics_history 非法指标名: {p}")
            elif not isinstance(metric_val, list):
                return self._deny("medium", "metrics_history metrics 参数类型非法")

        # 只读历史指标查询，低风险
        return self._allow("low", "metrics_history 只读历史指标查询")

    # ── 辅助方法 ────────────────────────────────────────────────────

    @staticmethod
    def _result(
        allowed: bool | None, risk_level: str, reason: str,
        requires_confirm: bool = False,
    ) -> dict[str, Any]:
        """统一结果工厂 —— 所有 _check_* 方法返回完整四字段"""
        return {
            "allowed": allowed,
            "risk_level": risk_level,
            "reason": reason,
            "requires_confirm": requires_confirm,
        }

    @classmethod
    def _allow(cls, risk_level: str, reason: str) -> dict[str, Any]:
        """放行（low 直接执行）"""
        return cls._result(True, risk_level, reason, requires_confirm=False)

    @classmethod
    def _deny(cls, risk_level: str, reason: str) -> dict[str, Any]:
        """策略硬拒绝（任何角色都不放行）"""
        return cls._result(False, risk_level, reason, requires_confirm=False)

    @classmethod
    def _confirm(cls, risk_level: str, reason: str) -> dict[str, Any]:
        """中风险待 RBAC 角色裁决 —— 由 analyze_tool_call 补全最终判定"""
        return cls._result(None, risk_level, reason, requires_confirm=True)
