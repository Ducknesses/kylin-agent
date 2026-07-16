"""知识库条目模型 —— 用于故障模式匹配和解决方案推荐

通过 Base.metadata.create_all() 自动建表，与项目其他模型保持一致。
"""

from sqlalchemy import Column, Float, Integer, String, Text

from app.models import Base


class KnowledgeItem(Base):
    """知识库条目表

    支持三种类型：
      - faq: 常见问题
      - fault_pattern: 故障模式
      - solution: 解决方案

    keywords 以 JSON 数组字符串存储，如 '["nginx","502"]'。
    """

    __tablename__ = "knowledge_items"

    id = Column(Integer, primary_key=True, autoincrement=True)
    type = Column(String, nullable=False, default="faq")
    title = Column(String, nullable=False)
    keywords = Column(Text, nullable=False, default="[]")
    symptoms = Column(Text, nullable=False, default="")
    solution = Column(Text, nullable=False, default="")
    confidence = Column(Float, nullable=False, default=0.5)
    created_at = Column(String, nullable=False)
    updated_at = Column(String, nullable=False)
