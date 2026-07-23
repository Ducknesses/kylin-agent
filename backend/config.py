"""全局配置"""
import os


class Settings:
    """应用配置，优先从环境变量读取"""

    # ── LLM 统一配置 ──────────────────────────────────────────────────
    # 主开关：false 时所有 Agent 走规则版 fallback
    LLM_ENABLED: bool = os.getenv("LLM_ENABLED", "false").lower() == "true"
    # 当前提供商：deepseek | local_openai_compatible
    LLM_PROVIDER: str = os.getenv("LLM_PROVIDER", "deepseek")
    LLM_MODEL: str = os.getenv("LLM_MODEL", "deepseek-v4-pro")
    LLM_BASE_URL: str = os.getenv("LLM_BASE_URL", "https://api.deepseek.com/v1")
    LLM_TIMEOUT: int = int(os.getenv("LLM_TIMEOUT", "45"))
    LLM_TEMPERATURE: float = float(os.getenv("LLM_TEMPERATURE", "0.2"))
    LLM_MAX_TOKENS: int = int(os.getenv("LLM_MAX_TOKENS", "2048"))

    # DeepSeek API（兼容旧配置字段）
    DEEPSEEK_API_KEY: str = os.getenv("DEEPSEEK_API_KEY", "")
    DEEPSEEK_BASE_URL: str = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1")

    # ── 本地模型预留配置 ───────────────────────────────────────────────
    # 切换方式：LLM_PROVIDER=local_openai_compatible
    LOCAL_LLM_ENABLED: bool = os.getenv("LOCAL_LLM_ENABLED", "false").lower() == "true"
    LOCAL_LLM_PROVIDER: str = os.getenv("LOCAL_LLM_PROVIDER", "local_openai_compatible")
    LOCAL_LLM_BASE_URL: str = os.getenv("LOCAL_LLM_BASE_URL", "http://127.0.0.1:8000/v1")
    LOCAL_LLM_MODEL: str = os.getenv("LOCAL_LLM_MODEL", "local-model")
    LOCAL_LLM_API_KEY: str = os.getenv("LOCAL_LLM_API_KEY", "")

    # MCP Server（VirtualBox 麒麟 V11）
    MCP_SERVER_URL: str = os.getenv("MCP_SERVER_URL", "http://192.168.56.101:8001")
    # 执行器 C 通过 Bearer Token 校验后端身份，生产环境必须配置
    MCP_AUTH_TOKEN: str = os.getenv("MCP_AUTH_TOKEN", "")
    # MCP 模式：mock（默认，B 独立开发）/ real（对接执行器 C）
    MCP_MODE: str = os.getenv("MCP_MODE", "mock")

    # Redis
    REDIS_URL: str = os.getenv("REDIS_URL", "redis://localhost:6379/0")

    # 数据库 —— 通过 DATABASE_URL 统一切换 SQLite / PostgreSQL
    # 开发环境（SQLite）：sqlite+aiosqlite:///./data/app.db
    # 生产环境（PostgreSQL）：postgresql+asyncpg://user:password@host:5432/dbname
    # 未设置 DATABASE_URL 时回退使用 SQLITE_DB（向后兼容）
    DATABASE_URL: str = os.getenv(
        "DATABASE_URL",
        f"sqlite+aiosqlite:///{os.getenv('SQLITE_DB', './data/app.db')}",
    )
    # 保留旧配置项以兼容已有代码直接读取 SQLITE_DB 的场景（逐步废弃）
    SQLITE_DB: str = os.getenv("SQLITE_DB", "./data/app.db")

    # 运行时参数
    COMMAND_TIMEOUT: int = int(os.getenv("COMMAND_TIMEOUT", "30"))
    MAX_INPUT_LENGTH: int = int(os.getenv("MAX_INPUT_LENGTH", "2000"))

    # FastAPI
    APP_HOST: str = os.getenv("APP_HOST", "0.0.0.0")
    APP_PORT: int = int(os.getenv("APP_PORT", "8000"))
    DEBUG: bool = os.getenv("DEBUG", "false").lower() == "true"

    # Token 认证（空字符串表示不启用认证，向后兼容）
    API_TOKEN: str = os.getenv("API_TOKEN", "")
    # 多 token 分级模式：read:tok1,op:tok2,admin:tok3（API_TOKENS 优先于 API_TOKEN）
    API_TOKENS: str = os.getenv("API_TOKENS", "")

# Deprecated: use LLM_ENABLED instead. Kept for backward compatibility only.
    USE_REAL_LLM: bool = os.getenv("USE_REAL_LLM", "false").lower() == "true"

    # 日志
    LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO")
    # 文件日志开关：true/1/yes → 启用，其他 → 禁用
    LOG_TO_FILE: bool = os.getenv("LOG_TO_FILE", "true").strip().lower() in ("true", "1", "yes")
    LOG_DIR: str = os.getenv("LOG_DIR", "./logs")
    LOG_FILE: str = os.getenv("LOG_FILE", "backend.log")
    # 轮转保留天数，非负整数
    LOG_BACKUP_COUNT: str = os.getenv("LOG_BACKUP_COUNT", "14")


settings = Settings()
