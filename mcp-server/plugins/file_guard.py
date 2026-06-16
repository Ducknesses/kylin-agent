"""文件保护插件：检查文件路径安全性，安全读取文件"""
import logging
import os

from config import config

logger = logging.getLogger("mcp.file_guard")

# 最大读取字节数（默认1MB）
MAX_READ_SIZE = 1 * 1024 * 1024


def _is_protected(path: str) -> tuple:
    """
    检查路径是否受保护
    返回 (is_protected, reason)
    """
    try:
        real_path = os.path.realpath(path)
    except Exception:
        return True, f"无法解析路径: {path}"

    # 检查是否在受保护路径列表中
    for protected in config.PROTECTED_PATHS:
        if real_path == protected or real_path.startswith(protected.rstrip("/") + "/"):
            return True, f"路径在保护清单中: {protected}"

    # 检查敏感扩展名
    basename = os.path.basename(real_path).lower()
    for ext in config.PROTECTED_EXTENSIONS:
        if basename.endswith(ext):
            return True, f"敏感文件类型: {ext}"

    return False, ""


def _check_path(path: str) -> dict:
    """检查文件路径状态"""
    is_protected, reason = _is_protected(path)

    result = {
        "path": path,
        "is_protected": is_protected,
        "reason": reason if is_protected else "路径安全",
    }

    try:
        real_path = os.path.realpath(path)
        result["real_path"] = real_path

        if os.path.exists(real_path):
            stat = os.stat(real_path)
            result["exists"] = True
            result["is_file"] = os.path.isfile(real_path)
            result["is_dir"] = os.path.isdir(real_path)
            result["size"] = stat.st_size
            result["permissions"] = oct(stat.st_mode)[-3:]
        else:
            result["exists"] = False
    except PermissionError:
        result["exists"] = False
        result["error"] = "权限不足，无法访问"
    except Exception as e:
        result["exists"] = False
        result["error"] = str(e)

    return result


def _read_file_safe(path: str, max_size: int = MAX_READ_SIZE) -> dict:
    """安全读取文件内容（只读）"""
    # 先做保护检查
    is_protected, reason = _is_protected(path)
    if is_protected:
        return {
            "error": f"文件受保护: {reason}",
            "path": path,
        }

    try:
        real_path = os.path.realpath(path)
    except Exception:
        return {"error": f"路径不存在或无法解析: {path}"}

    if not os.path.exists(real_path):
        return {"error": f"文件不存在: {path}"}

    if not os.path.isfile(real_path):
        return {"error": f"不是普通文件: {path}"}

    # 检查文件大小
    try:
        file_size = os.path.getsize(real_path)
    except OSError:
        return {"error": f"无法获取文件大小: {path}"}

    if file_size > max_size:
        return {
            "error": f"文件过大({file_size}字节)，超过限制({max_size}字节)",
            "path": real_path,
            "size": file_size,
        }

    # 读取文件
    try:
        with open(real_path, "r", encoding="utf-8", errors="ignore") as f:
            content = f.read()

        return {
            "path": path,
            "real_path": real_path,
            "size": file_size,
            "lines": len(content.split("\n")) if content else 0,
            "content": content,
        }
    except PermissionError:
        return {"error": f"权限不足: {path}"}
    except UnicodeDecodeError:
        # 二进制文件，返回元数据
        return {
            "path": path,
            "real_path": real_path,
            "size": file_size,
            "note": "文件可能是二进制格式，未返回内容",
        }
    except Exception as e:
        logger.exception("读取文件异常: %s", e)
        return {"error": str(e)}


def handle(arguments: dict) -> dict:
    """
    文件保护插件入口

    参数:
        arguments: {
            "action": "check" | "read",
            "path": "/etc/hosts",       # 要检查/读取的文件路径
            "max_size": 1048576          # 最大读取字节数（可选，默认1MB）
        }

    返回:
        文件检查结果或文件内容
    """
    action = arguments.get("action", "check")
    path = arguments.get("path", "")

    if not path:
        return {
            "error": "缺少必要参数: path",
            "usage": {"action": "check|read", "path": "/etc/hosts", "max_size": 1048576},
        }

    if action == "check":
        return _check_path(path)
    elif action == "read":
        max_size = int(arguments.get("max_size", MAX_READ_SIZE))
        return _read_file_safe(path, max_size=max_size)
    else:
        return {
            "error": f"不支持的操作: {action}",
            "allowed_actions": ["check", "read"],
            "note": "写操作被完全禁止",
        }