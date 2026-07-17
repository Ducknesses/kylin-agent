#!/bin/bash
# ============================================================
# MCP Server 一键卸载脚本（麒麟 V11 + LoongArch 目标机）
# ============================================================
# 用法: sudo ./uninstall.sh
# ============================================================
set -e

echo "=============================================="
echo "  MCP Server for Kylin OS Agent 卸载脚本"
echo "=============================================="
echo ""

# ---- 检查 root ----
if [ "$(id -u)" -ne 0 ]; then
    echo "[ERROR] 请使用 sudo 运行此脚本: sudo ./deploy/mcp-server/uninstall.sh"
    exit 1
fi

# ---- 读取当前 MCP 端口 ----
read_mcp_port() {
    if [ -f /opt/mcp-server/.env ]; then
        grep -oP '^MCP_PORT=\K.*' /opt/mcp-server/.env 2>/dev/null || echo "8001"
    else
        echo "8001"
    fi
}

MCP_PORT=$(read_mcp_port)

# ---- 确认卸载 ----
echo "[CONFIRM] 即将卸载 MCP Server，这将："
echo "  1. 停止 mcp-server 服务"
echo "  2. 移除 systemd 服务配置"
echo "  3. 删除 /opt/mcp-server 目录"
echo "  4. 清理防火墙规则（${MCP_PORT}/tcp）"
echo "  5. 删除 sudoers 白名单 (/etc/sudoers.d/agent-op)"
echo "  6. 删除 agent-read / agent-op 用户"
echo "  7. 清理日志文件"
echo ""

read -p "确定要继续吗？ (yes/no): " confirm
if [ "$confirm" != "yes" ]; then
    echo "已取消卸载。"
    exit 0
fi
echo ""

# ---- 1. 停止并禁用服务 ----
echo "[1/7] 停止 mcp-server 服务..."
if systemctl is-active --quiet mcp-server 2>/dev/null; then
    systemctl stop mcp-server
    echo "  服务已停止"
else
    echo "  服务未运行（跳过停止）"
fi

if systemctl is-enabled --quiet mcp-server 2>/dev/null; then
    systemctl disable mcp-server
    echo "  开机自启已禁用"
else
    echo "  开机自启未启用（跳过禁用）"
fi

# ---- 2. 移除 systemd 服务文件 ----
echo "[2/7] 移除 systemd 服务文件..."
rm -f /etc/systemd/system/mcp-server.service
systemctl daemon-reload
echo "  mcp-server.service 已移除"

# ---- 3. 清理防火墙规则 ----
echo "[3/7] 清理防火墙规则（端口 ${MCP_PORT}/tcp）..."

if command -v firewall-cmd &>/dev/null && systemctl is-active --quiet firewalld 2>/dev/null; then
    if firewall-cmd --permanent --query-port="${MCP_PORT}/tcp" &>/dev/null 2>&1; then
        firewall-cmd --permanent --remove-port="${MCP_PORT}/tcp"
        firewall-cmd --reload
        echo "  ✓ firewalld 规则已移除"
    else
        echo "  firewalld 中未找到该端口规则（跳过）"
    fi
elif command -v iptables &>/dev/null; then
    if iptables -C INPUT -p tcp --dport "${MCP_PORT}" -j ACCEPT &>/dev/null 2>&1; then
        iptables -D INPUT -p tcp --dport "${MCP_PORT}" -j ACCEPT
        echo "  ✓ iptables 规则已移除"
        if command -v iptables-save &>/dev/null; then
            if [ -d /etc/iptables ]; then
                iptables-save > /etc/iptables/rules.v4 2>/dev/null || true
                echo "  已更新 /etc/iptables/rules.v4"
            elif command -v netfilter-persistent &>/dev/null; then
                netfilter-persistent save 2>/dev/null || true
                echo "  已通过 netfilter-persistent 保存"
            fi
        fi
    else
        echo "  iptables 中未找到该端口规则（跳过）"
    fi
elif command -v ufw &>/dev/null; then
    ufw delete allow "${MCP_PORT}/tcp" 2>/dev/null || true
    echo "  ✓ ufw 规则已移除"
else
    echo "  [INFO] 未检测到防火墙工具，跳过防火墙清理"
fi

# ---- 4. 删除 sudoers 白名单 ----
echo "[4/7] 删除 sudoers 白名单..."
rm -f /etc/sudoers.d/agent-op
echo "  /etc/sudoers.d/agent-op 已删除"

# ---- 5. 删除 /opt/mcp-server ----
echo "[5/7] 删除 /opt/mcp-server 目录..."
rm -rf /opt/mcp-server
echo "  /opt/mcp-server 已删除"

# ---- 6. 删除专用用户 ----
echo "[6/7] 删除专用用户..."
if id agent-read &>/dev/null; then
    userdel -r agent-read 2>/dev/null || true
    echo "  agent-read 用户已删除"
else
    echo "  agent-read 用户不存在（跳过）"
fi
if id agent-op &>/dev/null; then
    userdel -r agent-op 2>/dev/null || true
    echo "  agent-op 用户已删除"
else
    echo "  agent-op 用户不存在（跳过）"
fi

# ---- 7. 清理日志 ----
echo "[7/7] 清理日志文件..."
rm -f /var/log/mcp-server.log
journalctl --rotate 2>/dev/null || true
journalctl --vacuum-time=1s 2>/dev/null || true
echo "  日志已清理"

echo ""
echo "=============================================="
echo "  ✓ MCP Server 卸载完成！"
echo "=============================================="
echo ""
echo "  以下内容已全部移除:"
echo "    - mcp-server systemd 服务"
echo "    - /opt/mcp-server 目录"
echo "    - 防火墙规则（${MCP_PORT}/tcp）"
echo "    - sudoers 白名单 (/etc/sudoers.d/agent-op)"
echo "    - agent-read / agent-op 用户"
echo "    - 相关日志文件"
echo ""