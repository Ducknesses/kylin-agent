"""命令执行沙箱插件：通过sandbox引擎安全执行系统命令

所有安全确认已由 backend SafetyGuard 统一处理，本插件仅负责执行。
"""
import logging

from sandbox import execute as sandbox_execute

logger = logging.getLogger("mcp.cmd_exec")


def handle(arguments: dict) -> dict:
    """
    在沙箱中安全执行系统命令

    参数:
        arguments: {
            "command": "df -h",       # 要执行的命令
            "timeout": 30,             # 超时秒数（可选，默认30）
            "user": "agent-read"       # 执行用户（可选，默认agent-read）
        }

    返回:
        {
            "stdout": str,
            "stderr": str,
            "returncode": int,
            "execution_time": float,
            "blocked": bool           # 是否被安全策略拦截
        }
    """
    command = arguments.get("command", "").strip()
    if not command:
        return {"blocked": True, "error": "缺少必要参数: command", "usage": {"command": "df -h", "timeout": 30}}

    timeout = int(arguments.get("timeout", 30))
    user = arguments.get("user", "agent-read")

    result = sandbox_execute(command=command, timeout=timeout, user=user)
    if result.get("blocked"):
        return {
            "blocked": True,
            "command": command,
            "reason": result.get("stderr", "命令被安全策略拦截"),
        }
    logger.info("[CmdExec] 执行命令: '%s'", command)
    return result