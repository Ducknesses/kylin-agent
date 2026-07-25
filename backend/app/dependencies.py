"""共享依赖装配 —— 跨请求复用的服务实例 + 认证依赖链

本模块仅创建需要跨模块共享的服务实例，不导入 API 路由。
后续 Action API 可通过本模块获取 FixOptionStore，无需从 chat.py 导入。
"""
import json
import logging

from app.mcp.client import MCPClient, MCPServerInfo, MCPTransport
from app.services.agent_harness import AgentHarness
from app.services.audit_service import AuditService
from app.services.fix_option_store import FixOptionStore
from app.services.fix_planner_agent import FixPlannerAgent
from app.services.llm_client import LLMClient
from app.services.mcp_server_manager import MCPServerManager
from app.services.safety_guard import SafetyGuard
from app.services.tool_registry import ToolRegistry
from config import settings

logger = logging.getLogger(__name__)

# ── 核心服务单例 ──────────────────────────────────────────────────

mcp_client = MCPClient()
tool_registry = ToolRegistry()
llm_client = LLMClient()
safety_guard = SafetyGuard()

# MCPServerManager —— 管理服务器生命周期 + 工具同步
server_manager = MCPServerManager(mcp_client=mcp_client, tool_registry=tool_registry)

# AgentHarness —— 注入共享依赖
agent_harness = AgentHarness(
    safety_guard=safety_guard,
    tool_registry=tool_registry,
    mcp_client=mcp_client,
)

# AuditService
audit_service = AuditService()

# FixOptionStore 共享实例
fix_option_store = FixOptionStore()

from app.services.confirmation_store import ConfirmationStore

# ConfirmationStore —— medium 确认存储
confirmation_store = ConfirmationStore()

from app.services.action_service import ActionService

# ActionService —— 注入真实执行依赖
action_service = ActionService(
    fix_option_store=fix_option_store,
    safety_guard=safety_guard,
    agent_harness=agent_harness,
    audit_service=audit_service,
    confirmation_store=confirmation_store,
    tool_registry=tool_registry,
)

# FixPlannerAgent —— 注入共享 ToolRegistry + LLMClient
fix_planner = FixPlannerAgent(
    tool_registry=tool_registry,
    llm_client=llm_client,
)

# MessageRepository —— 聊天消息持久化（SQLite，未来可替换为 PostgreSQL）
from app.repositories import SQLiteMessageRepository

message_repository = SQLiteMessageRepository()

# KnowledgeBaseService —— 知识库匹配（SQLite，未来可替换为向量检索）
from app.services.knowledge_service import KnowledgeBaseService

knowledge_service = KnowledgeBaseService()


# ── 向后兼容：从旧 MCP_SERVER_URL 配置加载单服务器 ────────────────

def _load_legacy_server() -> None:
    """如果旧 MCP_SERVER_URL 已配置但 MCP_SERVERS 为空，自动迁移"""
    if settings.MCP_SERVER_URL and not settings.MCP_SERVERS:
        server = MCPServerInfo(
            id="default",
            name="Default MCP Server",
            url=settings.MCP_SERVER_URL,
            transport=MCPTransport.SSE,
            auth_token=settings.MCP_AUTH_TOKEN or None,
            enabled=True,
            auto_discover=True,
        )
        mcp_client.register_server(server)
        logger.info(
            f"[dependencies] 从旧配置加载 MCP 服务器: {settings.MCP_SERVER_URL}"
        )
        return

    # 从 MCP_SERVERS JSON 加载（如果 DB 中也没有的话由 MCPServerManager 处理）
    if settings.MCP_SERVERS:
        try:
            servers = json.loads(settings.MCP_SERVERS)
            for item in servers:
                server = MCPServerInfo.from_dict(item)
                mcp_client.register_server(server)
                logger.info(f"[dependencies] 从 MCP_SERVERS 配置加载: {server.name}")
        except json.JSONDecodeError:
            logger.warning("[dependencies] MCP_SERVERS JSON 解析失败")


_load_legacy_server()


# ── 启动初始化异步函数（由 main.py 调用）───────────────────────────

async def init_mcp_servers() -> None:
    """启动时：从 DB 加载服务器列表，连接并发现工具"""
    try:
        # 先从 DB 加载（会与已注册的合并）
        await server_manager.load_from_db()
        # 连接所有启用的服务器
        await server_manager.connect_all_and_discover()
        total = len(tool_registry.get_tool_names())
        logger.info(
            f"[dependencies] MCP 初始化完成: {total} 个工具可用（纯动态模式）"
        )
    except Exception as e:
        logger.error(f"[dependencies] MCP 初始化失败: {e}")


async def shutdown_mcp_servers() -> None:
    """关闭时：断开所有 MCP 连接"""
    try:
        await mcp_client.disconnect_all()
        logger.info("[dependencies] MCP 连接已全部断开")
    except Exception as e:
        logger.error(f"[dependencies] MCP 关闭失败: {e}")


# ── 认证依赖链 ─────────────────────────────────────────────────────
# 多通道 token 提取 + 验证 + 权限门控
#
# 使用方式（路由函数）：
#   from app.dependencies import require_auth
#   from app.core.auth import AuthLevel
#
#   @router.post("/actions/execute")
#   async def execute(auth = Depends(require_auth(AuthLevel.OP))):
#       ...

import logging as _logging
from typing import Optional

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.core.auth import AuthContext, AuthLevel
from app.core.security import TokenStore

_auth_logger = _logging.getLogger(__name__)

# HTTPBearer 实例（auto_error=False 允许无 Authorization header 的请求继续）
_bearer_scheme = HTTPBearer(auto_error=False)

# TokenStore 单例
_token_store = TokenStore.singleton()


def get_token_store() -> TokenStore:
    """获取 TokenStore 单例（供 action/路由引用）"""
    return _token_store


async def extract_raw_token(
    request: Request,
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(_bearer_scheme),
) -> Optional[str]:
    """
    多通道 token 提取器。

    从以下来源按优先级提取原始 token：
    1. Authorization: Bearer <token>
    2. ?token=<token> query param
    3. [预留] X-API-Token header

    返回原始 token 字符串或 None。
    """
    if credentials:
        return credentials.credentials

    token = request.query_params.get("token")
    if token:
        return token

    return None


def require_auth(required_level: AuthLevel = AuthLevel.READ):
    """
    权限门控工厂函数。

    用法：
        @router.get("/config")
        async def config(auth: AuthContext = Depends(require_auth(AuthLevel.READ))):
            ...

    行为：
    - API_TOKEN 未配置 → 放行，auth = AuthContext.anonymous()
    - token 缺失       → 401 UNAUTHORIZED
    - token 无效       → 403 FORBIDDEN
    - 权限不足         → 403 FORBIDDEN
    - 验证通过         → 注入 AuthContext

    返回：AuthContext
    """

    async def _verify(
        request: Request,
        token: Optional[str] = Depends(extract_raw_token),
    ) -> AuthContext:
        store = get_token_store()

        if not store.is_configured():
            return AuthContext.anonymous(client_ip=request.client.host if request.client else "")

        if not token:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="缺少认证令牌，请在 Authorization header 或 ?token= 参数中提供",
                headers={"WWW-Authenticate": "Bearer"},
            )

        client_ip = request.client.host if request.client else ""
        auth = store.validate(token, client_ip)
        if auth is None:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="认证令牌无效",
            )

        if auth.level < required_level:
            _auth_logger.warning(
                f"[Auth] 权限不足: level={auth.level.value}, "
                f"required={required_level.value}, "
                f"hash={auth.token_hash}"
            )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"权限不足：当前 {auth.level.value}，需要 {required_level.value}",
            )

        _auth_logger.debug(
            f"[Auth] 认证通过: level={auth.level.value}, hash={auth.token_hash}"
        )
        return auth

    return _verify