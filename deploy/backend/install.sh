#!/bin/bash
# ============================================================
# Kylin Agent Backend 一键安装脚本（控制节点）
# ============================================================
# 用法: sudo ./install.sh
# 前提: 在项目根目录下执行
# ============================================================
set -e

echo "=============================================="
echo "  Kylin Agent Backend 安装脚本"
echo "  目标: 控制节点（x86_64 / ARM / WSL）"
echo "=============================================="
echo ""

# ---- 检查 root ----
if [ "$(id -u)" -ne 0 ]; then
    echo "[ERROR] 请使用 sudo 运行此脚本: sudo ./deploy/backend/install.sh"
    exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
INSTALL_DIR="/opt/kylin-agent"

# ---- 1. 检查 Python 版本 ----
echo "[1/6] 检查 Python 版本..."
if ! command -v python3 &>/dev/null; then
    echo "[ERROR] python3 未安装！请执行: sudo apt install python3 python3-venv python3-pip"
    exit 1
fi
PY_VERSION=$(python3 --version 2>&1)
echo "  ✓ $PY_VERSION"

PY_MAJOR=$(python3 -c 'import sys; print(sys.version_info.major)')
PY_MINOR=$(python3 -c 'import sys; print(sys.version_info.minor)')
if [ "$PY_MAJOR" -lt 3 ] || { [ "$PY_MAJOR" -eq 3 ] && [ "$PY_MINOR" -lt 10 ]; }; then
    echo "[ERROR] Python 版本需 >= 3.10，当前: ${PY_MAJOR}.${PY_MINOR}"
    exit 1
fi
echo ""

# ---- 2. 创建专用用户 ----
echo "[2/6] 创建 agent-read 用户..."

# 检测可用的 nologin shell（麒麟 V11 可能没有 /bin/false）
if [ -x /sbin/nologin ]; then
    NOLOGIN="/sbin/nologin"
elif [ -x /usr/sbin/nologin ]; then
    NOLOGIN="/usr/sbin/nologin"
elif [ -x /bin/false ]; then
    NOLOGIN="/bin/false"
elif [ -x /usr/bin/false ]; then
    NOLOGIN="/usr/bin/false"
else
    echo "[ERROR] 找不到有效的 nologin shell (/sbin/nologin, /bin/false 等)"
    exit 1
fi
echo "  使用 nologin shell: $NOLOGIN"

if id agent-read &>/dev/null; then
    echo "  agent-read 用户已存在，跳过创建"
else
    groupadd -f -r agent-read
    useradd -r -s "$NOLOGIN" -g agent-read agent-read
    echo "  agent-read 用户已创建"
fi
echo ""

# ---- 3. 复制后端源码 ----
echo "[3/6] 部署后端源码到 $INSTALL_DIR/backend ..."
mkdir -p "$INSTALL_DIR"

# 使用 rsync 排除非运行时文件（开发测试、日志、缓存等）
if command -v rsync &>/dev/null; then
    rsync -a --exclude='tests/' --exclude='logs/' --exclude='__pycache__/' \
        --exclude='*.pyc' --exclude='*.pyo' --exclude='.pytest_cache/' \
        --exclude='.git/' --exclude='venv/' --exclude='*.egg-info/' \
        "$PROJECT_ROOT/backend/" "$INSTALL_DIR/backend/"
else
    # 回退到 cp（兼容没有 rsync 的极简系统）
    cp -r "$PROJECT_ROOT/backend" "$INSTALL_DIR/backend"
    rm -rf "$INSTALL_DIR/backend/tests" "$INSTALL_DIR/backend/logs" 2>/dev/null || true
    find "$INSTALL_DIR/backend" -type d -name '__pycache__' -exec rm -rf {} + 2>/dev/null || true
    find "$INSTALL_DIR/backend" -type f -name '*.pyc' -delete 2>/dev/null || true
fi

# 复制 .env（如果存在）
if [ -f "$PROJECT_ROOT/backend/.env" ]; then
    cp "$PROJECT_ROOT/backend/.env" "$INSTALL_DIR/backend/.env"
    echo "  ✓ .env 已复制"
else
    cp "$PROJECT_ROOT/backend/.env.example" "$INSTALL_DIR/backend/.env"
    echo "  [WARN] 未找到 .env，已从 .env.example 创建，请立即编辑 $INSTALL_DIR/backend/.env"
fi
echo ""

# ---- 4. 创建虚拟环境并安装依赖 ----
echo "[4/6] 创建 Python 虚拟环境并安装依赖..."

# pip 镜像源：可通过 PIP_INDEX_URL 环境变量覆盖（注意：sudo 默认不传递环境变量，需要用 sudo -E 或写入 pip.conf）
PIP_INDEX="${PIP_INDEX_URL:-https://pypi.org/simple}"

# 写入全局 pip.conf 确保 pip 使用正确的镜像源（解决 sudo 不传递环境变量的问题）
mkdir -p /etc/pip.conf.d 2>/dev/null || true
cat > /etc/pip.conf << PIPCONF
[global]
index-url = ${PIP_INDEX}
timeout = 120
retries = 3
PIPCONF
echo "  已写入 /etc/pip.conf，pip 源: $PIP_INDEX"

# 同时也为虚拟环境内的 pip 设置（双保险）
cd "$INSTALL_DIR/backend"
python3 -m venv venv
source venv/bin/activate

# 虚拟环境内也配置 pip.conf
mkdir -p "$VIRTUAL_ENV/pip.conf.d" 2>/dev/null || true
cat > "$VIRTUAL_ENV/pip.conf" << PIPCONF
[global]
index-url = ${PIP_INDEX}
timeout = 120
retries = 3
PIPCONF

pip install --upgrade pip || {
    echo "[ERROR] pip 升级失败，请检查网络连接"
    echo "  当前 pip 源: $PIP_INDEX"
    echo "  提示: 可设置 PIP_INDEX_URL 使用国内镜像，例如："
    echo "    PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple sudo -E ./deploy/backend/install.sh"
    deactivate
    exit 1
}
pip install -r requirements.txt || {
    echo "[ERROR] Python 依赖安装失败，请检查网络或使用 PIP_INDEX_URL 指定镜像"
    deactivate
    exit 1
}
echo "  ✓ 依赖安装完成"

# 验证关键依赖（Python 模块 + 可执行文件 + WebSocket 支持库）
python3 -c "import fastapi; import uvicorn; print(f'  fastapi={fastapi.__version__}')" || {
    echo "[ERROR] Python 模块导入失败，fastapi/uvicorn 未正确安装"
    deactivate
    exit 1
}
# 注意：麒麟系统 uvloop 编译依赖 maturin 可能版本过旧导致崩溃，
# 因此 requirements.txt 已移除 uvicorn[standard]，改用 uvicorn + httptools 显式依赖。
# WebSocket 由独立的 websockets 包提供。
python3 -c "import websockets" || {
    echo "[ERROR] WebSocket 支持库未安装，请检查 pip install uvicorn 或 requirements.txt 中的 websockets"
    deactivate
    exit 1
}
# 验证 uvicorn 模块可用（使用 python -m 方式，避免依赖 venv/bin/uvicorn 入口点）
if ! python3 -c "import uvicorn; print(f'  uvicorn={uvicorn.__version__}')" 2>/dev/null; then
    echo "[ERROR] uvicorn 模块导入失败"
    echo "  请检查 pip install 是否成功，或手动执行:"
    echo "    cd $INSTALL_DIR/backend && source venv/bin/activate && pip install uvicorn httptools"
    deactivate
    exit 1
fi
echo "  ✓ uvicorn 模块验证通过"
deactivate
echo ""

# ---- 5. 创建数据目录 ----
echo "[5/6] 创建数据目录..."
mkdir -p "$INSTALL_DIR/backend/data"
echo "  ✓ data/ 目录已创建"
echo ""

# ---- 6. 安装 systemd 服务 + 修正权限 ----
echo "[6/6] 安装 systemd 服务..."
cp "$SCRIPT_DIR/kylin-agent.service" /etc/systemd/system/kylin-agent.service
systemctl daemon-reload
systemctl enable kylin-agent

echo "  修正文件所有权为 agent-read:agent-read ..."
chown -R agent-read:agent-read "$INSTALL_DIR/backend"
echo "  ✓ 权限已修正"
echo "  ✓ kylin-agent.service 已安装并设为开机自启"
echo ""

echo "=============================================="
echo "  ✓ Kylin Agent Backend 安装完成！"
echo "=============================================="
echo ""
echo "  ╔══════════════════════════════════════════════╗"
echo "  ║  📋 下一步：配置 Token（必须！）             ║"
echo "  ║                                              ║"
echo "  ║  编辑配置文件:                               ║"
echo "  ║    vim $INSTALL_DIR/backend/.env             ║"
echo "  ║                                              ║"
echo "  ║  需要配置的 Token:                           ║"
echo "  ║                                              ║"
echo "  ║  ① API_TOKEN — 前端连接后端的认证密钥        ║"
echo "  ║     前端在 ConnectionSettings 面板填入       ║"
echo "  ║     不设置则无需认证（开发环境可跳过）       ║"
echo "  ║                                              ║"
echo "  ║  ② MCP_AUTH_TOKEN — 后端连接 MCP Server      ║"
echo "  ║     需与麒麟目标机的 .env 中保持一致         ║"
echo "  ║     Mock 模式可跳过                          ║"
echo "  ║                                              ║"
echo "  ║  ③ DEEPSEEK_API_KEY — LLM API 密钥           ║"
echo "  ║     LLM_ENABLED=false 时可跳过               ║"
echo "  ║                                              ║"
echo "  ║  ④ REDIS_URL — Redis 连接地址                ║"
echo "  ║     用于任务状态持久化 + 会话缓存             ║"
echo "  ║     默认 redis://localhost:6379/0            ║"
echo "  ║     未安装 Redis 请执行:                     ║"
echo "  ║       sudo apt install redis-server           ║"
echo "  ║       sudo systemctl enable --now redis       ║"
echo "  ║     代码内置 fakeredis fallback，可延后安装   ║"
echo "  ║                                              ║"
echo "  ║  配置完成后:                                 ║"
echo "  ║    sudo systemctl start kylin-agent          ║"
echo "  ║    sudo journalctl -u kylin-agent -f         ║"
echo "  ║    curl http://127.0.0.1:8000/health         ║"
echo "  ╚══════════════════════════════════════════════╝"
echo ""
echo "  安装路径:    $INSTALL_DIR/backend"
echo "  配置文件:    $INSTALL_DIR/backend/.env"
echo "  服务名称:    kylin-agent"
echo "  监听地址:    127.0.0.1:8000"
echo ""
echo "  提示: Frontend + Nginx 请用 deploy/frontend/install.sh 单独安装"
echo ""