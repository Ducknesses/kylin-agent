#!/usr/bin/env python3
"""
Kylin Agent Backend 诊断工具

独立 CLI 脚本，可离线检测后端环境，不依赖 FastAPI 或其他 app 内部模块。

功能：
  1. 依赖检测 —— 检查 requirements.txt 中的包是否已安装
  2. 监听地址查看/修改 —— 读取/修改 .env 中的 APP_HOST/APP_PORT
  3. Backend 启动状态 —— 端口监听、/health、Redis、SQLite、systemd 服务
  4. 网络连通性 —— MCP Server、DeepSeek API、前端连通性检测

用法：
  python diagnose.py          # 完整检测
  python diagnose.py --quick  # 快速检测（跳过依赖，仅检测状态和连通性）
"""
import importlib.util
import ipaddress
import os
import re
import socket
import sqlite3
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlparse

from dotenv import load_dotenv

# ── ANSI 颜色 ──────────────────────────────────────────────────────
GREEN = "\033[92m"
RED = "\033[91m"
YELLOW = "\033[93m"
CYAN = "\033[96m"
BOLD = "\033[1m"
RESET = "\033[0m"

CHECK = f"{GREEN}✓{RESET}"
CROSS = f"{RED}✗{RESET}"
WARN = f"{YELLOW}⚠{RESET}"
ARROW = f"{CYAN}→{RESET}"

# ── 路径常量 ──────────────────────────────────────────────────────
SCRIPT_DIR = Path(__file__).resolve().parent
ENV_FILE = SCRIPT_DIR / ".env"
REQUIREMENTS_FILE = SCRIPT_DIR / "requirements.txt"
SERVICE_FILE = SCRIPT_DIR.parent / "deploy" / "backend" / "kylin-agent.service"
SQLITE_DB = SCRIPT_DIR / "data" / "audit.db"

# ── 加载 .env，与 run.py / config.py 保持一致 ─────────────────────
load_dotenv(ENV_FILE)

# ── 统计计数器 ──────────────────────────────────────────────────────
_passed = 0
_failed = 0
_warnings = 0


def _record(ok: bool, warning: bool = False) -> None:
    global _passed, _failed, _warnings
    if ok:
        _passed += 1
    elif warning:
        _warnings += 1
    else:
        _failed += 1


def _print_result(ok: bool, label: str, detail: str = "", indent: int = 2) -> None:
    prefix = " " * indent
    if ok:
        print(f"{prefix}{CHECK} {label}{' — ' + detail if detail else ''}")
    else:
        print(f"{prefix}{CROSS} {label}{' — ' + detail if detail else ''}")


def _print_warn(label: str, detail: str = "", indent: int = 2) -> None:
    prefix = " " * indent
    print(f"{prefix}{WARN} {label}{' — ' + detail if detail else ''}")


def _print_suggestion(text: str, indent: int = 4) -> None:
    prefix = " " * indent
    print(f"{prefix}{ARROW} {YELLOW}建议: {text}{RESET}")


# ═══════════════════════════════════════════════════════════════════
# 1. 依赖检测
# ═══════════════════════════════════════════════════════════════════

def _parse_requirements() -> list[tuple[str, str]]:
    """解析 requirements.txt，返回 [(包名, 原始行)] 列表"""
    packages = []
    if not REQUIREMENTS_FILE.exists():
        return packages

    for line in REQUIREMENTS_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        # 提取包名（去掉版本运算符和 extras 标记）
        # 格式: fastapi>=0.110.0, uvicorn[standard]>=0.29.0
        match = re.match(r"^([a-zA-Z0-9_\-]+)", line)
        if match:
            packages.append((match.group(1), line))
    return packages


def _check_package(pkg_name: str) -> tuple[bool, str]:
    """检查包是否可导入，返回 (已安装, 版本字符串)"""
    try:
        spec = importlib.util.find_spec(pkg_name)
        if spec is None:
            # 尝试映射常见包名差异
            alt = _PKG_NAME_MAP.get(pkg_name)
            if alt:
                spec = importlib.util.find_spec(alt)
            if spec is None:
                return False, ""
    except (ValueError, ImportError, ModuleNotFoundError):
        return False, ""

    # 尝试获取版本
    version = ""
    try:
        mod = __import__(pkg_name)
        version = getattr(mod, "__version__", "")
    except Exception:
        pass

    return True, version


# 包名到 import 名的映射（部分 pip 包名与 import 名不同）
_PKG_NAME_MAP = {
    "python-multipart": "multipart",
    "python-dotenv": "dotenv",
    "websockets": "websockets",
    "fakeredis": "fakeredis",
    "aiosqlite": "aiosqlite",
}


def check_dependencies() -> dict:
    """检测依赖项"""
    global _passed, _failed, _warnings
    print(f"\n{BOLD}[1/4] 依赖检测{RESET}")
    print("-" * 50)

    packages = _parse_requirements()
    if not packages:
        _print_warn("未找到 requirements.txt")
        return {"ok": False, "installed": [], "missing": []}

    installed = []
    missing = []

    for pkg_name, raw_line in packages:
        ok, version = _check_package(pkg_name)
        ver_str = f" ({version})" if version else ""
        if ok:
            _print_result(True, f"{pkg_name}{ver_str}")
            installed.append({"name": pkg_name, "version": version, "raw": raw_line})
            _record(True)
        else:
            _print_result(False, f"{pkg_name}", "未安装")
            _print_suggestion(f"pip install {pkg_name}")
            missing.append({"name": pkg_name, "raw": raw_line})
            _record(False)

    print(f"\n  已安装: {len(installed)}, 缺失: {len(missing)}")

    # 可选高性能依赖检测（uvloop / httptools，非阻塞）
    _check_optional_performance_deps()

    return {"ok": len(missing) == 0, "installed": installed, "missing": missing}


def _check_optional_performance_deps() -> None:
    """检测可选高性能依赖（uvloop / httptools），非阻塞性提示"""
    print(f"\n  {BOLD}可选高性能依赖{RESET}（uvicorn 性能优化）:")
    deps = [
        ("uvloop", "u w z l o o p"),
        ("httptools", "h t t p t o o l s"),
    ]
    for pkg, display in deps:
        ok, ver = _check_package(pkg)
        if ok:
            print(f"    {CHECK} {display} {'(' + ver + ')' if ver else ''} 已安装")
        else:
            print(f"    {WARN} {display} 未安装（非必须，但可提升高并发性能）")
            _print_suggestion(f"pip install {pkg}")


# ═══════════════════════════════════════════════════════════════════
# 2. 监听地址查看 & 修改
# ═══════════════════════════════════════════════════════════════════

def _load_env() -> dict[str, str]:
    """加载 .env 文件为键值对字典"""
    env = {}
    if not ENV_FILE.exists():
        return env
    for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        env[key.strip()] = value.strip().strip('"').strip("'")
    return env


def _save_env(env: dict[str, str]) -> None:
    """保存环境变量到 .env（保留原始注释结构 + 仅更新键值）"""
    if not ENV_FILE.exists():
        # 全新写入
        lines = [f"{k}={v}" for k, v in env.items()]
        ENV_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return

    original = ENV_FILE.read_text(encoding="utf-8").splitlines()
    updated_keys: set[str] = set()
    new_lines: list[str] = []

    for line in original:
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and "=" in stripped:
            key = stripped.split("=", 1)[0].strip()
            if key in env:
                new_lines.append(f"{key}={env[key]}")
                updated_keys.add(key)
                continue
        new_lines.append(line)

    # 追加新增的键
    for k, v in env.items():
        if k not in updated_keys:
            new_lines.append(f"{k}={v}")

    content = "\n".join(new_lines) + "\n"
    # 原子写入
    tmp_path = ENV_FILE.with_suffix(".env.tmp")
    tmp_path.write_text(content, encoding="utf-8")
    tmp_path.replace(ENV_FILE)


def _validate_host(host: str) -> bool:
    """校验 host 格式"""
    if not host:
        return False
    # 允许 0.0.0.0, 127.0.0.1, IP 地址, localhost
    if host in ("0.0.0.0", "localhost", "::"):
        return True
    try:
        ipaddress.ip_address(host)
        return True
    except ValueError:
        pass
    # 允许主机名（简单校验：不含空格、斜杠等）
    if re.match(r"^[a-zA-Z0-9]([a-zA-Z0-9\-\.]*[a-zA-Z0-9])?$", host):
        return True
    return False


def _validate_port(port: str) -> bool:
    """校验端口范围"""
    try:
        p = int(port)
        return 1 <= p <= 65535
    except (ValueError, TypeError):
        return False


def check_listen(interactive: bool = True) -> dict:
    """查看和修改监听地址（使用 os.getenv 与后端 config.py 保持一致）"""
    global _passed, _failed, _warnings
    print(f"\n{BOLD}[2/4] 监听地址{RESET}")
    print("-" * 50)

    host = os.getenv("APP_HOST", "0.0.0.0")
    port = os.getenv("APP_PORT", "8000")
    env = _load_env()  # 仅用于交互模式下回写 .env

    print(f"  当前配置: {host}:{port}")
    _record(True)

    if interactive:
        try:
            answer = input(f"\n  是否修改监听地址? [y/N]: ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            answer = "n"

        if answer in ("y", "yes"):
            new_host = input(f"  APP_HOST [{host}]: ").strip()
            if new_host:
                if _validate_host(new_host):
                    host = new_host
                else:
                    print(f"  {CROSS} 无效的 host 格式，保留原值")
                    _record(False)

            new_port = input(f"  APP_PORT [{port}]: ").strip()
            if new_port:
                if _validate_port(new_port):
                    port = new_port
                else:
                    print(f"  {CROSS} 无效的端口（1-65535），保留原值")
                    _record(False)

            env["APP_HOST"] = host
            env["APP_PORT"] = port
            _save_env(env)
            print(f"  {CHECK} 已更新为 {host}:{port}")
            print(f"  {ARROW} 请重启 Backend 服务使配置生效")
    else:
        print(f"  (非交互模式，跳过修改)")

    return {"host": host, "port": port}


# ═══════════════════════════════════════════════════════════════════
# 3. Backend 启动状态检测
# ═══════════════════════════════════════════════════════════════════

def _check_port_listening(host: str, port: str) -> tuple[bool, str]:
    """检查端口是否在监听"""
    try:
        p = int(port)
        # 尝试 TCP 连接
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(2)
        result = sock.connect_ex(("127.0.0.1" if host == "0.0.0.0" else host, p))
        sock.close()
        if result == 0:
            return True, f"端口 {port} 正在监听"
        else:
            return False, f"端口 {port} 未监听（连接失败）"
    except Exception as e:
        return False, f"端口检测失败: {e}"


def _check_health_endpoint(host: str, port: str) -> tuple[bool, str]:
    """检查 /health 端点"""
    import urllib.request
    import urllib.error

    target_host = "127.0.0.1" if host == "0.0.0.0" else host
    url = f"http://{target_host}:{port}/health"
    try:
        req = urllib.request.Request(url, method="GET")
        resp = urllib.request.urlopen(req, timeout=5)
        if resp.status == 200:
            body = resp.read().decode("utf-8", errors="replace")
            return True, f"/health 返回 200 — {body[:80]}"
        else:
            return False, f"/health 返回 {resp.status}"
    except urllib.error.URLError as e:
        return False, f"/health 无法连接: {e.reason}"
    except Exception as e:
        return False, f"/health 检测异常: {e}"


def _check_redis(env: dict[str, str]) -> tuple[bool, str]:
    """检测 Redis 连通性"""
    redis_url = env.get("REDIS_URL", "redis://localhost:6379/0")
    try:
        parsed = urlparse(redis_url)
        host = parsed.hostname or "localhost"
        port = parsed.port or 6379
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(3)
        result = sock.connect_ex((host, port))
        sock.close()
        if result == 0:
            # 尝试 redis ping
            try:
                import redis
                r = redis.from_url(redis_url, socket_connect_timeout=3)
                if r.ping():
                    return True, f"Redis 连通 ({host}:{port})"
                return False, f"Redis ping 失败 ({host}:{port})"
            except ImportError:
                return True, f"Redis 端口可达 ({host}:{port})，无法验证 ping（redis-py 未安装）"
            except Exception as e:
                return True, f"Redis 端口可达 ({host}:{port})，ping 异常: {e}"
        else:
            return False, f"Redis 不可达 ({host}:{port})"
    except Exception as e:
        return False, f"Redis 检测异常: {e}"


def _check_sqlite() -> tuple[bool, str]:
    """检测 SQLite 数据库"""
    # 检查数据目录是否存在
    data_dir = SCRIPT_DIR / "data"
    if not data_dir.exists():
        try:
            data_dir.mkdir(parents=True, exist_ok=True)
            return True, "data/ 目录已创建"
        except Exception as e:
            return False, f"data/ 目录创建失败: {e}"

    db_path = data_dir / "audit.db"
    if not db_path.exists():
        # 尝试创建并写入
        try:
            conn = sqlite3.connect(str(db_path))
            conn.execute("CREATE TABLE IF NOT EXISTS _diagnose_test (id INTEGER)")
            conn.execute("DROP TABLE _diagnose_test")
            conn.close()
            return True, "audit.db 可创建/写入"
        except Exception as e:
            return False, f"audit.db 创建失败: {e}"

    # 数据库文件存在，尝试读写
    try:
        conn = sqlite3.connect(str(db_path))
        conn.execute("SELECT 1")
        conn.close()
        return True, "audit.db 可读写"
    except Exception as e:
        return False, f"audit.db 读写失败: {e}"


def _check_systemd_service() -> tuple[bool, str]:
    """检测系统服务状态"""
    service_name = "kylin-agent"
    try:
        result = subprocess.run(
            ["systemctl", "is-active", service_name],
            capture_output=True, text=True, timeout=10,
        )
        status = result.stdout.strip()
        if status == "active":
            return True, f"systemd 服务 active"
        else:
            return False, f"systemd 服务状态: {status}"
    except FileNotFoundError:
        return False, "systemctl 不可用（非 systemd 系统）"
    except subprocess.TimeoutExpired:
        return False, "systemctl 命令超时"
    except Exception as e:
        return False, f"systemctl 检测异常: {e}"


def check_backend_status() -> dict[str, Any]:
    """检测 Backend 启动状态（使用 os.getenv 与后端 config.py 保持一致）"""
    global _passed, _failed, _warnings
    print(f"\n{BOLD}[3/4] Backend 启动状态{RESET}")
    print("-" * 50)

    host = os.getenv("APP_HOST", "0.0.0.0")
    port = os.getenv("APP_PORT", "8000")
    env = _load_env()  # 用于 Redis 等后续检测项

    results = {}

    # 3.1 端口监听
    ok, msg = _check_port_listening(host, port)
    _print_result(ok, "端口监听", msg)
    _record(ok)
    results["port_listening"] = {"ok": ok, "detail": msg}

    # 3.2 /health 端点
    if ok:
        h_ok, h_msg = _check_health_endpoint(host, port)
    else:
        # 端口没监听也尝试检测（可能在其他端口）
        h_ok, h_msg = _check_health_endpoint(host, port)
    _print_result(h_ok, "Health API", h_msg)
    _record(h_ok)
    results["health_api"] = {"ok": h_ok, "detail": h_msg}

    # 3.3 Redis
    r_ok, r_msg = _check_redis(env)
    _print_result(r_ok, "Redis", r_msg)
    if not r_ok:
        redis_url = env.get("REDIS_URL", "redis://localhost:6379/0")
        _print_suggestion(
            f"确认 Redis 服务已启动: systemctl start redis\n"
            + " " * 4 + f"或检查 REDIS_URL 配置: {redis_url}"
        )
    _record(r_ok)
    results["redis"] = {"ok": r_ok, "detail": r_msg}

    # 3.4 SQLite
    s_ok, s_msg = _check_sqlite()
    _print_result(s_ok, "SQLite", s_msg)
    _record(s_ok)
    results["sqlite"] = {"ok": s_ok, "detail": s_msg}

    # 3.5 systemd 服务
    svc_ok, svc_msg = _check_systemd_service()
    _print_result(svc_ok, "Systemd 服务", svc_msg)
    if not svc_ok and "不可用" not in svc_msg:
        _print_suggestion("systemctl start kylin-agent")
    _record(svc_ok)
    results["systemd_service"] = {"ok": svc_ok, "detail": svc_msg}

    return results


# ═══════════════════════════════════════════════════════════════════
# 4. 网络连通性检测
# ═══════════════════════════════════════════════════════════════════

def _http_probe(url: str, timeout: float = 5.0) -> tuple[bool, float, str]:
    """HTTP 探测，返回 (可达, 延迟ms, 错误信息)"""
    import urllib.request
    import urllib.error
    start = time.monotonic()
    try:
        req = urllib.request.Request(url, method="GET")
        resp = urllib.request.urlopen(req, timeout=timeout)
        elapsed = (time.monotonic() - start) * 1000
        if 200 <= resp.status < 400:
            return True, round(elapsed, 1), ""
        elif resp.status in (401, 403):
            return True, round(elapsed, 1), f"HTTP {resp.status}（认证问题）"
        else:
            return False, round(elapsed, 1), f"HTTP {resp.status}"
    except urllib.error.HTTPError as e:
        elapsed = (time.monotonic() - start) * 1000
        if e.code in (401, 403):
            return True, round(elapsed, 1), f"HTTP {e.code}（认证问题）"
        elif e.code == 502:
            return False, round(elapsed, 1), f"HTTP 502 Bad Gateway（目标服务未就绪）"
        elif e.code == 503:
            return False, round(elapsed, 1), f"HTTP 503 Service Unavailable（目标服务暂时不可用）"
        elif 400 <= e.code < 500:
            return False, round(elapsed, 1), f"HTTP {e.code}（客户端错误）"
        else:
            return False, round(elapsed, 1), f"HTTP {e.code}（服务器错误）"
    except urllib.error.URLError as e:
        elapsed = (time.monotonic() - start) * 1000
        reason = str(e.reason)
        return False, round(elapsed, 1), reason
    except Exception as e:
        return False, 0, str(e)


def _diagnose_network_failure(target_type: str, url: str, error: str) -> str:
    """根据连接失败原因给出建议"""
    error_lower = error.lower()

    if "name or service not known" in error_lower or "nodename nor servname" in error_lower or "getaddrinfo" in error_lower:
        return f"DNS 解析失败 — 检查 {target_type} 地址是否正确，或检查 /etc/resolv.conf DNS 配置"
    elif "timed out" in error_lower or "timeout" in error_lower:
        return f"连接超时 — 检查 {target_type} 服务是否启动、防火墙是否放行"
    elif "connection refused" in error_lower:
        return f"连接被拒绝 — {target_type} 服务未启动或端口不正确"
    elif "no route to host" in error_lower:
        return f"无路由到主机 — 检查网络配置、VPN 连接"
    elif "certificate" in error_lower or "ssl" in error_lower:
        return f"TLS/SSL 证书问题 — 检查 {target_type} 的 HTTPS 证书配置"
    elif "401" in error_lower or "403" in error_lower:
        return f"认证失败 — 检查 {target_type} 的 API Key 或 Token 配置"
    else:
        return f"连接失败 — 检查 {target_type} 服务状态和网络配置"


def check_network() -> dict[str, Any]:
    """检测网络连通性"""
    global _passed, _failed, _warnings
    print(f"\n{BOLD}[4/4] 网络连通性{RESET}")
    print("-" * 50)

    env = _load_env()
    results = {}

    # ── MCP Server ──────────────────────────────────────────────
    mcp_url = env.get("MCP_SERVER_URL", "http://192.168.56.101:8001")
    # 探测 MCP Server 的健康端点或根路径
    probe_url = mcp_url.rstrip("/") + "/"
    ok, latency, error = _http_probe(probe_url)
    detail = f"{round(latency)}ms" if ok and latency > 0 else (error or "未知错误")
    _print_result(ok, f"MCP Server ({mcp_url})", detail)
    if not ok:
        suggestion = _diagnose_network_failure("MCP Server", mcp_url, error)
        _print_suggestion(suggestion)
    _record(ok)
    results["mcp_server"] = {"url": mcp_url, "ok": ok, "latency_ms": latency, "error": error}

    # ── DeepSeek API ────────────────────────────────────────────
    llm_enabled = env.get("LLM_ENABLED", "false").lower() == "true"
    deepseek_url = env.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1")
    if llm_enabled:
        probe_url = deepseek_url.rstrip("/") + "/models"
        ok, latency, error = _http_probe(probe_url, timeout=5.0)
        detail = f"{round(latency)}ms" if ok and latency > 0 else (error or "未知错误")
        _print_result(ok, f"DeepSeek API ({deepseek_url})", detail)
        if not ok:
            suggestion = _diagnose_network_failure("DeepSeek API", deepseek_url, error)
            _print_suggestion(suggestion)
        _record(ok)
        results["deepseek_api"] = {"url": deepseek_url, "ok": ok, "latency_ms": latency, "error": error}
    else:
        _print_warn("DeepSeek API", "LLM_ENABLED=false，跳过检测")
        results["deepseek_api"] = {"url": deepseek_url, "ok": True, "skipped": True}

    # ── 前端 ────────────────────────────────────────────────────
    # 从 nginx 配置或环境变量获取前端地址
    frontend_url: Optional[str] = env.get("FRONTEND_URL")
    if not frontend_url:
        # 尝试从 nginx 配置推断
        nginx_conf = SCRIPT_DIR.parent / "deploy" / "frontend" / "nginx-kylin-agent.conf"
        if nginx_conf.exists():
            content = nginx_conf.read_text(encoding="utf-8")
            m = re.search(r"listen\s+(\d+)", content)
            if m:
                frontend_url = f"http://127.0.0.1:{m.group(1)}"
    if not frontend_url:
        frontend_url = "http://127.0.0.1:80"

    probe_url = frontend_url.rstrip("/") + "/"
    ok, latency, error = _http_probe(probe_url)
    detail = f"{round(latency)}ms" if ok and latency > 0 else (error or "未知错误")
    _print_result(ok, f"前端 ({frontend_url})", detail)
    if not ok:
        suggestion = _diagnose_network_failure("前端", frontend_url, error)
        _print_suggestion(suggestion)
        # 也检查一下 /health 端点（通过前端 Nginx 代理）
        health_url = frontend_url.rstrip("/") + "/health"
        h_ok, h_lat, _ = _http_probe(health_url)
        if h_ok:
            _print_suggestion(f"前端 /health 代理正常 ({round(h_lat)}ms)，前端静态文件可能未部署")
        else:
            _print_suggestion("确认 Nginx 已启动: systemctl start nginx")
    _record(ok)
    results["frontend"] = {"url": frontend_url, "ok": ok, "latency_ms": latency, "error": error}

    return results


# ═══════════════════════════════════════════════════════════════════
# 主入口
# ═══════════════════════════════════════════════════════════════════

def print_banner() -> None:
    print(f"{BOLD}{'='*50}{RESET}")
    print(f"{BOLD}  Kylin Agent Backend 诊断工具{RESET}")
    print(f"{BOLD}{'='*50}{RESET}")


def print_summary() -> None:
    total = _passed + _failed + _warnings
    print(f"\n{BOLD}{'='*50}{RESET}")
    print(f"{BOLD}  诊断完成: 通过 {_passed}/{total}{RESET}")
    if _warnings > 0:
        print(f"  警告: {_warnings}")
    if _failed > 0:
        print(f"{RED}  失败: {_failed}{RESET}")
    print(f"{BOLD}{'='*50}{RESET}")


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(
        description="Kylin Agent Backend 诊断工具",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--quick", action="store_true",
        help="快速检测（跳过依赖检测）",
    )
    parser.add_argument(
        "--non-interactive", action="store_true",
        help="非交互模式（不提示修改监听地址）",
    )
    args = parser.parse_args()

    print_banner()

    # 1. 依赖检测
    if not args.quick:
        check_dependencies()
    else:
        print(f"\n{BOLD}[1/4] 依赖检测{RESET} (跳过 — quick 模式)")

    # 2. 监听地址
    check_listen(interactive=not args.non_interactive)

    # 3. Backend 状态
    check_backend_status()

    # 4. 网络连通性
    check_network()

    print_summary()


if __name__ == "__main__":
    main()