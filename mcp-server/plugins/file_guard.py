"""文件保护插件：检查文件路径安全性，安全读取/写入文件"""
import hashlib
import json
import logging
import os
from datetime import datetime

from config import config

logger = logging.getLogger("mcp.file_guard")

# 最大读取字节数（默认1MB）
MAX_READ_SIZE = 1 * 1024 * 1024

# 允许写入的路径白名单
ALLOWED_WRITE_PATHS = ["/tmp/", "/opt/mcp-server/"]

# 审计日志路径
AUDIT_LOG = "/var/log/mcp-file-guard.log"


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


def _write_audit(action: str, path: str, user: str = "agent", result: str = ""):
    """写入审计日志（JSON 行格式，追加到 /var/log/mcp-file-guard.log）"""
    try:
        entry = {
            "timestamp": datetime.now().isoformat(),
            "action": action,
            "path": path,
            "user": user,
            "result": result
        }
        audit_dir = os.path.dirname(AUDIT_LOG)
        if audit_dir and not os.path.exists(audit_dir):
            os.makedirs(audit_dir, exist_ok=True)
        with open(AUDIT_LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception:
        pass


def _write_file_safe(path: str, content: str) -> dict:
    """安全写入文件（仅允许 /tmp/ 和 /opt/mcp-server/ 下的文件）"""
    # 保护检查
    is_protected, reason = _is_protected(path)
    if is_protected:
        _write_audit("write", path, result="rejected_protected")
        return {
            "error": f"文件受保护: {reason}",
            "path": path,
            "risk_level": "high"
        }

    try:
        real_path = os.path.realpath(path)
    except Exception:
        return {"error": f"路径无法解析: {path}"}

    # 写入路径白名单校验
    allowed = any(real_path.startswith(d) for d in ALLOWED_WRITE_PATHS)
    if not allowed:
        _write_audit("write", real_path, result="rejected_not_allowed")
        return {
            "error": "写入路径不在允许范围内（仅 /tmp/ 和 /opt/mcp-server/）",
            "path": real_path,
            "risk_level": "high"
        }

    # 预检查：计算变更前文件哈希摘要
    old_hash = ""
    if os.path.exists(real_path):
        try:
            with open(real_path, "rb") as f:
                old_hash = hashlib.md5(f.read()).hexdigest()
        except Exception:
            pass

    # 执行写入
    try:
        parent_dir = os.path.dirname(real_path)
        if parent_dir and not os.path.exists(parent_dir):
            os.makedirs(parent_dir, exist_ok=True)
        with open(real_path, "w", encoding="utf-8") as f:
            f.write(content)

        new_hash = hashlib.md5(content.encode("utf-8")).hexdigest()
        summary = f"changed {old_hash[:8]}->{new_hash[:8]}" if old_hash else f"created {new_hash[:8]}"
        _write_audit("write", real_path, result=summary)

        return {
            "path": real_path,
            "bytes_written": len(content.encode("utf-8")),
            "old_hash_prefix": old_hash[:8] if old_hash else None,
            "new_hash_prefix": new_hash[:8],
            "risk_level": "medium"
        }
    except Exception as e:
        _write_audit("write", real_path, result=f"error: {e}")
        return {"error": str(e), "path": real_path}


def handle(arguments: dict) -> dict:
    """
    文件保护插件入口

    参数:
        arguments: {
            "action": "check" | "read" | "write",
            "path": "/tmp/test.txt",     # 文件路径
            "content": "hello world",    # 写入内容（write 操作需要）
            "max_size": 1048576          # 最大读取字节数（可选，默认1MB）
        }

    返回:
        文件检查结果 / 文件内容 / 写入结果
    """
    action = arguments.get("action", "check")
    path = arguments.get("path", "")

    if not path:
        return {
            "error": "缺少必要参数: path",
            "usage": {
                "action": "check|read|write",
                "path": "/tmp/test.txt",
                "content": "写入内容（write 操作）",
                "max_size": 1048576
            },
        }

    if action == "check":
        _write_audit("check", path, result="allowed")
        return _check_path(path)

    elif action == "read":
        _write_audit("read", path, result="allowed")
        max_size = int(arguments.get("max_size", MAX_READ_SIZE))
        return _read_file_safe(path, max_size=max_size)

    elif action == "write":
        content = arguments.get("content")
        if content is None:
            return {"error": "write 操作需要 content 参数", "path": path}
        return _write_file_safe(path, content)

    else:
        return {
            "error": f"不支持的操作: {action}",
            "allowed_actions": ["check", "read", "write"],
        }
