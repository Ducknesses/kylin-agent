"""系统信息采集插件：CPU/内存/磁盘/负载"""
import logging
import os
import re
import time

logger = logging.getLogger("mcp.sys_info")


def _read_file(path: str) -> str:
    """安全读取文件内容"""
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            return f.read()
    except (PermissionError, FileNotFoundError):
        return ""


def _parse_proc_stat_cpu() -> tuple:
    """解析 /proc/stat 第一行 cpu 的 (total, idle) jiffies 值"""
    content = _read_file("/proc/stat")
    lines = content.strip().split("\n")
    for line in lines:
        if line.startswith("cpu "):
            values = line.split()[1:]
            values = [int(v) for v in values]
            # idle = idle + iowait（iowait 也算空闲）
            idle = values[3] + (values[4] if len(values) > 4 else 0)
            total = sum(values)
            return total, idle
    return None, None


def _get_cpu_info() -> dict:
    """通过两次采样 /proc/stat 计算瞬时 CPU 使用率"""
    try:
        cpu_count = os.cpu_count() or 1

        # 第一次采样
        total1, idle1 = _parse_proc_stat_cpu()
        if total1 is None:
            return {"error": "无法解析 /proc/stat"}

        # 等待一小段时间后第二次采样
        time.sleep(0.5)

        total2, idle2 = _parse_proc_stat_cpu()
        if total2 is None:
            return {"error": "无法解析 /proc/stat（第二次采样）"}

        # 计算 delta 得到瞬时使用率
        delta_total = total2 - total1
        delta_idle = idle2 - idle1
        if delta_total > 0:
            cpu_percent = round(((delta_total - delta_idle) / delta_total) * 100, 1)
        else:
            cpu_percent = 0.0

        load_avg = _get_load_avg()

        return {
            "cpu_count": cpu_count,
            "cpu_total_jiffies": total2,
            "cpu_idle_jiffies": idle2,
            "cpu_used_jiffies": total2 - idle2,
            "cpu_percent_snapshot": cpu_percent,
            "load_avg": load_avg,
        }

    except Exception as e:
        logger.exception("获取CPU信息失败: %s", e)
        return {"error": str(e)}


def _get_load_avg() -> list:
    """读取 /proc/loadavg 获取负载"""
    try:
        content = _read_file("/proc/loadavg")
        parts = content.strip().split()
        return [float(parts[0]), float(parts[1]), float(parts[2])]
    except Exception:
        return [0.0, 0.0, 0.0]


def _get_memory_info() -> dict:
    """从 /proc/meminfo 读取内存信息"""
    try:
        content = _read_file("/proc/meminfo")
        mem = {}

        for line in content.strip().split("\n"):
            if ":" in line:
                key, value = line.split(":", 1)
                # 提取数字部分（单位都是kB）
                numbers = re.findall(r"\d+", value)
                if numbers:
                    mem[key.strip()] = int(numbers[0])

        total_kb = mem.get("MemTotal", 0)
        free_kb = mem.get("MemFree", 0)
        available_kb = mem.get("MemAvailable", mem.get("MemFree", 0))
        buffers_kb = mem.get("Buffers", 0)
        cached_kb = mem.get("Cached", 0)
        used_kb = total_kb - available_kb
        percent = round((used_kb / total_kb * 100) if total_kb > 0 else 0, 1)

        swap_total = mem.get("SwapTotal", 0)
        swap_free = mem.get("SwapFree", 0)
        swap_used = swap_total - swap_free

        return {
            "total_mb": round(total_kb / 1024, 0),
            "used_mb": round(used_kb / 1024, 0),
            "free_mb": round(free_kb / 1024, 0),
            "available_mb": round(available_kb / 1024, 0),
            "buffers_mb": round(buffers_kb / 1024, 0),
            "cached_mb": round(cached_kb / 1024, 0),
            "percent": percent,
            "swap_total_mb": round(swap_total / 1024, 0),
            "swap_used_mb": round(swap_used / 1024, 0),
        }
    except Exception as e:
        logger.exception("获取内存信息失败: %s", e)
        return {"error": str(e)}


def _get_disk_info() -> list:
    """通过 df 命令获取磁盘使用情况"""
    import subprocess

    try:
        result = subprocess.run(
            ["df", "-h", "-x", "tmpfs", "-x", "devtmpfs", "-x", "squashfs"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode != 0:
            return [{"error": result.stderr.strip()}]

        lines = result.stdout.strip().split("\n")
        if len(lines) < 2:
            return []

        disks = []
        # 跳过标题行
        for line in lines[1:]:
            parts = line.split()
            if len(parts) >= 6:
                disks.append(
                    {
                        "filesystem": parts[0],
                        "size": parts[1],
                        "used": parts[2],
                        "available": parts[3],
                        "percent": parts[4],
                        "mount_point": parts[5],
                    }
                )
        return disks
    except subprocess.TimeoutExpired:
        return [{"error": "df 命令超时"}]
    except Exception as e:
        logger.exception("获取磁盘信息失败: %s", e)
        return [{"error": str(e)}]


def _get_network_info() -> dict:
    """从 /proc/net/dev 读取网络接口收发字节数（所有接口汇总）"""
    try:
        content = _read_file("/proc/net/dev")
        if not content:
            return {"bytes_recv": 0, "bytes_sent": 0, "error": "无法读取 /proc/net/dev"}

        bytes_recv = 0
        bytes_sent = 0
        for line in content.strip().split("\n")[2:]:  # 跳过前两行标题
            parts = line.split()
            if len(parts) >= 10:
                try:
                    bytes_recv += int(parts[1])   # Receive bytes
                    bytes_sent += int(parts[9])   # Transmit bytes
                except (ValueError, IndexError):
                    continue

        return {"bytes_recv": bytes_recv, "bytes_sent": bytes_sent}
    except Exception as e:
        logger.exception("获取网络信息失败: %s", e)
        return {"bytes_recv": 0, "bytes_sent": 0, "error": str(e)}


def _get_uptime() -> dict:
    """读取系统运行时间"""
    try:
        content = _read_file("/proc/uptime")
        if not content:
            return {"uptime_seconds": 0}

        parts = content.strip().split()
        uptime_seconds = float(parts[0])

        days = int(uptime_seconds // 86400)
        hours = int((uptime_seconds % 86400) // 3600)
        minutes = int((uptime_seconds % 3600) // 60)

        return {
            "uptime_seconds": round(uptime_seconds, 0),
            "uptime_str": f"{days}天{hours}小时{minutes}分钟",
        }
    except Exception:
        return {"uptime_seconds": 0, "uptime_str": "unknown"}


def handle(arguments: dict) -> dict:
    """
    处理系统信息查询

    参数:
        arguments: {"metric": "cpu"|"memory"|"disk"|"load"|"all"}

    返回:
        对应的系统指标数据
    """
    metric = arguments.get("metric", "all")

    result = {}

    if metric in ("cpu", "all"):
        result["cpu"] = _get_cpu_info()

    if metric in ("memory", "all"):
        result["memory"] = _get_memory_info()

    if metric in ("disk", "all"):
        result["disk"] = _get_disk_info()

    if metric in ("load", "all"):
        result["load"] = {"load_avg": _get_load_avg()}

    if metric in ("uptime", "all"):
        result["uptime"] = _get_uptime()

    if metric in ("network", "all"):
        result["network"] = _get_network_info()

    if not result:
        return {"error": f"未知的metric: {metric}", "available": ["cpu", "memory", "disk", "load", "uptime", "network", "all"]}

    result["timestamp"] = time.strftime("%Y-%m-%d %H:%M:%S")
    return result