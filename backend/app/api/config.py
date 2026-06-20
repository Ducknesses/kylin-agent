"""白名单/权限配置接口

正式接口：GET /api/config/whitelist（API 文档约定）
PUT 接口为阶段 2 预留，不作为 Day 1 核心验收。
"""
import logging
from typing import List

from fastapi import APIRouter

from app.core.rbac import COMMAND_WHITELIST, DANGEROUS_PATTERNS, Permission
from app.schemas.models import WhitelistUpdate

logger = logging.getLogger(__name__)
router = APIRouter()


def _build_whitelist_commands() -> list:
    """
    从 RBAC 低风险查询命令提取前端展示用白名单摘要

    返回 commands 不等于完整 RBAC 执行规则——这里只摘取 agent-read
    层的模式供前端白名单页展示，实际执行时仍走 check_command_permission。
    """
    commands = []
    for pattern in COMMAND_WHITELIST.get(Permission.READ, []):
        # 去掉正则锚点 ^ $ 和尾部 .*，转为面向用户的通配符形式
        display = pattern.strip("^$").replace(".*", "*").replace(r"\s+", " ")
        commands.append({"pattern": display, "risk": "low"})
    return commands


def _build_blocked_patterns() -> list:
    """从 RBAC 危险模式中提取前端展示用拦截规则"""
    # 直接使用 DANGEROUS_PATTERNS 原始字符串
    return list(DANGEROUS_PATTERNS)


@router.get("/config/whitelist")
async def get_whitelist() -> dict:
    """获取当前命令白名单（API 文档约定格式）"""
    return {
        "code": 200,
        "data": {
            "commands": _build_whitelist_commands(),
            "blocked_patterns": _build_blocked_patterns(),
        },
    }


@router.put("/config/whitelist")
async def update_whitelist(body: WhitelistUpdate) -> dict:
    """
    [阶段 2 预留] 更新命令白名单，当前不实际修改运行时常量
    """
    logger.info(f"[Config] 更新白名单请求: {len(body.commands)} 条命令")
    return {
        "code": 200,
        "message": "白名单更新接口已预留（阶段2实现持久化）",
        "current_count": len(body.commands),
    }
