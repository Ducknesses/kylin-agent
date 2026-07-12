"""FixOption 一键修复选项数据模型 —— Day 6

与 API 统一规范 v1.1 对齐。
"""
from typing import Literal

from pydantic import BaseModel, ConfigDict, field_validator

# ── 风险等级 ──────────────────────────────────────────────────────────
# 风险等级最终仍需由后端安全规则重新计算，不能直接信任规划器输入。

RiskLevel = Literal["low", "medium", "high"]


# ── FixOption ─────────────────────────────────────────────────────────

class FixOption(BaseModel):
    """LLM/规则生成的单个修复选项

    medium 风险等级的选项由前端二次确认后才可执行。
    high 风险等级不应生成 FixOption——规划阶段即拒绝生成可执行方案。
    rollback 只作为人工说明文本，不自动执行。
    """

    option_id: str
    title: str
    description: str
    risk_level: RiskLevel
    tool: str
    params: dict[str, object]
    requires_confirm: bool
    rollback: str | None = None

    model_config = ConfigDict(extra="forbid")

    # ── 非空字符串校验 ──────────────────────────────────────────────
    # option_id / title / description / tool 均 strip 后校验，
    # 空字符串或仅空白字符一律拒绝。

    @field_validator("option_id", "title", "description", "tool")
    @classmethod
    def _not_blank(cls, v: str) -> str:
        stripped = v.strip()
        if not stripped:
            raise ValueError("不能为空或仅包含空白字符")
        return stripped
