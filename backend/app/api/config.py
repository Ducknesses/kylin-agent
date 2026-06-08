"""白名单/权限配置接口"""
import logging
from typing import List

from fastapi import APIRouter

from app.core.rbac import COMMAND_WHITELIST
from app.schemas.models import WhitelistUpdate

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/config/whitelist")
async def get_whitelist() -> dict:
    """获取当前命令白名单"""
    return {
        "permissions": {
            k: [p for p in v]
            for k, v in COMMAND_WHITELIST.items()
        }
    }


@router.put("/config/whitelist")
async def update_whitelist(body: WhitelistUpdate) -> dict:
    """
    更新命令白名单（阶段1预留接口，实际写入需持久化）
    """
    logger.info(f"[Config] 更新白名单请求: {len(body.commands)} 条命令")
    # 阶段1仅返回当前列表，不实际修改常量（避免运行时修改导致安全问题）
    return {
        "message": "白名单更新接口已预留（阶段2实现持久化）",
        "current_count": len(body.commands),
    }
