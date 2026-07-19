"""安全规则数据唯一来源

集中管理全部安全规则常量，供以下模块使用：
  - app.services.safety_guard.SafetyGuard —— 用户输入 / 工具调用安全裁决
  - app.core.security.risk_classify       —— 输入风险分级（长度 / 黑名单 / 关键词）
  - app.api.config                        —— 白名单配置接口的默认值

只存放规则数据（含编译后的正则），判定逻辑归 SafetyGuard 与 risk_classify。
新增 / 调整规则时只需修改本文件。

注意：HIGH_RISK_PATTERNS 与 EXTRA_HIGH_RISK_PATTERNS 故意保持两个列表——
前者由 risk_classify 生成通用 reason（"匹配高危命令模式: ..."），
后者携带人工撰写的明确拦截原因，且两者在 SafetyGuard 中的检查顺序不同；
合并会改变对外返回的 reason 文本，故维持现状。
"""
import re

from app.core.auth import AuthLevel

# ════════════════════════════════════════════════════════════════════
# 1. 用户输入高危模式
# ════════════════════════════════════════════════════════════════════

# 1a. 基础高危命令黑名单（risk_classify 使用，reason 为通用格式）
HIGH_RISK_PATTERNS: list[str] = [
    r"rm\s+-rf\s+/.*",
    r"mkfs\.",
    r"dd\s+if=/dev/zero",
    r">\s*/etc/passwd",
    r">\s*/etc/shadow",
    r":\(\)\s*\{\s*:\|:\&\s*\};.*",  # fork bomb
    r"chmod\s+-R\s+777\s+/.*",
    r"mv\s+/.*\s+/dev/null",
]
HIGH_RISK_COMPILED: list[re.Pattern] = [
    re.compile(p, re.IGNORECASE) for p in HIGH_RISK_PATTERNS
]

# 1b. 补充高危模式（SafetyGuard 优先检查，携带明确拦截原因）
EXTRA_HIGH_RISK_PATTERNS: list[tuple[re.Pattern, str]] = [
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
    # chmod 777（基础黑名单仅覆盖 chmod -R 777，这里补全无 -R 的情况）
    (re.compile(r"\bchmod\s+777\b", re.IGNORECASE),
     "禁止 chmod 777 权限变更"),
    # sudo rm -rf（基础黑名单不匹配带 sudo 前缀的情况）
    (re.compile(r"\bsudo\s+rm\b", re.IGNORECASE),
     "禁止 sudo rm 高危删除操作"),
]

# ════════════════════════════════════════════════════════════════════
# 2. 审计绕过 / 安全关闭注入关键词（命中即判高危）
# ════════════════════════════════════════════════════════════════════

AUDIT_BYPASS_KEYWORDS: list[str] = [
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

# ════════════════════════════════════════════════════════════════════
# 3. 中危关键词（risk_classify 使用，命中 → 需二次确认）
# ════════════════════════════════════════════════════════════════════

MEDIUM_RISK_KEYWORDS: list[str] = [
    "systemctl stop",
    "systemctl disable",
    "iptables -F",
    "useradd",
    "userdel",
    "passwd",
    "chmod 777",
    "chown -R",
    "kill -9",
]

# ════════════════════════════════════════════════════════════════════
# 4. 中风险服务操作（需二次确认，但不禁用）
# ════════════════════════════════════════════════════════════════════

# 匹配：重启/停止/启动/重新加载 nginx，支持 systemctl/service + restart/stop/start/reload
MEDIUM_NGINX_PATTERN: re.Pattern = re.compile(
    # 匹配 systemctl restart nginx / service nginx restart / 重启 nginx 等
    r"((?:systemctl|service)\s+)?(restart|stop|start|reload|重启|停止|启动|重新加载)\s+nginx"
    r"|service\s+nginx\s+(restart|stop|start|reload)",
    re.IGNORECASE,
)

# ════════════════════════════════════════════════════════════════════
# 5. 工具调用通用规则
# ════════════════════════════════════════════════════════════════════

# 命令注入字符（service 名 / 文件路径中的非法字符）
INJECTION_CHARS_PATTERN: re.Pattern = re.compile(r"[;&|`$()><\n]")

# 敏感路径（high 拒绝 file_guard 访问）
SENSITIVE_PATHS: list[str] = [
    "/etc/passwd", "/etc/shadow", "/boot", "/root",
    "/var/lib", "/usr/bin", "/bin", "/sbin",
]

# 敏感文件后缀（密钥/证书）
SENSITIVE_EXTENSIONS: tuple[str, ...] = (
    ".pem", ".key", ".crt", ".cer", ".p12", ".pfx", ".jks", ".keystore",
)

# ════════════════════════════════════════════════════════════════════
# 6. cmd_exec 白名单（单表，按序匹配：exact low → exact medium → prefix low）
# ════════════════════════════════════════════════════════════════════
#
# 每条规则为 (match_mode, command, risk_level)：
#   match_mode: "exact"  整串匹配（命令已统一小写后比较）
#               "prefix" 前缀匹配（只读诊断命令，由 sandbox 做最终路径/字符校验）
#   risk_level: "low"    直接放行
#               "medium" 放行但需二次确认（由 RBAC 按角色最终裁决）
CMD_EXEC_RULES: list[tuple[str, str, str]] = [
    # ── exact + low：低风险只读查询 / 诊断 ──
    # 系统基本信息
    ("exact", "df -h", "low"), ("exact", "free -m", "low"),
    ("exact", "uptime", "low"), ("exact", "whoami", "low"),
    ("exact", "uname -a", "low"),
    ("exact", "hostnamectl", "low"), ("exact", "timedatectl", "low"),
    ("exact", "lscpu", "low"), ("exact", "lsblk", "low"),
    ("exact", "lsblk -f", "low"), ("exact", "mount", "low"),
    ("exact", "systemd-analyze", "low"),
    ("exact", "systemd-analyze blame", "low"),
    # 进程 / 资源
    ("exact", "ps aux", "low"), ("exact", "top -bn1", "low"),
    # 网络状态
    ("exact", "ip addr", "low"), ("exact", "ip route", "low"),
    ("exact", "ip link", "low"),
    ("exact", "ss -tlnp", "low"), ("exact", "ss -an", "low"),
    ("exact", "ss -tuln", "low"),
    ("exact", "ping -c 4 127.0.0.1", "low"),
    ("exact", "ping -c 4 localhost", "low"),
    ("exact", "curl -s --max-time 10 http://localhost", "low"),
    # 日志 / dmesg
    ("exact", "systemctl status nginx", "low"),
    ("exact", "systemctl status sshd", "low"),
    ("exact", "systemctl status docker", "low"),
    ("exact", "systemctl status cron", "low"),
    ("exact", "journalctl -u nginx -n 50", "low"),
    ("exact", "journalctl --no-pager -n 50", "low"),
    ("exact", "journalctl --no-pager -p err -n 50", "low"),
    ("exact", "journalctl --no-pager -u nginx -n 50", "low"),
    ("exact", "dmesg -T", "low"), ("exact", "dmesg --level=err", "low"),
    ("exact", "dmesg --level=err,warn -T", "low"),
    # /proc 只读
    ("exact", "cat /proc/cpuinfo", "low"),
    ("exact", "cat /proc/meminfo", "low"),
    ("exact", "cat /proc/version", "low"),
    ("exact", "cat /proc/loadavg", "low"),
    ("exact", "cat /proc/uptime", "low"),
    # 文件 / 磁盘使用
    ("exact", "ls -la /var/log", "low"), ("exact", "ls -la /tmp", "low"),
    ("exact", "du -sh /var/log", "low"), ("exact", "du -sh /tmp", "low"),
    # service 列表
    ("exact", "systemctl list-units --all", "low"),
    ("exact", "systemctl list-unit-files", "low"),
    ("exact", "systemctl list-units --type=service", "low"),
    ("exact", "systemctl list-units --type=service --state=running", "low"),
    ("exact", "systemctl list-units --type=service --state=failed", "low"),
    # 用户 / 登录
    ("exact", "last -n 20", "low"), ("exact", "lastb -n 20", "low"),
    ("exact", "w", "low"),

    # ── exact + medium：中危白名单（允许但需二次确认） ──
    # nginx
    ("exact", "systemctl restart nginx", "medium"),
    ("exact", "systemctl start nginx", "medium"),
    ("exact", "systemctl stop nginx", "medium"),
    ("exact", "systemctl reload nginx", "medium"),
    # sshd
    ("exact", "systemctl restart sshd", "medium"),
    ("exact", "systemctl reload sshd", "medium"),
    # cron / rsyslog
    ("exact", "systemctl restart cron", "medium"),
    ("exact", "systemctl restart rsyslog", "medium"),
    # docker
    ("exact", "systemctl restart docker", "medium"),
    ("exact", "docker ps", "medium"), ("exact", "docker ps -a", "medium"),
    ("exact", "docker images", "medium"), ("exact", "docker info", "medium"),

    # ── prefix + low：只读诊断命令前缀（sandbox 最终裁决） ──
    ("prefix", "ls ", "low"), ("prefix", "lsblk", "low"),
    ("prefix", "lscpu", "low"), ("prefix", "lspci", "low"),
    ("prefix", "lsusb", "low"),
    ("prefix", "cat ", "low"), ("prefix", "head ", "low"),
    ("prefix", "tail ", "low"), ("prefix", "df ", "low"),
    ("prefix", "du ", "low"),
    ("prefix", "free ", "low"), ("prefix", "ps ", "low"),
    ("prefix", "top ", "low"), ("prefix", "uptime", "low"),
    ("prefix", "whoami", "low"),
    ("prefix", "hostname", "low"), ("prefix", "id", "low"),
    ("prefix", "uname ", "low"), ("prefix", "hostnamectl", "low"),
    ("prefix", "timedatectl", "low"), ("prefix", "mount", "low"),
    ("prefix", "findmnt", "low"),
    ("prefix", "ss ", "low"), ("prefix", "ip ", "low"),
    ("prefix", "ping ", "low"), ("prefix", "netstat ", "low"),
    ("prefix", "dmesg ", "low"),
    ("prefix", "journalctl ", "low"), ("prefix", "file ", "low"),
    ("prefix", "stat ", "low"), ("prefix", "wc ", "low"),
]

# ════════════════════════════════════════════════════════════════════
# 7. SafetyGuard 角色权限常量
# ════════════════════════════════════════════════════════════════════

# 合法角色（未知角色一律按 viewer 处理，最小权限原则）
VALID_ROLES: tuple[str, ...] = ("viewer", "operator", "admin")

# 可执行中危操作的角色（medium 需二次确认）
ROLE_CAN_MEDIUM: set[str] = {"admin", "operator"}

# ════════════════════════════════════════════════════════════════════
# 8. 旧版 RBAC 默认白名单（仅供 /api/config/whitelist 提供默认值）
# ════════════════════════════════════════════════════════════════════


class Permission:
    """权限常量（与 AuthLevel 对齐）"""
    READ = AuthLevel.READ.value      # "agent-read"
    OP = AuthLevel.OP.value          # "agent-op"
    ADMIN = AuthLevel.ADMIN.value    # "agent-admin"


# 命令模板白名单：权限 -> 允许的正则模板
COMMAND_WHITELIST: dict[str, list[str]] = {
    Permission.READ: [
        r"^df\s*-h.*",
        r"^ps\s+aux.*",
        r"^cat\s+/var/log/.*",
        r"^journalctl\s+.*",
        r"^free\s+-h.*",
        r"^top\s+-bn1.*",
        r"^uptime.*",
        r"^uname\s+-a.*",
        r"^ls\s+.*",
        r"^ss\s+-tlnp.*",
        r"^netstat\s+-tlnp.*",
        r"^ip\s+addr.*",
        r"^ping\s+-c\s+\d+.*",
    ],
    Permission.OP: [
        r"^systemctl\s+(start|restart|status)\s+\w+.*",
        r"^service\s+\w+\s+(start|restart|status).*",
    ],
    Permission.ADMIN: [
        r"^systemctl\s+(stop|enable|disable)\s+\w+.*",
        r"^nginx\s+-t.*",
        r"^cp\s+.*",
        r"^mv\s+.*",
    ],
}

# 危险操作绝对禁止
DANGEROUS_PATTERNS: list[str] = [
    r";",
    r"&&",
    r"\|",
    r"`",
    r"\$\(",
    r">\s*/etc/passwd",
    r">\s*/etc/shadow",
    r">\s*/etc/sudoers",
    r"rm\s+-rf\s+/.*",
]
