"""全局配置"""
import os
from typing import Optional


class Settings:
    """应用配置，优先从环境变量读取"""

    # DeepSeek API
    DEEPSEEK_API_KEY: str = os.getenv("DEEPSEEK_API_KEY", "")
    DEEPSEEK_BASE_URL: str = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1")

    # MCP Server（VirtualBox 麒麟 V11）
    MCP_SERVER_URL: str = os.getenv("MCP_SERVER_URL", "http://192.168.56.101:8001")

    # Redis
    REDIS_URL: str = os.getenv("REDIS_URL", "redis://localhost:6379/0")

    # SQLite
    SQLITE_DB: str = os.getenv("SQLITE_DB", "./data/audit.db")

    # 运行时参数
    COMMAND_TIMEOUT: int = int(os.getenv("COMMAND_TIMEOUT", "30"))
    MAX_INPUT_LENGTH: int = int(os.getenv("MAX_INPUT_LENGTH", "2000"))

    # FastAPI
    APP_HOST: str = os.getenv("APP_HOST", "0.0.0.0")
    APP_PORT: int = int(os.getenv("APP_PORT", "8000"))
    DEBUG: bool = os.getenv("DEBUG", "false").lower() == "true"

    # 日志
    LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO")


settings = Settings()
