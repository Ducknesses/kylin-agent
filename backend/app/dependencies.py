"""共享依赖装配 —— 跨请求复用的服务实例

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
