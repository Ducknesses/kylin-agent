"""FixOption 一键修复选项数据模型 —— Day 6

与 API 统一规范 v1.1 对齐。
"""
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, field_validator, model_validator

# ── 风险等级 ──────────────────────────────────────────────────────────
# 风险等级最终仍需由后端安全规则重新计算，不能直接信任规划器输入。

RiskLevel = Literal["low", "medium", "high"]


# ── FixOption ─────────────────────────────────────────────────────────

class FixOption(BaseModel):
    """LLM/规则生成的单个修复选项"""
    option_id: str
    title: str
    description: str
    risk_level: RiskLevel
    tool: str
    params: dict[str, object]
    requires_confirm: bool
    rollback: str | None = None
    model_config = ConfigDict(extra="forbid")

    @field_validator("option_id", "title", "description", "tool")
    @classmethod
    def _not_blank(cls, v: str) -> str:
        stripped = v.strip()
        if not stripped:
            raise ValueError("不能为空或仅包含空白字符")
        return stripped


# ── Action API 模型 ──────────────────────────────────────────────────


class ActionExecuteRequest(BaseModel):
    session_id: str
    option_id: str
    model_config = ConfigDict(extra="forbid")

    @field_validator("session_id", "option_id")
    @classmethod
    def _not_blank(cls, v: str) -> str:
        stripped = v.strip()
        if not stripped:
            raise ValueError("不能为空或仅包含空白字符")
        return stripped

    @field_validator("option_id")
    @classmethod
    def _valid_option_id(cls, v: str) -> str:
        if not re.fullmatch(r"fix_[0-9a-f]{8}", v):
            raise ValueError("option_id 格式无效，需为 fix_{8位十六进制}")
        return v


ActionExecuteStatus = Literal["ready", "confirm_required", "blocked", "executed", "failed"]


class ActionExecuteResponse(BaseModel):
    option_id: str
    session_id: str
    trace_id: str
    status: ActionExecuteStatus
    risk_level: RiskLevel
    message: str
    requires_confirm: bool
    result_summary: str | None = None
    confirm_id: str | None = None

    @field_validator("confirm_id")
    @classmethod
    def _valid_confirm_id(cls, v: str | None) -> str | None:
        if v is not None and not re.fullmatch(r"cfm_[0-9a-f]{8}", v):
            raise ValueError("confirm_id 格式无效，需为 cfm_{8位十六进制}")
        return v

    @model_validator(mode="after")
    def _cross_validate(self) -> "ActionExecuteResponse":
        if self.status == "confirm_required":
            if self.confirm_id is None:
                raise ValueError("confirm_required 状态必须提供 confirm_id")
        elif self.confirm_id is not None:
            raise ValueError(f"{self.status} 状态不得包含 confirm_id")
        return self


# ── Confirm API 模型 ──────────────────────────────────────────────────


class ActionConfirmRequest(BaseModel):
    session_id: str
    confirm_id: str
    decision: Literal["approve", "reject"]
    model_config = ConfigDict(extra="forbid")

    @field_validator("session_id")
    @classmethod
    def _not_blank(cls, v: str) -> str:
        stripped = v.strip()
        if not stripped:
            raise ValueError("不能为空或仅包含空白字符")
        return stripped

    @field_validator("confirm_id")
    @classmethod
    def _valid_confirm_id(cls, v: str) -> str:
        if not re.fullmatch(r"cfm_[0-9a-f]{8}", v):
            raise ValueError("confirm_id 格式无效")
        return v


ActionConfirmStatus = Literal["executed", "failed", "rejected", "blocked", "expired", "conflict"]


class ActionConfirmResponse(BaseModel):
    confirm_id: str
    option_id: str
    session_id: str
    trace_id: str
    decision: Literal["approve", "reject"]
    status: ActionConfirmStatus
    message: str
    result_summary: str | None = None
