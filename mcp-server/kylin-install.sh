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
echo "[1/8] 创建目录 /opt/mcp-server..."
mkdir -p /opt/mcp-server

# ---- 2. 复制文件 ----
echo "[2/8] 复制项目文件..."
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cp -r "$SCRIPT_DIR"/* /opt/mcp-server/
# 确保 deploy 目录被正确复制
if [ -d "$SCRIPT_DIR/deploy" ]; then
    cp -r "$SCRIPT_DIR/deploy" /opt/mcp-server/
fi
echo "  文件已复制到 /opt/mcp-server/"

# ---- 3. 创建虚拟环境并安装依赖 ----
echo "[3/8] 创建 Python 虚拟环境..."
cd /opt/mcp-server
python3 -m venv venv
source venv/bin/activate
echo "[3/8] 安装依赖 (psutil)..."
pip install --quiet psutil>=5.9.0
echo "  psutil 安装完成"
deactivate

# ---- 4. 创建专用用户 ----
echo "[4/8] 创建非 root 用户..."
useradd -r -s /bin/false agent-read 2>/dev/null || echo "  agent-read 已存在"
useradd -r -s /bin/false agent-op 2>/dev/null || echo "  agent-op 已存在"

# 修复文件所有权：确保 agent-read 可以读取 /opt/mcp-server 下所有文件
echo "  修正 /opt/mcp-server 文件所有权为 agent-read:agent-read ..."
chown -R agent-read:agent-read /opt/mcp-server
echo "  文件权限已修正"

# ---- 5. 安装 sudoers 白名单 ----
echo "[5/8] 安装 sudoers 白名单 (agent-op)..."
cp "$SCRIPT_DIR/deploy/agent-op.sudoers" /etc/sudoers.d/agent-op
chmod 440 /etc/sudoers.d/agent-op
chown root:root /etc/sudoers.d/agent-op
echo "  /etc/sudoers.d/agent-op 已安装"

# ---- 6. 安装 systemd 服务 ----
echo "[6/8] 安装 systemd 服务..."
cp "$SCRIPT_DIR/deploy/mcp-server.service" /etc/systemd/system/mcp-server.service
systemctl daemon-reload
systemctl enable mcp-server
echo "  mcp-server.service 已安装并设为开机自启"

# ---- 7. 启动服务 ----
echo "[7/8] 启动 MCP Server..."
systemctl start mcp-server || {
    echo "[WARN] 服务启动失败，请查看日志: sudo journalctl -u mcp-server -n 50"
    exit 1
}

# ---- 8. 配置防火墙（允许 8001/tcp） ----
echo "[8/8] 配置防火墙（开放 MCP Server 端口 8001/tcp）..."

MCP_PORT=8001

# 检测防火墙类型并开放端口
# 麒麟 V11 默认使用 firewalld，但兼容 iptables 备选

if command -v firewall-cmd &>/dev/null && systemctl is-active --quiet firewalld 2>/dev/null; then
    # ── firewalld 路径 ──
    echo "  检测到 firewalld，添加永久规则..."

    # 检查规则是否已存在
    if firewall-cmd --permanent --query-port="${MCP_PORT}/tcp" &>/dev/null; then
        echo "  端口 ${MCP_PORT}/tcp 已存在于防火墙规则中，跳过添加"
    else
        firewall-cmd --permanent --add-port="${MCP_PORT}/tcp"
        firewall-cmd --reload
        echo "  ✓ firewalld 已开放 ${MCP_PORT}/tcp（永久规则）"
    fi

elif command -v iptables &>/dev/null; then
    # ── iptables 路径 ──
    echo "  检测到 iptables，添加 INPUT 规则..."

    # 检查规则是否已存在（避免重复）
    if iptables -C INPUT -p tcp --dport "${MCP_PORT}" -j ACCEPT &>/dev/null 2>&1; then
        echo "  端口 ${MCP_PORT}/tcp 已存在于 iptables 规则中，跳过添加"
    else
        iptables -I INPUT -p tcp --dport "${MCP_PORT}" -j ACCEPT
        echo "  ✓ iptables 已添加 ${MCP_PORT}/tcp ACCEPT 规则"
    fi

    # 持久化 iptables 规则
    if command -v iptables-save &>/dev/null; then
        if [ -d /etc/iptables ]; then
            iptables-save > /etc/iptables/rules.v4 2>/dev/null || true
            echo "  已保存 iptables 规则至 /etc/iptables/rules.v4"
        elif command -v netfilter-persistent &>/dev/null; then
            netfilter-persistent save 2>/dev/null || true
            echo "  已通过 netfilter-persistent 保存规则"
        else
            echo "  [WARN] 无法持久化 iptables 规则：未找到 iptables-save 或 netfilter-persistent"
            echo "  建议安装: apt install iptables-persistent"
        fi
    fi

elif command -v ufw &>/dev/null; then
    # ── ufw 路径（Ubuntu 系备选） ──
    echo "  检测到 ufw，添加规则..."
    ufw allow "${MCP_PORT}/tcp" 2>/dev/null || true
    echo "  ✓ ufw 已开放 ${MCP_PORT}/tcp"

else
    # ── 无防火墙工具 ──
    echo "  [WARN] 未检测到 firewalld / iptables / ufw"
    echo "  请手动确保端口 ${MCP_PORT}/tcp 在系统防火墙中被允许"
fi

echo ""
echo "=============================================="
echo "  ✓ MCP Server 安装完成！"
echo "=============================================="
echo ""
echo "  查看状态:  sudo systemctl status mcp-server"
echo "  查看日志:  sudo journalctl -u mcp-server -f"
echo "  测试接口:"
echo "    curl -X POST http://127.0.0.1:8001/jsonrpc \\"
echo "      -H 'Authorization: Bearer 123456789' \\"
echo "      -H 'Content-Type: application/json' \\"
echo "      -d '{\"jsonrpc\":\"2.0\",\"method\":\"ping\",\"id\":1}'"
echo ""
echo "  ⚠  请修改 API_TOKEN 环境变量（当前为默认值 123456789，仅限开发使用）:"
echo "    sudo sed -i 's/API_TOKEN=.*/API_TOKEN=sk-kylin-YOUR_RANDOM_TOKEN/' \\"
echo "        /etc/systemd/system/mcp-server.service"
echo "    sudo systemctl daemon-reload"
echo "    sudo systemctl restart mcp-server"
echo ""