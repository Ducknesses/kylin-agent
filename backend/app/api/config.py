"""白名单/权限配置接口

正式接口（最新前后端 API 统一规范 v1.0）：
  GET  /api/config/whitelist  → {commands, blocked_patterns}
  PUT  /api/config/whitelist  → {message, saved_commands, saved_blocked_patterns}
"""
import json
import logging

from fastapi import APIRouter

from app.audit.models import load_config, save_config
from app.core.rbac import COMMAND_WHITELIST, DANGEROUS_PATTERNS, Permission
from app.schemas.models import WhitelistUpdate

logger = logging.getLogger(__name__)
router = APIRouter()

# 运行时白名单配置缓存（启动时从 DB 加载，无 DB 记录时使用 RBAC 默认值）
_runtime_commands: list | None = None
_runtime_blocked: list | None = None


def _build_default_commands() -> list:
    """从 RBAC 常量构建默认 commands 列表（含 role 字段）"""
    commands = []
    for perm, patterns in COMMAND_WHITELIST.items():
        for pattern in patterns:
            display = pattern.strip("^$").replace(".*", "*").replace(r"\s+", " ")
            commands.append({
                "pattern": display,
                "role": perm,
                "risk": "low" if perm == Permission.READ else "medium",
            })
    return commands


def _build_default_blocked() -> list:
    """从 RBAC 常量构建默认 blocked_patterns 列表"""
    return list(DANGEROUS_PATTERNS)


async def _load_runtime_config() -> None:
    """启动时从 DB 加载白名单配置，无记录时使用默认值"""
    global _runtime_commands, _runtime_blocked

    raw_commands = await load_config("whitelist_commands")
    if raw_commands:
        try:
            _runtime_commands = json.loads(raw_commands)
        except json.JSONDecodeError:
            logger.warning("[Config] DB 中 whitelist_commands 格式异常，使用默认值")

    if _runtime_commands is None:
        _runtime_commands = _build_default_commands()

    raw_blocked = await load_config("whitelist_blocked")
    if raw_blocked:
        try:
            _runtime_blocked = json.loads(raw_blocked)
        except json.JSONDecodeError:
            logger.warning("[Config] DB 中 whitelist_blocked 格式异常，使用默认值")

    if _runtime_blocked is None:
        _runtime_blocked = _build_default_blocked()


@router.get("/config/whitelist")
async def get_whitelist() -> dict:
    """获取当前命令白名单 —— 直接返回 commands 和 blocked_patterns"""
    if _runtime_commands is None:
        await _load_runtime_config()

    return {
        "commands": _runtime_commands,
        "blocked_patterns": _runtime_blocked,
    }


@router.put("/config/whitelist")
async def update_whitelist(body: WhitelistUpdate) -> dict:
    """更新白名单并持久化到 SQLite"""
    global _runtime_commands, _runtime_blocked

    # 从 Pydantic 模型提取数据
    new_commands = [cmd.model_dump() for cmd in body.commands]
    new_blocked = body.blocked_patterns or []

    # 持久化到 DB
    await save_config("whitelist_commands", json.dumps(new_commands, ensure_ascii=False))
    await save_config("whitelist_blocked", json.dumps(new_blocked, ensure_ascii=False))

    # 更新内存缓存
    _runtime_commands = new_commands
    _runtime_blocked = new_blocked

    logger.info(f"[Config] 白名单已更新: {len(new_commands)} 条命令, {len(new_blocked)} 条拦截规则")
    return {
        "message": "白名单已更新",
        "saved_commands": len(new_commands),
        "saved_blocked_patterns": len(new_blocked),
    }
