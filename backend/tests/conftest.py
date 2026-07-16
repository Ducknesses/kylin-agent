"""测试配置 —— 使用临时数据库，确保测试隔离

- 通过 DATABASE_URL 指向临时 SQLite 文件，不污染开发数据库
- Redis 使用 fakeredis fallback
"""
import os
import tempfile

_temp_db = tempfile.mktemp(suffix=".db")
# 统一数据库入口：DATABASE_URL 优先于 SQLITE_DB
os.environ.setdefault("DATABASE_URL", f"sqlite+aiosqlite:///{_temp_db}")
os.environ.setdefault("SQLITE_DB", _temp_db)
# 跳过 Redis 连接，直接使用 fakeredis
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
