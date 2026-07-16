"""KnowledgeBaseService 单元测试

覆盖：
  1. 知识条目创建
  2. 关键词匹配
  3. 无匹配返回
  4. DiagnoseAgent 调用知识库
  5. 知识库异常 fallback
  6. 空数据库运行
  7. 原有诊断流程不受影响（回归）
"""

import pytest

from app.services.knowledge_service import KnowledgeBaseService
from app.services.diagnose_agent import DiagnoseAgent
from app.services.reporter_agent import ReporterAgent


# ═══════════════════════════════════════════════════════════════════
# 辅助：创建带测试数据的 KnowledgeBaseService
# ═══════════════════════════════════════════════════════════════════

async def _seed_kb(kb: KnowledgeBaseService) -> None:
    """向知识库中预置测试数据"""
    await kb.add_item(
        type="fault_pattern",
        title="nginx 502 Bad Gateway",
        keywords=["nginx", "502", "bad gateway", "upstream"],
        symptoms="nginx 返回 502 错误，upstream 连接失败",
        solution="检查 upstream 服务是否正常运行，确认 proxy_pass 配置正确",
        confidence=0.9,
    )
    await kb.add_item(
        type="faq",
        title="CPU 使用率过高",
        keywords=["cpu", "使用率", "高负载", "100%"],
        symptoms="CPU 使用率持续超过 90%",
        solution="使用 top 命令排查高 CPU 进程，考虑扩容或优化代码",
        confidence=0.8,
    )
    await kb.add_item(
        type="solution",
        title="磁盘空间不足处理",
        keywords=["disk", "磁盘", "空间不足", "no space"],
        symptoms="磁盘使用率超过 90%，写入失败",
        solution="清理旧日志文件，使用 du -sh 查找大文件",
        confidence=0.7,
    )


# ═══════════════════════════════════════════════════════════════════
# Test 1: 知识条目创建
# ═══════════════════════════════════════════════════════════════════

class TestKnowledgeItemCreation:

    @pytest.mark.asyncio
    async def test_add_and_list_items(self):
        """创建条目后能够列出"""
        kb = KnowledgeBaseService()
        result = await kb.add_item(
            type="faq",
            title="测试条目",
            keywords=["test", "pytest"],
            symptoms="测试症状",
            solution="测试解决方案",
            confidence=0.5,
        )
        assert result is not None
        assert result["title"] == "测试条目"
        assert result["type"] == "faq"
        assert result["confidence"] == 0.5
        assert "test" in result["keywords"]

        items = await kb.list_items()
        assert any(item["title"] == "测试条目" for item in items)


# ═══════════════════════════════════════════════════════════════════
# Test 2: 关键词匹配
# ═══════════════════════════════════════════════════════════════════

class TestKeywordMatching:

    @pytest.mark.asyncio
    async def test_match_by_keyword(self):
        """通过关键词匹配知识条目"""
        kb = KnowledgeBaseService()
        await _seed_kb(kb)

        result = await kb.search(
            user_input="nginx 返回 502 错误怎么办",
            intent="root_cause_analysis",
        )
        assert result["matched"] is True
        assert len(result["items"]) >= 1
        assert any("nginx" in item["title"].lower() for item in result["items"])
        assert result["confidence"] > 0

    @pytest.mark.asyncio
    async def test_match_by_intent_and_input(self):
        """通过意图和用户输入联合匹配"""
        kb = KnowledgeBaseService()
        await _seed_kb(kb)

        result = await kb.search(
            user_input="CPU 使用率 100%",
            intent="cpu_query",
        )
        assert result["matched"] is True
        assert any("cpu" in item["title"].lower() for item in result["items"])

    @pytest.mark.asyncio
    async def test_match_with_observations(self):
        """带 observations 的匹配"""
        kb = KnowledgeBaseService()
        await _seed_kb(kb)

        result = await kb.search(
            user_input="nginx 有问题",
            intent="service_status_query",
            observations=[
                {"tool": "service_mgr", "params": {"service": "nginx"}, "ok": True,
                 "result": {"is_active": False}},
            ],
        )
        assert result["matched"] is True
        assert any("nginx" in item["title"].lower() for item in result["items"])


# ═══════════════════════════════════════════════════════════════════
# Test 3: 无匹配返回
# ═══════════════════════════════════════════════════════════════════

class TestNoMatch:

    @pytest.mark.asyncio
    async def test_no_match_returns_empty(self):
        """无匹配时返回空结果"""
        kb = KnowledgeBaseService()
        await _seed_kb(kb)

        result = await kb.search(
            user_input="xyz 完全不相关的查询内容",
            intent="unknown",
        )
        assert result["matched"] is False
        assert result["items"] == []
        assert result["confidence"] == 0.0


# ═══════════════════════════════════════════════════════════════════
# Test 4: DiagnoseAgent 调用知识库
# ═══════════════════════════════════════════════════════════════════

class TestDiagnoseAgentWithKnowledge:

    @pytest.mark.asyncio
    async def test_search_knowledge_with_service(self):
        """DiagnoseAgent 注入 knowledge_service 后能查询知识库"""
        kb = KnowledgeBaseService()
        await _seed_kb(kb)

        agent = DiagnoseAgent(knowledge_service=kb)
        result = await agent.search_knowledge(
            intent="root_cause_analysis",
            observations=[],
            user_input="nginx 502",
        )
        assert result["matched"] is True
        assert any("nginx" in item.get("title", "").lower() for item in result["items"])

    @pytest.mark.asyncio
    async def test_search_knowledge_without_service(self):
        """未注入 knowledge_service 时返回空结果（不抛异常）"""
        agent = DiagnoseAgent()  # 不注入 knowledge_service
        result = await agent.search_knowledge(
            intent="cpu_query",
            observations=[],
            user_input="CPU 使用率",
        )
        assert result["matched"] is False
        assert result["items"] == []


# ═══════════════════════════════════════════════════════════════════
# Test 5: 知识库异常 fallback
# ═══════════════════════════════════════════════════════════════════

class TestKnowledgeFallback:

    @pytest.mark.asyncio
    async def test_search_never_raises(self):
        """search() 在任何情况下都不抛异常"""
        kb = KnowledgeBaseService()
        # 即使传入奇怪的数据也不应抛异常
        result = await kb.search(
            user_input="",
            intent="",
            observations=None,  # type: ignore
        )
        assert isinstance(result, dict)
        assert "matched" in result
        assert "items" in result

    @pytest.mark.asyncio
    async def test_add_item_never_raises(self):
        """add_item() 失败时返回 None，不抛异常"""
        kb = KnowledgeBaseService()
        result = await kb.add_item(
            type="faq",
            title=None,  # type: ignore - 故意传 None 测试健壮性
            keywords=None,  # type: ignore
            symptoms=None,  # type: ignore
            solution=None,  # type: ignore
        )
        # 要么成功返回 dict，要么返回 None
        assert result is None or isinstance(result, dict)


# ═══════════════════════════════════════════════════════════════════
# Test 6: 空数据库运行
# ═══════════════════════════════════════════════════════════════════

class TestEmptyDatabase:

    @pytest.mark.asyncio
    async def test_search_on_empty_kb(self):
        """未预置数据的知识库搜索返回空结果"""
        kb = KnowledgeBaseService()
        result = await kb.search(
            user_input="这是一个完全无关的查询 that should not match anything",
            intent="unknown",
        )
        assert result["matched"] is False
        assert result["items"] == []
        assert result["confidence"] == 0.0

    @pytest.mark.asyncio
    async def test_list_returns_list(self):
        """list_items 总是返回列表（不抛异常）"""
        kb = KnowledgeBaseService()
        items = await kb.list_items()
        assert isinstance(items, list)


# ═══════════════════════════════════════════════════════════════════
# Test 7: 原有诊断流程不受影响（回归测试）
# ═══════════════════════════════════════════════════════════════════

class TestRegression:

    def test_diagnose_agent_plan_unchanged(self):
        """DiagnoseAgent.plan() 行为不变"""
        agent = DiagnoseAgent()
        result = agent.plan({"intent": "cpu_query"})
        assert len(result["plans"]) == 1
        assert result["plans"][0]["tool"] == "sys_info"
        assert result["plans"][0]["params"]["metric"] == "cpu"

    def test_diagnose_agent_with_knowledge_service_plan_unchanged(self):
        """注入 knowledge_service 后 plan() 行为不变"""
        kb = KnowledgeBaseService()
        agent = DiagnoseAgent(knowledge_service=kb)
        result = agent.plan({"intent": "memory_query"})
        assert len(result["plans"]) == 1
        assert result["plans"][0]["tool"] == "sys_info"
        assert result["plans"][0]["params"]["metric"] == "memory"

    def test_reporter_generate_without_knowledge(self):
        """ReporterAgent.generate() 不带 knowledge_result 时行为不变"""
        agent = ReporterAgent()
        report = agent.generate(
            intent_result={"intent": "cpu_query"},
            observations=[],
            user_input="CPU 使用率",
        )
        assert "诊断报告" in report
        # 不应包含知识依据章节
        assert "知识依据" not in report

    def test_reporter_generate_with_knowledge(self):
        """ReporterAgent.generate() 带 knowledge_result 时追加知识依据"""
        agent = ReporterAgent()
        knowledge_result = {
            "matched": True,
            "items": [
                {
                    "title": "CPU 使用率过高",
                    "type": "faq",
                    "solution": "使用 top 排查高 CPU 进程",
                    "confidence": 0.8,
                }
            ],
            "confidence": 0.8,
        }
        report = agent.generate(
            intent_result={"intent": "cpu_query"},
            observations=[],
            user_input="CPU 使用率",
            knowledge_result=knowledge_result,
        )
        assert "诊断报告" in report
        assert "知识依据" in report
        assert "CPU 使用率过高" in report

    def test_reporter_agent_context_field_exists(self):
        """AgentContext 有 knowledge_result 字段"""
        from app.services.agent_context import AgentContext
        ctx = AgentContext(session_id="test", user_input="test")
        assert hasattr(ctx, "knowledge_result")
        assert ctx.knowledge_result is None
