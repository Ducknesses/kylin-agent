"""RedisStorage —— Redis 基础操作封装

职责：
  - 只负责 Redis 基础操作：set / get / delete / exists / expire
  - 不包含业务逻辑，不处理 FixOption / Confirmation
  - Redis 不可用时明确失败，不自动降级

Key 命名规范由调用方决定，本层不做约束。
"""
import json
import logging
from typing import Any

import redis as redis_lib

from config import settings

logger = logging.getLogger(__name__)


class StorageUnavailableError(RuntimeError):
    """Redis 存储不可用异常 —— 禁止自动降级为内存存储"""


def _build_redis() -> redis_lib.Redis:
    """建立 Redis 连接，连接失败直接抛出 StorageUnavailableError"""
    try:
        client = redis_lib.from_url(settings.REDIS_URL, decode_responses=True)
        client.ping()
        logger.info("Redis 连接成功: %s", settings.REDIS_URL)
        return client
    except Exception as exc:
        msg = "Redis 存储不可用，无法连接"
        logger.error("%s: %s", msg, exc)
        raise StorageUnavailableError(msg) from exc


class RedisStorage:
    """Redis 基础存储 —— 纯 KV 操作，零业务逻辑"""

    def __init__(self, client: redis_lib.Redis | None = None) -> None:
        """可注入 Redis 客户端（测试注入 fakeredis），默认从配置构建"""
        self._client = client or _build_redis()

    # ── 基础操作 ──────────────────────────────────────────────────

    def set(self, key: str, value: dict[str, Any], ttl: int | None = None) -> bool:
        """写入 JSON 值，可选 TTL（秒）"""
        raw = json.dumps(value, ensure_ascii=False, default=str)
        if ttl is not None and ttl > 0:
            self._client.set(key, raw, ex=ttl)
        else:
            self._client.set(key, raw)
        return True

    def get(self, key: str) -> dict[str, Any] | None:
        """读取并反序列化 JSON，不存在返回 None"""
        raw = self._client.get(key)
        if raw is None:
            return None
        try:
            return json.loads(raw)  # type: ignore[no-any-return]
        except json.JSONDecodeError:
            logger.warning("Redis key %s 的值不是有效 JSON，返回 None", key)
            return None

    def delete(self, key: str) -> bool:
        """删除 key，返回是否删除成功"""
        return bool(self._client.delete(key))

    def exists(self, key: str) -> bool:
        """检查 key 是否存在"""
        return bool(self._client.exists(key))

    def expire(self, key: str, ttl: int) -> bool:
        """设置 key 过期时间（秒），返回是否成功"""
        return bool(self._client.expire(key, ttl))

    def ttl(self, key: str) -> int:
        """获取 key 剩余生存时间（秒），-1 表示永不过期，-2 表示不存在"""
        return self._client.ttl(key)  # type: ignore[no-any-return]

    def raw_get(self, key: str) -> str | None:
        """读取原始字符串值（不经过 JSON 解析）"""
        return self._client.get(key)  # type: ignore[no-any-return]

    def raw_set(self, key: str, value: str, ttl: int | None = None) -> bool:
        """写入原始字符串值"""
        if ttl is not None and ttl > 0:
            self._client.set(key, value, ex=ttl)
        else:
            self._client.set(key, value)
        return True

    def keys(self, pattern: str) -> list[str]:
        """按 pattern 列出所有匹配 key（慎用，生产环境避免 KEYS）"""
        return self._client.keys(pattern)  # type: ignore[no-any-return]

    def scan_keys(self, pattern: str, count: int = 100) -> list[str]:
        """使用 SCAN 迭代匹配 key（推荐）"""
        cursor = 0
        result: list[str] = []
        while True:
            cursor, keys = self._client.scan(cursor, match=pattern, count=count)
            result.extend(keys)  # type: ignore[arg-type]
            if cursor == 0:
                break
        return result

    # ── 原子 CAS 操作 ────────────────────────────────────────────

    def cas_update(
        self,
        key: str,
        expected_status: str | None,
        update_fn: Any,
        ttl: int | None = None,
    ) -> dict[str, Any] | None:
        """乐观锁 CAS 更新：WATCH → GET → check → MULTI → SET → EXEC

        返回更新后的数据，或 None 表示 CAS 失败/状态不匹配/update_fn 拒绝。
        """
        pipeline = self._client.pipeline()
        try:
            pipeline.watch(key)
            raw = pipeline.get(key)
            if raw is None:
                pipeline.unwatch()
                return None
            data = json.loads(raw)
            if expected_status is not None and data.get("status") != expected_status:
                pipeline.unwatch()
                return None
            new_data = update_fn(data)
            if new_data is None:
                # update_fn 拒绝更新（状态不满足转移条件）
                pipeline.unwatch()
                return None
            new_raw = json.dumps(new_data, ensure_ascii=False, default=str)
            pipeline.multi()
            if ttl is not None and ttl > 0:
                pipeline.set(key, new_raw, ex=ttl)
            else:
                pipeline.set(key, new_raw)
            result = pipeline.execute()
            if result is None:
                # WATCH 检测到并发修改
                return None
            return new_data
        except Exception:
            pipeline.reset()
            raise

    def execute_script(self, script: str, keys: list[str], args: list[str]) -> Any:
        """执行 Lua 脚本，原子性完成多步操作（生产环境 EVAL）

        测试环境（fakeredis 不支持 EVAL）自动降级为 CAS 语义。
        """
        try:
            return self._client.eval(script, len(keys), *keys, *args)
        except Exception:
            # fakeredis 不支持 EVAL → 委托给调用方的 CAS 路径
            raise RuntimeError("EVAL not available") from None
