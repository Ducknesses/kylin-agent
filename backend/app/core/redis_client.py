"""Redis 客户端封装，支持 fakeredis fallback"""
import json
import logging
from typing import Optional

from config import settings

logger = logging.getLogger(__name__)

_redis_instance = None


def get_redis():
    """获取 Redis 客户端，连接失败时 fallback 到 fakeredis"""
    global _redis_instance
    if _redis_instance is not None:
        return _redis_instance

    try:
        import redis as redis_lib
        client = redis_lib.from_url(settings.REDIS_URL, decode_responses=True)
        client.ping()
        logger.info("Redis 连接成功")
        _redis_instance = client
        return client
    except Exception as e:
        logger.warning(f"真实 Redis 连接失败: {e}，fallback 到 fakeredis")
        try:
            import fakeredis
            fake = fakeredis.FakeRedis(decode_responses=True)
            logger.info("fakeredis 初始化成功")
            _redis_instance = fake
            return fake
        except ImportError:
            logger.error("fakeredis 未安装，Redis 功能不可用")
            raise


def set_session(session_id: str, data: dict, expire: int = 3600) -> None:
    """将会话写入 Redis"""
    r = get_redis()
    r.setex(f"session:{session_id}", expire, json.dumps(data, ensure_ascii=False))


def get_session(session_id: str) -> Optional[dict]:
    """从 Redis 读取会话"""
    r = get_redis()
    raw = r.get(f"session:{session_id}")
    if raw:
        return json.loads(raw)
    return None


def list_sessions() -> list:
    """列出所有会话键"""
    r = get_redis()
    keys = r.keys("session:*")
    sessions = []
    for k in keys:
        raw = r.get(k)
        if raw:
            sessions.append(json.loads(raw))
    return sessions


def delete_session(session_id: str) -> None:
    """删除会话"""
    r = get_redis()
    r.delete(f"session:{session_id}")
