#!/usr/bin/env python3
"""启动脚本：uvicorn app.main:app"""
import os
import sys

# 将 backend 目录加入路径，确保后续 import 正确
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.chdir(os.path.dirname(os.path.abspath(__file__)))

# 加载 .env 文件中的环境变量（本地密钥等，不提交到 git）
from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))

import uvicorn
from config import settings

if __name__ == "__main__":
    uvicorn.run(
        "app.main:app",
        host=settings.APP_HOST,
        port=settings.APP_PORT,
        reload=settings.DEBUG,
        log_level=settings.LOG_LEVEL.lower(),
    )
