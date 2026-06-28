"""MCP-Server 自身状态监控与端口热变更插件

提供以下 metric：
  - status       : 基础运行状态（PID、启动时间、监听地址、注册工具列表）
  - connections  : 自身进程的网络连接（通过 ss 过滤 PID）
  - resources    : 资源使用（内存、CPU、文件描述符、线程数）
  - requests     : 请求统计（总请求数、成功/失败计数）
  - change_port  : 热修改监听端口 & 绑定地址
  - all          : 以上全部

依赖 server.py 暴露的模块级变量：
  - server_instance     : 当前 HTTPServer 对象引用
  - server_start_time   : 启动时间戳
  - request_stats       : 请求统计计数器字典
  - restart_server()    : 热重启 server 的函数
"""
import logging
import os
import time
import subprocess

logger = logging.getLogger("mcp.mcp_self_monitor")

# ============================================================
# 内部引用（由 server.py 在初始化时设置）
# ============================================================
server_instance = None       # HTTPServer 对象
server_start_time = 0.0      # time.time() 启动时间戳
request_stats = {            # 请求统计
    "total": 0,
    "success": 0,
    "errors": 0,
    "last_reset": 0.0,
}
restart_server_cb = None     # 回调函数：restart_server(new_host, new_port)


def _read_proc_file(pid: int, filename: str) -> str:
    """安全读取 /proc/<pid>/<filename>"""
    path = f"/proc/{pid}/{filename}"
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            return f.read()
    except (PermissionError, FileNotFoundError, ProcessLookupError):
        return ""
    except Exception as e:
        logger.debug("读取 %s 失败: %s", path, e)
        return ""


def _format_uptime(seconds: float) -> str:
    """将秒数格式化为可读的 uptime 字符串"""
    days, rem = divmod(int(seconds), 86400)
    hours, rem = divmod(rem, 3600)
    minutes, secs = divmod(rem, 60)
    parts = []
    if days > 0:
        parts.append(f"{days}d")
    if hours > 0:
        parts.append(f"{hours}h")
    if minutes > 0:
        parts.append(f"{minutes}m")
    parts.append(f"{secs}s")
    return " ".join(parts)


def _get_status() -> dict:
    """获取 MCP-Server 基础运行状态"""
    pid = os.getpid()
    uptime_seconds = time.time() - server_start_time if server_start_time else 0

    host = "unknown"
    port = 0
    # 优先从 socket 获取实时监听地址
    if server_instance is not None:
        try:
            sock = server_instance.socket
            if sock is not None:
                addr = sock.getsockname()
                host = addr[0]
                port = addr[1]
        except Exception:
            pass
    # 回退到 server_address
    if host == "unknown" and server_instance is not None:
        try:
            srv_addr = server_instance.server_address
            if srv_addr:
                host, port = srv_addr[0], srv_addr[1]
        except Exception:
            pass

    tools_list = []
    try:
        import sys
        server_mod = sys.modules.get("server")
        if server_mod and hasattr(server_mod, "TOOLS"):
            tools_list = list(getattr(server_mod, "TOOLS").keys())
    except Exception:
        pass

    start_time_str = ""
    if server_start_time:
        start_time_str = time.strftime(
            "%Y-%m-%d %H:%M:%S", time.localtime(server_start_time)
        )

    return {
        "pid": pid,
        "uptime": _format_uptime(uptime_seconds),
        "uptime_seconds": round(uptime_seconds, 1),
        "host": host,
        "port": port,
        "start_time": start_time_str,
        "tools": tools_list,
    }


def _get_connections() -> dict:
    """查询 MCP-Server 进程自身的网络连接（通过 ss -tunlp 过滤 PID）"""
    pid = os.getpid()
    try:
        result = subprocess.run(
            ["ss", "-tunlp"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        lines = result.stdout.strip().split("\n")
        own_connections = []
        for line in lines[1:]:
            line = line.strip()
            if not line:
                continue
            if f"pid={pid}" not in line:
                continue
            parts = line.split()
            if len(parts) >= 5:
                conn = {
                    "netid": parts[0] if len(parts) > 0 else "",
                    "state": parts[1] if len(parts) > 1 else "",
                    "recv_q": parts[2] if len(parts) > 2 else "",
                    "send_q": parts[3] if len(parts) > 3 else "",
                    "local": parts[4] if len(parts) > 4 else "",
                    "peer": parts[5] if len(parts) > 5 else "",
                    "process": " ".join(parts[6:]) if len(parts) > 6 else "",
                }
                own_connections.append(conn)

        return {
            "total": len(own_connections),
            "connections": own_connections,
        }
    except FileNotFoundError:
        return {"error": "ss 命令不可用"}
    except subprocess.TimeoutExpired:
        return {"error": "ss 命令执行超时"}
    except Exception as e:
        logger.exception("获取自身连接信息失败: %s", e)
        return {"error": str(e)}


def _get_resources() -> dict:
    """查询 MCP-Server 进程资源使用情况"""
    pid = os.getpid()
    result = {
        "memory_rss_mb": 0.0,
        "memory_vms_mb": 0.0,
        "cpu_percent": 0.0,
        "threads": 0,
        "open_fds": 0,
        "open_fds_limit": 0,
        "state": "",
    }

    status_content = _read_proc_file(pid, "status")
    if status_content:
        for line in status_content.split("\n"):
            line = line.strip()
            if line.startswith("VmRSS:"):
                parts = line.split()
                if len(parts) >= 2:
                    try:
                        result["memory_rss_mb"] = round(int(parts[1]) / 1024, 1)
                    except ValueError:
                        pass
            elif line.startswith("VmSize:"):
                parts = line.split()
                if len(parts) >= 2:
                    try:
                        result["memory_vms_mb"] = round(int(parts[1]) / 1024, 1)
                    except ValueError:
                        pass
            elif line.startswith("Threads:"):
                parts = line.split()
                if len(parts) >= 2:
                    try:
                        result["threads"] = int(parts[1])
                    except ValueError:
                        pass
            elif line.startswith("State:"):
                result["state"] = line[6:].strip()

    try:
        ps_result = subprocess.run(
            ["ps", "-p", str(pid), "-o", "%cpu=,%mem="],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if ps_result.returncode == 0 and ps_result.stdout.strip():
            parts = ps_result.stdout.strip().split()
            if len(parts) >= 1:
                try:
                    result["cpu_percent"] = float(parts[0])
                except ValueError:
                    pass
            if len(parts) >= 2:
                try:
                    result["mem_percent"] = float(parts[1])
                except ValueError:
                    pass
    except Exception:
        pass

    fd_dir = f"/proc/{pid}/fd"
    try:
        fds = os.listdir(fd_dir)
        result["open_fds"] = len(fds)
    except (PermissionError, FileNotFoundError):
        result["open_fds"] = -1

    try:
        import resource
        soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
        result["open_fds_limit"] = soft
    except Exception:
        pass

    return result


def _get_requests() -> dict:
    """获取请求统计"""
    total = request_stats.get("total", 0)
    success = request_stats.get("success", 0)
    errors = request_stats.get("errors", 0)
    last_reset = request_stats.get("last_reset", 0)

    success_rate = 0.0
    if total > 0:
        success_rate = round(success / total * 100, 1)

    return {
        "total": total,
        "success": success,
        "errors": errors,
        "success_rate_percent": success_rate,
        "since": time.strftime(
            "%Y-%m-%d %H:%M:%S", time.localtime(last_reset)
        ) if last_reset else "N/A",
    }


def _change_port(arguments: dict) -> dict:
    """热修改监听端口 & 地址"""
    if restart_server_cb is None:
        return {"error": "热重启回调未注册，server 不支持端口变更"}

    new_host = arguments.get("host")
    new_port = arguments.get("port")

    if new_host is None and new_port is None:
        return {
            "error": "至少需要提供 host 或 port 参数之一",
            "usage": "提供 host, port 或两者都提供",
        }

    if new_port is not None:
        try:
            new_port = int(new_port)
            if new_port < 1 or new_port > 65535:
                return {"error": f"端口号无效: {new_port}，有效范围 1-65535"}
            if new_port < 1024:
                return {"error": f"端口 {new_port} 是特权端口（< 1024），请使用非特权端口"}
        except (ValueError, TypeError):
            return {"error": f"端口号格式无效: {new_port}"}

    if new_host is not None:
        new_host = str(new_host).strip()
        if not new_host:
            return {"error": "host 不能为空"}

    try:
        result = restart_server_cb(new_host, new_port)
        return result
    except Exception as e:
        logger.exception("端口变更失败: %s", e)
        return {"error": f"端口变更失败: {str(e)}"}


def handle(arguments: dict) -> dict:
    """
    处理 MCP-Server 自身状态监控请求

    参数:
        arguments: {
            "metric": "status"|"connections"|"resources"|"requests"|"change_port"|"all",
            "host": "0.0.0.0",       # 仅 change_port 时使用
            "port": 9090             # 仅 change_port 时使用
        }

    返回:
        MCP-Server 自身监控数据
    """
    metric = arguments.get("metric", "status")
    result = {}

    if metric == "change_port":
        return _change_port(arguments)

    if metric in ("status", "all"):
        result["status"] = _get_status()

    if metric in ("connections", "all"):
        result["connections"] = _get_connections()

    if metric in ("resources", "all"):
        result["resources"] = _get_resources()

    if metric in ("requests", "all"):
        result["requests"] = _get_requests()

    if not result:
        return {
            "error": f"未知的 metric: {metric}",
            "available": ["status", "connections", "resources", "requests", "change_port", "all"],
        }

    return result
