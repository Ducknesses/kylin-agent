"""AgentContext —— 单次运维对话的上下文容器

职责：
  - 保存单次请求的完整上下文（用户输入、意图、风险等级、工具调用、观察结果、最终回复）
  - 不执行系统命令、不调用 LLM、不调用 MCP
  - trace_id 在实例化时自动生成，避免外部遗忘
"""

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone


@dataclass
class AgentContext:
    """单次 Agent 对话的完整上下文

    所有字段均可选初始化，仅 session_id 和 user_input 在创建时必须提供，
    其余字段在编排流程中逐步填充。
    """

    session_id: str
    user_input: str

    # ── 角色，默认 viewer（最小权限原则） ──
    role: str = "viewer"

    # ── 自动生成 trace_id，用于全链路追踪 ──
    trace_id: str = field(default_factory=lambda: str(uuid.uuid4()))

    # ── 意图与风险，由 IntentAgent / SafetyGuard 填充 ──
    intent: str | None = None
    risk_level: str | None = None

    # ── 工具调用记录，每条为 {"tool": str, "params": dict, "result": dict|None} ──
    # 使用 default_factory 避免可变默认值污染
    tool_calls: list[dict] = field(default_factory=list)

    # ── 观察结果，每条为 MCP 工具返回的原始观察 ──
    observations: list[dict] = field(default_factory=list)

    # ── 最终回复，由 ReporterAgent 填充 ──
    final_response: str | None = None

    # ── 时间戳（可选，不影响现有接口） ──
    created_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    updated_at: str | None = None

    def mark_updated(self) -> None:
        """刷新 updated_at 时间戳"""
        self.updated_at = datetime.now(timezone.utc).isoformat()

    def add_tool_call(self, tool: str, params: dict, result: dict | None = None) -> None:
        """追加一条工具调用记录"""
        self.tool_calls.append({
            "tool": tool,
            "params": params,
            "result": result,
        })
        self.mark_updated()

    def add_observation(self, observation: dict) -> None:
        """追加一条观察结果"""
        self.observations.append(observation)
        self.mark_updated()
