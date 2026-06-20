"""日志读取插件：journalctl / var/log 安全读取"""
import logging
import os
import subprocess

from config import config

logger = logging.getLogger("mcp.log_reader")

# 日志类型
LOG_SOURCES = {
    "messages": "/var/log/messages",
    "secure": "/var/log/secure",
    "syslog": "/var/log/syslog",
    "dmesg": "/var/log/dmesg",
    "boot": "/var/log/boot.log",
    "cron": "/var/log/cron",
    "maillog": "/var/log/maillog",
    "nginx_access": "/var/log/nginx/access.log",
    "nginx_error": "/var/log/nginx/error.log",
    "mysql": "/var/log/mysql/error.log",
}


def _safe_path(log_file: str) -> str:
    """
    安全校验日志路径，防止目录遍历攻击
    只允许 /var/log/ 下的文件
    """
    # 处理预定义别名
    if log_file in LOG_SOURCES:
        real_path = LOG_SOURCES[log_file]
    else:
        real_path = log_file

    # 解析真实路径，防符号链接遍历
    try:
        real_path = os.path.realpath(real_path)
    except Exception:
        return ""

    # 必须在 /var/log/ 或 /var/log/ 子目录下
    allowed_prefixes = ["/var/log/", "/var/log"]
    is_allowed = any(real_path.startswith(prefix) or real_path == "/var/log" for prefix in allowed_prefixes)

    if not is_allowed:
        logger.warning("[LogReader] 禁止访问路径: %s (resolved: %s)", log_file, real_path)
        return ""

    return real_path


def _read_log_file(filepath: str, lines: int = 50, keyword: str = None) -> dict:
    """读取日志文件尾部内容，支持关键词过滤"""
    real_path = _safe_path(filepath)
    if not real_path:
        return {"error": f"禁止访问路径: {filepath}"}

    if not os.path.isfile(real_path):
        return {"error": f"文件不存在: {filepath}"}

    # 检查文件大小，超过100MB拒绝
    try:
        size = os.path.getsize(real_path)
        if size > 100 * 1024 * 1024:
            return {"error": f"文件过大({size}字节)，拒绝读取: {filepath}"}
    except OSError as e:
        return {"error": f"无法获取文件大小: {e}"}

    # 限制最大行数
    max_lines = min(lines, config.MAX_OUTPUT_LINES)

    try:
        result = subprocess.run(
            ["tail", "-n", str(max_lines), real_path],
            capture_output=True,
            text=True,
            timeout=10,
        )
        content = result.stdout
        log_lines = content.strip().split("\n") if content.strip() else []

        # 关键词过滤
        if keyword:
            log_lines = [l for l in log_lines if keyword in l]

        return {
            "source": filepath,
            "real_path": real_path,
            "lines_requested": lines,
            "lines_returned": len(log_lines),
            "keyword": keyword,
            "content": "\n".join(log_lines),
            "stderr": result.stderr,
        }
    except subprocess.TimeoutExpired:
        return {"error": f"读取文件超时: {filepath}"}
    except Exception as e:
        logger.exception("读取日志文件异常: %s", e)
        return {"error": str(e)}


def _read_journal(service: str = None, lines: int = 50, since: str = None, keyword: str = None) -> dict:
    """通过 journalctl 读取系统日志，支持关键词过滤"""
    cmd = ["journalctl", "--no-pager", "--lines", str(min(lines, config.MAX_OUTPUT_LINES))]

    if service:
        cmd.extend(["-u", service])

    if since:
        cmd.extend(["--since", since])

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=15,
        )
        content = result.stdout
        log_lines = content.strip().split("\n") if content.strip() else []

        # 关键词过滤
        if keyword:
            log_lines = [l for l in log_lines if keyword in l]

        return {
            "source": "journalctl",
            "service": service,
            "since": since,
            "lines_requested": lines,
            "lines_returned": len(log_lines),
            "keyword": keyword,
            "content": "\n".join(log_lines),
            "stderr": result.stderr,
        }
    except subprocess.TimeoutExpired:
        return {"error": "journalctl 执行超时"}
    except FileNotFoundError:
        return {"error": "journalctl 命令不可用（系统可能不使用 systemd）"}
    except Exception as e:
        logger.exception("journalctl 执行异常: %s", e)
        return {"error": str(e)}


def handle(arguments: dict) -> dict:
    """
    处理日志读取请求

    参数:
        arguments: {
            "type": "journalctl" | "file",
            "service": "sshd",          # journalctl 模式下筛选服务
            "source": "messages",       # file 模式下日志来源（别名或路径）
            "lines": 50,                # 读取行数
            "since": "30m",             # 时间范围（仅 journalctl）
            "keyword": "error"          # 关键词过滤（可选）
        }

    返回:
         日志内容（支持关键词过滤）
    """
    log_type = arguments.get("type", "file")
    lines = int(arguments.get("lines", 50))
    service = arguments.get("service", "")
    source = arguments.get("source", "messages")
    since = arguments.get("since", "")
    keyword = arguments.get("keyword", "")

    if log_type == "journalctl":
        return _read_journal(service=service, lines=lines, since=since, keyword=keyword or None)
    elif log_type == "file":
        return _read_log_file(filepath=source, lines=lines, keyword=keyword or None)
    else:
        return {"error": f"不支持的日志类型: {log_type}，可选: journalctl / file"}
