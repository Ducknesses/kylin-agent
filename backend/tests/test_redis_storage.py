"""RedisStorage 测试

覆盖：
  - set / get
  - delete
  - exists
  - expire
  - scan_keys
  - JSON 序列化/反序列化
"""
import pytest
from app.services.storage.redis_storage import RedisStorage, StorageUnavailableError

try:
    import fakeredis
    HAS_FAKEREDIS = True
except ImportError:
    HAS_FAKEREDIS = False


@pytest.fixture(scope="module")
def storage():
    """创建使用 fakeredis 的 RedisStorage（模块级，避免重复创建开销）"""
    if not HAS_FAKEREDIS:
        pytest.skip("fakeredis 未安装")
    client = fakeredis.FakeRedis(decode_responses=True)
    return RedisStorage(client=client)


class TestRedisStorage:
    """RedisStorage 基础操作测试"""

    def test_set_and_get(self, storage):
        storage.set("test:key1", {"name": "value", "num": 42})
        result = storage.get("test:key1")
        assert result is not None
        assert result["name"] == "value"
        assert result["num"] == 42

    def test_get_nonexistent_returns_none(self, storage):
        assert storage.get("nonexistent:key") is None

    def test_delete(self, storage):
        storage.set("test:del", {"x": 1})
        assert storage.exists("test:del") is True
        assert storage.delete("test:del") is True
        assert storage.exists("test:del") is False

    def test_delete_nonexistent(self, storage):
        assert storage.delete("no:such:key") is False

    def test_exists(self, storage):
        assert storage.exists("test:exists") is False
        storage.set("test:exists", {"a": 1})
        assert storage.exists("test:exists") is True

    def test_set_with_ttl(self, storage):
        storage.set("test:ttl", {"data": "x"}, ttl=3600)
        result = storage.get("test:ttl")
        assert result is not None
        assert result["data"] == "x"

    def test_set_without_ttl(self, storage):
        storage.set("test:no:ttl", {"data": "y"})
        result = storage.get("test:no:ttl")
        assert result is not None
        assert result["data"] == "y"

    def test_expire(self, storage):
        storage.set("test:expire:me", {"data": "z"})
        assert storage.expire("test:expire:me", 60) is True

    def test_expire_nonexistent(self, storage):
        assert storage.expire("no:such:key", 60) is False

    def test_keys(self, storage):
        storage.set("test:keys:a", {"id": "a"})
        storage.set("test:keys:b", {"id": "b"})
        storage.set("other:keys:c", {"id": "c"})
        matching = storage.keys("test:keys:*")
        assert len(matching) == 2

    def test_scan_keys(self, storage):
        storage.set("test:scan:a", {"id": "a"})
        storage.set("test:scan:b", {"id": "b"})
        storage.set("other:scan:c", {"id": "c"})
        matching = storage.scan_keys("test:scan:*")
        assert len(matching) == 2

    def test_json_with_special_chars(self, storage):
        data = {"msg": "你好世界 🌍", "arr": [1, 2, 3], "null_val": None}
        storage.set("test:unicode", data)
        result = storage.get("test:unicode")
        assert result is not None
        assert result["msg"] == "你好世界 🌍"
        assert result["arr"] == [1, 2, 3]
        assert result["null_val"] is None

    def test_overwrite(self, storage):
        storage.set("test:overwrite", {"ver": 1})
        storage.set("test:overwrite", {"ver": 2})
        result = storage.get("test:overwrite")
        assert result is not None
        assert result["ver"] == 2


class TestStorageUnavailable:
    """Redis 不可用场景"""

    def test_build_redis_fails_with_bad_url(self, monkeypatch):
        """无效的 Redis URL 应抛出 StorageUnavailableError"""
        # 直接 monkeypatch _build_redis 来模拟连接失败
        def _fake_build():
            raise StorageUnavailableError("Redis 存储不可用，无法连接")
        monkeypatch.setattr(
            "app.services.storage.redis_storage._build_redis",
            _fake_build,
        )
        with pytest.raises(StorageUnavailableError, match="Redis 存储不可用"):
            RedisStorage()
