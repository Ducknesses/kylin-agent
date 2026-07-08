"""命令执行沙箱插件：通过sandbox引擎安全执行系统命令"""
import logging
import uuid

from sandbox import execute as sandbox_execute

logger = logging.getLogger("mcp.cmd_exec")

# 只读命令白名单（无需确认直接执行）
READ_ONLY_COMMANDS = {
    "df -h", "free -m", "uptime", "whoami", "uname -a",
    "ps aux", "top -bn1", "ls -la", "ss -tlnp",
    "netstat -tlnp", "ip addr", "hostname", "id",
    "lscpu", "lsblk", "cat /proc/loadavg", "cat /proc/meminfo",
}


def handle(arguments: dict) -> dict:
    """
    在沙箱中安全执行系统命令

    参数:
        arguments: {
            "command": "df -h",       # 要执行的命令（必须在白名单中）
            "timeout": 30,             # 超时秒数（可选，默认30）
            "user": "agent-read"       # 执行用户（可选，默认agent-read）
            "_skip_pending": false     # 内部标记：跳过pending（确认后重试时使用）
        }

    返回:
        {
            "stdout": str,
            "stderr": str,
            "returncode": int,
            "execution_time": float,
            "blocked": bool           # 是否被安全策略拦截
        }
        或
        {
            "_pending_confirmation": True,
            "confirm_id": str,
            "tool": "cmd_exec",
            "command": str,
            "reason": str,
        }
    """
    command = arguments.get("command", "").strip()
    if not command:
        return {"error": "缺少必要参数: command", "usage": {"command": "df -h", "timeout": 30}}

    timeout = int(arguments.get("timeout", 30))
    user = arguments.get("user", "agent-read")
    skip_pending = arguments.get("_skip_pending", False)

    # 先做沙箱安全检查（黑名单拦截）
    result = sandbox_execute(command=command, timeout=timeout, user=user)

    # 如果被沙箱拦截，直接返回错误
    if result.get("blocked"):
        return {
            "blocked": True,
            "command": command,
            "reason": result.get("stderr", "命令被安全策略拦截"),
        }

    # 只读命令白名单 → 直接执行沙箱结果（不触发 pending）
    normalized = command.strip()
    if normalized in READ_ONLY_COMMANDS:
        logger.info("[CmdExec] 只读命令直接执行: '%s'", command)
        return result

    # 非只读命令 → 需要 pending_confirmation（除 skip_pending 外）
    if skip_pending:
        logger.info("[CmdExec] 确认后执行: '%s'", command)
        return result

    confirm_id = f"mcp_pending_{uuid.uuid4().hex[:12]}"
    logger.info("[CmdExec] 命令需确认: '%s', confirm_id=%s", command, confirm_id)
    return {
        "_pending_confirmation": True,
        "confirm_id": confirm_id,
        "tool": "cmd_exec",
        "command": command,
        "reason": f"命令执行需要确认: {command[:80]}",
        "pending_args": {"command": command, "timeout": timeout, "user": user, "_skip_pending": True},
    }
