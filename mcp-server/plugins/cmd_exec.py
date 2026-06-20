"""命令执行沙箱插件：通过sandbox引擎安全执行系统命令"""
import logging

from sandbox import execute as sandbox_execute

logger = logging.getLogger("mcp.cmd_exec")


def handle(arguments: dict) -> dict:
    """
    在沙箱中安全执行系统命令

    参数:
        arguments: {
            "command": "df -h",       # 要执行的命令（必须在白名单中）
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
        return {"error": "缺少必要参数: command", "usage": {"command": "df -h", "timeout": 30}}

    timeout = int(arguments.get("timeout", 30))
    user = arguments.get("user", "agent-read")

    logger.info("[CmdExec] 请求执行: '%s' (user=%s, timeout=%ds)", command, user, timeout)

    result = sandbox_execute(command=command, timeout=timeout, user=user)

    # 如果被拦截，返回错误格式
    if result.get("blocked"):
        return {
            "blocked": True,
            "command": command,
            "reason": result.get("stderr", "命令被安全策略拦截"),
        }

    return result