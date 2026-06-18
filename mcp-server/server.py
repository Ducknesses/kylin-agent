"""MCP Server 主入口：HTTP JSON-RPC 2.0 服务"""
import hmac
import json
import logging
import logging.handlers
import os
import signal
import sys
import traceback
from http.server import HTTPServer, BaseHTTPRequestHandler

# 确保能找到同目录模块
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from config import config
from plugins import sys_info, service_mgr, log_reader, net_monitor, cmd_exec, file_guard

# ============================================================
# 工具注册表
# ============================================================
TOOLS = {
    "sys_info": sys_info.handle,
    "service_mgr": service_mgr.handle,
    "log_reader": log_reader.handle,
    "net_monitor": net_monitor.handle,
    "cmd_exec": cmd_exec.handle,
    "file_guard": file_guard.handle,
}

# JSON-RPC 2.0 标准错误码
JSONRPC_ERRORS = {
    "PARSE_ERROR": (-32700, "解析错误"),
    "INVALID_REQUEST": (-32600, "无效请求"),
    "METHOD_NOT_FOUND": (-32601, "未知工具名"),
    "INVALID_PARAMS": (-32602, "参数错误"),
    "INTERNAL_ERROR": (-32603, "内部错误"),
    "COMMAND_BLOCKED": (-32600, "命令被安全策略拦截"),
    "EXECUTION_FAILED": (-32000, "命令执行失败"),
}


def setup_logging():
    """配置日志：同时输出到文件和控制台"""
    log_format = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # 文件日志（带轮转，每个文件10MB保留3个）
    log_dir = os.path.dirname(config.LOG_FILE)
    if log_dir and not os.path.exists(log_dir):
        os.makedirs(log_dir, exist_ok=True)

    try:
        file_handler = logging.handlers.RotatingFileHandler(
            config.LOG_FILE,
            maxBytes=10 * 1024 * 1024,
            backupCount=3,
            encoding="utf-8",
        )
        file_handler.setFormatter(log_format)
        file_handler.setLevel(getattr(logging, config.LOG_LEVEL.upper(), logging.INFO))
    except PermissionError:
        # 无权写入 /var/log 时回退到当前目录
        fallback_log = os.path.join(os.path.dirname(__file__), "mcp-server.log")
        file_handler = logging.StreamHandler()
        file_handler.setFormatter(log_format)
        file_handler.setLevel(logging.DEBUG)
        print(f"[WARN] 无法写入 {config.LOG_FILE}，日志输出到控制台")

    # 控制台日志
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(log_format)
    console_handler.setLevel(getattr(logging, config.LOG_LEVEL.upper(), logging.INFO))

    root_logger = logging.getLogger()
    root_logger.setLevel(logging.DEBUG)
    root_logger.addHandler(file_handler)
    root_logger.addHandler(console_handler)

    return root_logger


def make_jsonrpc_error(code: int, message: str, req_id=None, extra: dict = None) -> dict:
    """构造 JSON-RPC 2.0 错误响应"""
    error = {"code": code, "message": message}
    if extra:
        error["data"] = extra
    return {"jsonrpc": "2.0", "error": error, "id": req_id}


def make_jsonrpc_response(result, req_id=None) -> dict:
    """构造 JSON-RPC 2.0 成功响应"""
    return {"jsonrpc": "2.0", "result": result, "id": req_id}


def handle_tools_list(req_id=None) -> dict:
    """列出所有可用工具及其参数定义"""
    tool_defs = {
        "sys_info": {
            "description": "获取系统信息（CPU、内存、磁盘、负载）",
            "parameters": {
                "metric": "cpu|memory|disk|load|uptime|all",
            },
        },
        "service_mgr": {
            "description": "管理系统服务",
            "parameters": {
                "action": "status|start|stop|restart",
                "service": "服务名称",
            },
        },
        "log_reader": {
            "description": "读取系统日志，支持关键词过滤",
            "parameters": {
                "type": "journalctl|file",
                "source": "日志来源（别名或路径）",
                "lines": "行数",
                "service": "服务名（journalctl模式）",
                "since": "时间范围",
                "keyword": "关键词过滤（可选）",
            },
        },
        "net_monitor": {
            "description": "网络监控信息（连接/流量/网卡/路由/DNS/监听端口）",
            "parameters": {
                "metric": "connections|traffic|interfaces|routes|dns|listen|all",
                "port": "端口号（listen模式筛选，可选）",
            },
        },
        "cmd_exec": {
            "description": "在沙箱中安全执行系统命令",
            "parameters": {
                "command": "要执行的命令",
                "timeout": "超时秒数（默认30）",
                "user": "执行用户（默认agent-read）",
            },
        },
        "file_guard": {
            "description": "文件安全检查、读取与安全写入（带审计日志）",
            "parameters": {
                "action": "check|read|write",
                "path": "文件路径",
                "content": "写入内容（write 操作）",
                "max_size": "最大读取字节数（默认1MB）",
            },
        },
    }
    return make_jsonrpc_response({"tools": list(TOOLS.keys()), "definitions": tool_defs}, req_id)


def process_request(method: str, params: dict, req_id=None) -> dict:
    """
    处理单个 JSON-RPC 请求

    method: "tools/call" | "tools/list" | "ping"
    params: {"name": "sys_info", "arguments": {"metric": "cpu"}}  (tools/call)
    """
    # 方法路由
    if method == "ping":
        return make_jsonrpc_response({"pong": True, "version": "1.0.0", "tools_count": len(TOOLS)}, req_id)

    if method == "tools/list":
        return handle_tools_list(req_id)

    if method == "tools/call":
        tool_name = params.get("name", "")
        arguments = params.get("arguments", {})

        if not tool_name:
            return make_jsonrpc_error(*JSONRPC_ERRORS["INVALID_PARAMS"], req_id,
                                      extra={"detail": "缺少参数: name"})

        if tool_name not in TOOLS:
            return make_jsonrpc_error(*JSONRPC_ERRORS["METHOD_NOT_FOUND"], req_id,
                                      extra={"detail": f"未知工具: {tool_name}", "available": list(TOOLS.keys())})

        # 调用工具处理函数
        logger.info("[Server] 调用工具: %s, 参数: %s", tool_name, arguments)
        try:
            result = TOOLS[tool_name](arguments)
            return make_jsonrpc_response(result, req_id)
        except Exception as e:
            tb = traceback.format_exc()
            logger.error("[Server] 工具 %s 执行异常:\n%s", tool_name, tb)
            return make_jsonrpc_error(*JSONRPC_ERRORS["INTERNAL_ERROR"], req_id,
                                      extra={"detail": str(e), "tool": tool_name})

    # 未知方法
    return make_jsonrpc_error(*JSONRPC_ERRORS["METHOD_NOT_FOUND"], req_id,
                              extra={"detail": f"未知方法: {method}"})


class MCPHandler(BaseHTTPRequestHandler):
    """HTTP 请求处理器"""

    # 禁止日志输出到标准错误（用logging替代）
    def log_message(self, format, *args):
        logger.debug("HTTP: %s", format % args)

    def _send_json(self, data: dict, status: int = 200):
        """发送 JSON 响应"""
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "http://localhost:5173")
        self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        """处理 CORS 预检请求"""
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "http://localhost:5173")
        self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
        self.end_headers()

    def _verify_token(self):
        """Bearer Token 认证：使用 hmac.compare_digest 防时序攻击"""
        auth = self.headers.get("Authorization", "")
        if not auth.startswith("Bearer "):
            return False
        token = auth[7:]
        return hmac.compare_digest(token, config.API_TOKEN)

    def do_GET(self):
        """GET 请求返回健康检查信息"""
        if self.path in ("/", "/health", "/ping"):
            self._send_json({
                "status": "ok",
                "service": "MCP Server for Kylin OS",
                "version": "1.0.0",
                "tools": len(TOOLS),
                "available_tools": list(TOOLS.keys()),
            })
        else:
            self._send_json({"error": "仅支持 POST 到 /mcp/v1/tools/ 接口"}, 404)

    def do_POST(self):
        """POST 请求处理 JSON-RPC 2.0 调用"""

        # Bearer Token 认证（所有 POST 请求强制校验）
        if not self._verify_token():
            self._send_json(
                make_jsonrpc_error(-32001, "未授权：Token 认证失败", None,
                                   extra={"detail": "请在 Authorization 头中提供有效的 Bearer Token"}),
                401,
            )
            return

        # 检查路径
        if self.path not in ("/mcp/v1/tools/call", "/mcp/v1/tools/list", "/mcp/v1/rpc", "/jsonrpc"):
            self._send_json(
                {"error": "未找到接口，请使用 /mcp/v1/tools/call"},
                404,
            )
            return

        # 读取请求体
        content_length = int(self.headers.get("Content-Length", 0))
        if content_length == 0:
            self._send_json(
                make_jsonrpc_error(*JSONRPC_ERRORS["INVALID_REQUEST"], None,
                                   extra={"detail": "请求体为空"}),
                400,
            )
            return

        if content_length > 10 * 1024 * 1024:  # 10MB上限
            self._send_json(
                make_jsonrpc_error(*JSONRPC_ERRORS["INVALID_REQUEST"], None,
                                   extra={"detail": "请求体过大"}),
                413,
            )
            return

        raw_body = self.rfile.read(content_length).decode("utf-8")

        # 解析 JSON
        try:
            request_data = json.loads(raw_body)
        except json.JSONDecodeError as e:
            logger.warning("[Server] JSON解析失败: %s", e)
            self._send_json(
                make_jsonrpc_error(*JSONRPC_ERRORS["PARSE_ERROR"], None,
                                   extra={"detail": str(e)}),
                400,
            )
            return

        # 验证 JSON-RPC 2.0 格式
        if not isinstance(request_data, dict):
            self._send_json(
                make_jsonrpc_error(*JSONRPC_ERRORS["INVALID_REQUEST"], None,
                                   extra={"detail": "请求必须是JSON对象"}),
                400,
            )
            return

        jsonrpc = request_data.get("jsonrpc", "")
        if jsonrpc != "2.0":
            self._send_json(
                make_jsonrpc_error(*JSONRPC_ERRORS["INVALID_REQUEST"], None,
                                   extra={"detail": "jsonrpc字段必须为'2.0'"}),
                400,
            )
            return

        method = request_data.get("method", "")
        params = request_data.get("params", {})
        req_id = request_data.get("id")

        if not method:
            self._send_json(
                make_jsonrpc_error(*JSONRPC_ERRORS["INVALID_REQUEST"], req_id,
                                   extra={"detail": "缺少method字段"}),
                400,
            )
            return

        # 处理请求
        logger.info(
            "[Server] 收到请求: id=%s, method=%s, params=%s",
            req_id, method, str(params)[:200],
        )

        response = process_request(method, params, req_id)

        # 如果是通知（无id），不返回响应
        if req_id is None:
            self._send_json({"jsonrpc": "2.0", "result": None}, 204)
            return

        self._send_json(response)


def create_server():
    """创建并配置 HTTP Server"""
    server = HTTPServer((config.HOST, config.PORT), MCPHandler)

    # 设置超时
    server.timeout = 5

    return server


logger = None  # 模块级，setup_logging() 后设置


def main():
    """启动 MCP Server"""
    global logger
    logger = setup_logging()

    logger.info("=" * 60)
    logger.info("MCP Server for Kylin OS Agent 启动中...")
    logger.info("监听地址: %s:%d", config.HOST, config.PORT)
    logger.info("已注册工具 (%d): %s", len(TOOLS), list(TOOLS.keys()))
    logger.info("日志文件: %s", config.LOG_FILE)
    logger.info("=" * 60)

    # 优雅退出处理
    server = create_server()

    def shutdown_handler(signum, frame):
        logger.info("收到信号 %s，正在关闭服务器...", signum)
        server.shutdown()
        logger.info("MCP Server 已停止")

    signal.signal(signal.SIGTERM, shutdown_handler)
    signal.signal(signal.SIGINT, shutdown_handler)

    try:
        logger.info("MCP Server 已启动，等待请求...")
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    except Exception as e:
        logger.exception("服务器异常: %s", e)
        sys.exit(1)
    finally:
        server.server_close()
        logger.info("MCP Server 已关闭")


if __name__ == "__main__":
    main()