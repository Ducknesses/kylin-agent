"""服务管理插件：systemctl status/start/stop/restart"""
import logging
import subprocess

from config import config

logger = logging.getLogger("mcp.service_mgr")

# 允许的操作
ALLOWED_ACTIONS = ["status", "start", "stop", "restart", "is-active", "is-enabled"]


def _validate_service(service: str) -> tuple:
    """
    校验服务是否在允许列表中
    返回 (is_valid, error_msg)
    """
    # 剥离 .service 后缀
    service_name = service.replace(".service", "").strip()

    # 检查是否在禁止列表中
    for blocked in config.BLOCKED_SERVICES:
        if service_name == blocked or service_name.startswith(blocked + "."):
            return False, f"禁止操作核心系统服务: {service_name}"

    # 检查是否在允许列表中
    if service_name not in config.ALLOWED_SERVICES:
        return False, f"服务不在允许列表中: {service_name}"

    return True, ""


def _execute_systemctl(action: str, service: str) -> dict:
    """执行 systemctl 命令"""
    cmd = ["systemctl", action, service]

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=15,
        )
        return {
            "action": action,
            "service": service,
            "output": result.stdout.strip(),
            "error_output": result.stderr.strip(),
            "exit_code": result.returncode,
            "is_active": result.returncode == 0,
        }
    except subprocess.TimeoutExpired:
        return {
            "action": action,
            "service": service,
            "error": f"systemctl {action} 执行超时",
            "exit_code": -1,
            "is_active": False,
        }
    except FileNotFoundError:
        return {
            "action": action,
            "service": service,
            "error": "systemctl 命令未找到（可能不在系统PATH中）",
            "exit_code": -1,
            "is_active": False,
        }
    except Exception as e:
        logger.exception("systemctl 执行异常: %s", e)
        return {
            "action": action,
            "service": service,
            "error": str(e),
            "exit_code": -1,
            "is_active": False,
        }


def _parse_status_output(output: str) -> dict:
    """解析 systemctl status 输出，提取关键信息"""
    info = {}

    for line in output.split("\n"):
        line = line.strip()
        if line.startswith("Active:"):
            info["active_state"] = line.replace("Active:", "").strip()
        elif line.startswith("Loaded:"):
            info["loaded"] = line.replace("Loaded:", "").strip()
        elif line.startswith("Main PID:"):
            info["main_pid"] = line.replace("Main PID:", "").strip()
        elif line.startswith("CGroup:"):
            info["cgroup"] = line.replace("CGroup:", "").strip()[:200]

    return info


def handle(arguments: dict) -> dict:
    """
    处理服务管理操作

    参数:
        arguments: {
            "action": "status"|"start"|"stop"|"restart",
            "service": "nginx"  # 服务名
        }

    返回:
        服务操作结果
    """
    action = arguments.get("action", "")
    service = arguments.get("service", "")

    # 参数校验
    if not action or not service:
        return {
            "error": "缺少必要参数: action 和 service",
            "usage": {"action": "status|start|stop|restart", "service": "服务名"},
        }

    if action not in ALLOWED_ACTIONS:
        return {
            "error": f"不支持的操作: {action}",
            "allowed_actions": ALLOWED_ACTIONS,
        }

    # 服务名校验
    is_valid, error_msg = _validate_service(service)
    if not is_valid:
        logger.warning("[ServiceMgr] %s", error_msg)
        return {"error": error_msg, "allowed_services": config.ALLOWED_SERVICES}

    # 执行操作
    service_full = service.replace(".service", "") + ".service"
    result = _execute_systemctl(action, service_full)

    # 对 status 操作做额外解析
    if action == "status" and result.get("output"):
        parsed = _parse_status_output(result["output"])
        result["parsed"] = parsed

    return result