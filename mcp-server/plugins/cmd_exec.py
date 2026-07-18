"""命令执行沙箱插件：通过sandbox引擎安全执行系统命令"""
import logging
import uuid

from sandbox import execute as sandbox_execute, _match_command_pattern

logger = logging.getLogger("mcp.cmd_exec")


def handle(arguments: dict) -> dict:
    """
    在沙箱中安全执行系统命令

    参数:
        arguments: {
            "command": "df -h",       # 要执行的命令（白名单直接执行，其余需二次确认）
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
        return {"blocked": True, "error": "缺少必要参数: command", "usage": {"command": "df -h", "timeout": 30}}

    timeout = int(arguments.get("timeout", 30))
    user = arguments.get("user", "agent-read")
    skip_pending = arguments.get("_skip_pending", False)
    normalized = command.strip()

    # 白名单命令 → 直接执行（不触发 pending）
    matched_pattern = _match_command_pattern(normalized)
    if matched_pattern:
        result = sandbox_execute(command=command, timeout=timeout, user=user)
        if result.get("blocked"):
            return {
                "blocked": True,
                "command": command,
                "reason": result.get("stderr", "命令被安全策略拦截"),
            }
        logger.info("[CmdExec] 白名单命令直接执行: '%s' (匹配规则: %s)", command, matched_pattern)
        return result

    # skip_pending → 用户已确认，执行命令
    if skip_pending:
        result = sandbox_execute(command=command, timeout=timeout, user=user)
        if result.get("blocked"):
            return {
                "blocked": True,
                "command": command,
                "reason": result.get("stderr", "命令被安全策略拦截"),
            }
        logger.info("[CmdExec] 确认后执行: '%s'", command)
        return result

    # 非只读、未确认 → 返回 pending_confirmation，不执行命令
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
