"""KnowledgeBaseService —— 知识库匹配服务

职责：
  - 保存/查询知识条目（KnowledgeItem）
  - 基于关键词 + 意图匹配知识条目
  - 返回结构化匹配结果
  - 异常时返回空结果（不抛异常），确保调用方可以安全 fallback

设计：
  - 使用项目已有的 SQLAlchemy async session（不创建新数据库连接）
  - 当前实现为关键词匹配（KeywordRetriever），接口预留未来替换为 VectorRetriever
  - 所有公开方法都不抛异常，失败时返回安全的默认值
"""

import json
import logging
from datetime import datetime, timezone

from sqlalchemy import select

from app.core.database import get_session
from app.models.knowledge import KnowledgeItem

logger = logging.getLogger(__name__)


class KnowledgeBaseService:
    """知识库匹配服务

    使用方式：
        kb = KnowledgeBaseService()
        result = await kb.search(
            user_input="nginx 502 错误",
            intent="root_cause_analysis",
            observations=[...],
        )
        # → {"matched": True, "items": [...], "confidence": 0.85}
    """

    # ── 表初始化 ──────────────────────────────────────────────────────

    async def _ensure_tables(self) -> None:
        """确保 knowledge_items 表存在（幂等）。

        生产环境由 init_engine() 负责建表，此方法作为测试/首次使用的兜底。
        """
        try:
            from app.core.database import _engine
            from app.models import Base
            async with _engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
        except Exception as e:
            logger.warning(f"[KnowledgeBase] 建表检查失败（已忽略）: {e}")

    # ── 公开接口 ──────────────────────────────────────────────────────

    async def search(
        self,
        user_input: str,
        intent: str = "unknown",
        observations: list[dict] | None = None,
    ) -> dict:
        """根据用户输入、意图和观测结果搜索匹配的知识条目

        参数:
            user_input: 用户原始输入
            intent: 意图识别结果（如 cpu_query, root_cause_analysis）
            observations: 工具观测结果列表

        返回:
            {
                "matched": bool,
                "items": list[dict],
                "confidence": float,
            }
            无匹配时返回 {"matched": false, "items": [], "confidence": 0.0}
            异常时同样返回空结果，不抛异常
        """
        try:
            items = await self._fetch_all()
            if not items:
                return {"matched": False, "items": [], "confidence": 0.0}

            obs_summary = self._summarize_observations(observations or [])
            search_text = f"{user_input} {intent} {obs_summary}".lower()

            matched = self._match_items(items, search_text)

            if not matched:
                return {"matched": False, "items": [], "confidence": 0.0}

            max_conf = max(item["confidence"] for item in matched)
            return {"matched": True, "items": matched, "confidence": max_conf}

        except Exception as e:
            logger.warning(f"[KnowledgeBase] 搜索异常（已忽略，返回空结果）: {e}")
            return {"matched": False, "items": [], "confidence": 0.0}

    async def add_item(
        self,
        type: str,
        title: str,
        keywords: list[str],
        symptoms: str,
        solution: str,
        confidence: float = 0.5,
    ) -> dict | None:
        """添加知识条目

        返回:
            成功时返回条目 dict，失败时返回 None（不抛异常）
        """
        try:
            await self._ensure_tables()
            now = datetime.now(timezone.utc).isoformat()
            async with get_session() as session:
                item = KnowledgeItem(
                    type=type,
                    title=title,
                    keywords=json.dumps(keywords, ensure_ascii=False),
                    symptoms=symptoms,
                    solution=solution,
                    confidence=confidence,
                    created_at=now,
                    updated_at=now,
                )
                session.add(item)
                await session.commit()
                await session.refresh(item)
                logger.info(f"[KnowledgeBase] 知识条目已添加: id={item.id}, title={title}")
                return self._item_to_dict(item)
        except Exception as e:
            logger.warning(f"[KnowledgeBase] 添加知识条目失败（已忽略）: {e}")
            return None

    async def list_items(self) -> list[dict]:
        """列出所有知识条目（管理用途）"""
        try:
            items = await self._fetch_all()
            return items
        except Exception as e:
            logger.warning(f"[KnowledgeBase] 列出条目失败: {e}")
            return []

    # ── 内部实现 ──────────────────────────────────────────────────────

    async def _fetch_all(self) -> list[dict]:
        """从数据库获取所有知识条目"""
        await self._ensure_tables()
        async with get_session() as session:
            result = await session.execute(select(KnowledgeItem))
            rows = result.scalars().all()
            return [self._item_to_dict(row) for row in rows]

    def _match_items(self, items: list[dict], search_text: str) -> list[dict]:
        """基于关键词匹配条目，按 confidence 降序返回匹配项"""
        matched: list[dict] = []
        for item in items:
            keywords: list[str] = []
            try:
                kw_raw = item.get("keywords", "[]")
                if isinstance(kw_raw, str):
                    keywords = json.loads(kw_raw)
                elif isinstance(kw_raw, list):
                    keywords = kw_raw
            except (json.JSONDecodeError, TypeError):
                keywords = []

            # 检查关键词匹配
            kw_match = any(kw.lower() in search_text for kw in keywords if isinstance(kw, str))
            # 检查标题匹配
            title_match = (item.get("title", "") or "").lower() in search_text
            # 检查症状匹配
            symptoms_match = any(
                word in search_text
                for word in (item.get("symptoms", "") or "").lower().split()
                if len(word) >= 2
            )

            if kw_match or title_match or symptoms_match:
                matched.append(item)

        matched.sort(key=lambda x: x.get("confidence", 0.0), reverse=True)
        return matched

    @staticmethod
    def _summarize_observations(observations: list[dict]) -> str:
        """将 observations 摘要为搜索文本"""
        if not observations:
            return ""
        parts: list[str] = []
        for obs in observations:
            if not isinstance(obs, dict):
                continue
            tool = obs.get("tool", "")
            ok = obs.get("ok", False)
            error = obs.get("error", "")
            if not ok and error:
                parts.append(f"{tool} failed: {error}")
            elif ok:
                parts.append(tool)
        return " ".join(parts)

    @staticmethod
    def _item_to_dict(item: KnowledgeItem) -> dict:
        """将 ORM 对象转为字典"""
        keywords_raw = item.keywords or "[]"
        try:
            keywords = json.loads(keywords_raw)
        except (json.JSONDecodeError, TypeError):
            keywords = []
        return {
            "id": item.id,
            "type": item.type,
            "title": item.title,
            "keywords": keywords,
            "symptoms": item.symptoms,
            "solution": item.solution,
            "confidence": item.confidence,
            "created_at": item.created_at,
            "updated_at": item.updated_at,
        }
