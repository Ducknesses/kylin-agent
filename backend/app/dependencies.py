"""共享依赖装配 —— 跨请求复用的服务实例

本模块仅创建需要跨模块共享的服务实例，不导入 API 路由。
后续 Action API 可通过本模块获取 FixOptionStore，无需从 chat.py 导入。
"""
from app.services.fix_option_store import FixOptionStore
from app.services.fix_planner_agent import FixPlannerAgent
from app.services.llm_client import LLMClient
from app.services.tool_registry import ToolRegistry

# 共享 ToolRegistry + LLMClient —— FixPlanner 与 Orchestrator 共用
tool_registry = ToolRegistry()
llm_client = LLMClient()

# FixOptionStore 共享实例
fix_option_store = FixOptionStore()

# FixPlannerAgent —— 注入共享 ToolRegistry + LLMClient
fix_planner = FixPlannerAgent(
    tool_registry=tool_registry,
    llm_client=llm_client,
)
