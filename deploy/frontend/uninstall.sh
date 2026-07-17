#!/bin/bash
# ============================================================
# Kylin Agent Frontend 一键卸载脚本（控制节点）
# ============================================================
# 用法: sudo ./uninstall.sh
# ============================================================
set -e

echo "=============================================="
echo "  Kylin Agent Frontend 卸载脚本"
echo "=============================================="
echo ""

# ---- 检查 root ----
if [ "$(id -u)" -ne 0 ]; then
    echo "[ERROR] 请使用 sudo 运行此脚本: sudo ./deploy/frontend/uninstall.sh"
    exit 1
fi

INSTALL_DIR="/opt/kylin-agent"

# ---- 确认卸载 ----
echo "[CONFIRM] 即将卸载 Kylin Agent Frontend，这将："
echo "  1. 删除 $INSTALL_DIR/frontend/dist 静态文件"
echo "  2. 移除 Nginx 反向代理配置"
echo ""
read -p "确定要继续吗？ (yes/no): " confirm
if [ "$confirm" != "yes" ]; then
    echo "已取消卸载。"
    exit 0
fi
echo ""

# ---- 1. 删除前端静态文件 ----
echo "[1/3] 删除前端静态文件..."
rm -rf "$INSTALL_DIR/frontend/dist"
echo "  $INSTALL_DIR/frontend/dist 已删除"
echo ""

# ---- 2. 移除 Nginx 配置 ----
echo "[2/3] 移除 Nginx 配置..."
# 兼容 apt 系和 yum/dnf 系的 Nginx 配置路径
rm -f /etc/nginx/sites-available/kylin-agent
rm -f /etc/nginx/sites-enabled/kylin-agent
rm -f /etc/nginx/conf.d/kylin-agent.conf
if [ -f /etc/nginx/conf.d/default.conf.bak ]; then
    mv /etc/nginx/conf.d/default.conf.bak /etc/nginx/conf.d/default.conf 2>/dev/null || true
fi
if command -v nginx &>/dev/null; then
    systemctl reload nginx 2>/dev/null || true
    echo "  Nginx 配置已移除并重载"
else
    echo "  Nginx 未安装（跳过）"
fi
echo ""

# ---- 3. 清理空目录 ----
echo "[3/3] 清理 frontend 目录..."
rm -rf "$INSTALL_DIR/frontend"
echo "  $INSTALL_DIR/frontend 已删除"
echo ""

echo "=============================================="
echo "  ✓ Kylin Agent Frontend 卸载完成！"
echo "=============================================="
echo ""
echo "  以下内容已全部移除:"
echo "    - $INSTALL_DIR/frontend/dist 静态文件"
echo "    - Nginx 反向代理配置 (/etc/nginx/sites-available/kylin-agent)"
echo ""
echo "  注意: Nginx 包未被移除，如需清理请手动执行:"
echo "    基于 Debian/Ubuntu: sudo apt purge nginx"
echo "    基于 RHEL/Kylin:   sudo yum remove nginx  或  sudo dnf remove nginx"
echo ""