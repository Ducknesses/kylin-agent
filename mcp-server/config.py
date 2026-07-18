"""MCP Server 配置"""
import json
import logging
import os
import sys
from pathlib import Path

# 优先从项目目录下的 .env 文件加载环境变量

try:
    from dotenv import load_dotenv
    _env_path = Path(__file__).parent / ".env"
    if _env_path.exists():
        _loaded = load_dotenv(_env_path)
        if _loaded:
            _token_val = os.getenv("API_TOKEN") or ""
            print(f"[INFO] .env 文件已加载 (路径={_env_path}, token长度={len(_token_val)})", file=sys.stderr)
        else:
            print(f"[WARN] .env 文件存在但加载返回 False (路径={_env_path})", file=sys.stderr)
    else:
        print(f"[WARN] .env 文件不存在 (期望路径={_env_path})，API_TOKEN 将为空", file=sys.stderr)
except ImportError:
    print(
        "[WARN] python-dotenv 未安装，将只使用系统环境变量。"
        "建议: pip install python-dotenv",
        file=sys.stderr,
    )
except Exception as _e:
    print(f"[ERROR] .env 文件加载异常: {_e}", file=sys.stderr)


# ── 配置值校验 ──────────────────────────────────────────────────────

def _get_positive_int_env(name: str, default: int) -> int:
    """从环境变量读取正整数，非法值启动时报错"""
    raw = os.getenv(name, "")
    if raw == "" or raw is None:
        return default
    try:
        val = int(raw)
    except ValueError:
        raise ValueError(
            f"配置项 {name} 的值不是有效整数: {raw!r}"
        )
    if val <= 0:
        raise ValueError(
            f"配置项 {name} 必须为正整数，当前值: {val}"
        )
    return val


def _get_non_negative_int_env(name: str, default: int) -> int:
    """从环境变量读取非负整数，非法值启动时报错"""
    raw = os.getenv(name, "")
    if raw == "" or raw is None:
        return default
    try:
        val = int(raw)
    except ValueError:
        raise ValueError(
            f"配置项 {name} 的值不是有效整数: {raw!r}"
        )
    if val < 0:
        raise ValueError(
            f"配置项 {name} 不能为负数，当前值: {val}"
        )
    return val


def _get_non_negative_float_env(name: str, default: float) -> float:
    """从环境变量读取非负浮点数，非法值启动时报错"""
    raw = os.getenv(name, "")
    if raw == "" or raw is None:
        return default
    try:
        val = float(raw)
    except ValueError:
        raise ValueError(
            f"配置项 {name} 的值不是有效数字: {raw!r}"
        )
    if val < 0:
        raise ValueError(
            f"配置项 {name} 不能为负数，当前值: {val}"
        )
    return val


# ── 白名单规则：从 JSON 文件加载，文件不存在时使用内置默认值 ──────

_WHITELIST_DEFAULTS = {
    "allowed_users": ["agent-read", "agent-op", "agent-admin", "agent"],
    "allowed_commands": [
        "df -h", "free -m", "free -h", "uptime", "uname -a", "uname -r",
        "hostnamectl", "lscpu", "lsblk",
        "cat /proc/cpuinfo", "cat /proc/meminfo", "cat /proc/loadavg",
        "cat /proc/stat", "cat /proc/uptime", "cat /proc/net/dev",
        "ps aux --sort=-%cpu", "ps aux --sort=-%mem", "ps aux", "top -b -n 1",
        "systemctl status {service}", "systemctl start {service}",
        "systemctl stop {service}", "systemctl restart {service}",
        "systemctl is-active {service}", "systemctl is-enabled {service}",
        "journalctl -u {service} -n {lines}",
        "journalctl -u {service} --since {since}",
        "journalctl -n {lines}", "journalctl --since {since}",
        "cat /var/log/{logfile}", "tail -n {lines} /var/log/{logfile}",
        "ss -tunlp", "ss -tunl", "ss -tun",
        "ip addr", "ip addr show", "ip link", "ip route",
        "cat /etc/resolv.conf",
        "cat {filepath}", "head -n {lines} {filepath}",
        "tail -n {lines} {filepath}",
        "ls -la {dirpath}", "ls -l {dirpath}",
    ],
    "danger_patterns": [
        ";", "&&", "||", "|", "$(", "`", "${", ">>", ">", "<", "<<<", "&",
    ],
    "protected_paths": [
        "/etc/passwd", "/etc/shadow", "/etc/shadow-",
        "/etc/gshadow", "/etc/gshadow-",
        "/etc/sudoers", "/etc/sudoers.d",
        "/boot", "/usr/lib/modules", "/usr/src",
        "/etc/ssh/sshd_config", "/etc/ssh/ssh_config", "/etc/ssh/ssh_host_",
        "/etc/pam.d", "/etc/security",
    ],
    "protected_extensions": [
        ".pem", ".key", ".crt", ".cer",
        ".p12", ".pfx", ".jks", ".keystore",
    ],
    "allowed_services": [
        "nginx", "httpd", "apache2", "sshd", "ssh",
        "mysql", "mysqld", "mariadb", "postgresql",
        "redis", "redis-server", "docker",
        "cron", "crond", "rsyslog", "fail2ban",
        "iptables", "firewalld",
    ],
    "blocked_services": [
        "systemd", "systemd-logind", "systemd-journald",
        "network", "NetworkManager",
        "dbus", "dbus-daemon", "polkit", "auditd",
        "mcp-server",
    ],
}

_logger = logging.getLogger("mcp.config")


def _load_whitelist_rules() -> dict:
    """从 whitelist_rules.json 加载白名单规则。
    
    加载顺序：
    1. 优先从 config.py 同目录下的 whitelist_rules.json 读取
    2. 如果文件不存在或解析失败，使用代码内置默认值
    """
    rules_path = Path(__file__).parent / "whitelist_rules.json"
    if rules_path.exists():
        try:
            with open(rules_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            # 校验 JSON 中是否包含所有必需的 key，缺失的用默认值补全
            merged = {}
            for key, default_val in _WHITELIST_DEFAULTS.items():
                merged[key] = data.get(key, default_val)
            _logger.info("[Config] 白名单规则已从 %s 加载", rules_path)
            return merged
        except (json.JSONDecodeError, OSError) as e:
            _logger.warning(
                "[Config] 白名单规则文件 %s 解析失败: %s，使用内置默认值",
                rules_path, e,
            )
            print(
                f"\n[WARNING] whitelist_rules.json 解析失败: {e}\n"
                f"  文件路径: {rules_path}\n"
                f"  已回退到内置默认值。请检查 JSON 格式并重启服务。\n",
                file=sys.stderr,
            )
    else:
        _logger.info(
            "[Config] 白名单规则文件 %s 不存在，使用内置默认值", rules_path
        )
    return dict(_WHITELIST_DEFAULTS)


_whitelist = _load_whitelist_rules()


class Config:
    """全局配置，优先从环境变量读取。支持运行时动态更新部分字段。"""

    # HTTP 服务
    HOST: str = os.getenv("MCP_HOST", "127.0.0.1")
    PORT: int = int(os.getenv("MCP_PORT", "8001"))

    def update_runtime(self, key: str, value):
        """
        运行时动态更新配置项。
        当前仅支持更新 HOST 和 PORT。
        更新后返回 True，如果 key 不支持动态更新则返回 False。
        """
        if key == "HOST":
            self.HOST = str(value)
            return True
        elif key == "PORT":
            self.PORT = int(value)
            return True
        return False

    # Bearer Token 认证（生产环境必须通过环境变量或 .env 文件配置）
    API_TOKEN: str = os.getenv("API_TOKEN", "")

    # 沙箱配置
    COMMAND_TIMEOUT: int = int(os.getenv("COMMAND_TIMEOUT", "30"))
    MAX_OUTPUT_LINES: int = int(os.getenv("MAX_OUTPUT_LINES", "1000"))

    # ── cgroups v2 资源限制 ──────────────────────────────────────
    # 总开关：false 时完全跳过 cgroups，仅保留 timeout
    CGROUP_ENABLED: bool = os.getenv("CGROUP_ENABLED", "false").lower() in ("true", "1", "yes")
    # CPU: quota（微秒/period）和 period（微秒），默认 50% CPU（50000/100000）
    CGROUP_CPU_QUOTA: int = _get_positive_int_env("CGROUP_CPU_QUOTA", 50000)
    CGROUP_CPU_PERIOD: int = _get_positive_int_env("CGROUP_CPU_PERIOD", 100000)
    # Memory: 字节，默认 256MB
    CGROUP_MEMORY_MAX: int = _get_positive_int_env("CGROUP_MEMORY_MAX", 268435456)
    # Memory Swap: 字节，默认 0（禁用 swap），允许 0
    CGROUP_MEMORY_SWAP_MAX: int = _get_non_negative_int_env("CGROUP_MEMORY_SWAP_MAX", 0)
    # PIDs: 最大进程数，默认 64（防 fork bomb）
    CGROUP_PIDS_MAX: int = _get_positive_int_env("CGROUP_PIDS_MAX", 64)
    # IO: io.max 格式（空=不限制），例如 "8:0 rbps=10485760 wiops=100"
    CGROUP_IO_MAX: str = os.getenv("CGROUP_IO_MAX", "")
    # cgroup 清理超时（秒）
    CGROUP_CLEANUP_TIMEOUT: float = _get_non_negative_float_env("CGROUP_CLEANUP_TIMEOUT", 5.0)

    # 允许以哪些用户身份执行命令
    ALLOWED_USERS: list = _whitelist["allowed_users"]

    # 日志
    LOG_FILE: str = os.getenv("LOG_FILE", "/var/log/mcp-server.log")
    LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO")

    # 指标缓存（SQLite 本地存储）
    METRICS_DB_PATH: str = os.getenv("METRICS_DB_PATH", "/var/lib/mcp-server/metrics.db")
    METRICS_COLLECT_INTERVAL: int = int(os.getenv("METRICS_COLLECT_INTERVAL", "15"))
    METRICS_MAX_RETENTION_HOURS: int = int(os.getenv("METRICS_MAX_RETENTION_HOURS", "24"))
    METRICS_MAX_ROWS: int = int(os.getenv("METRICS_MAX_ROWS", "100000"))

    # 命令白名单：{pattern} 表示可变参数占位符
    # 支持精确匹配和参数化匹配
    ALLOWED_COMMANDS: list = _whitelist["allowed_commands"]

    # 敏感文件保护清单：任何写操作禁止访问这些路径
    PROTECTED_PATHS: list = _whitelist["protected_paths"]

    # 可读但禁止写的敏感文件扩展名
    PROTECTED_EXTENSIONS: list = _whitelist["protected_extensions"]

    # 危险Shell元字符（禁止出现在命令中）
    DANGER_PATTERNS: list = _whitelist["danger_patterns"]

    # 服务管理白名单：只允许操作这些服务
    ALLOWED_SERVICES: list = _whitelist["allowed_services"]

    # 禁止操作的核心系统服务（即使用户在 ALLOWED_SERVICES 中写了也拦截）
    BLOCKED_SERVICES: list = _whitelist["blocked_services"]


config = Config()