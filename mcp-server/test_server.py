#!/usr/bin/env python3
"""MCP Server 启动与功能验证测试脚本"""

import subprocess
import sys
import time
import json
import os
import signal
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError

BASE_URL = "http://localhost:8001"

# ---------- 工具函数 ----------

def http_post(path: str, data: dict) -> tuple:
    """发送 JSON-RPC POST 请求，返回 (status, body_dict)"""
    url = f"{BASE_URL}{path}"
    body = json.dumps(data).encode("utf-8")
    headers = {
        "Content-Type": "application/json",
        "Authorization": "Bearer change-me-in-production",
    }
    req = Request(url, data=body, headers=headers)
    try:
        with urlopen(req, timeout=5) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except HTTPError as e:
        return e.code, json.loads(e.read().decode("utf-8"))
    except URLError as e:
        return 0, {"error": str(e.reason)}


def http_get(path: str) -> dict:
    """发送 GET 请求"""
    url = f"{BASE_URL}{path}"
    try:
        with urlopen(url, timeout=5) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except HTTPError as e:
        return json.loads(e.read().decode("utf-8"))
    except URLError as e:
        return {"error": str(e.reason)}


def check(condition, label: str) -> bool:
    status = "✓ PASS" if condition else "✗ FAIL"
    print(f"  [{status}] {label}")
    return condition


# ---------- 主流程 ----------

def main():
    all_passed = True

    # 1. 清理旧进程
    print("=" * 50)
    print("[步骤 1] 清理端口 8001 上可能残留的旧进程...")
    subprocess.run(
        "lsof -ti :8001 | xargs -r kill -9 2>/dev/null",
        shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
    )
    time.sleep(0.5)
    print("  清理完成")
    print()

    # 2. 启动 server.py
    print("[步骤 2] 启动 MCP Server (后台子进程)...")
    server_dir = os.path.dirname(os.path.abspath(__file__))
    log_path = os.path.join(server_dir, "test_server.log")
    log_fp = open(log_path, "w")

    proc = subprocess.Popen(
        [sys.executable, "server.py"],
        cwd=server_dir,
        stdout=log_fp,
        stderr=log_fp,
    )
    print(f"  Server PID={proc.pid}, 日志={log_path}")

    # 3. 等待服务就绪（最多等 5 秒）
    print("[步骤 3] 等待服务就绪...")
    ready = False
    for i in range(10):
        try:
            urlopen(f"{BASE_URL}/", timeout=1)
            ready = True
            break
        except URLError:
            time.sleep(0.5)
    if not ready:
        print("  ✗ FAIL: 服务启动超时")
        print("  最近日志:")
        os.system(f"tail -20 {log_path}")
        proc.kill()
        sys.exit(1)
    print("  服务已就绪")
    print()

    try:
        # ---- 测试 1: GET / 健康检查 ----
        print("[测试 1] GET / 健康检查")
        resp = http_get("/")
        ok = check(
            resp.get("status") == "ok" and resp.get("service", "").startswith("MCP Server"),
            f"status=ok, service=MCP Server, tools={resp.get('tools')}"
        )
        all_passed = all_passed and ok
        print(f"  响应: {json.dumps(resp, ensure_ascii=False)}")
        print()

        # ---- 测试 2: JSON-RPC ping ----
        print("[测试 2] JSON-RPC ping")
        status, resp = http_post("/jsonrpc", {"jsonrpc": "2.0", "method": "ping", "id": 1})
        ok1 = check(status == 200, f"HTTP 200 (got {status})")
        ok2 = check(
            resp.get("result", {}).get("pong") is True,
            f"pong=True, version={resp.get('result', {}).get('version')}"
        )
        all_passed = all_passed and ok1 and ok2
        print(f"  响应: {json.dumps(resp, ensure_ascii=False)}")
        print()

        # ---- 测试 3: JSON-RPC tools/list ----
        print("[测试 3] JSON-RPC tools/list")
        status, resp = http_post("/jsonrpc", {"jsonrpc": "2.0", "method": "tools/list", "id": 2})
        tools_list = resp.get("result", {}).get("tools", [])
        ok = check(
            status == 200 and len(tools_list) >= 6,
            f"HTTP 200, 工具数={len(tools_list)} (期望 >= 6)"
        )
        all_passed = all_passed and ok
        print(f"  工具: {tools_list}")
        print()

        # ---- 测试 4: JSON-RPC tools/call (sys_info) ----
        print("[测试 4] JSON-RPC tools/call → sys_info (metric=cpu)")
        status, resp = http_post("/jsonrpc", {
            "jsonrpc": "2.0",
            "method": "tools/call",
            "params": {"name": "sys_info", "arguments": {"metric": "cpu"}},
            "id": 3
        })
        result = resp.get("result", {})
        ok = check(
            status == 200 and "cpu" in result and "timestamp" in result,
            f"HTTP 200, result包含cpu+timestamp (keys={list(result.keys())})"
        )
        all_passed = all_passed and ok
        if "cpu" in result:
            print(f"  cpu data: {json.dumps(result['cpu'], ensure_ascii=False)[:200]}")
        if "timestamp" in result:
            print(f"  timestamp: {result['timestamp']}")
        print()

        # ---- 测试 5: 未知方法应返回错误 ----
        print("[测试 5] JSON-RPC 未知方法 → 应返回错误")
        status, resp = http_post("/jsonrpc", {"jsonrpc": "2.0", "method": "no_such_method", "id": 4})
        ok = check(
            resp.get("error", {}).get("code") == -32601,
            f"error.code=-32601 (METHOD_NOT_FOUND)"
        )
        all_passed = all_passed and ok
        print(f"  响应: {json.dumps(resp, ensure_ascii=False)}")
        print()

        # ---- 测试 6: 未知路径返回 404 ----
        print("[测试 6] GET /nonexistent → 应返回 404")
        resp = http_get("/nonexistent")
        ok = check(
            "error" in resp,
            "返回了错误信息"
        )
        all_passed = all_passed and ok
        print(f"  响应: {json.dumps(resp, ensure_ascii=False)}")
        print()

        # ---- 测试 7: file_guard write 操作 ----
        print("[测试 7] file_guard → write 写入测试文件")
        status, resp = http_post("/jsonrpc", {
            "jsonrpc": "2.0",
            "method": "tools/call",
            "params": {"name": "file_guard", "arguments": {
                "action": "write",
                "path": "/tmp/p1-test-write.txt",
                "content": "P1 test content for file_guard write"
            }},
            "id": 5
        })
        result = resp.get("result", {})
        ok = check(
            status == 200 and "bytes_written" in result and "new_hash_prefix" in result,
            f"write 成功, bytes_written={result.get('bytes_written')}, hash={result.get('new_hash_prefix')}"
        )
        all_passed = all_passed and ok
        print(f"  响应: {json.dumps(result, ensure_ascii=False)}")
        print()

        # ---- 测试 8: file_guard write 拒绝高危路径 ----
        print("[测试 8] file_guard → write 拒绝写入 /etc/passwd")
        status, resp = http_post("/jsonrpc", {
            "jsonrpc": "2.0",
            "method": "tools/call",
            "params": {"name": "file_guard", "arguments": {
                "action": "write",
                "path": "/etc/passwd",
                "content": "malicious"
            }},
            "id": 6
        })
        result = resp.get("result", {})
        ok = check(
            result.get("risk_level") == "high" or "error" in str(result),
            "write /etc/passwd 被拒绝"
        )
        all_passed = all_passed and ok
        print(f"  响应: {json.dumps(result, ensure_ascii=False)[:200]}")
        print()

        # ---- 测试 9: net_monitor listen 模式 ----
        print("[测试 9] net_monitor → listen 监听端口查询")
        status, resp = http_post("/jsonrpc", {
            "jsonrpc": "2.0",
            "method": "tools/call",
            "params": {"name": "net_monitor", "arguments": {"metric": "listen"}},
            "id": 7
        })
        result = resp.get("result", {})
        ok = check(
            status == 200 and "listeners" in result,
            f"listen 成功, 监听端口数={result.get('listeners', {}).get('total', 0)}"
        )
        all_passed = all_passed and ok
        print(f"  listeners total: {result.get('listeners', {}).get('total', 'N/A')}")
        print()

        # ---- 测试 10: net_monitor listen 按端口筛选 ----
        print("[测试 10] net_monitor → listen port=8001 筛选")
        status, resp = http_post("/jsonrpc", {
            "jsonrpc": "2.0",
            "method": "tools/call",
            "params": {"name": "net_monitor", "arguments": {"metric": "listen", "port": 8001}},
            "id": 8
        })
        result = resp.get("result", {})
        ok = check(
            status == 200 and "listeners" in result,
            f"listen port=8001 成功, 匹配数={result.get('listeners', {}).get('total', 0)}"
        )
        all_passed = all_passed and ok
        print(f"  listeners total: {result.get('listeners', {}).get('total', 'N/A')}")
        print()

        # ---- 测试 11: log_reader keyword 过滤 ----
        print("[测试 11] log_reader → keyword 过滤测试")
        status, resp = http_post("/jsonrpc", {
            "jsonrpc": "2.0",
            "method": "tools/call",
            "params": {"name": "log_reader", "arguments": {
                "type": "file",
                "source": "syslog",
                "lines": 20,
                "keyword": "systemd"
            }},
            "id": 9
        })
        result = resp.get("result", {})
        # 兼容文件不存在的情况（非麒麟 V11 环境 /var/log/syslog 可能也不存在）
        has_keyword = "keyword" in result
        ok = check(
            status == 200 and ("content" in result or "error" in result),
            f"keyword 过滤请求完成, keyword={result.get('keyword')}, has_content={'content' in result}"
        )
        all_passed = all_passed and ok
        print(f"  响应: keyword={result.get('keyword')}, lines={result.get('lines_returned')}")
        print()

    finally:
        # 4. 关闭服务
        print("[清理] 关闭 MCP Server...")
        proc.send_signal(signal.SIGTERM)
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
        log_fp.close()
        print(f"  进程 {proc.pid} 已终止")
        print(f"  完整日志见: {log_path}")

    # 结果汇总
    print()
    print("=" * 50)
    if all_passed:
        print("  ✓ 全部测试通过！MCP Server 运行正常。")
    else:
        print("  ✗ 存在失败的测试，请检查上方输出。")
    print("=" * 50)

    return 0 if all_passed else 1


if __name__ == "__main__":
    sys.exit(main())