"""共享依赖装配 —— 跨请求复用的服务实例 + 认证依赖链

本模块仅创建需要跨模块共享的服务实例，不导入 API 路由。
后续 Action API 可通过本模块获取 FixOptionStore，无需从 chat.py 导入。
"""
from app.services.agent_harness import AgentHarness
from app.services.audit_service import AuditService
from app.services.fix_option_store import FixOptionStore
from app.services.fix_planner_agent import FixPlannerAgent
from app.services.llm_client import LLMClient
from app.services.safety_guard import SafetyGuard
from app.services.tool_registry import ToolRegistry

# 共享 ToolRegistry + LLMClient —— FixPlanner 与 Orchestrator 共用
tool_registry = ToolRegistry()
llm_client = LLMClient()

# 共享 SafetyGuard + MCPClient —— ActionService 与 Orchestrator 共用
safety_guard = SafetyGuard()

from app.mcp.client import MCPClient

mcp_client = MCPClient()

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
#
# 通道优先级：
#   1. Authorization: Bearer <token>  (HTTPBearer)
#   2. ?token=<token>                  (query param，兼容 WebSocket)
#   3. 未来: X-API-Token header

import logging
from typing import Optional

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.core.auth import AuthContext, AuthLevel
from app.core.security import TokenStore

logger = logging.getLogger(__name__)

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
    # 1. Bearer Token
    if credentials:
        return credentials.credentials

    # 2. Query String
    token = request.query_params.get("token")
    if token:
        return token

    # 3. [预留] 自定义 Header
    # token = request.headers.get("X-API-Token")
    # if token:
    #     return token

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

        # 未配置认证 → 匿名放行（向后兼容）
        if not store.is_configured():
            return AuthContext.anonymous(client_ip=request.client.host if request.client else "")

        # 需要认证但没提供 token
        if not token:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="缺少认证令牌，请在 Authorization header 或 ?token= 参数中提供",
                headers={"WWW-Authenticate": "Bearer"},
            )

        # 验证 token
        client_ip = request.client.host if request.client else ""
        auth = store.validate(token, client_ip)
        if auth is None:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="认证令牌无效",
            )

        # 权限检查
        if auth.level < required_level:
            logger.warning(
                f"[Auth] 权限不足: level={auth.level.value}, "
                f"required={required_level.value}, "
                f"hash={auth.token_hash}"
            )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"权限不足：当前 {auth.level.value}，需要 {required_level.value}",
            )

        logger.debug(
            f"[Auth] 认证通过: level={auth.level.value}, hash={auth.token_hash}"
        )
        return auth

    return _verify