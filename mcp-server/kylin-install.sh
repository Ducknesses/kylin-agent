#!/bin/bash
# DEPRECATED: 此脚本已迁移到 deploy/mcp-server/install.sh
# 请使用: sudo ./deploy/mcp-server/install.sh
# 本文件保留仅用于向后兼容，后续版本将移除。
# ============================================================
# MCP Server 一键安装脚本（麒麟 V11 + LoongArch）
# ============================================================
# 用法: sudo ./kylin-install.sh
# 前提: 已在 mcp-server 目录下执行，或所有源文件已复制到目标路径
# ============================================================
set -e

echo "=============================================="
echo "  MCP Server for Kylin OS Agent 安装脚本"
echo "  目标平台: 麒麟 V11 + LoongArch"
echo "=============================================="
echo ""

# ---- 检查是否为 root 或具有 sudo 权限 ----
if [ "$(id -u)" -ne 0 ]; then
    echo "[ERROR] 请使用 sudo 运行此脚本: sudo ./kylin-install.sh"
    exit 1
fi

# ---- 1. 创建目标目录 ----
echo "[1/7] 创建目录 /opt/mcp-server..."
mkdir -p /opt/mcp-server

# ---- 2. 复制文件 ----
echo "[2/7] 复制项目文件..."
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cp -r "$SCRIPT_DIR"/* /opt/mcp-server/
# 确保 deploy 目录和隐藏文件（.env.example 等）被正确复制
if [ -d "$SCRIPT_DIR/deploy" ]; then
    cp -r "$SCRIPT_DIR/deploy" /opt/mcp-server/
fi
# 显式复制隐藏文件（* 通配符不包含以 . 开头的文件）
if [ -f "$SCRIPT_DIR/.env.example" ]; then
    cp "$SCRIPT_DIR/.env.example" /opt/mcp-server/
fi
if [ -f "$SCRIPT_DIR/.env" ]; then
    cp "$SCRIPT_DIR/.env" /opt/mcp-server/
fi
echo "  文件已复制到 /opt/mcp-server/"

# ---- 3. 创建虚拟环境并安装依赖 ----
echo "[3/7] 创建 Python 虚拟环境..."
cd /opt/mcp-server
python3 -m venv venv
source venv/bin/activate
echo "[3/7] 安装依赖 (psutil)..."
pip install --quiet psutil>=5.9.0
echo "  psutil 安装完成"
deactivate

# ---- 4. 创建专用用户 ----
echo "[4/7] 创建非 root 用户..."
useradd -r -s /bin/false agent-read 2>/dev/null || echo "  agent-read 已存在"
useradd -r -s /bin/false agent-op 2>/dev/null || echo "  agent-op 已存在"

# 修复文件所有权：确保 agent-read 可以读取 /opt/mcp-server 下所有文件
echo "  修正 /opt/mcp-server 文件所有权为 agent-read:agent-read ..."
chown -R agent-read:agent-read /opt/mcp-server
echo "  文件权限已修正"

# ---- 5. 安装 sudoers 白名单 ----
echo "[5/7] 安装 sudoers 白名单 (agent-op)..."
cp "$SCRIPT_DIR/deploy/agent-op.sudoers" /etc/sudoers.d/agent-op
chmod 440 /etc/sudoers.d/agent-op
chown root:root /etc/sudoers.d/agent-op
echo "  /etc/sudoers.d/agent-op 已安装"

# ---- 6. 安装 systemd 服务 ----
echo "[6/7] 安装 systemd 服务..."
cp "$SCRIPT_DIR/deploy/mcp-server.service" /etc/systemd/system/mcp-server.service
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
echo "  ║  📋 下一步：运行配置向导完成初始设置        ║"
echo "  ║                                              ║"
echo "  ║    sudo ./kylin-wizard.sh                   ║"
echo "  ║                                              ║"
echo "  ║  向导可帮助您：                              ║"
echo "  ║    • 修改监听地址并配置防火墙                ║"
echo "  ║    • 生成/修改认证 Token 并测试连接          ║"
echo "  ║    • 查看当前配置与状态                      ║"
echo "  ╚══════════════════════════════════════════════╝"
echo ""
echo "  查看状态:  sudo systemctl status mcp-server"
echo "  查看日志:  sudo journalctl -u mcp-server -f"
echo ""
