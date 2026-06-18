#!/bin/bash
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
# 确保 deploy 目录被正确复制
if [ -d "$SCRIPT_DIR/deploy" ]; then
    cp -r "$SCRIPT_DIR/deploy" /opt/mcp-server/
fi
echo "  文件已复制到 /opt/mcp-server/"

# 确保 agent-read 用户有权限读取所有文件
chown -R agent-read:agent-read /opt/mcp-server

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
echo "  查看状态:  sudo systemctl status mcp-server"
echo "  查看日志:  sudo journalctl -u mcp-server -f"
echo "  测试接口:"
echo "    curl -X POST http://127.0.0.1:8001/jsonrpc \\"
echo "      -H 'Authorization: Bearer change-me-in-production' \\"
echo "      -H 'Content-Type: application/json' \\"
echo "      -d '{\"jsonrpc\":\"2.0\",\"method\":\"ping\",\"id\":1}'"
echo ""
echo "  ⚠  请修改 API_TOKEN 环境变量（当前为默认值）:"
echo "    sudo sed -i 's/API_TOKEN=.*/API_TOKEN=sk-kylin-YOUR_RANDOM_TOKEN/' \\"
echo "        /etc/systemd/system/mcp-server.service"
echo "    sudo systemctl daemon-reload"
echo "    sudo systemctl restart mcp-server"
echo ""