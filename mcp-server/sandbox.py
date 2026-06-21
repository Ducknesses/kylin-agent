"""沙箱执行引擎：命令白名单校验、危险模式过滤、超时控制、用户降级"""
import logging
import os
import re
import subprocess
import time

from config import config

logger = logging.getLogger("mcp.sandbox")


def _match_command_pattern(cmd: str) -> str:
    """
    检查命令是否匹配白名单中的某个模式
    支持参数化匹配，如 "systemctl status {service}" 匹配 "systemctl status nginx"
    返回匹配到的白名单模式字符串，未匹配返回 None
    """
    cmd_clean = cmd.strip()

    for pattern in config.ALLOWED_COMMANDS:
        # 先尝试精确匹配（不含占位符的固定命令）
        if "{" not in pattern and "}" not in pattern:
            if cmd_clean == pattern:
                return pattern
            continue

        # 参数化匹配：将占位符 {xxx} 替换为捕获任意内容的非空正则
        escaped = re.escape(pattern)
        # re.escape 会把 { 转成 \{ ，需要转回来
        escaped = escaped.replace(r"\{", "{").replace(r"\}", "}")
        # 将 {word} 替换为 .+ （非空匹配）
        regex_str = re.sub(r"\{[^}]+\}", r".+", escaped)
        regex_str = "^" + regex_str + "$"

        try:
            if re.match(regex_str, cmd_clean):
                return pattern
        except re.error:
            logger.error("白名单正则编译失败: pattern=%s, regex=%s", pattern, regex_str)
            continue

    return None


def _check_danger_patterns(cmd: str) -> tuple:
    """
    检查命令中是否包含危险Shell元字符
    返回 (is_safe, blocked_char)
    """
    for danger in config.DANGER_PATTERNS:
        if danger in cmd:
            return False, danger
    return True, ""


def _check_protected_paths(cmd: str) -> tuple:
    """
    检查命令参数中是否包含受保护的文件路径
    返回 (is_safe, blocked_path)
    """
    for path in config.PROTECTED_PATHS:
        # 检查命令中是否包含完整路径或其前缀
        if path in cmd:
            # 额外检查：判断是读操作还是写操作
            # 白名单中只有 cat/head/tail/ls 可读，其余操作禁止访问保护路径
            cmd_parts = cmd.strip().split()
            if cmd_parts:
                base_cmd = os.path.basename(cmd_parts[0])
                read_only_cmds = ("cat", "head", "tail", "ls", "file")
                if base_cmd not in read_only_cmds:
                    return False, path
            # 即使是读操作，也检查保护路径（完全禁止访问）
            return False, path

    # 检查敏感扩展名（.pem, .key 等）
    for ext in config.PROTECTED_EXTENSIONS:
        if ext in cmd.lower():
            return False, ext

    return True, ""


def _validate_user(user: str) -> str:
    """校验执行用户是否在允许列表中"""
    if user not in config.ALLOWED_USERS:
        logger.warning("用户 %s 不在允许列表，降级为 agent-read", user)
        return "agent-read"
    return user


def _build_safe_cmd(command: str, matched_pattern: str) -> list:
    """
    将命令字符串解析为 subprocess 可直接使用的列表（避免shell注入）
    """
    # 取命令的第一个词作为可执行文件
    parts = command.strip().split()
    return parts


def execute(command: str, timeout: int = 30, user: str = "agent-read") -> dict:
    """
    在沙箱中安全执行命令

    参数:
        command: 要执行的命令字符串
        timeout: 超时秒数，默认30
        user:   执行用户，默认 agent-read

    返回:
        {
            "stdout": str,      # 标准输出
            "stderr": str,      # 标准错误
            "returncode": int,  # 退出码
            "execution_time": float,  # 执行耗时(秒)
        }
    """
    start_time = time.time()

    # ==== 第1步：白名单校验 ====
    matched = _match_command_pattern(command)
    if not matched:
        msg = f"命令不在白名单中: {command}"
        logger.warning("[Sandbox] %s", msg)
        return {
            "stdout": "",
            "stderr": msg,
            "returncode": -1,
            "execution_time": round(time.time() - start_time, 3),
            "blocked": True,
        }

    # ==== 第2步：危险模式过滤 ====
    safe, blocked = _check_danger_patterns(command)
    if not safe:
        msg = f"命令包含危险字符 '{blocked}': {command}"
        logger.warning("[Sandbox] %s", msg)
        return {
            "stdout": "",
            "stderr": msg,
            "returncode": -1,
            "execution_time": round(time.time() - start_time, 3),
            "blocked": True,
        }

    # ==== 第3步：敏感文件保护 ====
    safe, blocked_path = _check_protected_paths(command)
    if not safe:
        msg = f"命令访问受保护路径 '{blocked_path}': {command}"
        logger.warning("[Sandbox] %s", msg)
        return {
            "stdout": "",
            "stderr": msg,
            "returncode": -1,
            "execution_time": round(time.time() - start_time, 3),
            "blocked": True,
        }

    # ==== 第4步：用户校验 ====
    user = _validate_user(user)

    # ==== 第5步：构建执行命令 ====
    cmd_parts = _build_safe_cmd(command, matched)
    actual_timeout = min(timeout, config.COMMAND_TIMEOUT)

    # 如果当前不是root，不需要sudo降级；否则用sudo -u降级
    current_uid = os.getuid() if hasattr(os, "getuid") else 0
    if current_uid == 0 and user != "root":
        exec_cmd = ["sudo", "-u", user] + cmd_parts
    else:
        exec_cmd = cmd_parts

    # ==== 第6步：执行命令 ====
    logger.info("[Sandbox] 执行: %s (user=%s, timeout=%ds)", command, user, actual_timeout)

    try:
        result = subprocess.run(
            exec_cmd,
            capture_output=True,
            text=True,
            timeout=actual_timeout,
            # 不在shell中执行，避免注入
            shell=False,
        )
    except subprocess.TimeoutExpired:
        elapsed = round(time.time() - start_time, 3)
        logger.warning("[Sandbox] 命令超时: %s (%.1fs)", command, elapsed)
        return {
            "stdout": "",
            "stderr": f"命令执行超时（{actual_timeout}秒）: {command}",
            "returncode": -1,
            "execution_time": elapsed,
            "blocked": True,
        }
    except FileNotFoundError:
        elapsed = round(time.time() - start_time, 3)
        logger.error("[Sandbox] 命令未找到: %s", exec_cmd[0])
        return {
            "stdout": "",
            "stderr": f"命令未找到: {exec_cmd[0]}",
            "returncode": -1,
            "execution_time": elapsed,
            "blocked": True,
        }
    except Exception as e:
        elapsed = round(time.time() - start_time, 3)
        logger.exception("[Sandbox] 命令执行异常: %s", e)
        return {
            "stdout": "",
            "stderr": f"执行异常: {str(e)}",
            "returncode": -1,
            "execution_time": elapsed,
            "blocked": True,
        }

    elapsed = round(time.time() - start_time, 3)

    # 截断输出，防止OOM
    stdout = result.stdout or ""
    stderr = result.stderr or ""

    if len(stdout) > config.MAX_OUTPUT_LINES * 200:
        stdout_lines = stdout.split("\n")
        stdout = "\n".join(stdout_lines[: config.MAX_OUTPUT_LINES])
        stdout += f"\n... [截断，原始行数: {len(stdout_lines)}]"

    logger.info(
        "[Sandbox] 执行完成: exit=%d, stdout=%d, stderr=%d, time=%.3fs",
        result.returncode,
        len(stdout),
        len(stderr),
        elapsed,
    )

    return {
        "stdout": stdout,
        "stderr": stderr,
        "returncode": result.returncode,
        "execution_time": elapsed,
    }