"""WebSocket 断连容错回归测试

复现问题：客户端在 Agent 长耗时流程（如 LLM 调用超时 20s）中断开连接后，
后端继续 send 触发 RuntimeError（ASGI: send after websocket.close），
导致整个流程崩溃、assistant 帧全部未持久化，前端永远卡在"正在输出"。

覆盖：
  1. 断连后不抛异常、停止发送、剩余帧全部落库
  2. 正常连接下所有帧按序发送并落库（回归正常路径）
"""
import pytest

from app.api import chat as chat_module


class _FakeOrchestrator:
    """按预置帧序列产出的假 Orchestrator"""

    def __init__(self, frames):
        self._frames = frames

    def handle_chat(self, session_id, user_input, role="viewer",
                    confirmed=False, trace_id=None):
        async def _gen():
            for frame in self._frames:
                yield frame
        return _gen()


class _FakeWebSocket:
    """发送 fail_after 次之后模拟"close 后再 send"的 ASGI 错误"""

    def __init__(self, fail_after=None):
        self.sent = []
        self._fail_after = fail_after

    async def send_json(self, payload):
        if self._fail_after is not None and len(self.sent) >= self._fail_after:
            raise RuntimeError(
                "Unexpected ASGI message 'websocket.send', after sending 'websocket.close'."
            )
        self.sent.append(payload)


class _RecordingRepo:
    """记录 save_message / append_chunk 调用的假仓储"""

    def __init__(self):
        self.saved = []

    async def save_message(self, **kwargs):
        self.saved.append(kwargs)

    async def append_chunk(self, session_id: str, trace_id: str, content: str) -> None:
        """记录 chunk 追加调用，语义上只记录一条 chunk 消息。"""
        self.saved.append({
            "session_id": session_id,
            "role": "assistant",
            "content": content,
            "message_type": "chunk",
            "trace_id": trace_id,
        })


_FRAMES = [
    {"type": "status", "trace_id": "t1", "message": "正在分析您的请求..."},
    {"type": "tool_call", "trace_id": "t1", "tool": "sys_info", "tool_call_id": "tc_1",
     "params": {"metric": "cpu"}, "ok": True, "result": {"cpu_percent": 23.5}},
    {"type": "chunk", "trace_id": "t1", "content": "CPU 使用率正常。"},
    {"type": "done", "trace_id": "t1"},
]


@pytest.fixture
def patched(monkeypatch):
    repo = _RecordingRepo()
    monkeypatch.setattr(chat_module, "_orchestrator", _FakeOrchestrator(_FRAMES))
    monkeypatch.setattr(chat_module, "message_repository", repo)
    return repo


class TestDisconnectResilience:
    """断连容错：发送失败不崩溃、剩余帧完整落库"""

    @pytest.mark.asyncio
    async def test_disconnect_mid_flow_no_crash_and_full_persist(self, patched):
        """第 2 帧起发送失败：不抛 RuntimeError，后续帧仅落库"""
        ws = _FakeWebSocket(fail_after=1)
        await chat_module._run_agent_flow(ws, "s1", "查看 CPU", "viewer", trace_id="t1")

        assert len(ws.sent) == 1  # 只有第 1 帧真正发出
        persisted_types = [s["message_type"] for s in patched.saved]
        assert persisted_types == ["status", "tool_call", "chunk"]

    @pytest.mark.asyncio
    async def test_healthy_flow_sends_and_persists_all(self, patched):
        """正常路径：所有帧按序发送，关键帧全部落库"""
        ws = _FakeWebSocket()
        await chat_module._run_agent_flow(ws, "s1", "查看 CPU", "viewer", trace_id="t1")

        assert ws.sent == _FRAMES
        persisted_types = [s["message_type"] for s in patched.saved]
        assert persisted_types == ["status", "tool_call", "chunk"]
