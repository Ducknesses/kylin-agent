#!/bin/bash
# ============================================================
# Kylin Agent Backend 一键卸载脚本（控制节点）
# ============================================================
# 用法: sudo ./uninstall.sh
# ============================================================
set -e

echo "=============================================="
echo "  Kylin Agent Backend 卸载脚本"
echo "=============================================="
echo ""

# ---- 检查 root ----
if [ "$(id -u)" -ne 0 ]; then
    echo "[ERROR] 请使用 sudo 运行此脚本: sudo ./deploy/backend/uninstall.sh"
    exit 1
fi

INSTALL_DIR="/opt/kylin-agent"

# ---- 确认卸载 ----
echo "[CONFIRM] 即将卸载 Kylin Agent Backend，这将："
echo "  1. 停止 kylin-agent 服务"
echo "  2. 移除 systemd 服务配置"
echo "  3. 删除 $INSTALL_DIR/backend 目录"
echo "  4. 删除 agent-read 用户"
echo ""
read -p "确定要继续吗？ (yes/no): " confirm
if [ "$confirm" != "yes" ]; then
    echo "已取消卸载。"
    exit 0
fi
echo ""

# ---- 1. 停止并禁用服务 ----
echo "[1/4] 停止 kylin-agent 服务..."
if systemctl is-active --quiet kylin-agent 2>/dev/null; then
    systemctl stop kylin-agent
    echo "  服务已停止"
else
    echo "  服务未运行（跳过停止）"
fi

if systemctl is-enabled --quiet kylin-agent 2>/dev/null; then
    systemctl disable kylin-agent
    echo "  开机自启已禁用"
else
    echo "  开机自启未启用（跳过禁用）"
fi
echo ""

# ---- 2. 移除 systemd 服务文件 ----
echo "[2/4] 移除 systemd 服务文件..."
rm -f /etc/systemd/system/kylin-agent.service
systemctl daemon-reload
echo "  kylin-agent.service 已移除"
echo ""

# ---- 3. 删除后端目录 ----
echo "[3/4] 删除 $INSTALL_DIR/backend 目录..."
rm -rf "$INSTALL_DIR/backend"
echo "  $INSTALL_DIR/backend 已删除"
echo ""

# ---- 4. 删除专用用户 ----
echo "[4/4] 删除 agent-read 用户..."
if id agent-read &>/dev/null; then
    userdel -r agent-read 2>/dev/null || true
    echo "  agent-read 用户已删除"
else
    echo "  agent-read 用户不存在（跳过）"
fi
echo ""

echo "=============================================="
echo "  ✓ Kylin Agent Backend 卸载完成！"
echo "=============================================="
echo ""
echo "  以下内容已全部移除:"
echo "    - kylin-agent systemd 服务"
echo "    - $INSTALL_DIR/backend 目录"
echo "    - agent-read 用户"
echo ""
echo "  提示: Frontend + Nginx 请用 deploy/frontend/uninstall.sh 单独卸载"
echo ""