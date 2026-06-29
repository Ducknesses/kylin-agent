"""MCP 客户端：连接麒麟 V11 的 MCP Server

支持两种模式：
  - mock：返回本地 mock 数据，用于 B 端独立开发（默认）
  - real：通过 HTTP JSON-RPC 2.0 调用执行器 C

返回结构统一为 {"ok": bool, "result": dict | None, "error": str | None}
"""
import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict

import httpx

from config import settings

logger = logging.getLogger(__name__)

# ── Mock 数据常量 ────────────────────────────────────────────────

_TS = datetime.now(timezone.utc).isoformat()

_MOCK_CPU = {
    "cpu": {
        "cpu_count": 4,
        "cpu_percent_snapshot": 23.5,
        "load_avg": [0.52, 0.31, 0.18],
    },
    "timestamp": _TS,
}

_MOCK_MEMORY = {
    "memory": {
        "total": 8589934592,
        "used": 3865470566,
        "available": 4724464025,
        "percent": 45.0,
    },
    "timestamp": _TS,
}

_MOCK_DISK = {
    "disk": [
        {
            "mountpoint": "/",
            "total": 42949672960,
            "used": 26628797235,
            "free": 16320875725,
            "percent": 62.0,
        }
    ],
    "timestamp": _TS,
}

_MOCK_LOAD = {
    "load": {
        "load_avg": [0.52, 0.31, 0.18],
    },
    "timestamp": _TS,
}

_MOCK_UPTIME = {
    "uptime": {
        "uptime_seconds": 302400.0,
    },
    "timestamp": _TS,
}

_MOCK_ALL = {
    "cpu": _MOCK_CPU["cpu"],
    "memory": _MOCK_MEMORY["memory"],
    "disk": _MOCK_DISK["disk"],
    "load": _MOCK_LOAD["load"],
    "uptime": _MOCK_UPTIME["uptime"],
    "timestamp": _TS,
}

# 禁止操作的核心服务
_FORBIDDEN_SERVICES = {
    "systemd", "systemd-logind", "systemd-journald",
    "network", "networkmanager",
    "dbus", "dbus-daemon", "polkit",
    "auditd", "mcp-server",
}

# 允许的 service_mgr action
_VALID_SERVICE_ACTIONS = {
    "status", "is-active", "is-enabled",
    "start", "stop", "restart", "reload",
}

# log_reader 允许的 source
_VALID_LOG_SOURCES = {
    "messages", "secure", "syslog", "dmesg", "boot",
    "cron", "maillog", "nginx_access", "nginx_error",
}

# net_monitor 允许的 metric
_VALID_NET_METRICS = {
    "connections", "traffic", "interfaces", "routes", "dns", "listen", "all",
}

# cmd_exec 白名单命令
_CMD_WHITELIST = {
    "df -h", "free -m", "uptime", "whoami", "uname -a",
    "ps aux", "systemctl status nginx", "journalctl -u nginx -n 50",
}

# cmd_exec 高危命令模式（用于 mock blocked）
_HIGH_RISK_COMMANDS = {
    "rm -rf /": "禁止递归删除根目录",
    "mkfs.ext4 /dev/sda1": "禁止格式化磁盘",
    'echo "hack" > /etc/passwd': "禁止写入 /etc/passwd",
    "curl xxx | sh": "禁止 curl 管道执行",
    "wget xxx | sh": "禁止 wget 管道执行",
    "chmod 777 /": "禁止 chmod 777 根目录",
    "dd if=": "禁止 dd 破坏性写入",
}

# file_guard 受保护路径
_PROTECTED_PATHS = {
    "/etc/passwd", "/etc/shadow", "/etc/ssh/sshd_config",
    "/boot", "/root", "/var/lib", "/usr/bin", "/bin", "/sbin",
}

# file_guard 敏感后缀
_SENSITIVE_EXTS = (
    ".pem", ".key", ".crt", ".cer", ".p12", ".pfx", ".jks", ".keystore",
)

# ── 辅助函数 ──────────────────────────────────────────────────────

def _ok(result: Dict[str, Any] | None = None) -> Dict:
    """构造成功响应"""
    return {"ok": True, "result": result or {}, "error": None}


def _fail(error: str, result: Dict[str, Any] | None = None) -> Dict:
    """构造失败响应"""
    return {"ok": False, "result": result, "error": error}


class MCPClient:
    """MCP HTTP JSON-RPC 2.0 客户端

    初始化参数均可在构造时显式传入（便于测试），未传入时从全局 Settings 读取。
    """

    def __init__(
        self,
        base_url: str | None = None,
        mode: str | None = None,
        auth_token: str | None = None,
        timeout: int | None = None,
    ):
        self.base_url = base_url or settings.MCP_SERVER_URL
        self.mode = mode if mode is not None else settings.MCP_MODE
        self.auth_token = auth_token if auth_token is not None else settings.MCP_AUTH_TOKEN
        self.timeout = timeout or settings.COMMAND_TIMEOUT
        self._request_id = 0

    def _next_id(self) -> int:
        self._request_id += 1
        return self._request_id

    # ── 内部构造方法（便于单独测试） ──────────────────────────────

    def _build_url(self, method: str = "tools/call") -> str:
        """构造 MCP JSON-RPC 端点 URL"""
        return f"{self.base_url}/mcp/v1/{method}"

    def _build_payload(self, tool_name: str, arguments: Dict[str, Any]) -> Dict:
        """构造 JSON-RPC 2.0 请求体（使用 params.arguments，不使用 params.args）"""
        return {
            "jsonrpc": "2.0",
            "method": "tools/call",
            "params": {
                "name": tool_name,
                "arguments": arguments,
            },
            "id": self._next_id(),
        }

    def _build_headers(self) -> Dict[str, str]:
        """构造 HTTP 请求头（包含 Authorization Bearer，不含完整 token 日志输出）"""
        headers = {"Content-Type": "application/json; charset=utf-8"}
        if self.auth_token:
            headers["Authorization"] = f"Bearer {self.auth_token}"
        return headers

    # ── Mock 分支：工具分发 ────────────────────────────────────────

    def _mock_call_tool(self, tool_name: str, arguments: Dict[str, Any] | None = None) -> Dict:
        """Mock 模式：根据工具名分发给各自的 mock handler"""
        args = arguments or {}

        handlers = {
            "sys_info": self._mock_sys_info,
            "service_mgr": self._mock_service_mgr,
            "log_reader": self._mock_log_reader,
            "net_monitor": self._mock_net_monitor,
            "cmd_exec": self._mock_cmd_exec,
            "file_guard": self._mock_file_guard,
        }

        handler = handlers.get(tool_name)
        if handler is None:
            return _fail(f"Mock 未实现工具: {tool_name}")
        return handler(args)

    # ── Mock: sys_info ─────────────────────────────────────────────

    def _mock_sys_info(self, args: Dict[str, Any]) -> Dict:
        metric = args.get("metric", "all")

        if metric == "cpu":
            return _ok(_MOCK_CPU)
        if metric == "memory":
            return _ok(_MOCK_MEMORY)
        if metric == "disk":
            return _ok(_MOCK_DISK)
        if metric == "load":
            return _ok(_MOCK_LOAD)
        if metric == "uptime":
            return _ok(_MOCK_UPTIME)
        if metric == "all":
            return _ok(_MOCK_ALL)

        return _fail(f"不支持的 sys_info metric: {metric}")

    # ── Mock: service_mgr ──────────────────────────────────────────

    def _mock_service_mgr(self, args: Dict[str, Any]) -> Dict:
        action = (args.get("action") or "").strip().lower()
        service = (args.get("service") or args.get("name") or "").strip().lower()

        if not action or action not in _VALID_SERVICE_ACTIONS:
            return _fail(f"非法的 service_mgr action: {action or '(空)'}")

        if not service:
            return _fail("service 名称为空")

        # 禁止操作的核心服务
        if service in _FORBIDDEN_SERVICES:
            return _fail(f"禁止操作核心服务: {service}")

        # 只读操作
        if action in ("status", "is-active", "is-enabled"):
            return _ok({
                "action": action,
                "service": f"{service}.service",
                "output": f"● {service}.service - mock active service",
                "error_output": "",
                "exit_code": 0,
                "is_active": True,
                "parsed": {
                    "active_state": "active (running)",
                    "loaded": "loaded (/lib/systemd/system/{}.service; enabled)",
                },
                "mock": True,
            })

        # 变更操作（start / stop / restart / reload）— mock 模拟成功
        return _ok({
            "action": action,
            "service": f"{service}.service",
            "output": f"Mock {action} {service} — 未真实执行系统命令",
            "error_output": "",
            "exit_code": 0,
            "mock": True,
        })

    # ── Mock: log_reader ───────────────────────────────────────────

    def _mock_log_reader(self, args: Dict[str, Any]) -> Dict:
        log_type = args.get("type", "journalctl")
        source = (args.get("source") or args.get("service") or "").strip()
        lines_raw = args.get("lines", 50)
        keyword = args.get("keyword", "")

        # lines 校验
        try:
            lines = int(str(lines_raw))
        except (ValueError, TypeError):
            return _fail(f"lines 参数格式非法: {lines_raw}")
        if lines < 1:
            return _fail(f"lines 必须 >= 1: {lines}")
        if lines > 500:
            return _fail(f"lines 超过上限 500: {lines}")

        if log_type == "journalctl":
            if not source:
                return _fail("journalctl 模式缺少 service 参数")
        elif log_type == "file":
            if source not in _VALID_LOG_SOURCES:
                return _fail(f"不支持或不允许的日志源: {source}")
            # 拒绝敏感路径
            if source.startswith("/") and ("/etc/" in source or "/root/" in source):
                return _fail(f"不允许访问敏感路径: {source}")
        else:
            return _fail(f"不支持的 log_reader type: {log_type}")

        # 构造 mock 日志
        base_logs = [
            "2026-06-08 20:30:15 [error] mock upstream timed out",
            "2026-06-08 20:30:16 [error] mock connect() failed",
            "2026-06-08 20:30:18 [info] mock request processed",
            "2026-06-08 20:31:01 [warn] mock upstream response slow",
            "2026-06-08 20:31:02 [error] mock 502 Bad Gateway",
            "2026-06-08 20:31:05 [info] mock health check passed",
        ]

        if keyword:
            mock_logs = [l for l in base_logs if keyword.lower() in l.lower()]
        else:
            mock_logs = base_logs

        # 按 lines 截取
        mock_logs = mock_logs[:lines]

        return _ok({
            "type": log_type,
            "source": source,
            "lines": len(mock_logs),
            "logs": mock_logs,
            "mock": True,
        })

    # ── Mock: net_monitor ──────────────────────────────────────────

    def _mock_net_monitor(self, args: Dict[str, Any]) -> Dict:
        metric = args.get("metric", "all")
        port = args.get("port")

        if metric not in _VALID_NET_METRICS:
            return _fail(f"不支持的 net_monitor metric: {metric}")

        if metric == "connections":
            return _ok({
                "metric": "connections",
                "connections": [
                    {"proto": "tcp", "local": "0.0.0.0:22", "remote": "*:*", "state": "LISTEN"},
                    {"proto": "tcp", "local": "0.0.0.0:80", "remote": "*:*", "state": "LISTEN"},
                    {"proto": "tcp", "local": "0.0.0.0:443", "remote": "*:*", "state": "LISTEN"},
                ],
                "mock": True,
            })

        if metric == "traffic":
            return _ok({
                "metric": "traffic",
                "traffic": {
                    "bytes_sent": 1234567890,
                    "bytes_recv": 9876543210,
                    "packets_sent": 500000,
                    "packets_recv": 1200000,
                },
                "mock": True,
            })

        if metric == "interfaces":
            return _ok({
                "metric": "interfaces",
                "interfaces": [
                    {"name": "lo", "mac": "00:00:00:00:00:00", "ip": "127.0.0.1"},
                    {"name": "eth0", "mac": "08:00:27:ab:cd:ef", "ip": "192.168.56.102"},
                ],
                "mock": True,
            })

        if metric == "routes":
            return _ok({
                "metric": "routes",
                "routes": [
                    {"destination": "0.0.0.0/0", "gateway": "192.168.56.1", "iface": "eth0"},
                    {"destination": "192.168.56.0/24", "gateway": "0.0.0.0", "iface": "eth0"},
                ],
                "mock": True,
            })

        if metric == "dns":
            return _ok({
                "metric": "dns",
                "dns": {
                    "servers": ["8.8.8.8", "114.114.114.114"],
                    "search_domains": ["local"],
                },
                "mock": True,
            })

        if metric == "listen":
            listeners = [
                {"proto": "tcp", "local_address": "0.0.0.0:22", "process": "sshd", "pid": 1234},
                {"proto": "tcp", "local_address": "0.0.0.0:80", "process": "nginx", "pid": 5678},
                {"proto": "tcp", "local_address": "0.0.0.0:443", "process": "nginx", "pid": 5678},
            ]
            if port is not None:
                listeners = [l for l in listeners if str(port) in l["local_address"].split(":")[-1]]
            return _ok({
                "metric": "listen",
                "listeners": listeners,
                "mock": True,
            })

        # metric == "all"
        return _ok({
            "metric": "all",
            "connections": [
                {"proto": "tcp", "local": "0.0.0.0:80", "remote": "*:*", "state": "LISTEN"},
            ],
            "interfaces": [
                {"name": "eth0", "mac": "08:00:27:ab:cd:ef", "ip": "192.168.56.102"},
            ],
            "mock": True,
        })

    # ── Mock: cmd_exec ─────────────────────────────────────────────

    def _mock_cmd_exec(self, args: Dict[str, Any]) -> Dict:
        command = (args.get("command") or "").strip()
        if not command:
            return _fail("命令为空", {"blocked": True, "command": "", "reason": "命令为空"})

        # 高危命令检查
        normalized = command.lower().replace(" ", "")
        for high_risk, reason in [
            ("rm-rf/", "禁止递归删除根目录"),
            ("mkfs.ext4/dev/sda1", "禁止格式化磁盘"),
            ('echo"hack">/etc/passwd', "禁止写入 /etc/passwd"),
        ]:
            if high_risk in normalized:
                return _fail("命令被安全策略拦截", {
                    "blocked": True,
                    "command": command,
                    "reason": reason,
                    "mock": True,
                })

        # curl/wget 管道
        if ("curl" in normalized or "wget" in normalized) and "|" in command:
            return _fail("命令被安全策略拦截", {
                "blocked": True,
                "command": command,
                "reason": "禁止 curl/wget 管道执行脚本",
                "mock": True,
            })

        # chmod 777
        if "chmod777" in normalized:
            return _fail("命令被安全策略拦截", {
                "blocked": True,
                "command": command,
                "reason": "禁止 chmod 777 权限变更",
                "mock": True,
            })

        # dd 破坏性写入
        if command.startswith("dd ") and "of=/dev/" in command:
            return _fail("命令被安全策略拦截", {
                "blocked": True,
                "command": command,
                "reason": "禁止 dd 破坏性写入磁盘",
                "mock": True,
            })

        # 白名单检查
        if command not in _CMD_WHITELIST:
            return _fail("命令被安全策略拦截", {
                "blocked": True,
                "command": command,
                "reason": "命令不在白名单中或匹配高危模式",
                "mock": True,
            })

        # 白名单命令 mock 成功
        mock_stdout_map = {
            "df -h": "Filesystem      Size  Used Avail Use% Mounted on\n/dev/mock        40G   25G   15G  62% /",
            "free -m": "              total        used        free      shared  buff/cache   available\nMem:           8192        3688        2048         128        2456        4504",
            "uptime": " 20:35:01 up 3 days, 12:00,  1 user,  load average: 0.52, 0.31, 0.18",
            "whoami": "agent",
            "uname -a": "Linux kylin-v11 5.10.0 mock-generic #1 SMP 2026 x86_64 GNU/Linux",
            "ps aux": "USER       PID %CPU %MEM    VSZ   RSS TTY      STAT START   TIME COMMAND\nroot         1  0.0  0.1 225432  9216 ?        Ss   08:00   0:02 /sbin/init\nagent    12345  0.5  1.2 850432 98304 ?        Ssl  08:05   0:30 /usr/bin/python3",
            "systemctl status nginx": "● nginx.service - A high performance web server\n   Loaded: loaded (/lib/systemd/system/nginx.service; enabled)\n   Active: active (running) since Mon 2026-06-08 08:05:00 UTC",
            "journalctl -u nginx -n 50": "Jun 08 20:30:15 kylin-v11 nginx[1234]: mock log entry 1\nJun 08 20:30:16 kylin-v11 nginx[1234]: mock log entry 2",
        }

        return _ok({
            "stdout": mock_stdout_map.get(command, f"mock output for: {command}"),
            "stderr": "",
            "returncode": 0,
            "execution_time": 0.01,
            "mock": True,
        })

    # ── Mock: file_guard ───────────────────────────────────────────

    def _mock_file_guard(self, args: Dict[str, Any]) -> Dict:
        action = (args.get("action") or "").strip()
        path = (args.get("path") or "").strip()

        if not action:
            return _fail("file_guard action 为空")
        if action not in ("check", "read", "write"):
            return _fail(f"非法的 file_guard action: {action}")
        if not path:
            return _fail("file_guard path 为空")

        # 敏感后缀检查
        if path.lower().endswith(_SENSITIVE_EXTS):
            return _fail(f"禁止访问密钥/证书文件: {path}")

        # 受保护路径
        is_protected = any(path.startswith(p) for p in _PROTECTED_PATHS)

        if action == "check":
            return _ok({
                "path": path,
                "is_protected": is_protected,
                "reason": f"路径在保护清单中: {path}" if is_protected else "路径不在保护清单中",
                "real_path": path,
                "exists": True,
                "is_file": True,
                "size": 3421,
                "permissions": "600" if is_protected else "644",
                "mock": True,
            })

        if action == "read":
            # 只允许 /var/log/ 下路径
            if is_protected:
                return _fail(f"禁止读取受保护路径: {path}")
            if not path.startswith("/var/log/"):
                return _fail(f"只允许读取 /var/log/ 下路径: {path}")
            return _ok({
                "path": path,
                "content": f"[mock content of {path}]",
                "size": 1024,
                "mock": True,
            })

        # action == "write"
        if is_protected:
            return _fail(f"禁止写入受保护路径: {path}")
        if not (path.startswith("/tmp/") or path.startswith("/opt/mcp-server/")):
            return _fail(f"只允许写入 /tmp/ 或 /opt/mcp-server/ 下路径: {path}")
        return _ok({
            "path": path,
            "written": True,
            "bytes": len(args.get("content", "")),
            "mock": True,
        })

    # ── Real 模式错误分类（静态/类方法，便于测试） ─────────────

    @staticmethod
    def _handle_http_error(status_code: int) -> Dict:
        """HTTP 非 2xx 状态码 → 脱敏错误响应"""
        mapping = {
            401: "MCP Server 认证失败",
            403: "MCP Server 权限不足",
            404: "MCP 工具调用接口不存在",
            400: "MCP 请求参数错误",
            422: "MCP 请求参数错误",
            500: "MCP Server 内部错误",
            502: "MCP Server 内部错误",
            503: "MCP Server 内部错误",
            504: "MCP Server 内部错误",
        }
        msg = mapping.get(status_code)
        if msg:
            return _fail(msg)
        return _fail("MCP Server 响应异常")

    @staticmethod
    def _handle_jsonrpc_error(error_obj: Any) -> Dict:
        """JSON-RPC error 对象 → 脱敏错误响应（按 code 分类）"""
        code = error_obj.get("code", 0) if isinstance(error_obj, dict) else 0
        mapping = {
            -32700: "MCP 返回 JSON 解析错误",
            -32600: "MCP 请求格式无效",
            -32601: "MCP 方法或工具不存在",
            -32602: "MCP 工具参数错误",
            -32603: "MCP 工具内部错误",
            -32000: "MCP 命令执行失败",
            -32001: "MCP Token 认证失败",
        }
        msg = mapping.get(code)
        if msg:
            return _fail(msg)
        return _fail("MCP 调用失败")

    @staticmethod
    def _is_blocked_result(result: Any) -> bool:
        """判断 MCP result 是否为安全拦截"""
        return isinstance(result, dict) and result.get("blocked") is True

    # ── 主调用入口 ───────────────────────────────────────────────

    async def call_tool(
        self,
        tool_name: str,
        arguments: Dict[str, Any] | None = None,
    ) -> Dict:
        """
        调用 MCP 执行器工具

        参数:
            tool_name: 工具名（sys_info, service_mgr, log_reader, net_monitor, cmd_exec, file_guard）
            arguments: 工具参数，可选，默认为空字典

        返回:
            {"ok": bool, "result": dict | None, "error": str | None}
        """
        arguments = arguments or {}

        if self.mode == "mock":
            return self._mock_call_tool(tool_name, arguments)

        # Real 模式：检查认证令牌
        if not self.auth_token:
            logger.warning(f"[MCP] real 模式缺少 MCP_AUTH_TOKEN")
            return _fail("MCP 认证令牌未配置")

        return await self._real_call_tool(tool_name, arguments)

    async def _real_call_tool(self, tool_name: str, arguments: Dict[str, Any]) -> Dict:
        """Real 模式：HTTP JSON-RPC 2.0 调用 + 完整错误分类"""
        payload = self._build_payload(tool_name, arguments)
        headers = self._build_headers()
        url = self._build_url()

        try:
            timeout = httpx.Timeout(self.timeout + 5.0, connect=5.0)
            async with httpx.AsyncClient(timeout=timeout) as client:
                resp = await client.post(url, headers=headers, json=payload)

                # ── HTTP 非 2xx ──
                if resp.status_code >= 400:
                    logger.warning(
                        f"[MCP] HTTP {resp.status_code} — {tool_name}"
                    )
                    return self._handle_http_error(resp.status_code)

                # ── JSON 解析 ──
                try:
                    data = resp.json()
                except Exception:
                    logger.error(f"[MCP] JSON 解析失败 — {tool_name}")
                    return _fail("MCP Server 返回格式错误")

                # ── JSON-RPC error ──
                if "error" in data:
                    logger.warning(f"[MCP] JSON-RPC error — {tool_name}: {data['error']}")
                    return self._handle_jsonrpc_error(data["error"])

                # ── 缺少 result ──
                if "result" not in data:
                    logger.error(f"[MCP] 缺少 result — {tool_name}")
                    return _fail("MCP Server 返回内容不完整")

                result = data["result"]

                # ── result.blocked ──
                if self._is_blocked_result(result):
                    logger.warning(
                        f"[MCP] 工具 {tool_name} 被 MCP Server 安全拦截: "
                        f"{result.get('reason', '未知原因')}"
                    )
                    return _fail("命令被安全策略拦截", result)

                logger.info(f"[MCP] 工具 {tool_name} 调用成功")
                return _ok(result)

        except httpx.TimeoutException:
            logger.error(f"[MCP] 工具 {tool_name} 调用超时")
            return _fail("MCP Server 请求超时")
        except httpx.ConnectError:
            logger.error(f"[MCP] MCP Server 连接失败 — {tool_name}")
            return _fail("MCP Server 连接失败")
        except Exception:
            logger.exception(f"[MCP] 工具调用异常 — {tool_name}")
            return _fail("MCP 工具调用异常")

    # ── 便捷方法 ────────────────────────────────────────────────

    async def get_system_metrics(self) -> Dict:
        """获取系统指标（调用 sys_info 工具）"""
        return await self.call_tool("sys_info", {"metric": "all"})

    async def list_tools(self) -> Dict:
        """列出 MCP Server 上可用的工具（如果 Server 支持）"""
        if self.mode == "mock":
            return _ok({"tools": []})

        if not self.auth_token:
            logger.warning("[MCP] list_tools 缺少 MCP_AUTH_TOKEN")
            return _fail("MCP 认证令牌未配置")

        payload = {
            "jsonrpc": "2.0",
            "method": "tools/list",
            "id": self._next_id(),
        }
        headers = self._build_headers()
        url = self._build_url("tools/list")

        try:
            timeout = httpx.Timeout(10.0, connect=5.0)
            async with httpx.AsyncClient(timeout=timeout) as client:
                resp = await client.post(url, headers=headers, json=payload)

                # ── HTTP 非 2xx ──
                if resp.status_code >= 400:
                    logger.warning(
                        f"[MCP] list_tools HTTP {resp.status_code}"
                    )
                    return self._handle_http_error(resp.status_code)

                # ── JSON 解析 ──
                try:
                    data = resp.json()
                except Exception:
                    logger.error("[MCP] list_tools JSON 解析失败")
                    return _fail("MCP Server 返回格式错误")

                # ── JSON-RPC error ──
                if "error" in data:
                    logger.warning(f"[MCP] list_tools JSON-RPC error: {data['error']}")
                    return self._handle_jsonrpc_error(data["error"])

                # ── 提取 result ──
                return _ok(data.get("result", data))

        except httpx.TimeoutException:
            logger.error("[MCP] 获取工具列表超时")
            return _fail("获取工具列表超时")
        except httpx.ConnectError:
            logger.error("[MCP] MCP Server 连接失败 — tools/list")
            return _fail("MCP Server 连接失败")
        except Exception:
            logger.exception("[MCP] 获取工具列表异常")
            return _fail("获取工具列表失败")
