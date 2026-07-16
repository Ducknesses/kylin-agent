"""FastAPI 入口，注册所有路由"""
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import chat, sessions, monitor, audit, config as config_api, actions
from app.core.database import init_engine
from app.core.logging_config import setup_logging
from config import settings

# 统一日志初始化（幂等，ConsoleHandler + TimedRotatingFileHandler + 脱敏Filter + trace_id）
setup_logging(
    log_level=settings.LOG_LEVEL,
    log_to_file=settings.LOG_TO_FILE,
    log_dir=settings.LOG_DIR,
    log_file=settings.LOG_FILE,
    log_backup_count=settings.LOG_BACKUP_COUNT,
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期：启动时初始化数据库并预加载白名单配置"""
    logger.info("正在初始化数据库...")
    try:
        await init_engine()
        logger.info("数据库初始化完成")
    except Exception as e:
        logger.error(f"数据库初始化失败: {e}")

    # 预加载白名单配置到内存缓存（避免首次请求才懒加载）
    logger.info("正在预加载白名单配置...")
    try:
        from app.api.config import preload_config
        await preload_config()
        logger.info("白名单配置预加载完成")
    except Exception as e:
        logger.error(f"白名单配置预加载失败: {e}")

    yield
    logger.info("应用关闭")


app = FastAPI(
    title="麒麟智能运维 Agent 后端",
    description="赛题 A2 - 安全智能运维 Agent 后端服务",
    version="0.1.0",
    lifespan=lifespan,
    debug=settings.DEBUG,
)

# CORS（开发阶段允许前端跨域，生产环境应收紧）
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 注册路由
app.include_router(chat.router, prefix="/ws")
app.include_router(sessions.router, prefix="/api")
app.include_router(monitor.router, prefix="/api")
app.include_router(audit.router, prefix="/api")
app.include_router(config_api.router, prefix="/api")
app.include_router(actions.router, prefix="/api")


@app.get("/health")
async def health_check() -> dict:
    """健康检查接口"""
    return {"status": "ok", "version": "0.1.0"}
