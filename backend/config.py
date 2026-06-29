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
    # 执行器 C 通过 Bearer Token 校验后端身份，生产环境必须配置
    MCP_AUTH_TOKEN: str = os.getenv("MCP_AUTH_TOKEN", "")
    # MCP 模式：mock（默认，B 独立开发）/ real（对接执行器 C）
    MCP_MODE: str = os.getenv("MCP_MODE", "mock")

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

    # Token 认证（空字符串表示不启用认证，向后兼容）
    API_TOKEN: str = os.getenv("API_TOKEN", "")

    # Mock/Real 模式切换
    # true  → 走真实 DeepSeek + MCP 链路
    # false → 走 Mock 编排器（仅供前端联调）
    USE_REAL_LLM: bool = os.getenv("USE_REAL_LLM", "false").lower() == "true"

    # 日志
    LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO")


settings = Settings()
