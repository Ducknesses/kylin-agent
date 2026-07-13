"""LLM 安全边界测试 —— 全部使用 mock，不依赖真实 API Key

覆盖：
  1. SafetyGuard 没有导入 LLMClient
  2. AgentHarness 没有导入 LLMClient
  3. ToolRegistry 没有导入 LLMClient
  4. chat.py 不直接调用 MCPClient（通过 AgentHarness）
  5. chat.py 不直接调用 LLMClient 执行业务
  6. Agent 不直接调用 MCPClient
  7. 测试输出不包含 API Key / Authorization / Bearer
"""

import ast
import inspect
import re


# ═══════════════════════════════════════════════════════════════════
# 导入安全检查
# ═══════════════════════════════════════════════════════════════════

def _get_imports_from_file(filepath: str) -> list[str]:
    """从 Python 文件中提取所有 import 语句"""
    try:
        with open(filepath) as f:
            source = f.read()
    except FileNotFoundError:
        return []
    return [
        line.strip()
        for line in source.split("\n")
        if line.strip().startswith("import ") or line.strip().startswith("from ")
    ]


class TestSafetyBoundary:
    """安全模块不得导入 LLMClient"""

    def test_safety_guard_no_llm_import(self):
        """SafetyGuard 不应导入 LLMClient 或 llm_client"""
        imports = _get_imports_from_file(
            "backend/app/services/safety_guard.py"
        )
        llm_imports = [i for i in imports if "llm" in i.lower()]
        assert len(llm_imports) == 0, (
            f"SafetyGuard 不应导入 LLM 模块，发现: {llm_imports}"
        )

    def test_tool_registry_no_llm_import(self):
        """ToolRegistry 不应导入 LLMClient 或 llm_client"""
        imports = _get_imports_from_file(
            "backend/app/services/tool_registry.py"
        )
        llm_imports = [i for i in imports if "llm" in i.lower()]
        assert len(llm_imports) == 0, (
            f"ToolRegistry 不应导入 LLM 模块，发现: {llm_imports}"
        )

    def test_agent_harness_no_llm_import(self):
        """AgentHarness 不应导入 LLMClient 或 llm_client"""
        imports = _get_imports_from_file(
            "backend/app/services/agent_harness.py"
        )
        llm_imports = [i for i in imports if "llm" in i.lower()]
        assert len(llm_imports) == 0, (
            f"AgentHarness 不应导入 LLM 模块，发现: {llm_imports}"
        )


class TestAgentNoMCPDirectCall:
    """Agent 不应直接调用 MCPClient"""

    def test_intent_agent_no_mcp_import(self):
        """IntentAgent 不应导入 MCPClient"""
        imports = _get_imports_from_file(
            "backend/app/services/intent_agent.py"
        )
        mcp_imports = [i for i in imports if "mcp" in i.lower() and "client" in i.lower()]
        assert len(mcp_imports) == 0, (
            f"IntentAgent 不应导入 MCPClient，发现: {mcp_imports}"
        )

    def test_diagnose_agent_no_mcp_import(self):
        """DiagnoseAgent 不应导入 MCPClient"""
        imports = _get_imports_from_file(
            "backend/app/services/diagnose_agent.py"
        )
        mcp_imports = [i for i in imports if "mcp" in i.lower() and "client" in i.lower()]
        assert len(mcp_imports) == 0, (
            f"DiagnoseAgent 不应导入 MCPClient，发现: {mcp_imports}"
        )

    def test_reporter_agent_no_mcp_import(self):
        """ReporterAgent 不应导入 MCPClient"""
        imports = _get_imports_from_file(
            "backend/app/services/reporter_agent.py"
        )
        mcp_imports = [i for i in imports if "mcp" in i.lower() and "client" in i.lower()]
        assert len(mcp_imports) == 0, (
            f"ReporterAgent 不应导入 MCPClient，发现: {mcp_imports}"
        )


class TestChatPySafety:
    """chat.py 安全检查"""

    def test_chat_no_mcp_client_import(self):
        """chat.py 不应直接导入 MCPClient（允许从 dependencies 导入共享实例）"""
        imports = _get_imports_from_file("backend/app/api/chat.py")
        mcp_imports = [
            i for i in imports
            if (("mcp" in i.lower() and "client" in i.lower())
                or ("executor" in i.lower() and "mcp" in i.lower()))
            and "dependencies" not in i
        ]
        assert len(mcp_imports) == 0, (
            f"chat.py 不应直接导入 MCPClient 或 Executor，发现: {mcp_imports}"
        )

    def test_chat_no_llm_router_import(self):
        """chat.py 不应直接导入 LLM router（route_request）"""
        imports = _get_imports_from_file("backend/app/api/chat.py")
        llm_imports = [i for i in imports if "llm.router" in i or "route_request" in i]
        assert len(llm_imports) == 0, (
            f"chat.py 不应直接导入 LLM router，发现: {llm_imports}"
        )

    def test_chat_no_llm_client_business_call(self):
        """chat.py 不应在业务代码中直接调用 LLMClient"""
        with open("backend/app/api/chat.py") as f:
            source = f.read()
        # chat.py 不应有 LLMClient() 实例化（协议层不应直接调用 LLM）
        # _handle_message 中不应有 LLM 调用
        assert "LLMClient()" not in source, "chat.py 不应直接实例化 LLMClient"

    def test__real_orchestrate_removed(self):
        """_real_orchestrate 已删除"""
        with open("backend/app/api/chat.py") as f:
            source = f.read()
        assert "_real_orchestrate" not in source, (
            "_real_orchestrate 死代码未删除"
        )

    def test__stream_orchestrator_removed(self):
        """_stream_orchestrator 已删除"""
        with open("backend/app/api/chat.py") as f:
            source = f.read()
        assert "_stream_orchestrator" not in source, (
            "_stream_orchestrator 死代码未删除"
        )


class TestAgentLLMDirectToolCall:
    """Agent LLM 路径不可直接调用工具"""

    def test_intent_agent_llm_no_tool_exec(self):
        """IntentAgent 的 detect_with_llm 不应包含 MCP 调用"""
        with open("backend/app/services/intent_agent.py") as f:
            source = f.read()
        # detect_with_llm 函数体内不应有 call_tool 或 execute
        fn_start = source.find("async def detect_with_llm")
        if fn_start >= 0:
            fn_body = source[fn_start:]
            # 不应有 call_tool
            assert "call_tool" not in fn_body, "detect_with_llm 不应调用 MCPClient.call_tool"
            assert "mcp_client" not in fn_body.lower(), "detect_with_llm 不应使用 mcp_client"

    def test_diagnose_agent_llm_no_tool_exec(self):
        """DiagnoseAgent 的 plan_with_llm 不应包含 MCP 调用"""
        with open("backend/app/services/diagnose_agent.py") as f:
            source = f.read()
        fn_start = source.find("async def plan_with_llm")
        if fn_start >= 0:
            fn_body = source[fn_start:]
            assert "call_tool" not in fn_body, "plan_with_llm 不应调用 MCPClient.call_tool"

    def test_reporter_agent_llm_no_tool_exec(self):
        """ReporterAgent 的 generate_with_llm 不应包含 MCP 调用"""
        with open("backend/app/services/reporter_agent.py") as f:
            source = f.read()
        fn_start = source.find("async def generate_with_llm")
        if fn_start >= 0:
            fn_body = source[fn_start:]
            assert "call_tool" not in fn_body, "generate_with_llm 不应调用 MCPClient.call_tool"


class TestNoSensitiveInfoInTests:
    """测试文件不包含敏感信息"""

    def test_test_files_no_api_key(self):
        """测试文件不应包含 API Key"""
        import glob
        test_files = glob.glob("backend/tests/test_llm*.py")
        for test_file in test_files:
            with open(test_file) as f:
                content = f.read()
            # 不应包含真实的 sk- key（mock 中用 sk-test-key 可以）
            real_key_pattern = re.compile(r"sk-[a-zA-Z0-9]{30,}")
            assert not real_key_pattern.search(content), (
                f"{test_file} 包含疑似真实 API Key"
            )

    def test_test_files_no_bearer_real_token(self):
        """测试文件不应包含真实的认证头 token"""
        import glob
        test_files = glob.glob("backend/tests/test_llm*.py")
        for test_file in test_files:
            with open(test_file) as f:
                content = f.read()
            # 不应包含 JWT token
            jwt_pattern = re.compile(r"eyJ[a-zA-Z0-9\-_]+\.[a-zA-Z0-9\-_]+\.[a-zA-Z0-9\-_]+")
            assert not jwt_pattern.search(content), (
                f"{test_file} 包含疑似真实 JWT token"
            )


class TestLLMModelNotInSafetyModules:
    """安全模块代码中不出现 LLM 调用"""

    def test_safety_guard_code_no_llm(self):
        """SafetyGuard 源代码中不包含 LLM 调用"""
        imports = _get_imports_from_file("backend/app/services/safety_guard.py")
        llm_refs = [i for i in imports if "llm" in i.lower()]
        assert len(llm_refs) == 0

    def test_agent_harness_code_no_llm(self):
        """AgentHarness 源代码中不包含 LLM 调用"""
        imports = _get_imports_from_file("backend/app/services/agent_harness.py")
        llm_refs = [i for i in imports if "llm" in i.lower()]
        assert len(llm_refs) == 0
