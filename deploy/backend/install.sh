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
echo "[1/7] 检查 Python 版本..."
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
echo "[2/7] 创建 agent-read 用户..."
useradd -r -s /bin/false agent-read 2>/dev/null || echo "  agent-read 用户已存在"
echo ""

# ---- 3. 复制后端源码 ----
echo "[3/7] 部署后端源码到 $INSTALL_DIR ..."
mkdir -p "$INSTALL_DIR"
# 复制 backend/ 目录
cp -r "$PROJECT_ROOT/backend" "$INSTALL_DIR/backend"
# 复制前端构建产物（如果存在）
if [ -d "$PROJECT_ROOT/frontend/dist" ]; then
    cp -r "$PROJECT_ROOT/frontend/dist" "$INSTALL_DIR/frontend/dist"
    echo "  ✓ 前端构建产物已复制"
else
    echo "  [WARN] 未找到 frontend/dist/，请先在前端目录执行 npm run build"
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
echo "[4/7] 创建 Python 虚拟环境并安装依赖..."
cd "$INSTALL_DIR/backend"
python3 -m venv venv
source venv/bin/activate
pip install --quiet --upgrade pip
pip install --quiet -r requirements.txt
echo "  ✓ 依赖安装完成"
# 验证关键依赖
python3 -c "import fastapi; import uvicorn; print(f'  fastapi={fastapi.__version__}')"
deactivate
echo ""

# ---- 5. 创建数据目录 ----
echo "[5/7] 创建数据目录..."
mkdir -p "$INSTALL_DIR/backend/data"
echo "  ✓ data/ 目录已创建"
echo ""

# ---- 6. 安装 systemd 服务 ----
echo "[6/7] 安装 systemd 服务..."
cp "$SCRIPT_DIR/kylin-agent.service" /etc/systemd/system/kylin-agent.service
systemctl daemon-reload
systemctl enable kylin-agent
echo "  ✓ kylin-agent.service 已安装并设为开机自启"
echo ""

# ---- 7. 修正文件权限 ----
echo "[7/7] 修正文件所有权为 agent-read:agent-read ..."
chown -R agent-read:agent-read "$INSTALL_DIR"
echo "  ✓ 权限已修正"
echo ""

# ---- 可选：安装 Nginx 配置 ----
read -p "是否安装 Nginx 反向代理配置？(yes/no): " install_nginx
if [ "$install_nginx" = "yes" ]; then
    if ! command -v nginx &>/dev/null; then
        echo "[INFO] Nginx 未安装，正在安装..."
        apt-get update -qq && apt-get install -y -qq nginx
    fi
    cp "$SCRIPT_DIR/nginx-kylin-agent.conf" /etc/nginx/sites-available/kylin-agent
    ln -sf /etc/nginx/sites-available/kylin-agent /etc/nginx/sites-enabled/kylin-agent
    # 移除默认站点
    rm -f /etc/nginx/sites-enabled/default
    if nginx -t 2>&1; then
        systemctl reload nginx
        echo "  ✓ Nginx 配置已安装并重载"
    else
        echo "  [ERROR] Nginx 配置测试失败，请检查 /etc/nginx/sites-available/kylin-agent"
    fi
else
    echo "  已跳过 Nginx 配置"
fi
echo ""

echo "=============================================="
echo "  ✓ Kylin Agent Backend 安装完成！"
echo "=============================================="
echo ""
echo "  ╔══════════════════════════════════════════════╗"
echo "  ║  📋 下一步                                   ║"
echo "  ║                                              ║"
echo "  ║  1. 编辑配置:                                ║"
echo "  ║     vim $INSTALL_DIR/backend/.env            ║"
echo "  ║                                              ║"
echo "  ║  2. 启动服务:                                ║"
echo "  ║     sudo systemctl start kylin-agent         ║"
echo "  ║                                              ║"
echo "  ║  3. 查看日志:                                ║"
echo "  ║     sudo journalctl -u kylin-agent -f        ║"
echo "  ║                                              ║"
echo "  ║  4. 健康检查:                                ║"
echo "  ║     curl http://127.0.0.1:8000/health        ║"
echo "  ╚══════════════════════════════════════════════╝"
echo ""
echo "  安装路径:   $INSTALL_DIR"
echo "  服务名称:   kylin-agent"
echo "  监听地址:   127.0.0.1:8000"
echo ""