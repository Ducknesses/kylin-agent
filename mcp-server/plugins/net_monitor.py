"""网络监控插件：ss / ip / proc/net/dev"""
import logging
import os
import re
import subprocess

logger = logging.getLogger("mcp.net_monitor")


def _read_file(path: str) -> str:
    """安全读取文件"""
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            return f.read()
    except (PermissionError, FileNotFoundError):
        return ""


def _get_connections() -> dict:
    """通过 ss 命令获取网络连接"""
    try:
        result = subprocess.run(
            ["ss", "-tunlp"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        lines = result.stdout.strip().split("\n")

        connections = []
        for line in lines[1:]:  # 跳过标题行
            line = line.strip()
            if not line:
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
                connections.append(conn)

        return {
            "total": len(connections),
            "connections": connections[:200],  # 限制返回数量
        }
    except FileNotFoundError:
        return {"error": "ss 命令不可用"}
    except subprocess.TimeoutExpired:
        return {"error": "ss 命令执行超时"}
    except Exception as e:
        logger.exception("获取连接信息失败: %s", e)
        return {"error": str(e)}


def _get_traffic() -> dict:
    """从 /proc/net/dev 读取网络流量"""
    content = _read_file("/proc/net/dev")
    if not content:
        return {"error": "无法读取 /proc/net/dev"}

    interfaces = {}
    for line in content.strip().split("\n")[2:]:  # 跳过前两行标题
        if ":" not in line:
            continue
        iface, stats = line.split(":", 1)
        iface = iface.strip()
        parts = stats.split()
        if len(parts) >= 10:
            interfaces[iface] = {
                "rx_bytes": int(parts[0]),
                "rx_packets": int(parts[1]),
                "rx_errors": int(parts[2]),
                "rx_dropped": int(parts[3]),
                "tx_bytes": int(parts[8]),
                "tx_packets": int(parts[9]),
                "tx_errors": int(parts[10]),
                "tx_dropped": int(parts[11]),
            }

    return {"interfaces": interfaces}


def _get_interfaces() -> dict:
    """通过 ip addr 获取网卡信息"""
    try:
        result = subprocess.run(
            ["ip", "addr"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        return {"output": result.stdout, "error": result.stderr}
    except FileNotFoundError:
        return {"error": "ip 命令不可用"}
    except subprocess.TimeoutExpired:
        return {"error": "ip 命令执行超时"}
    except Exception as e:
        logger.exception("获取网卡信息失败: %s", e)
        return {"error": str(e)}


def _get_routes() -> dict:
    """通过 ip route 获取路由表"""
    try:
        result = subprocess.run(
            ["ip", "route"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        return {"output": result.stdout, "error": result.stderr}
    except FileNotFoundError:
        return {"error": "ip 命令不可用"}
    except subprocess.TimeoutExpired:
        return {"error": "ip route 执行超时"}
    except Exception as e:
        logger.exception("获取路由信息失败: %s", e)
        return {"error": str(e)}


def _get_dns() -> dict:
    """读取 DNS 配置"""
    content = _read_file("/etc/resolv.conf")
    if not content:
        return {"error": "无法读取 /etc/resolv.conf"}

    nameservers = []
    for line in content.strip().split("\n"):
        line = line.strip()
        if line.startswith("nameserver"):
            parts = line.split()
            if len(parts) >= 2:
                nameservers.append(parts[1])

    return {"nameservers": nameservers, "raw": content}


def _get_listeners(port: int = None) -> dict:
    """通过 ss -tlnp 获取监听端口"""
    try:
        cmd = ["ss", "-tlnp"]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        lines = result.stdout.strip().split("\n")
        listeners = []
        for line in lines[1:]:  # 跳过标题行
            line = line.strip()
            if not line:
                continue
            parts = line.split()
            if len(parts) >= 4:
                state = parts[0]
                local = parts[3]
                process = " ".join(parts[5:]) if len(parts) > 5 else ""
                if port is not None and f":{port}" not in local:
                    continue
                listeners.append({
                    "state": state,
                    "local": local,
                    "process": process
                })
        return {"total": len(listeners), "listeners": listeners[:200]}
    except FileNotFoundError:
        return {"error": "ss 命令不可用"}
    except subprocess.TimeoutExpired:
        return {"error": "ss 命令执行超时"}
    except Exception as e:
        logger.exception("获取监听端口失败: %s", e)
        return {"error": str(e)}


def handle(arguments: dict) -> dict:
    """
    处理网络监控请求

    参数:
        arguments: {
            "metric": "connections"|"traffic"|"interfaces"|"routes"|"dns"|"listen"|"all",
            "port": 80              # 可选，仅 listen 模式筛选特定端口
        }

    返回:
        网络监控数据
    """
    metric = arguments.get("metric", "all")
    port = arguments.get("port")

    result = {}

    if metric in ("connections", "all"):
        result["connections"] = _get_connections()

    if metric in ("traffic", "all"):
        result["traffic"] = _get_traffic()

    if metric in ("interfaces", "all"):
        result["interfaces"] = _get_interfaces()

    if metric in ("routes", "all"):
        result["routes"] = _get_routes()

    if metric in ("dns", "all"):
        result["dns"] = _get_dns()

    if metric in ("listen", "all"):
        result["listeners"] = _get_listeners(port=port)

    if not result:
        return {
            "error": f"未知的metric: {metric}",
            "available": ["connections", "traffic", "interfaces", "routes", "dns", "listen", "all"],
        }

    return result
