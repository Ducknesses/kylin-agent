"""测试配置 —— 设置 SQLite 路径为临时文件"""
import os
import tempfile

# 确保测试使用临时数据库，不污染开发数据库
os.environ.setdefault("SQLITE_DB", tempfile.mktemp(suffix=".db"))
# 跳过 Redis 连接，直接使用 fakeredis
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
