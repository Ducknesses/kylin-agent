#!/bin/bash
# ============================================================
# MCP Server 一键安装脚本（麒麟 V11 + LoongArch 目标机）
# ============================================================
# 用法: sudo ./install.sh
# 前提: 在项目根目录下执行
# ============================================================
set -e

echo "=============================================="
echo "  MCP Server for Kylin OS Agent 安装脚本"
echo "  目标平台: 麒麟 V11 + LoongArch"
echo "=============================================="
echo ""

# ---- 检查 root ----
if [ "$(id -u)" -ne 0 ]; then
    echo "[ERROR] 请使用 sudo 运行此脚本: sudo ./deploy/mcp-server/install.sh"
    exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
INSTALL_DIR="/opt/mcp-server"

# ---- 0. 架构检查 ----
if [ -f "$SCRIPT_DIR/loongarch-check.sh" ]; then
    echo "[0/7] 运行架构兼容性检查..."
    bash "$SCRIPT_DIR/loongarch-check.sh" || {
        echo "[WARN] 架构检查有警告，继续安装..."
    }
    echo ""
fi

# ---- 1. 创建目标目录 ----
echo "[1/7] 创建目录 $INSTALL_DIR ..."
mkdir -p "$INSTALL_DIR"

# ---- 2. 复制文件 ----
echo "[2/7] 复制项目文件..."
cp -r "$PROJECT_ROOT/mcp-server"/* "$INSTALL_DIR/"
# 确保 deploy 目录被正确复制
if [ -d "$PROJECT_ROOT/mcp-server/deploy" ]; then
    cp -r "$PROJECT_ROOT/mcp-server/deploy" "$INSTALL_DIR/"
fi
# 显式复制隐藏文件
if [ -f "$PROJECT_ROOT/mcp-server/.env.example" ]; then
    cp "$PROJECT_ROOT/mcp-server/.env.example" "$INSTALL_DIR/"
fi
if [ -f "$PROJECT_ROOT/mcp-server/.env" ]; then
    cp "$PROJECT_ROOT/mcp-server/.env" "$INSTALL_DIR/"
else
    cp "$PROJECT_ROOT/mcp-server/.env.example" "$INSTALL_DIR/.env" 2>/dev/null || true
fi
echo "  文件已复制到 $INSTALL_DIR/"

# ---- 3. 创建虚拟环境并安装依赖 ----
echo "[3/7] 创建 Python 虚拟环境..."
cd "$INSTALL_DIR"
python3 -m venv venv
source venv/bin/activate
echo "[3/7] 安装依赖..."
pip install --quiet --upgrade pip
if [ -f "$INSTALL_DIR/requirements.txt" ]; then
    pip install --quiet -r requirements.txt
else
    pip install --quiet psutil>=5.9.0 python-dotenv>=1.0.0
fi
echo "  依赖安装完成"
deactivate

# ---- 4. 创建专用用户 ----
echo "[4/7] 创建非 root 用户..."
useradd -r -s /bin/false agent-read 2>/dev/null || echo "  agent-read 已存在"
useradd -r -s /bin/false agent-op 2>/dev/null || echo "  agent-op 已存在"

# 修复文件所有权
echo "  修正 $INSTALL_DIR 文件所有权为 agent-read:agent-read ..."
chown -R agent-read:agent-read "$INSTALL_DIR"
echo "  文件权限已修正"

# ---- 5. 安装 sudoers 白名单 ----
echo "[5/7] 安装 sudoers 白名单 (agent-op)..."
if [ -f "$SCRIPT_DIR/agent-op.sudoers" ]; then
    cp "$SCRIPT_DIR/agent-op.sudoers" /etc/sudoers.d/agent-op
    chmod 440 /etc/sudoers.d/agent-op
    chown root:root /etc/sudoers.d/agent-op
    echo "  /etc/sudoers.d/agent-op 已安装"
else
    echo "  [WARN] agent-op.sudoers 未找到，跳过"
fi

# ---- 6. 安装 systemd 服务 ----
echo "[6/7] 安装 systemd 服务..."
if [ -f "$SCRIPT_DIR/mcp-server.service" ]; then
    cp "$SCRIPT_DIR/mcp-server.service" /etc/systemd/system/mcp-server.service
else
    echo "  [ERROR] mcp-server.service 未找到！"
    exit 1
fi
systemctl daemon-reload
systemctl enable mcp-server
echo "  mcp-server.service 已安装并设为开机自启"

# ---- 7. 启动服务 ----
echo "[7/7] 启动 MCP Server..."
systemctl start mcp-server || {
    echo "[WARN] 服务启动失败，请查看日志: sudo journalctl -u mcp-server -n 50"
    exit 1
}

echo ""
echo "=============================================="
echo "  ✓ MCP Server 安装完成！"
echo "=============================================="
echo ""
echo "  ╔══════════════════════════════════════════════╗"
echo "  ║  📋 下一步                                   ║"
echo "  ║                                              ║"
echo "  ║  如需修改配置:                               ║"
echo "  ║    cd $INSTALL_DIR                           ║"
echo "  ║    sudo bash kylin-wizard.sh                 ║"
echo "  ║                                              ║"
echo "  ║  向导可帮助您：                              ║"
echo "  ║    • 修改监听地址并配置防火墙                ║"
echo "  ║    • 生成/修改认证 Token 并测试连接          ║"
echo "  ║    • 查看当前配置与状态                      ║"
echo "  ╚══════════════════════════════════════════════╝"
echo ""
echo "  查看状态:  sudo systemctl status mcp-server"
echo "  查看日志:  sudo journalctl -u mcp-server -f"
echo "  安装路径:  $INSTALL_DIR"
echo ""