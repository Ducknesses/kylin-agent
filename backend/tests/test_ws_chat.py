"""WebSocket 聊天接口 Day2 Mock 测试

测试场景：
  1. 连接 /ws/chat/{session_id}
  2. ping → pong
  3. CPU 查询 → status + tool_call + chunk + done
  4. nginx 状态 → status + tool_call + chunk + done
  5. 高危 rm -rf / → risk_alert（无 tool_call）
  6. 中危 重启 nginx → risk_alert + confirm_id
  7. confirm approve → mock 确认流程
  8. confirm reject → 取消流程
  9. 异常输入测试
"""
import json

import pytest
from fastapi.testclient import TestClient
from fastapi.websockets import WebSocketDisconnect

from app.main import app

client = TestClient(app)


class TestWebSocketBasic:
    """基础连接与协议测试"""

    def test_connect_and_ping(self):
        """连接 /ws/chat/{id} 并发送 ping → pong"""
        with client.websocket_connect("/ws/chat/test-ping") as ws:
            ws.send_text(json.dumps({"type": "ping"}))
            data = ws.receive_json()
            assert data["type"] == "pong"

    def test_cpu_query(self):
        """CPU 查询返回 status → tool_call(sys_info) → chunk → done"""
        with client.websocket_connect("/ws/chat/test-cpu") as ws:
            ws.send_text(json.dumps({"type": "chat", "content": "查看CPU使用率"}))
            # 按顺序收集消息
            msgs = []
            for _ in range(5):
                msgs.append(ws.receive_json())
                if msgs[-1]["type"] == "done":
                    break

            types = [m["type"] for m in msgs]
            assert "status" in types
            assert "tool_call" in types
            assert "chunk" in types
            assert "done" in types

            # tool_call 应引用 sys_info
            tool_calls = [m for m in msgs if m["type"] == "tool_call"]
            assert any(tc["tool"] == "sys_info" for tc in tool_calls)

    def test_nginx_status(self):
        """nginx 状态查询返回 tool_call(service_mgr) → done"""
        with client.websocket_connect("/ws/chat/test-nginx") as ws:
            ws.send_text(json.dumps({"type": "chat", "content": "查看nginx状态"}))
            msgs = []
            for _ in range(5):
                msgs.append(ws.receive_json())
                if msgs[-1]["type"] == "done":
                    break

            tool_calls = [m for m in msgs if m["type"] == "tool_call"]
            assert any(tc["tool"] == "service_mgr" for tc in tool_calls), f"got tool_calls={tool_calls}"

    def test_high_risk_blocked(self):
        """高危 rm -rf / 返回 risk_alert，不返回 tool_call"""
        with client.websocket_connect("/ws/chat/test-highrisk") as ws:
            ws.send_text(json.dumps({"type": "chat", "content": "rm -rf /"}))
            data = ws.receive_json()
            assert data["type"] == "risk_alert"
            assert data["level"] == "high"

    def test_medium_risk_confirm_id(self):
        """中危 重启 nginx 返回 risk_alert medium + confirm_id"""
        with client.websocket_connect("/ws/chat/test-medium") as ws:
            ws.send_text(json.dumps({"type": "chat", "content": "重启 nginx"}))
            data = ws.receive_json()
            assert data["type"] == "risk_alert"
            assert data["level"] == "medium"
            assert "confirm_id" in data
            assert data["confirm_id"].startswith("cfm_")

    def test_confirm_reject(self):
        """中危 → confirm reject → 操作已取消"""
        with client.websocket_connect("/ws/chat/test-cfm-reject") as ws:
            # 触发中危
            ws.send_text(json.dumps({"type": "chat", "content": "重启 nginx"}))
            alert = ws.receive_json()
            cid = alert["confirm_id"]

            # 发送 reject
            ws.send_text(json.dumps({"type": "confirm", "confirm_id": cid, "decision": "reject"}))
            status_msg = ws.receive_json()
            assert status_msg["type"] == "status"
            assert "取消" in status_msg.get("content", "")

            done_msg = ws.receive_json()
            assert done_msg["type"] == "done"

    def test_confirm_approve(self):
        """中危 → confirm approve → mock 确认流程"""
        with client.websocket_connect("/ws/chat/test-cfm-approve") as ws:
            # 触发中危
            ws.send_text(json.dumps({"type": "chat", "content": "重启 nginx"}))
            alert = ws.receive_json()
            cid = alert["confirm_id"]

            # 发送 approve
            ws.send_text(json.dumps({"type": "confirm", "confirm_id": cid, "decision": "approve"}))
            msgs = []
            for _ in range(6):
                msgs.append(ws.receive_json())
                if msgs[-1]["type"] == "done":
                    break

            types = [m["type"] for m in msgs]
            assert "status" in types
            assert "tool_call" in types
            assert "chunk" in types
            assert "done" in types


class TestErrorHandling:
    """异常输入处理测试"""

    def test_missing_content(self):
        """chat 缺少 content → error"""
        with client.websocket_connect("/ws/chat/test-err1") as ws:
            ws.send_text(json.dumps({"type": "chat"}))
            data = ws.receive_json()
            assert data["type"] == "error"
            assert "content" in data.get("message", "").lower() or "输入" in data.get("message", "")

    def test_empty_content(self):
        """content 为空字符串 → error"""
        with client.websocket_connect("/ws/chat/test-err2") as ws:
            ws.send_text(json.dumps({"type": "chat", "content": ""}))
            data = ws.receive_json()
            assert data["type"] == "error"

    def test_content_not_string(self):
        """content 不是字符串 → error"""
        with client.websocket_connect("/ws/chat/test-err3") as ws:
            ws.send_text(json.dumps({"type": "chat", "content": 12345}))
            data = ws.receive_json()
            assert data["type"] == "error"

    def test_unknown_type(self):
        """未知 type → error"""
        with client.websocket_connect("/ws/chat/test-err4") as ws:
            ws.send_text(json.dumps({"type": "unknown_type"}))
            data = ws.receive_json()
            assert data["type"] == "error"

    def test_invalid_json(self):
        """非法 JSON → error"""
        with client.websocket_connect("/ws/chat/test-err5") as ws:
            ws.send_text("not a json {{{")
            data = ws.receive_json()
            assert data["type"] == "error"

    def test_confirm_no_pending(self):
        """无 pending 时 confirm → error"""
        with client.websocket_connect("/ws/chat/test-err6") as ws:
            ws.send_text(json.dumps({"type": "confirm", "confirm_id": "wrong", "decision": "approve"}))
            data = ws.receive_json()
            assert data["type"] == "error"
            assert "没有待确认" in data.get("message", "")

    def test_confirm_wrong_id(self):
        """错误 confirm_id → error（保留 pending）"""
        with client.websocket_connect("/ws/chat/test-err7") as ws:
            # 触发中危
            ws.send_text(json.dumps({"type": "chat", "content": "重启 nginx"}))
            alert = ws.receive_json()
            assert "confirm_id" in alert

            # 发送错误 confirm_id
            ws.send_text(json.dumps({"type": "confirm", "confirm_id": "wrong_id", "decision": "approve"}))
            err = ws.receive_json()
            assert err["type"] == "error"
            assert "不匹配" in err.get("message", "")

    def test_confirm_invalid_decision(self):
        """非法 decision → error（保留 pending）"""
        with client.websocket_connect("/ws/chat/test-err8") as ws:
            ws.send_text(json.dumps({"type": "chat", "content": "重启 nginx"}))
            alert = ws.receive_json()
            cid = alert["confirm_id"]

            ws.send_text(json.dumps({"type": "confirm", "confirm_id": cid, "decision": "bad"}))
            err = ws.receive_json()
            assert err["type"] == "error"

    def test_confirm_missing_id(self):
        """confirm 缺少 confirm_id → error"""
        with client.websocket_connect("/ws/chat/test-err9") as ws:
            ws.send_text(json.dumps({"type": "chat", "content": "重启 nginx"}))
            ws.receive_json()  # consume risk_alert

            ws.send_text(json.dumps({"type": "confirm", "decision": "approve"}))
            err = ws.receive_json()
            assert err["type"] == "error"
            assert "confirm_id" in err.get("message", "")
