"""共享依赖装配 —— 跨请求复用的服务实例

本模块仅创建需要跨模块共享的服务实例，不导入 API 路由。
后续 Action API 可通过本模块获取 FixOptionStore，无需从 chat.py 导入。
"""
from app.services.fix_option_store import FixOptionStore
from app.services.fix_planner_agent import FixPlannerAgent

# FixOptionStore 共享实例 —— 后续 Action API 复用同一实例
fix_option_store = FixOptionStore()

# FixPlannerAgent 规则版
fix_planner = FixPlannerAgent()
