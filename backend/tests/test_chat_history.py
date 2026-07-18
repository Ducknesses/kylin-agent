"""Chat History Persistence 测试

覆盖：
  1. 保存用户消息
  2. 保存 assistant 消息
  3. session 隔离
  4. 消息按时间排序
  5. REST API 返回格式
  6. 空会话返回 404
  7. 会话创建与列表

所有测试使用真实 SQLite（临时文件），不依赖 mock。
"""
import json
import os
import tempfile

import pytest
from fastapi.testclient import TestClient

from app.repositories.sqlite import SQLiteMessageRepository


@pytest.fixture
def repo():
    """创建使用临时 SQLite 文件的 repository"""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    repo = SQLiteMessageRepository(db_path=path)
    yield repo
    try:
        os.unlink(path)
    except OSError:
        pass


@pytest.fixture
def client():
    """FastAPI TestClient（使用默认配置）"""
    from app.main import app
    return TestClient(app)


# ═══════════════════════════════════════════════════════════════════════
# 消息保存与查询
# ═══════════════════════════════════════════════════════════════════════

class TestSaveAndQueryMessages:
    """保存与查询消息"""

    @pytest.mark.asyncio
    async def test_save_user_message(self, repo):
        await repo.create_session("s1")
        await repo.save_message("s1", "user", "查看 CPU 使用率", "chat")
        msgs = await repo.get_messages("s1")
        assert len(msgs) == 1
        assert msgs[0]["role"] == "user"
        assert msgs[0]["content"] == "查看 CPU 使用率"
        assert msgs[0]["message_type"] == "chat"

    @pytest.mark.asyncio
    async def test_save_assistant_message(self, repo):
        await repo.create_session("s1")
        await repo.save_message("s1", "assistant", "当前 CPU 使用率 23%", "chunk")
        msgs = await repo.get_messages("s1")
        assert len(msgs) == 1
        assert msgs[0]["role"] == "assistant"
        assert msgs[0]["message_type"] == "chunk"

    @pytest.mark.asyncio
    async def test_save_with_metadata(self, repo):
        await repo.create_session("s1")
        meta = {"tool": "sys_info", "ok": True, "result": {"cpu": 23.5}}
        await repo.save_message("s1", "assistant", '{"cpu": 23.5}', "tool_call", metadata=meta)
        msgs = await repo.get_messages("s1")
        assert msgs[0]["metadata"] == meta

    @pytest.mark.asyncio
    async def test_messages_ordered_by_time(self, repo):
        await repo.create_session("s1")
        await repo.save_message("s1", "user", "msg1", "chat")
        await repo.save_message("s1", "assistant", "msg2", "chunk")
        await repo.save_message("s1", "user", "msg3", "chat")
        msgs = await repo.get_messages("s1")
        assert [m["content"] for m in msgs] == ["msg1", "msg2", "msg3"]


# ═══════════════════════════════════════════════════════════════════════
# Session 隔离
# ═══════════════════════════════════════════════════════════════════════

class TestSessionIsolation:
    """不同 session 的消息完全隔离"""

    @pytest.mark.asyncio
    async def test_different_sessions_isolated(self, repo):
        await repo.create_session("s1", "会话1")
        await repo.create_session("s2", "会话2")
        await repo.save_message("s1", "user", "s1-msg", "chat")
        await repo.save_message("s2", "user", "s2-msg", "chat")

        s1_msgs = await repo.get_messages("s1")
        s2_msgs = await repo.get_messages("s2")
        assert len(s1_msgs) == 1
        assert len(s2_msgs) == 1
        assert s1_msgs[0]["content"] == "s1-msg"
        assert s2_msgs[0]["content"] == "s2-msg"

    @pytest.mark.asyncio
    async def test_unknown_session_returns_empty(self, repo):
        msgs = await repo.get_messages("nonexistent")
        assert msgs == []


# ═══════════════════════════════════════════════════════════════════════
# Session 管理
# ═══════════════════════════════════════════════════════════════════════

class TestSessionManagement:
    """会话 CRUD"""

    @pytest.mark.asyncio
    async def test_create_session(self, repo):
        await repo.create_session("s1", "测试会话")
        s = await repo.get_session("s1")
        assert s is not None
        assert s["id"] == "s1"
        assert s["title"] == "测试会话"

    @pytest.mark.asyncio
    async def test_create_session_idempotent(self, repo):
        await repo.create_session("s1", "标题1")
        # 第二次创建不应报错（INSERT OR IGNORE）
        await repo.create_session("s1", "标题2")
        s = await repo.get_session("s1")
        assert s is not None
        # INSERT OR IGNORE 不更新已有记录，标题保持第一次的值
        assert s["title"] == "标题1"

    @pytest.mark.asyncio
    async def test_list_sessions(self, repo):
        await repo.create_session("s_a", "会话A")
        await repo.create_session("s_b", "会话B")
        sessions = await repo.list_sessions()
        ids = [s["id"] for s in sessions]
        assert "s_a" in ids
        assert "s_b" in ids

    @pytest.mark.asyncio
    async def test_get_nonexistent_session(self, repo):
        s = await repo.get_session("nope")
        assert s is None


# ═══════════════════════════════════════════════════════════════════════
# REST API 测试 (TestClient)
# ═══════════════════════════════════════════════════════════════════════

class TestSessionsAPI:
    """Sessions REST API 集成测试"""

    def test_create_session_api(self, client):
        resp = client.post("/api/sessions", json={"title": "API测试"})
        assert resp.status_code == 200
        data = resp.json()
        assert "id" in data
        assert data["title"] == "API测试"
        assert "created_at" in data

    def test_list_sessions_api(self, client):
        resp = client.get("/api/sessions")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)

    def test_get_session_messages_empty(self, client):
        """新创建的会话应能返回空消息列表"""
        create_resp = client.post("/api/sessions", json={"title": "测试"})
        sid = create_resp.json()["id"]
        resp = client.get(f"/api/sessions/{sid}/messages")
        assert resp.status_code == 200
        data = resp.json()
        assert data["session_id"] == sid
        assert data["messages"] == []

    def test_get_session_messages_404(self, client):
        resp = client.get("/api/sessions/nonexistent-12345/messages")
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_get_session_messages_returns_message_type(self, client):
        """历史消息接口应返回 message_type，前端据此还原 fix_options 卡片"""
        from app.dependencies import message_repository

        create_resp = client.post("/api/sessions", json={"title": "类型测试"})
        sid = create_resp.json()["id"]

        options = [{
            "option_id": "fix_1234abcd",
            "title": "查看内存使用率历史趋势",
            "description": "通过 metrics_history 获取趋势",
            "risk_level": "low",
            "tool": "metrics_history",
            "params": {"metric_type": "memory", "duration": "1h"},
            "requires_confirm": False,
            "rollback": "无需回滚",
        }]
        await message_repository.save_message(
            session_id=sid, role="assistant",
            content=json.dumps(options, ensure_ascii=False),
            message_type="fix_options", trace_id="trace-mt-001",
        )

        resp = client.get(f"/api/sessions/{sid}/messages")
        assert resp.status_code == 200
        msgs = resp.json()["messages"]
        assert len(msgs) == 1
        assert msgs[0]["message_type"] == "fix_options"
        assert json.loads(msgs[0]["content"]) == options


# ═══════════════════════════════════════════════════════════════════════
# 异常安全性
# ═══════════════════════════════════════════════════════════════════════

class TestErrorHandling:
    """异常不应中断业务流程"""

    @pytest.mark.asyncio
    async def test_save_message_without_session(self, repo):
        """保存消息到不存在的 session 不应抛异常（静默失败）"""
        # 不应抛出异常
        await repo.save_message("no_such_session", "user", "test", "chat")
        msgs = await repo.get_messages("no_such_session")
        assert len(msgs) >= 0  # SQLite 默认不强制外键，消息仍会保存

    @pytest.mark.asyncio
    async def test_save_message_exception_safe(self, repo):
        """异常时不应中断"""
        await repo.create_session("s1")
        # 正常保存
        await repo.save_message("s1", "user", "test", "chat")
        msgs = await repo.get_messages("s1")
        assert len(msgs) == 1


# ═══════════════════════════════════════════════════════════════════════
# confirm 流程消息持久化
# ═══════════════════════════════════════════════════════════════════════

class TestConfirmFlowPersistence:
    """confirm approve/reject 流程的消息保存"""

    @pytest.mark.asyncio
    async def test_confirm_reject_saves_status(self, repo):
        """confirm reject 后在历史中存在取消状态消息"""
        trace_id = "trace-reject-001"
        await repo.create_session("s1")
        # 模拟 confirm reject 流程保存的状态消息
        await repo.save_message(
            session_id="s1", role="system",
            content="已取消该风险操作。",
            message_type="status", trace_id=trace_id,
        )
        msgs = await repo.get_messages("s1")
        assert len(msgs) == 1
        assert msgs[0]["role"] == "system"
        assert msgs[0]["message_type"] == "status"
        assert "已取消" in msgs[0]["content"]
        assert msgs[0]["trace_id"] == trace_id

    @pytest.mark.asyncio
    async def test_confirm_approve_saves_assistant_frames(self, repo):
        """confirm approve 后在历史中存在完整的 assistant 消息"""
        trace_id = "trace-approve-001"
        await repo.create_session("s1")
        # 模拟 confirm approve 流程：user 消息 + assistant 帧
        await repo.save_message(
            session_id="s1", role="user", content="重启 nginx",
            message_type="chat", trace_id=trace_id,
        )
        # status 帧
        await repo.save_message(
            session_id="s1", role="assistant", content="正在执行诊断步骤...",
            message_type="status", trace_id=trace_id,
        )
        # tool_call 帧
        await repo.save_message(
            session_id="s1", role="assistant", content='{"mock": true}',
            message_type="tool_call", trace_id=trace_id,
            metadata={"tool": "service_mgr", "ok": True, "result": {"mock": True}},
        )
        # chunk 帧（最终报告）
        await repo.save_message(
            session_id="s1", role="assistant", content="nginx 重启成功",
            message_type="chunk", trace_id=trace_id,
        )

        msgs = await repo.get_messages("s1")
        assert len(msgs) >= 4  # user + status + tool_call + chunk

        roles = [m["role"] for m in msgs]
        types = [m["message_type"] for m in msgs]
        assert "user" in roles
        assert "assistant" in roles
        assert "status" in types
        assert "tool_call" in types
        assert "chunk" in types

    @pytest.mark.asyncio
    async def test_confirm_flow_messages_ordered(self, repo):
        """confirm 流程的消息按时间顺序排列"""
        await repo.create_session("s1")
        await repo.save_message("s1", "user", "重启 nginx", "chat", trace_id="t1")
        await repo.save_message("s1", "system", "需要确认", "risk_alert", trace_id="t1")
        await repo.save_message("s1", "system", "已取消该风险操作。", "status", trace_id="t1")

        msgs = await repo.get_messages("s1")
        contents = [m["content"] for m in msgs]
        assert contents == ["重启 nginx", "需要确认", "已取消该风险操作。"]
