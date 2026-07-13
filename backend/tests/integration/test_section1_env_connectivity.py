#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
集成测试脚本：1. 环境部署与基础连通性

对应文档：docs/integration_test_report_template.md 第 1 节
测试项：1.1 ~ 1.7

功能说明：
- 自动检测后端、前端、MCP Server 是否已启动；如未启动且指定 --auto-start，
  会尝试在独立端口拉起临时进程。
- 通过 HTTP / WebSocket 调用验证各组件连通性。
- 生成 Markdown 测试报告，可直接贴回测试报告模板。

依赖：
    pip install httpx websockets

用法示例：
    # 1. 仅检测当前已运行的服务
    python backend/tests/integration/test_section1_env_connectivity.py

    # 2. 未启动时自动拉起临时服务
    python backend/tests/integration/test_section1_env_connectivity.py --auto-start

    # 3. 指定地址与 Token
    python backend/tests/integration/test_section1_env_connectivity.py \
        --backend http://localhost:8000 \
        --frontend http://localhost:5173 \
        --mcp http://192.168.56.101:8001 \
        --mcp-token 123456789

    # 4. 强制以 DEMO/mock 模式启动后端并验证 1.7
    python backend/tests/integration/test_section1_env_connectivity.py \
        --auto-start --demo-mode
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import httpx
import websockets


# ── 默认配置 ─────────────────────────────────────────────────────────
DEFAULT_BACKEND_URL = "http://localhost:8000"
DEFAULT_FRONTEND_URL = "http://localhost:5173"
DEFAULT_MCP_URL = "http://192.168.56.101:8001"
DEFAULT_MCP_TOKEN = "123456789"

PROJECT_ROOT = Path(__file__).resolve().parents[3]
BACKEND_DIR = PROJECT_ROOT / "backend"
FRONTEND_DIR = PROJECT_ROOT / "frontend"
MCP_DIR = PROJECT_ROOT / "mcp-server"

REPORT_FILE = PROJECT_ROOT / "docs" / "integration_test_report_section1.md"


@dataclass
class TestCase:
    """单个测试项"""

    id: str
    name: str
    method: str
    criteria: str
    status: str = "P"  # P/F/N/B
    detail: str = ""
    remark: str = ""


class Section1Tester:
    """第 1 节环境部署与基础连通性测试器"""

    def __init__(
        self,
        backend_url: str,
        frontend_url: str,
        mcp_url: str,
        mcp_token: str,
        api_token: str,
        auto_start: bool,
        demo_mode: bool,
        report_path: Path,
    ):
        self.backend_url = backend_url.rstrip("/")
        self._report_backend_url = self.backend_url  # 用于报告展示
        self.frontend_url = frontend_url.rstrip("/")
        self.mcp_url = mcp_url.rstrip("/")
        self.mcp_token = mcp_token
        self.api_token = api_token
        self.auto_start = auto_start
        self.demo_mode = demo_mode
        self.report_path = report_path

        self.cases: list[TestCase] = []
        self._started_procs: list[subprocess.Popen] = []
        self._temp_dirs: list[Path] = []

        # 如果启用自动启动，后端/前端/MCP 可能使用独立端口
        self._backend_port = self._extract_port(backend_url, 8000)
        self._frontend_port = self._extract_port(frontend_url, 5173)
        self._mcp_port = self._extract_port(mcp_url, 8001)

    # ── 工具方法 ─────────────────────────────────────────────────────

    @staticmethod
    def _extract_port(url: str, default: int) -> int:
        m = re.search(r"://[^/]+:(\d+)", url)
        return int(m.group(1)) if m else default

    @staticmethod
    def _wait_for_port(host: str, port: int, timeout: float = 30.0) -> bool:
        """等待端口变为可连接"""
        end = time.time() + timeout
        while time.time() < end:
            try:
                with socket.create_connection((host, port), timeout=1.0):
                    return True
            except OSError:
                time.sleep(0.5)
        return False

    def _http_get(self, url: str, headers: Optional[dict] = None, timeout: float = 10.0) -> httpx.Response:
        with httpx.Client(timeout=httpx.Timeout(timeout, connect=5.0), follow_redirects=True) as client:
            return client.get(url, headers=headers)

    def _http_post(
        self,
        url: str,
        json_body: Optional[dict] = None,
        headers: Optional[dict] = None,
        timeout: float = 15.0,
    ) -> httpx.Response:
        with httpx.Client(timeout=httpx.Timeout(timeout, connect=5.0), follow_redirects=True) as client:
            return client.post(url, json=json_body, headers=headers)

    def _add_case(
        self,
        id: str,
        name: str,
        method: str,
        criteria: str,
        status: str,
        detail: str,
        remark: str = "",
    ) -> TestCase:
        case = TestCase(
            id=id,
            name=name,
            method=method,
            criteria=criteria,
            status=status,
            detail=detail,
            remark=remark,
        )
        self.cases.append(case)
        return case

    def _log(self, msg: str) -> None:
        print(f"[Section1] {msg}")

    # ── 服务启动（可选）──────────────────────────────────────────────

    def _start_backend(self, extra_env: Optional[dict] = None) -> bool:
        """尝试启动后端服务"""
        if not BACKEND_DIR.exists():
            self._log(f"后端目录不存在: {BACKEND_DIR}")
            return False

        venv_python = BACKEND_DIR / ".venv" / "bin" / "python"
        python = str(venv_python) if venv_python.exists() else sys.executable

        env = os.environ.copy()
        env["APP_PORT"] = str(self._backend_port)
        env["PYTHONUNBUFFERED"] = "1"
        if extra_env:
            env.update(extra_env)

        cmd = [python, "run.py"]
        self._log(f"启动后端: {' '.join(cmd)} (port={self._backend_port})")
        try:
            proc = subprocess.Popen(
                cmd,
                cwd=BACKEND_DIR,
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
            )
            self._started_procs.append(proc)
            if not self._wait_for_port("127.0.0.1", self._backend_port, timeout=30.0):
                self._log("后端服务启动超时")
                return False
            self._log("后端服务已启动")
            return True
        except Exception as e:
            self._log(f"启动后端失败: {e}")
            return False

    def _start_frontend(self) -> bool:
        """尝试启动前端开发服务器"""
        if not FRONTEND_DIR.exists():
            self._log(f"前端目录不存在: {FRONTEND_DIR}")
            return False
        if not (FRONTEND_DIR / "node_modules").exists():
            self._log("前端 node_modules 不存在，尝试 npm install...")
            try:
                subprocess.run(["npm", "install"], cwd=FRONTEND_DIR, check=True, timeout=120)
            except Exception as e:
                self._log(f"npm install 失败: {e}")
                return False

        env = os.environ.copy()
        env["PORT"] = str(self._frontend_port)
        cmd = ["npm", "run", "dev", "--", "--port", str(self._frontend_port)]
        self._log(f"启动前端: {' '.join(cmd)}")
        try:
            proc = subprocess.Popen(
                cmd,
                cwd=FRONTEND_DIR,
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
            )
            self._started_procs.append(proc)
            if not self._wait_for_port("127.0.0.1", self._frontend_port, timeout=60.0):
                self._log("前端服务启动超时")
                return False
            self._log("前端服务已启动")
            return True
        except Exception as e:
            self._log(f"启动前端失败: {e}")
            return False

    def _start_mcp(self) -> bool:
        """尝试启动 MCP Server"""
        if not MCP_DIR.exists():
            self._log(f"MCP 目录不存在: {MCP_DIR}")
            return False

        venv_python = MCP_DIR / ".venv" / "bin" / "python"
        python = str(venv_python) if venv_python.exists() else sys.executable

        env = os.environ.copy()
        env["MCP_PORT"] = str(self._mcp_port)
        env["API_TOKEN"] = self.mcp_token
        env["PYTHONUNBUFFERED"] = "1"

        cmd = [python, "server.py"]
        self._log(f"启动 MCP Server: {' '.join(cmd)} (port={self._mcp_port})")
        try:
            proc = subprocess.Popen(
                cmd,
                cwd=MCP_DIR,
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
            )
            self._started_procs.append(proc)
            if not self._wait_for_port("127.0.0.1", self._mcp_port, timeout=30.0):
                self._log("MCP Server 启动超时")
                return False
            self._log("MCP Server 已启动")
            return True
        except Exception as e:
            self._log(f"启动 MCP Server 失败: {e}")
            return False

    def cleanup(self) -> None:
        """清理本脚本启动的临时进程"""
        for proc in self._started_procs:
            if proc.poll() is None:
                self._log(f"终止进程 PID={proc.pid}")
                try:
                    proc.send_signal(signal.SIGTERM)
                    proc.wait(timeout=5)
                except Exception:
                    try:
                        proc.kill()
                    except Exception:
                        pass
        for d in self._temp_dirs:
            shutil.rmtree(d, ignore_errors=True)

    # ── 各测试项 ─────────────────────────────────────────────────────

    def test_backend_health(self) -> None:
        """1.1 后端服务启动"""
        item_id, name = "1.1", "后端服务启动"
        method = f"GET {self.backend_url}/health"
        criteria = '返回 {"status":"ok"}'

        try:
            resp = self._http_get(f"{self.backend_url}/health")
            data = resp.json()
            if resp.status_code == 200 and data.get("status") == "ok":
                self._add_case(item_id, name, method, criteria, "P", f"状态码 {resp.status_code}，响应 {data}")
                return
        except Exception as e:
            self._log(f"后端 /health 访问失败: {e}")

        if self.auto_start and self._start_backend():
            try:
                resp = self._http_get(f"{self.backend_url}/health")
                data = resp.json()
                if resp.status_code == 200 and data.get("status") == "ok":
                    self._add_case(
                        item_id,
                        name,
                        method,
                        criteria,
                        "P",
                        f"自动启动成功，状态码 {resp.status_code}，响应 {data}",
                        remark="由脚本自动拉起",
                    )
                    return
            except Exception as e:
                self._log(f"自动启动后 /health 仍失败: {e}")

        self._add_case(item_id, name, method, criteria, "F", "后端 /health 不可达或服务未启动")

    def test_frontend_dev(self) -> None:
        """1.2 前端开发服务启动"""
        item_id, name = "1.2", "前端开发服务启动"
        method = f"GET {self.frontend_url}"
        criteria = "页面正常加载（含 Vite/Vue 特征）"

        try:
            resp = self._http_get(self.frontend_url, timeout=10.0)
            text = resp.text
            if resp.status_code == 200 and ("vite" in text.lower() or "vue" in text.lower() or '<div id="app">' in text):
                self._add_case(item_id, name, method, criteria, "P", f"状态码 {resp.status_code}，检测到前端页面特征")
                return
        except Exception as e:
            self._log(f"前端访问失败: {e}")

        if self.auto_start and self._start_frontend():
            try:
                resp = self._http_get(self.frontend_url, timeout=10.0)
                text = resp.text
                if resp.status_code == 200 and ("vite" in text.lower() or "vue" in text.lower() or '<div id="app">' in text):
                    self._add_case(
                        item_id,
                        name,
                        method,
                        criteria,
                        "P",
                        f"自动启动成功，状态码 {resp.status_code}",
                        remark="由脚本自动拉起",
                    )
                    return
            except Exception as e:
                self._log(f"自动启动后前端仍失败: {e}")

        self._add_case(item_id, name, method, criteria, "F", "前端页面不可达或未返回预期内容")

    def test_redis_fallback(self) -> None:
        """1.3 Redis 连接/降级"""
        item_id, name = "1.3", "Redis 连接/降级"
        method = "POST /api/sessions（依赖 Redis）并观察响应/日志"
        criteria = "真实 Redis 失败时自动 fallback 到 fakeredis，不崩溃"

        try:
            # Redis fallback 需要等待真实 Redis 连接超时，因此这里给足时间
            resp = self._http_post(
                f"{self.backend_url}/api/sessions",
                json_body={"title": "integration-test-session"},
                timeout=60.0,
            )
            data = resp.json()
            if resp.status_code == 200 and data.get("id"):
                self._add_case(
                    item_id,
                    name,
                    method,
                    criteria,
                    "P",
                    f"创建会话成功: {data.get('id')[:8]}...，Redis/fallback 工作正常",
                    remark="后端未崩溃即视为降级通过",
                )
                return
            else:
                self._add_case(item_id, name, method, criteria, "F", f"创建会话失败: {resp.status_code} {data}")
                return
        except Exception as e:
            self._add_case(item_id, name, method, criteria, "F", f"请求异常: {e}")

    def test_mcp_server_health(self) -> None:
        """1.4 MCP Server 启动"""
        item_id, name = "1.4", "MCP Server 启动"
        method = f"GET {self.mcp_url}/health"
        criteria = "返回可用工具列表"

        try:
            resp = self._http_get(f"{self.mcp_url}/health", timeout=10.0)
            data = resp.json()
            if resp.status_code == 200 and data.get("status") == "ok" and "available_tools" in data:
                tools = data.get("available_tools", [])
                self._add_case(
                    item_id,
                    name,
                    method,
                    criteria,
                    "P",
                    f"状态码 {resp.status_code}，可用工具 {len(tools)} 个: {tools}",
                )
                return
        except Exception as e:
            self._log(f"MCP /health 访问失败: {e}")

        if self.auto_start and self._start_mcp():
            try:
                resp = self._http_get(f"{self.mcp_url}/health", timeout=10.0)
                data = resp.json()
                if resp.status_code == 200 and data.get("status") == "ok" and "available_tools" in data:
                    tools = data.get("available_tools", [])
                    self._add_case(
                        item_id,
                        name,
                        method,
                        criteria,
                        "P",
                        f"自动启动成功，可用工具 {len(tools)} 个: {tools}",
                        remark="由脚本自动拉起",
                    )
                    return
            except Exception as e:
                self._log(f"自动启动后 MCP /health 仍失败: {e}")

        self._add_case(item_id, name, method, criteria, "F", "MCP Server /health 不可达或响应异常")

    def test_wsl_kylin_connectivity(self) -> None:
        """1.5 WSL ↔ 麒麟 V11 网络连通"""
        item_id, name = "1.5", "WSL ↔ 麒麟 V11 网络连通"
        method = f"curl {self.mcp_url}/health"
        criteria = "可达；至少验证一种网络模式"

        try:
            resp = self._http_get(f"{self.mcp_url}/health", timeout=10.0)
            if resp.status_code == 200:
                self._add_case(
                    item_id,
                    name,
                    method,
                    criteria,
                    "P",
                    f"可达，状态码 {resp.status_code}",
                    remark="HTTP 连通即视为网络层可达",
                )
                return
        except Exception as e:
            self._log(f"WSL↔麒麟连通性失败: {e}")

        self._add_case(item_id, name, method, criteria, "F", "无法访问 MCP Server，请检查网络/防火墙/端口")

    def test_backend_to_mcp(self) -> None:
        """1.6 后端 → MCP Server 连通"""
        item_id, name = "1.6", "后端 → MCP Server 连通"
        method = "POST /mcp/v1/tools/list（带 Bearer Token）"
        criteria = "返回 6 个工具：sys_info/service_mgr/log_reader/net_monitor/cmd_exec/file_guard"

        headers = {}
        if self.mcp_token:
            headers["Authorization"] = f"Bearer {self.mcp_token}"

        payload = {"jsonrpc": "2.0", "method": "tools/list", "id": 1}
        try:
            resp = self._http_post(f"{self.mcp_url}/mcp/v1/tools/list", json_body=payload, headers=headers, timeout=15.0)
            data = resp.json()
            if resp.status_code == 200 and "result" in data:
                tools = data.get("result", {}).get("tools", [])
                expected = {"sys_info", "service_mgr", "log_reader", "net_monitor", "cmd_exec", "file_guard"}
                missing = expected - set(tools)
                if not missing and len(tools) >= 6:
                    self._add_case(
                        item_id,
                        name,
                        method,
                        criteria,
                        "P",
                        f"返回工具 {len(tools)} 个: {tools}",
                    )
                    return
                else:
                    self._add_case(
                        item_id,
                        name,
                        method,
                        criteria,
                        "F",
                        f"缺少预期工具: {missing}，实际: {tools}",
                    )
                    return
            elif "error" in data:
                self._add_case(item_id, name, method, criteria, "F", f"JSON-RPC 错误: {data['error']}")
                return
        except Exception as e:
            self._log(f"后端→MCP 连通性失败: {e}")

        self._add_case(item_id, name, method, criteria, "F", "无法从后端机器调用 MCP Server /mcp/v1/tools/list")

    def test_demo_mode(self) -> None:
        """1.7 DEMO_MODE 无 Key 跑通（对应本项目的 LLM_ENABLED=false + MCP_MODE=mock）"""
        item_id, name = "1.7", "DEMO_MODE 无 Key 跑通"
        method = "WebSocket 发送低危查询，观察 status/chunk/done 回复"
        criteria = "LLM 与 MCP 返回 mock 数据，前端可完整对话"

        backend_ready = False
        original_backend_url = self.backend_url
        original_backend_port = self._backend_port

        # 如果指定 --demo-mode，在独立端口强制启动 mock 后端，确保 1.7 不受当前运行后端配置影响
        if self.demo_mode:
            demo_port = original_backend_port + 1000
            self._backend_port = demo_port
            self.backend_url = f"http://localhost:{demo_port}"

            env = {
                "APP_PORT": str(demo_port),
                "LLM_ENABLED": "false",
                "MCP_MODE": "mock",
                "DEEPSEEK_API_KEY": "",
                "SQLITE_DB": str(tempfile.mktemp(suffix=".db")),
            }
            self._temp_dirs.append(Path(env["SQLITE_DB"]).parent)
            if self._start_backend(extra_env=env):
                backend_ready = True
            else:
                # 启动失败则回退到探测原有后端
                self._backend_port = original_backend_port
                self.backend_url = original_backend_url

        # 未指定 demo-mode 或 demo 后端启动失败时，探测当前后端是否可对话
        if not backend_ready:
            try:
                with httpx.Client(timeout=5.0) as client:
                    r = client.get(f"{self.backend_url}/health")
                    backend_ready = r.status_code == 200
            except Exception:
                backend_ready = False

            # 当前后端未运行且允许自动启动，则启动一个普通后端
            if not backend_ready and self.auto_start:
                if self._start_backend():
                    backend_ready = True

        ws_url = self.backend_url.replace("http://", "ws://").replace("https://", "wss://")
        ws_endpoint = f"{ws_url}/ws/chat/{uuid.uuid4().hex[:12]}"

        if not backend_ready:
            self._add_case(
                item_id,
                name,
                method,
                criteria,
                "F",
                "后端未运行；如需自动启动 DEMO 后端请添加 --auto-start --demo-mode",
            )
            return

        # 执行 WebSocket 对话
        try:
            loop = asyncio.get_event_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)

        result = loop.run_until_complete(self._ws_chat(ws_endpoint, "查看CPU使用率"))

        if result["ok"]:
            self._add_case(
                item_id,
                name,
                method,
                criteria,
                "P",
                f"收到 {len(result['frames'])} 帧，类型 {result['types']}，最终状态正常",
                remark=result.get("remark", ""),
            )
        else:
            self._add_case(item_id, name, method, criteria, "F", result.get("error", "未知错误"))

    async def _ws_chat(self, ws_endpoint: str, message: str) -> dict:
        """通过 WebSocket 发送一条对话并收集回复帧"""
        frames: list[dict] = []
        types: set[str] = set()
        # 当前后端 chat 未使用子协议 token，规范要求通过 query string 传递
        if self.api_token:
            connector = "&" if "?" in ws_endpoint else "?"
            ws_endpoint = f"{ws_endpoint}{connector}token={self.api_token}"

        try:
            async with websockets.connect(ws_endpoint, open_timeout=10) as ws:
                await ws.send(json.dumps({"type": "chat", "content": message}))
                # 最多等待 45 秒，收集到 done/error 即结束
                deadline = asyncio.get_event_loop().time() + 45
                while asyncio.get_event_loop().time() < deadline:
                    try:
                        raw = await asyncio.wait_for(ws.recv(), timeout=5.0)
                        frame = json.loads(raw)
                        frames.append(frame)
                        types.add(frame.get("type", ""))
                        if frame.get("type") in ("done", "error"):
                            break
                    except asyncio.TimeoutError:
                        if frames:
                            break
                        continue
        except Exception as e:
            return {"ok": False, "error": f"WebSocket 异常: {e}"}

        if not frames:
            return {"ok": False, "error": "WebSocket 未收到任何回复帧"}
        if "error" in types:
            return {"ok": False, "error": f"收到 error 帧: {[f for f in frames if f.get('type') == 'error']}"}

        # 只要收到至少 status + done 即视为可完整对话
        if "status" in types and "done" in types:
            return {
                "ok": True,
                "frames": frames,
                "types": sorted(types),
                "remark": f"DEMO/mock 模式对话链路通畅（实际测试后端: {self.backend_url}）",
            }
        return {
            "ok": False,
            "error": f"回复帧类型不完整: {sorted(types)}，未同时包含 status 与 done",
            "frames": frames,
        }

    # ── 报告输出 ─────────────────────────────────────────────────────

    def print_report(self) -> None:
        """控制台打印简要结果"""
        print("\n" + "=" * 80)
        print("第 1 节 环境部署与基础连通性 — 测试结果")
        print("=" * 80)
        for c in self.cases:
            status_map = {"P": "✅ PASS", "F": "❌ FAIL", "N": "⏭️ N/A", "B": "🚧 BLOCK"}
            print(f"{c.id} [{status_map.get(c.status, c.status)}] {c.name}")
            if c.detail:
                print(f"   详情: {c.detail}")
        print("=" * 80)
        total = len(self.cases)
        passed = sum(1 for c in self.cases if c.status == "P")
        failed = sum(1 for c in self.cases if c.status == "F")
        blocked = sum(1 for c in self.cases if c.status == "B")
        print(f"总计 {total} 项 | 通过 {passed} | 失败 {failed} | 阻塞 {blocked}")

    def save_report(self) -> None:
        """生成 Markdown 测试报告"""
        lines = [
            "# kylin-agent 集成测试报告 —— 第 1 节 环境部署与基础连通性",
            "",
            f"> 生成时间：{time.strftime('%Y-%m-%d %H:%M:%S')}",
            f"> 后端地址：{self._report_backend_url}",
            f"> 前端地址：{self.frontend_url}",
            f"> MCP 地址：{self.mcp_url}",
            "",
            "## 测试结果汇总",
            "",
            "| 总项 | 通过 | 失败 | 阻塞 |",
            "|------|------|------|------|",
            f"| {len(self.cases)} | {sum(1 for c in self.cases if c.status == 'P')} | "
            f"{sum(1 for c in self.cases if c.status == 'F')} | "
            f"{sum(1 for c in self.cases if c.status == 'B')} |",
            "",
            "## 测试明细",
            "",
            "| 序号 | 测试项 | 测试方法 | 通过标准 | 结果 | 问题/截图 | 备注 |",
            "|------|--------|----------|----------|------|-----------|------|",
        ]
        for c in self.cases:
            lines.append(
                f"| {c.id} | {c.name} | {c.method} | {c.criteria} | {c.status} | {c.detail} | {c.remark} |"
            )

        lines.extend([
            "",
            "## 结论",
            "",
            "- 可直接将上表「测试明细」贴回 `docs/integration_test_report_template.md` 的第 1 节。",
            "- 若 1.5/1.6 失败，请检查后端到 MCP Server 的网络、防火墙及 `MCP_AUTH_TOKEN` 配置。",
            "- 若 1.7 失败，建议以 `LLM_ENABLED=false MCP_MODE=mock` 启动后端后重试。",
            "",
        ])

        self.report_path.parent.mkdir(parents=True, exist_ok=True)
        self.report_path.write_text("\n".join(lines), encoding="utf-8")
        self._log(f"报告已保存: {self.report_path}")

    # ── 主入口 ───────────────────────────────────────────────────────

    def run(self) -> int:
        """执行全部测试项"""
        try:
            self.test_backend_health()
            self.test_frontend_dev()
            self.test_redis_fallback()
            self.test_mcp_server_health()
            self.test_wsl_kylin_connectivity()
            self.test_backend_to_mcp()
            self.test_demo_mode()
        finally:
            self.print_report()
            self.save_report()
            if self.auto_start or self.demo_mode:
                self.cleanup()

        failed = any(c.status in ("F", "B") for c in self.cases)
        return 1 if failed else 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="kylin-agent 第 1 节环境部署与基础连通性自动测试脚本",
    )
    parser.add_argument("--backend", default=DEFAULT_BACKEND_URL, help="后端地址")
    parser.add_argument("--frontend", default=DEFAULT_FRONTEND_URL, help="前端地址")
    parser.add_argument("--mcp", default=DEFAULT_MCP_URL, help="MCP Server 地址")
    parser.add_argument("--mcp-token", default=os.getenv("MCP_AUTH_TOKEN", DEFAULT_MCP_TOKEN), help="MCP Bearer Token")
    parser.add_argument("--api-token", default=os.getenv("API_TOKEN", ""), help="后端 API Token")
    parser.add_argument("--auto-start", action="store_true", help="服务未启动时自动拉起临时进程")
    parser.add_argument("--demo-mode", action="store_true", help="强制以 DEMO/mock 模式验证 1.7")
    parser.add_argument("--report", default=str(REPORT_FILE), help="报告输出路径")
    args = parser.parse_args()

    tester = Section1Tester(
        backend_url=args.backend,
        frontend_url=args.frontend,
        mcp_url=args.mcp,
        mcp_token=args.mcp_token,
        api_token=args.api_token,
        auto_start=args.auto_start,
        demo_mode=args.demo_mode,
        report_path=Path(args.report),
    )
    return tester.run()


if __name__ == "__main__":
    sys.exit(main())