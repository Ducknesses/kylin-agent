"""应用配置模型 —— 键值对持久化（白名单等）"""

from sqlalchemy import Column, String, Text

from app.models import Base


class AppConfig(Base):
    """键值配置表"""

    __tablename__ = "app_config"

    key = Column(String, primary_key=True)
    value = Column(Text, nullable=False)
    updated_at = Column(String, nullable=False)
