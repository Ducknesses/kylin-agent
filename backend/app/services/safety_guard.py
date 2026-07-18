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


# ── 工具调用 RBAC 角色映射 ────────────────────────────────────────────

# 角色权限：viewer 只能 low，operator/admin 可以 low + medium（需 confirm）
_ROLE_CAN_MEDIUM = {"admin", "operator"}  # admin/operator 可执行中危操作

# cmd_exec 白名单命令（低风险只读查询 / 诊断）
_CMD_EXEC_WHITELIST: list[str] = [
    # ── 系统基本信息 ──
    "df -h", "free -m", "uptime", "whoami", "uname -a",
    "hostnamectl", "timedatectl", "lscpu", "lsblk", "lsblk -f", "mount",
    "systemd-analyze", "systemd-analyze blame",
    # ── 进程 / 资源 ──
    "ps aux", "top -bn1",
    # ── 网络状态 ──
    "ip addr", "ip route", "ip link",
    "ss -tlnp", "ss -an", "ss -tuln",
    "ping -c 4 127.0.0.1", "ping -c 4 localhost",
    "curl -s --max-time 10 http://localhost",
    # ── 日志 / dmesg ──
    "systemctl status nginx", "systemctl status sshd",
    "systemctl status docker", "systemctl status cron",
    "journalctl -u nginx -n 50",
    "journalctl --no-pager -n 50",
    "journalctl --no-pager -p err -n 50",
    "journalctl --no-pager -u nginx -n 50",
    "dmesg -T", "dmesg --level=err",
    "dmesg --level=err,warn -T",
    # ── /proc 只读 ──
    "cat /proc/cpuinfo", "cat /proc/meminfo",
    "cat /proc/version", "cat /proc/loadavg",
    "cat /proc/uptime",
    # ── 文件 / 磁盘使用 ──
    "ls -la /var/log", "ls -la /tmp",
    "du -sh /var/log", "du -sh /tmp",
    # ── service 列表 ──
    "systemctl list-units --all", "systemctl list-unit-files",
    "systemctl list-units --type=service",
    "systemctl list-units --type=service --state=running",
    "systemctl list-units --type=service --state=failed",
    # ── 用户 / 登录 ──
    "last -n 20", "lastb -n 20", "w",
]

# cmd_exec 中危白名单命令（允许但需二次确认）
_CMD_EXEC_MEDIUM_WHITELIST: list[str] = [
    # nginx
    "systemctl restart nginx", "systemctl start nginx",
    "systemctl stop nginx", "systemctl reload nginx",
    # sshd
    "systemctl restart sshd", "systemctl reload sshd",
    # cron / rsyslog
    "systemctl restart cron", "systemctl restart rsyslog",
    # docker
    "systemctl restart docker",
    "docker ps", "docker ps -a",
    "docker images", "docker info",
]

# 敏感路径（high 拒绝 file_guard 访问）
_SENSITIVE_PATHS: list[str] = [
    "/etc/passwd", "/etc/shadow", "/boot", "/root",
    "/var/lib", "/usr/bin", "/bin", "/sbin",
]

# 敏感文件后缀（密钥/证书）
_SENSITIVE_EXTENSIONS: tuple[str, ...] = (
    ".pem", ".key", ".crt", ".cer", ".p12", ".pfx", ".jks", ".keystore",
)

# 命令注入字符（service 名中的非法字符）
_INJECTION_CHARS_PATTERN = re.compile(r"[;&|`$()><\n]")

# 中风险 nginx 服务操作（需二次确认，但不禁用）
# 匹配：重启/停止/启动/重新加载 nginx，支持 systemctl/service + restart/stop/start/reload
_MEDIUM_NGINX_PATTERN = re.compile(
    # 匹配 systemctl restart nginx / service nginx restart / 重启 nginx 等
    r"((?:systemctl|service)\s+)?(restart|stop|start|reload|重启|停止|启动|重新加载)\s+nginx"
    r"|service\s+nginx\s+(restart|stop|start|reload)",
    re.IGNORECASE,
)


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
            return {
                "allowed": False,
                "risk_level": "low",
                "reason": "输入不能为空",
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

        # 3. Prompt Injection 检测（优先于中风险，防止「忽略规则 + restart nginx」被误判为 medium）
        injection = detect_injection(stripped)
        if injection["detected"]:
            logger.warning(f"[SafetyGuard] Prompt Injection: {injection['reason']}")
            return {
                "allowed": False,
                "risk_level": "high",
                "reason": f"输入安全检测未通过: {injection['reason']}",
                "requires_confirm": False,
            }

        # 4. 中风险 nginx 服务操作检测（Prompt 注入已排除，仅正常运维操作到此）
        if _MEDIUM_NGINX_PATTERN.search(stripped):
            logger.info(f"[SafetyGuard] 中风险服务操作: {stripped[:60]}")
            return {
                "allowed": True,
                "risk_level": "medium",
                "reason": f"该操作涉及服务变更，需要确认: {stripped[:50]}",
                "requires_confirm": True,
            }

        # 5. 调已有 risk_classify 做完整风险分级（跳过注入检测，前面已做过）
        risk = risk_classify(stripped, skip_injection_check=True)

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

    # ── 工具调用检查 ─────────────────────────────────────────────────

    def analyze_tool_call(
        self, tool: str, params: dict, role: str = "viewer"
    ) -> dict[str, Any]:
        """对 MCP 工具调用进行安全裁决

        参数:
            tool: 工具名（sys_info / log_reader / service_mgr / cmd_exec / file_guard）
            params: 工具参数
            role: 用户角色（viewer / operator / admin），未知按 viewer 处理

        返回:
            {allowed, risk_level, reason, requires_confirm}
        """
        # 规范化 role，未知角色按 viewer
        role = role.lower() if role else "viewer"
        if role not in ("viewer", "operator", "admin"):
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

        # 应用 RBAC：high 永远拒绝，medium 需要角色权限
        if result["risk_level"] == "high":
            result["allowed"] = False
            result["requires_confirm"] = False
            return result

        if result["risk_level"] == "medium":
            # 如果 _check_* 已显式拒绝，则不因角色放行
            if result.get("allowed") is False:
                return result
            if role in _ROLE_CAN_MEDIUM:
                result["allowed"] = True
                result["requires_confirm"] = True
                return result
            else:
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
        if service and _INJECTION_CHARS_PATTERN.search(service):
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
        """service_mgr 工具检查：按 action + 服务名分级（去除 dead code）"""
        action = (params.get("action") or "").strip().lower()
        service_name = (params.get("service") or params.get("name") or "").strip()

        # 服务名为空或含注入字符
        if not service_name or _INJECTION_CHARS_PATTERN.search(service_name):
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

        # 变更 action → medium（需确认）
        if action in medium_actions:
            return {
                "risk_level": "medium",
                "reason": f"中风险服务操作: {action} {service_name}",
            }

        # disable（非高风险服务）→ medium 拒绝
        if action == "disable":
            return self._deny("medium", f"禁止执行 disable 操作: {service_name}")

        # 非法 action
        return self._deny("medium", f"非法的 service_mgr action: {action}")

    # 只读诊断命令前缀：不修改系统状态，委托 sandbox 做最终路径校验
    _READONLY_CMD_PREFIXES: tuple[str, ...] = (
        "ls ", "lsblk", "lscpu", "lspci", "lsusb",
        "cat ", "head ", "tail ", "df ", "du ",
        "free ", "ps ", "top ", "uptime", "whoami",
        "hostname", "id", "uname ", "hostnamectl",
        "timedatectl", "mount", "findmnt",
        "ss ", "ip ", "ping ", "netstat ", "dmesg ",
        "journalctl ", "file ", "stat ", "wc ",
    )

    def _check_cmd_exec(self, params: dict) -> dict[str, Any]:
        """cmd_exec 工具检查：高危拦截 → 精确白名单 → 只读前缀放行（sandbox 兜底）"""
        command = (params.get("command") or "").strip()
        if not command:
            return self._deny("high", "命令为空")

        # 1. 高危检测（shell 注入、破坏性命令、安全绕过等）
        risk = self.analyze_user_input(command)
        if risk["risk_level"] == "high":
            return self._deny("high", f"高危命令: {risk['reason']}")

        normalized_cmd = command.lower().strip()

        # 2. 精确白名单命令 —— 直接放行（保留原有精确匹配逻辑）
        for allowed in _CMD_EXEC_WHITELIST:
            if normalized_cmd == allowed.lower():
                return self._allow("low", f"白名单命令: {command}")

        # 3. 中危白名单命令 —— 需二次确认（service restart/stop 等）
        for allowed in _CMD_EXEC_MEDIUM_WHITELIST:
            if normalized_cmd == allowed.lower():
                return self._result(
                    allowed=True, risk_level="medium",
                    reason=f"中危白名单命令: {command}",
                    requires_confirm=True,
                )

        # 4. 只读诊断命令前缀 —— 低风险放行，由 sandbox 做最终路径/字符校验
        for prefix in self._READONLY_CMD_PREFIXES:
            if normalized_cmd.startswith(prefix) or normalized_cmd == prefix:
                return self._allow("low", f"只读诊断命令（sandbox 最终裁决）: {command[:60]}")

        # 5. 其余命令拒绝
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
        if _INJECTION_CHARS_PATTERN.search(path):
            return self._deny("high", "文件路径包含命令注入字符")

        path_lower = path.lower()

        # 敏感路径
        for sensitive in _SENSITIVE_PATHS:
            if path_lower.startswith(sensitive):
                return self._deny("high", f"禁止访问敏感路径: {path}")

        # .ssh 目录
        if "/.ssh/" in path_lower or path_lower.endswith("/.ssh"):
            return self._deny("high", f"禁止访问 SSH 密钥目录: {path}")

        # 敏感文件后缀（密钥/证书）
        for ext in _SENSITIVE_EXTENSIONS:
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
                return {
                    "risk_level": "medium",
                    "reason": f"中风险文件写入: {path}",
                }
            return self._deny("high", f"禁止写入路径: {path}")

        return self._deny("medium", f"非法的 file_guard action: {action}")

    def _check_net_monitor(
        self,
        params: dict[str, object],
    ) -> dict[str, object]:
        """检查网络监控调用的动态安全边界。

        metric 的枚举合法性由 ToolRegistry 负责；
        这里只保留防御性类型检查和安全语义判断。
        """

        metric = params.get("metric", "all")

        if not isinstance(metric, str):
            return self._deny(
                "medium",
                "net_monitor metric 类型非法",
            )

        # 当前 Registry 中允许的 net_monitor 操作均为只读监控。
        return self._allow(
            "low",
            "net_monitor 只读网络监控",
        )

    def _check_metrics_history(
        self,
        params: dict[str, object],
    ) -> dict[str, object]:
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
    def _result(allowed: bool, risk_level: str, reason: str, requires_confirm: bool = False) -> dict[str, Any]:
        """统一结果工厂 —— 所有 _check_* 方法返回完整四字段"""
        return {
            "allowed": allowed,
            "risk_level": risk_level,
            "reason": reason,
            "requires_confirm": requires_confirm,
        }

    @classmethod
    def _allow(cls, risk_level: str, reason: str) -> dict[str, Any]:
        return cls._result(True, risk_level, reason, requires_confirm=False)

    @classmethod
    def _deny(cls, risk_level: str, reason: str) -> dict[str, Any]:
        return cls._result(False, risk_level, reason, requires_confirm=False)
