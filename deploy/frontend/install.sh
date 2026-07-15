#!/bin/bash
# ============================================================
# Kylin Agent Frontend 一键安装脚本（控制节点）
# ============================================================
# 用法: sudo ./install.sh
# 前提: 在项目根目录下执行，且已执行 npm run build
# ============================================================
set -e

echo "=============================================="
echo "  Kylin Agent Frontend 安装脚本"
echo "  目标: 控制节点（x86_64 / ARM / WSL）"
echo "=============================================="
echo ""

# ---- 检查 root ----
if [ "$(id -u)" -ne 0 ]; then
    echo "[ERROR] 请使用 sudo 运行此脚本: sudo ./deploy/frontend/install.sh"
    exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
INSTALL_DIR="/opt/kylin-agent"

# ---- 1. 检查前端构建产物 ----
echo "[1/4] 检查前端构建产物..."
if [ ! -d "$PROJECT_ROOT/frontend/dist" ]; then
    echo "[INFO] 未找到 frontend/dist/，正在执行构建..."
    cd "$PROJECT_ROOT/frontend"
    if ! command -v npm &>/dev/null; then
        echo "[ERROR] npm 未安装！请先安装 Node.js >= 18"
        exit 1
    fi
    npm install --silent
    npm run build
    cd "$PROJECT_ROOT"
    if [ ! -d "$PROJECT_ROOT/frontend/dist" ]; then
        echo "[ERROR] 前端构建失败，frontend/dist/ 不存在"
        exit 1
    fi
fi
echo "  ✓ 前端构建产物就绪"
echo ""

# ---- 2. 复制静态文件 ----
echo "[2/4] 部署前端静态文件到 $INSTALL_DIR/frontend/dist ..."
mkdir -p "$INSTALL_DIR/frontend/dist"
cp -r "$PROJECT_ROOT/frontend/dist"/* "$INSTALL_DIR/frontend/dist/"
echo "  ✓ 静态文件已复制"
echo ""

# ---- 3. 检测包管理器并安装 Nginx ----
echo "[3/4] 安装 Nginx 反向代理配置..."

# 检测包管理器类型
if command -v apt-get &>/dev/null; then
    PKG_MGR="apt"
    NGINX_CONF_DIR="/etc/nginx/sites-available"
    NGINX_ENABLED_DIR="/etc/nginx/sites-enabled"
    NGINX_DEFAULT_CONF="/etc/nginx/sites-enabled/default"
elif command -v dnf &>/dev/null; then
    PKG_MGR="dnf"
    NGINX_CONF_DIR="/etc/nginx/conf.d"
    NGINX_ENABLED_DIR=""
    NGINX_DEFAULT_CONF="/etc/nginx/conf.d/default.conf"
elif command -v yum &>/dev/null; then
    PKG_MGR="yum"
    NGINX_CONF_DIR="/etc/nginx/conf.d"
    NGINX_ENABLED_DIR=""
    NGINX_DEFAULT_CONF="/etc/nginx/conf.d/default.conf"
else
    echo "[ERROR] 无法检测到支持的包管理器 (apt-get/dnf/yum)"
    exit 1
fi
echo "[INFO] 检测到包管理器: $PKG_MGR"

if ! command -v nginx &>/dev/null; then
    echo "[INFO] Nginx 未安装，正在安装..."
    case "$PKG_MGR" in
        apt)
            apt-get update -qq && apt-get install -y -qq nginx
            ;;
        dnf)
            dnf install -y -q nginx
            ;;
        yum)
            yum install -y -q nginx
            ;;
    esac
fi

# 部署 Nginx 配置
if [ "$PKG_MGR" = "apt" ]; then
    # Debian/Ubuntu 系: sites-available + sites-enabled 模式
    cp "$SCRIPT_DIR/nginx-kylin-agent.conf" "$NGINX_CONF_DIR/kylin-agent"
    ln -sf "$NGINX_CONF_DIR/kylin-agent" "$NGINX_ENABLED_DIR/kylin-agent"
    rm -f "$NGINX_DEFAULT_CONF"
else
    # RHEL/CentOS/Kylin (yum/dnf) 系: conf.d 目录模式
    cp "$SCRIPT_DIR/nginx-kylin-agent.conf" "$NGINX_CONF_DIR/kylin-agent.conf"
    if [ -f "$NGINX_DEFAULT_CONF" ]; then
        mv "$NGINX_DEFAULT_CONF" "${NGINX_DEFAULT_CONF}.bak" 2>/dev/null || rm -f "$NGINX_DEFAULT_CONF"
    fi
fi

if nginx -t 2>&1; then
    # 重新加载或启动 Nginx
    if systemctl is-active --quiet nginx 2>/dev/null; then
        systemctl reload nginx 2>/dev/null || true
    else
        systemctl start nginx 2>/dev/null || true
    fi
    systemctl enable nginx 2>/dev/null || true
    echo "  ✓ Nginx 配置已安装并已启动（已设置开机自启）"
else
    if [ "$PKG_MGR" = "apt" ]; then
        echo "  [ERROR] Nginx 配置测试失败，请检查 $NGINX_CONF_DIR/kylin-agent"
    else
        echo "  [ERROR] Nginx 配置测试失败，请检查 $NGINX_CONF_DIR/kylin-agent.conf"
    fi
    exit 1
fi
echo ""

# ---- 4. 修正文件权限与 SELinux 上下文 ----
echo "[4/4] 修正文件权限与 SELinux 上下文..."

# 确保 nginx 用户可读所有文件
chown -R agent-read:agent-read "$INSTALL_DIR/frontend" 2>/dev/null || true
# 目录需要执行权限，文件需要读权限
chmod -R 755 "$INSTALL_DIR/frontend/dist"

# 如果 SELinux 开启，设置 httpd 可读的文件上下文（解决 403）
if command -v selinuxenabled &>/dev/null && selinuxenabled; then
    echo "[INFO] 检测到 SELinux Enforcing，设置文件安全上下文..."
    # 确保 semanage 工具已安装
    if ! command -v semanage &>/dev/null; then
        case "$PKG_MGR" in
            dnf) dnf install -y -q policycoreutils-python-utils ;;
            yum) yum install -y -q policycoreutils-python-utils ;;
            apt) apt-get install -y -qq policycoreutils-python-utils ;;
        esac
    fi
    semanage fcontext -a -t httpd_sys_content_t "$INSTALL_DIR/frontend/dist(/.*)?" 2>/dev/null || true
    restorecon -R "$INSTALL_DIR/frontend/dist" 2>/dev/null || true
    # 也允许 Nginx 反向代理连接后端
    if ! getsebool httpd_can_network_connect 2>/dev/null | grep -q on; then
        setsebool -P httpd_can_network_connect on 2>/dev/null || true
    fi
    echo "  ✓ SELinux 上下文已设置"
else
    echo "  ✓ 权限已修正"
fi

# 将 nginx 用户加入 agent-read 组（确保可读）
if getent group agent-read &>/dev/null; then
    usermod -a -G agent-read nginx 2>/dev/null || true
fi

echo ""

echo "=============================================="
echo "  ✓ Kylin Agent Frontend 安装完成！"
echo "=============================================="
echo ""

# 获取本机所有非回环 IPv4 地址
SERVER_IPS=$(ip -4 addr show scope global 2>/dev/null | grep -oP '(?<=inet\s)\d+(\.\d+){3}' || true)
if [ -z "$SERVER_IPS" ]; then
    # 如果 ip 命令不可用，尝试 hostname -I
    SERVER_IPS=$(hostname -I 2>/dev/null | tr ' ' '\n' | grep -v '^$' || true)
fi

echo "  前端访问地址:"
if [ -n "$SERVER_IPS" ]; then
    echo "$SERVER_IPS" | while read -r ip; do
        [ -n "$ip" ] && echo "    http://$ip"
    done
else
    echo "    http://<服务器IP>（请手动执行 ip addr 查看本机IP）"
fi
echo ""
echo "  静态文件路径: $INSTALL_DIR/frontend/dist"
if [ "$PKG_MGR" = "apt" ]; then
    echo "  Nginx 配置:   /etc/nginx/sites-available/kylin-agent"
else
    echo "  Nginx 配置:   $NGINX_CONF_DIR/kylin-agent.conf"
fi
echo ""
echo "  注意: 请确保 Backend 已安装并运行在 127.0.0.1:8000"
echo "        否则 Nginx 反向代理无法正常工作"
echo ""
