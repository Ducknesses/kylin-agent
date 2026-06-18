"""MCP Server 配置"""
import os


class Config:
    """全局配置，优先从环境变量读取"""

    # HTTP 服务
    HOST: str = os.getenv("MCP_HOST", "127.0.0.1")
    PORT: int = int(os.getenv("MCP_PORT", "8001"))

    # Bearer Token 认证
    API_TOKEN: str = os.getenv("API_TOKEN", "change-me-in-production")

    # 沙箱配置
    COMMAND_TIMEOUT: int = int(os.getenv("COMMAND_TIMEOUT", "30"))
    MAX_OUTPUT_LINES: int = int(os.getenv("MAX_OUTPUT_LINES", "1000"))

    # 允许以哪些用户身份执行命令
    ALLOWED_USERS: list = ["agent-read", "agent-op", "agent-admin", "agent"]

    # 日志
    LOG_FILE: str = os.getenv("LOG_FILE", "/var/log/mcp-server.log")
    LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO")

    # 命令白名单：{pattern} 表示可变参数占位符
    # 支持精确匹配和参数化匹配
    ALLOWED_COMMANDS: list = [
        # 系统信息类
        "df -h",
        "free -m",
        "free -h",
        "uptime",
        "uname -a",
        "uname -r",
        "hostnamectl",
        "lscpu",
        "lsblk",
        "cat /proc/cpuinfo",
        "cat /proc/meminfo",
        "cat /proc/loadavg",
        "cat /proc/stat",
        "cat /proc/uptime",
        "cat /proc/net/dev",
        # 进程类
        "ps aux --sort=-%cpu",
        "ps aux --sort=-%mem",
        "ps aux",
        "top -b -n 1",
        # 服务管理类（参数化）
        "systemctl status {service}",
        "systemctl start {service}",
        "systemctl stop {service}",
        "systemctl restart {service}",
        "systemctl is-active {service}",
        "systemctl is-enabled {service}",
        # 日志类（参数化）
        "journalctl -u {service} -n {lines}",
        "journalctl -u {service} --since {since}",
        "journalctl -n {lines}",
        "journalctl --since {since}",
        "cat /var/log/{logfile}",
        "tail -n {lines} /var/log/{logfile}",
        # 网络类
        "ss -tunlp",
        "ss -tunl",
        "ss -tun",
        "ip addr",
        "ip addr show",
        "ip link",
        "ip route",
        "cat /etc/resolv.conf",
        # 文件读取
        "cat {filepath}",
        "head -n {lines} {filepath}",
        "tail -n {lines} {filepath}",
        "ls -la {dirpath}",
        "ls -l {dirpath}",
    ]

    # 敏感文件保护清单：任何写操作禁止访问这些路径
    PROTECTED_PATHS: list = [
        "/etc/passwd",
        "/etc/shadow",
        "/etc/shadow-",
        "/etc/gshadow",
        "/etc/gshadow-",
        "/etc/sudoers",
        "/etc/sudoers.d",
        "/boot",
        "/usr/lib/modules",
        "/usr/src",
        "/etc/ssh/sshd_config",
        "/etc/ssh/ssh_config",
        "/etc/ssh/ssh_host_",
        "/etc/pam.d",
        "/etc/security",
    ]

    # 可读但禁止写的敏感文件扩展名
    PROTECTED_EXTENSIONS: list = [
        ".pem", ".key", ".crt", ".cer",
        ".p12", ".pfx", ".jks", ".keystore",
    ]

    # 危险Shell元字符（禁止出现在命令中）
    DANGER_PATTERNS: list = [
        # 命令链接符
        ";", "&&", "||", "|",
        # 命令替换
        "$(", "`", "${{",
        # 重定向（读写都危险）
        ">>", ">", "<", "<<<",
        # 后台运行
        "&",
        # 通配符（防目录遍历/批量操作）
        # 注意：不禁止 * 因为 ps aux --sort=-%cpu 需要
    ]

    # 服务管理白名单：只允许操作这些服务
    ALLOWED_SERVICES: list = [
        "nginx", "httpd", "apache2",
        "sshd", "ssh",
        "mysql", "mysqld", "mariadb",
        "postgresql",
        "redis", "redis-server",
        "docker",
        "cron", "crond",
        "rsyslog",
        "fail2ban",
        "iptables", "firewalld",
    ]

    # 禁止操作的核心系统服务（即使用户在 ALLOWED_SERVICES 中写了也拦截）
    BLOCKED_SERVICES: list = [
        "systemd", "systemd-logind", "systemd-journald",
        "network", "NetworkManager",
        "dbus", "dbus-daemon",
        "polkit",
        "auditd",
        "mcp-server",
    ]


config = Config()