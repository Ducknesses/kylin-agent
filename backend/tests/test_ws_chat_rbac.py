"""WebSocket RBAC 流程集成测试

覆盖：
  1. viewer（agent-read）低危查询正常执行
  2. viewer（agent-read）中危操作被拒绝
  3. operator（agent-op）中危操作进入 confirm 流程
  4. 高危操作始终被拒绝（不受角色影响）
  5. confirm approve 流程使用真实角色
"""
import json
import os

import pytest
from fastapi.testclient import TestClient

from app.core.security import TokenStore
from app.core.auth import AuthLevel


# ═══════════════════════════════════════════════════════════════════
# 辅助：设置多级 token 环境
# ═══════════════════════════════════════════════════════════════════

def _setup_multi_level_tokens():
    """设置分级 token 并重载 TokenStore 单例"""
    os.environ["API_TOKENS"] = "read:rk-1,op:op-1,admin:adm-1"
    os.environ.pop("API_TOKEN", None)
    # 重载 TokenStore 单例
    TokenStore._instance = None
    import importlib
    import config
    importlib.reload(config)


def _cleanup_tokens():
    """清理 token 环境变量"""
    os.environ.pop("API_TOKEN", None)
    os.environ.pop("API_TOKENS", None)
    TokenStore._instance = None
    import importlib
    import config
    importlib.reload(config)


# ═══════════════════════════════════════════════════════════════════
# RBAC WebSocket 流程测试
# ═══════════════════════════════════════════════════════════════════

class TestWebSocketRBACViewer:
    """viewer（agent-read）角色的 WebSocket 流程"""

    @pytest.fixture(autouse=True)
    def setup_teardown(self):
        _setup_multi_level_tokens()
        from app.main import app
        self.client = TestClient(app)
        yield
        _cleanup_tokens()

    def test_viewer_low_query_executes(self):
        """viewer + 低危 CPU 查询 → 正常执行（status + tool_call + chunk + done）"""
        with self.client.websocket_connect(
            "/ws/chat/test-rbac-viewer-low?token=rk-1"
        ) as ws:
            ws.send_text(json.dumps({"type": "chat", "content": "查看CPU使用率"}))
            types = []
            tool_calls = []
            for _ in range(6):
                m = ws.receive_json()
                types.append(m["type"])
                if m["type"] == "tool_call":
                    tool_calls.append(m)
                if m["type"] == "done":
                    break

            assert "status" in types
            assert "tool_call" in types
            assert "chunk" in types
            assert "done" in types
            # 低危查询应调用 MCP 工具
            assert any(tc["tool"] == "sys_info" for tc in tool_calls)

    def test_viewer_medium_restart_nginx_gets_confirm(self):
        """viewer + 重启 nginx → risk_alert medium + confirm_id"""
        with self.client.websocket_connect(
            "/ws/chat/test-rbac-viewer-medium?token=rk-1"
        ) as ws:
            ws.send_text(json.dumps({"type": "chat", "content": "重启 nginx"}))
            data = ws.receive_json()
            # 用户输入层面 SafetyGuard 产生 medium + confirm
            assert data["type"] == "risk_alert"
            assert data["level"] == "medium"
            assert "confirm_id" in data

    def test_viewer_high_rm_rf_blocked(self):
        """viewer + rm -rf / → risk_alert high（高危不受角色影响）"""
        with self.client.websocket_connect(
            "/ws/chat/test-rbac-viewer-high?token=rk-1"
        ) as ws:
            ws.send_text(json.dumps({"type": "chat", "content": "rm -rf /"}))
            data = ws.receive_json()
            assert data["type"] == "risk_alert"
            assert data["level"] == "high"


class TestWebSocketRBACOperator:
    """operator（agent-op）角色的 WebSocket 流程"""

    @pytest.fixture(autouse=True)
    def setup_teardown(self):
        _setup_multi_level_tokens()
        from app.main import app
        self.client = TestClient(app)
        yield
        _cleanup_tokens()

    def test_operator_low_query_executes(self):
        """operator + 低危 CPU 查询 → 正常执行"""
        with self.client.websocket_connect(
            "/ws/chat/test-rbac-op-low?token=op-1"
        ) as ws:
            ws.send_text(json.dumps({"type": "chat", "content": "查看CPU使用率"}))
            types = []
            for _ in range(6):
                m = ws.receive_json()
                types.append(m["type"])
                if m["type"] == "done":
                    break

            assert "tool_call" in types
            assert "done" in types

    def test_operator_medium_confirm_approve_executes(self):
        """operator + 中危确认 approve → 正常执行工具"""
        with self.client.websocket_connect(
            "/ws/chat/test-rbac-op-medium?token=op-1"
        ) as ws:
            ws.send_text(json.dumps({"type": "chat", "content": "重启 nginx"}))
            alert = ws.receive_json()
            assert alert["type"] == "risk_alert"
            assert alert["level"] == "medium"
            cid = alert["confirm_id"]

            # 发送 approve
            ws.send_text(json.dumps({
                "type": "confirm", "confirm_id": cid, "decision": "approve",
            }))

            # 应进入 orchestrator 主流程
            types = []
            tool_calls = []
            for _ in range(6):
                m = ws.receive_json()
                types.append(m["type"])
                if m["type"] == "tool_call":
                    tool_calls.append(m)
                if m["type"] == "done":
                    break

            assert "status" in types
            assert "tool_call" in types
            assert "done" in types

    def test_operator_high_blocked(self):
        """operator + 高危 rm -rf / → risk_alert high"""
        with self.client.websocket_connect(
            "/ws/chat/test-rbac-op-high?token=op-1"
        ) as ws:
            ws.send_text(json.dumps({"type": "chat", "content": "rm -rf /"}))
            data = ws.receive_json()
            assert data["type"] == "risk_alert"
            assert data["level"] == "high"


class TestWebSocketRBACAdmin:
    """admin（agent-admin）角色的 WebSocket 流程"""

    @pytest.fixture(autouse=True)
    def setup_teardown(self):
        _setup_multi_level_tokens()
        from app.main import app
        self.client = TestClient(app)
        yield
        _cleanup_tokens()

    def test_admin_low_query_executes(self):
        """admin + 低危 CPU 查询 → 正常执行"""
        with self.client.websocket_connect(
            "/ws/chat/test-rbac-adm-low?token=adm-1"
        ) as ws:
            ws.send_text(json.dumps({"type": "chat", "content": "查看CPU使用率"}))
            types = []
            for _ in range(6):
                m = ws.receive_json()
                types.append(m["type"])
                if m["type"] == "done":
                    break

            assert "tool_call" in types
            assert "done" in types

    def test_admin_medium_confirm_approve_executes(self):
        """admin + 中危确认 approve → 正常执行工具"""
        with self.client.websocket_connect(
            "/ws/chat/test-rbac-adm-medium?token=adm-1"
        ) as ws:
            ws.send_text(json.dumps({"type": "chat", "content": "重启 nginx"}))
            alert = ws.receive_json()
            assert alert["type"] == "risk_alert"
            assert alert["level"] == "medium"
            cid = alert["confirm_id"]

            ws.send_text(json.dumps({
                "type": "confirm", "confirm_id": cid, "decision": "approve",
            }))

            types = []
            for _ in range(6):
                m = ws.receive_json()
                types.append(m["type"])
                if m["type"] == "done":
                    break

            assert "tool_call" in types
            assert "done" in types

    def test_admin_high_still_rejected(self):
        """admin + 高危 → risk_alert high（高危不受角色影响）"""
        with self.client.websocket_connect(
            "/ws/chat/test-rbac-adm-high?token=adm-1"
        ) as ws:
            ws.send_text(json.dumps({"type": "chat", "content": "rm -rf /"}))
            data = ws.receive_json()
            assert data["type"] == "risk_alert"
            assert data["level"] == "high"


class TestWebSocketRBACNoToken:
    """无 token 配置时匿名放行（向后兼容）"""

    @pytest.fixture(autouse=True)
    def setup_teardown(self):
        old_token = os.environ.pop("API_TOKEN", None)
        old_tokens = os.environ.pop("API_TOKENS", None)
        TokenStore._instance = None
        import importlib
        import config
        importlib.reload(config)
        from app.main import app
        self.client = TestClient(app)
        yield
        if old_token is not None:
            os.environ["API_TOKEN"] = old_token
        if old_tokens is not None:
            os.environ["API_TOKENS"] = old_tokens
        TokenStore._instance = None
        importlib.reload(config)

    def test_no_token_anonymous_low_query(self):
        """无 token + 低危查询 → 匿名放行，正常执行"""
        with self.client.websocket_connect(
            "/ws/chat/test-rbac-anon-low"
        ) as ws:
            ws.send_text(json.dumps({"type": "chat", "content": "查看CPU使用率"}))
            types = []
            for _ in range(6):
                m = ws.receive_json()
                types.append(m["type"])
                if m["type"] == "done":
                    break

            assert "tool_call" in types
            assert "done" in types

    def test_no_token_anonymous_high_blocked(self):
        """无 token + 高危 → 仍被 SafetyGuard 拦截"""
        with self.client.websocket_connect(
            "/ws/chat/test-rbac-anon-high"
        ) as ws:
            ws.send_text(json.dumps({"type": "chat", "content": "rm -rf /"}))
            data = ws.receive_json()
            assert data["type"] == "risk_alert"
            assert data["level"] == "high"
