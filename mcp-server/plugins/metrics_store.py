"""系统指标本地缓存插件：SQLite 存储 + 后台采集 + 按时间查询 + 过期清理

提供三个层次的能力：
  1. 本地 SQLite 持久化（启动后自动持续采集）
  2. JSON-RPC 工具 "metrics_history"（大模型按时间调用）
  3. 过期数据自动删除（定时 + 行数上限双重兜底）

线程安全：通过 threading.Lock 保护 SQLite 连接和后台采集状态。
"""
import logging
import os
import sqlite3
import threading
import time

logger = logging.getLogger("mcp.metrics_store")

# ============================================================
# 模块级状态
# ============================================================
_db_path: str = ""
_db_lock = threading.Lock()
_collect_thread: threading.Thread | None = None
_cleanup_thread: threading.Thread | None = None
_stop_event = threading.Event()

# 由 server.py 注入的配置引用
config_ref = None


def _get_config():
    """获取配置引用，延迟导入避免循环依赖"""
    global config_ref
    if config_ref is None:
        from config import config as cfg
        config_ref = cfg
    return config_ref


# ============================================================
# 数据库初始化
# ============================================================

def _get_connection() -> sqlite3.Connection:
    """获取线程安全的数据库连接（_db_path 需已由 init_db() 设置）"""
    conn = sqlite3.connect(_db_path, check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


def init_db():
    """初始化数据库表结构和索引（幂等），同时解析并设置 _db_path"""
    global _db_path

    cfg = _get_config()
    _db_path = getattr(cfg, "METRICS_DB_PATH", "/var/lib/mcp-server/metrics.db")

    # 确保目录存在
    db_dir = os.path.dirname(_db_path)
    if db_dir and not os.path.exists(db_dir):
        try:
            os.makedirs(db_dir, exist_ok=True)
        except PermissionError:
            fallback = os.path.join(os.path.dirname(__file__), "..", "metrics.db")
            _db_path = os.path.abspath(fallback)
            logger.warning("无法创建 %s，回退到 %s", db_dir, _db_path)

    with _db_lock:
        conn = _get_connection()
        try:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS metrics (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp REAL NOT NULL,
                    cpu_percent REAL,
                    load_1 REAL,
                    load_5 REAL,
                    load_15 REAL,
                    memory_percent REAL,
                    memory_used_mb REAL,
                    memory_total_mb REAL,
                    disk_percent REAL,
                    disk_used_gb REAL,
                    disk_total_gb REAL,
                    net_recv_bytes INTEGER,
                    net_sent_bytes INTEGER,
                    net_recv_kbps REAL,
                    net_sent_kbps REAL
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_metrics_ts ON metrics(timestamp)
            """)
            conn.commit()
            logger.info("[MetricsStore] 数据库初始化完成: %s", _db_path)
        finally:
            conn.close()


# ============================================================
# 数据写入
# ============================================================

def _parse_metrics_data(data: dict) -> tuple:
    """解析采集数据为 INSERT 参数元组。
    
    返回:
        (ts, cpu_pct, load_1, load_5, load_15,
         mem_pct, mem_used, mem_total,
         disk_pct, disk_used_gb, disk_total_gb,
         net_bytes_recv, net_bytes_sent, net_recv_kbps, net_sent_kbps)
    """
    cpu = data.get("cpu", {})
    memory = data.get("memory", {})
    disk = data.get("disk", [])
    load = data.get("load", {})
    network = data.get("network", {})

    if isinstance(disk, list) and len(disk) > 0:
        disk_data = {}
        for d in disk:
            if isinstance(d, dict) and d.get("mount_point") == "/":
                disk_data = d
                break
        if not disk_data and isinstance(disk[0], dict):
            disk_data = disk[0]
    elif isinstance(disk, dict):
        disk_data = disk
    else:
        disk_data = {}

    load_avg = load.get("load_avg", [0, 0, 0])
    if isinstance(load_avg, list) and len(load_avg) >= 3:
        l1, l5, l15 = load_avg[0], load_avg[1], load_avg[2]
    else:
        l1, l5, l15 = 0.0, 0.0, 0.0

    disk_pct_str = str(disk_data.get("percent", "0")).replace("%", "").strip()
    try:
        disk_pct = float(disk_pct_str)
    except (ValueError, TypeError):
        disk_pct = 0.0

    disk_size_str = str(disk_data.get("size", "0")).replace("G", "").replace("T", "").replace("M", "").strip()
    disk_used_str = str(disk_data.get("used", "0")).replace("G", "").replace("T", "").replace("M", "").strip()
    try:
        disk_total_gb = float(disk_size_str)
        disk_used_gb = float(disk_used_str)
    except (ValueError, TypeError):
        disk_total_gb, disk_used_gb = 0.0, 0.0

    ts = time.time()

    return (
        ts,
        cpu.get("cpu_percent_snapshot", 0.0),
        l1, l5, l15,
        memory.get("percent", 0.0),
        memory.get("used_mb", 0.0),
        memory.get("total_mb", 0.0),
        disk_pct, disk_used_gb, disk_total_gb,
        network.get("bytes_recv", 0),
        network.get("bytes_sent", 0),
        network.get("net_recv_kbps", 0.0),
        network.get("net_sent_kbps", 0.0),
    )


def _insert_metrics_with_conn(conn: sqlite3.Connection, data: dict) -> bool:
    """使用已有连接写入指标（由 _collect_loop 持有持久连接调用）"""
    try:
        values = _parse_metrics_data(data)

        conn.execute(
            """INSERT INTO metrics
               (timestamp, cpu_percent, load_1, load_5, load_15,
                memory_percent, memory_used_mb, memory_total_mb,
                disk_percent, disk_used_gb, disk_total_gb,
                net_recv_bytes, net_sent_bytes, net_recv_kbps, net_sent_kbps)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            values,
        )
        conn.commit()

        # 检查行数上限
        cfg = _get_config()
        max_rows = getattr(cfg, "METRICS_MAX_ROWS", 100000)
        row_count = conn.execute("SELECT COUNT(*) FROM metrics").fetchone()[0]
        if row_count > max_rows:
            excess = row_count - max_rows
            conn.execute(
                "DELETE FROM metrics WHERE id IN (SELECT id FROM metrics ORDER BY timestamp ASC LIMIT ?)",
                (excess,),
            )
            conn.commit()
            logger.info("[MetricsStore] 行数上限清理: 删除 %d 条旧记录", excess)

        return True
    except Exception as e:
        logger.exception("[MetricsStore] 写入指标失败: %s", e)
        return False


def _cleanup_expired_with_conn(conn: sqlite3.Connection):
    """使用已有连接清理过期数据"""
    cfg = _get_config()
    max_hours = getattr(cfg, "METRICS_MAX_RETENTION_HOURS", 24)
    cutoff = time.time() - max_hours * 3600

    cursor = conn.execute("DELETE FROM metrics WHERE timestamp < ?", (cutoff,))
    deleted = cursor.rowcount
    if deleted > 0:
        conn.commit()
        logger.info("[MetricsStore] 过期清理: 删除 %d 条记录 (超过 %d 小时)", deleted, max_hours)


def insert_metrics(data: dict) -> bool:
    """
    将一次采集的指标数据写入 SQLite（独立调用，每次新建连接）。

    参数:
        data: sys_info.handle({"metric":"all"}) 的返回结果

    返回:
        True 成功, False 失败
    """
    try:
        with _db_lock:
            conn = _get_connection()
            try:
                return _insert_metrics_with_conn(conn, data)
            finally:
                conn.close()
    except Exception as e:
        logger.exception("[MetricsStore] 写入指标失败: %s", e)
        return False


# ============================================================
# 数据查询
# ============================================================

def query_metrics(from_ts: float | None = None, to_ts: float | None = None,
                  metrics: list[str] | None = None, limit: int = 5000) -> dict:
    """
    按时间范围查询历史指标数据。

    参数:
        from_ts: 开始时间戳 (Unix 秒)，默认 5 分钟前
        to_ts:   结束时间戳 (Unix 秒)，默认当前时间
        metrics: 需要返回的指标列名列表，如 ["cpu", "memory", "disk"]；默认全部
        limit:   最大返回条数，默认 5000

    返回:
        {"count": N, "interval_seconds": 15, "data": [...]}
    """
    if from_ts is None:
        from_ts = time.time() - 300  # 默认最近 5 分钟
    if to_ts is None:
        to_ts = time.time()

    # 构建 SELECT 列
    all_columns = [
        "timestamp", "cpu_percent", "load_1", "load_5", "load_15",
        "memory_percent", "memory_used_mb", "memory_total_mb",
        "disk_percent", "disk_used_gb", "disk_total_gb",
        "net_recv_bytes", "net_sent_bytes", "net_recv_kbps", "net_sent_kbps",
    ]

    if metrics is None or len(metrics) == 0 or "all" in metrics:
        selected = all_columns
    else:
        selected = ["timestamp"]
        col_map = {
            "cpu": ["cpu_percent", "load_1", "load_5", "load_15"],
            "memory": ["memory_percent", "memory_used_mb", "memory_total_mb"],
            "disk": ["disk_percent", "disk_used_gb", "disk_total_gb"],
            "network": ["net_recv_bytes", "net_sent_bytes", "net_recv_kbps", "net_sent_kbps"],
        }
        for m in metrics:
            if m in col_map:
                selected.extend(col_map[m])
        selected = list(dict.fromkeys(selected))  # 去重保持顺序

    columns_str = ", ".join(selected)

    with _db_lock:
        conn = _get_connection()
        try:
            rows = conn.execute(
                f"SELECT {columns_str} FROM metrics WHERE timestamp >= ? AND timestamp <= ? ORDER BY timestamp ASC LIMIT ?",
                (from_ts, to_ts, limit),
            ).fetchall()

            data = []
            for row in rows:
                # 将 SELECT 结果与列名组合
                point = {}
                for i, col in enumerate(selected):
                    if i < len(row):
                        val = row[i]
                        point[col] = round(val, 2) if isinstance(val, float) else val
                # timestamp 字段都带 ts 别名
                if "timestamp" in point:
                    point["ts"] = point.pop("timestamp")
                data.append(point)

            cfg = _get_config()
            interval = getattr(cfg, "METRICS_COLLECT_INTERVAL", 15)

            return {
                "count": len(data),
                "interval_seconds": interval,
                "from_ts": from_ts,
                "to_ts": to_ts,
                "data": data,
            }
        finally:
            conn.close()


# ============================================================
# 过期数据清理
# ============================================================

def cleanup_expired():
    """删除超过保留时间的过期数据（独立调用，每次新建连接）"""
    with _db_lock:
        conn = _get_connection()
        try:
            _cleanup_expired_with_conn(conn)
        finally:
            conn.close()


def vacuum_db():
    """回收数据库空间（启动时执行一次）"""
    try:
        with _db_lock:
            conn = _get_connection()
            try:
                conn.execute("VACUUM")
                logger.info("[MetricsStore] VACUUM 完成")
            finally:
                conn.close()
    except Exception as e:
        logger.warning("[MetricsStore] VACUUM 失败: %s", e)


# ============================================================
# 后台采集线程
# ============================================================

def _collect_loop():
    """后台采集线程主循环 — 使用持久连接复用，避免高频 create/close 开销"""
    cfg = _get_config()
    interval = getattr(cfg, "METRICS_COLLECT_INTERVAL", 15)
    logger.info("[MetricsStore] 后台采集线程启动，间隔 %d 秒", interval)

    from plugins import sys_info

    # 持久连接：整个线程生命周期内复用同一个连接
    conn = _get_connection()
    try:
        prev_net = {"bytes_recv": 0, "bytes_sent": 0, "ts": 0.0}

        while not _stop_event.is_set():
            try:
                result = sys_info.handle({"metric": "all"})
                if "error" not in result:
                    now_ts = time.time()

                    # 从 sys_info 结果中提取网络原始计数器，计算 kbps 速率
                    net_data = result.get("network", {})
                    bytes_recv = net_data.get("bytes_recv", 0)
                    bytes_sent = net_data.get("bytes_sent", 0)
                    net_recv_kbps = 0.0
                    net_sent_kbps = 0.0
                    if prev_net["ts"] > 0:
                        elapsed = now_ts - prev_net["ts"]
                        if elapsed > 0:
                            rx_delta = max(0, bytes_recv - prev_net["bytes_recv"])
                            tx_delta = max(0, bytes_sent - prev_net["bytes_sent"])
                            net_recv_kbps = round(rx_delta / elapsed / 1024, 1)
                            net_sent_kbps = round(tx_delta / elapsed / 1024, 1)
                    prev_net = {"bytes_recv": bytes_recv, "bytes_sent": bytes_sent, "ts": now_ts}

                    # 将计算出的速率回填到 result 中
                    result["network"]["net_recv_kbps"] = net_recv_kbps
                    result["network"]["net_sent_kbps"] = net_sent_kbps

                    # 持锁写入 + 清理，复用持久连接
                    with _db_lock:
                        _insert_metrics_with_conn(conn, result)
                        _cleanup_expired_with_conn(conn)
                else:
                    logger.warning("[MetricsStore] 采集失败: %s", result.get("error"))
            except Exception as e:
                logger.exception("[MetricsStore] 采集异常: %s", e)

            # 等待下一次采集，支持被 stop_event 提前中断
            _stop_event.wait(interval)
    finally:
        conn.close()
        logger.info("[MetricsStore] 后台采集线程已停止 — 持久连接已关闭")


def start_collect_thread():
    """启动后台采集线程"""
    global _collect_thread, _stop_event

    if _collect_thread is not None and _collect_thread.is_alive():
        logger.warning("[MetricsStore] 采集线程已在运行")
        return

    _stop_event.clear()
    _collect_thread = threading.Thread(target=_collect_loop, daemon=True, name="metrics-collector")
    _collect_thread.start()


def stop_collect_thread():
    """停止后台采集线程"""
    global _collect_thread

    _stop_event.set()
    if _collect_thread is not None:
        _collect_thread.join(timeout=5)
        _collect_thread = None


# ============================================================
# JSON-RPC handle 函数
# ============================================================

def handle(arguments: dict) -> dict:
    """
    处理 metrics_history 工具调用（JSON-RPC tools/call 入口）

    参数:
        arguments: {
            "from_ts": 1752470000.0,    # 开始时间戳 (Unix 秒)，可选
            "to_ts": 1752473600.0,      # 结束时间戳 (Unix 秒)，可选
            "metrics": ["cpu", "memory", "disk", "network"],  # 可选，默认 all
            "limit": 5000,              # 可选，最大返回条数
        }

    返回:
        查询结果字典
    """
    from_ts = arguments.get("from_ts")
    to_ts = arguments.get("to_ts")
    limit = arguments.get("limit", 5000)
    metrics = arguments.get("metrics")
    if isinstance(metrics, str):
        metrics = [m.strip() for m in metrics.split(",")]

    # 参数校验
    if from_ts is not None:
        try:
            from_ts = float(from_ts)
        except (ValueError, TypeError):
            return {"error": f"from_ts 格式无效: {from_ts}"}

    if to_ts is not None:
        try:
            to_ts = float(to_ts)
        except (ValueError, TypeError):
            return {"error": f"to_ts 格式无效: {to_ts}"}

    try:
        limit = int(limit)
        if limit < 1:
            limit = 1
        if limit > 10000:
            limit = 10000
    except (ValueError, TypeError):
        return {"error": f"limit 格式无效: {arguments.get('limit')}"}

    return query_metrics(from_ts=from_ts, to_ts=to_ts, metrics=metrics, limit=limit)