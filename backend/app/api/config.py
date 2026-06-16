"""白名单/权限配置接口"""
import logging
from typing import Dict, List

from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.core.rbac import COMMAND_WHITELIST, Permission

logger = logging.getLogger(__name__)
router = APIRouter()


class WhitelistEntry(BaseModel):
    """单条白名单条目"""
    pattern: str = Field(..., description="命令模板正则")
    role: str = Field(..., description="适用角色: agent-read/agent-op/agent-admin")
    risk: str = Field(default="low", description="风险等级: low/medium/high")


class WhitelistUpdate(BaseModel):
    """更新白名单请求"""
    commands: List[WhitelistEntry] = Field(default_factory=list, description="白名单条目列表")


def _risk_by_role(role: str) -> str:
    """根据角色默认风险等级"""
    return {
        Permission.READ: "low",
        Permission.OP: "medium",
        Permission.ADMIN: "high",
    }.get(role, "low")


@router.get("/config/whitelist")
async def get_whitelist() -> List[Dict]:
    """获取当前命令白名单，返回前端可直接展示的列表格式"""
    result = []
    for role, patterns in COMMAND_WHITELIST.items():
        for pattern in patterns:
            result.append({
                "pattern": pattern,
                "role": role,
                "risk": _risk_by_role(role),
            })
    return result


@router.put("/config/whitelist")
async def update_whitelist(body: WhitelistUpdate) -> dict:
    """
    更新命令白名单（阶段2预留接口，实际持久化待后续实现）
    """
    logger.info(f"[Config] 更新白名单请求: {len(body.commands)} 条命令")
    # 阶段2：校验格式并返回成功，实际写入文件/数据库在阶段3实现
    return {
        "message": "白名单更新已接收（阶段2未持久化，仅内存校验通过）",
        "current_count": len(body.commands),
    }
