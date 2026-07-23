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

# 创建虚拟环境并配置 pip
cd "$INSTALL_DIR"
python3 -m venv venv
source venv/bin/activate

# 虚拟环境内也配置 pip.conf（双保险）
mkdir -p "$VIRTUAL_ENV/pip.conf.d" 2>/dev/null || true
cat > "$VIRTUAL_ENV/pip.conf" << PIPCONF
[global]
index-url = ${PIP_INDEX}
timeout = 120
retries = 3
PIPCONF

echo "[3/7] 安装依赖..."
pip install --upgrade pip || {
    echo "[ERROR] pip 升级失败，请检查网络连接"
    echo "  当前 pip 源: $PIP_INDEX"
    echo "  提示: 可设置 PIP_INDEX_URL 使用国内镜像，例如："
    echo "    PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple sudo -E ./deploy/mcp-server/install.sh"
    deactivate
    exit 1
}
if [ -f "$INSTALL_DIR/requirements.txt" ]; then
    pip install -r requirements.txt
else
    pip install psutil>=5.9.0 python-dotenv>=1.0.0
fi
echo "  依赖安装完成"

# 验证关键依赖
python3 -c "import psutil; print(f'  psutil={psutil.__version__}')" || {
    echo "[ERROR] psutil 模块无法导入"
    deactivate
    exit 1
}
echo "  ✓ psutil 导入验证通过"
deactivate

# ---- 4. 创建专用用户 ----
echo "[4/7] 创建非 root 用户..."

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

for user in agent-read agent-op; do
    if id "$user" &>/dev/null; then
        echo "  用户 $user 已存在，跳过创建"
    else
        groupadd -f -r "$user"
        useradd -r -s "$NOLOGIN" -g "$user" "$user"
        echo "  用户 $user 已创建"
    fi
done

# 允许 agent-read 读取 systemd journal（journalctl 需要 systemd-journal/adm/wheel 组权限）
if getent group systemd-journal >/dev/null 2>&1; then
    usermod -a -G systemd-journal agent-read
    echo "  agent-read 已加入 systemd-journal 组"
else
    echo "  [WARN] systemd-journal 组不存在，跳过加入；如使用 adm 组，请手动加入"
fi

# 修复文件所有权（放在用户创建后面）
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
echo "  ║    vim .env                                  ║"
echo "  ║                                              ║"
echo "  ║  配置项目说明:                               ║"
echo "  ║    • MCP_SERVER_HOST — 监听地址              ║"
echo "  ║    • MCP_AUTH_TOKEN — 认证 Token             ║"
echo "  ║    • CGROUP_ENABLED — 资源隔离开关           ║"
echo "  ║                                              ║"
echo "  ║  cgroup 资源隔离 (可选，推荐开启):           ║"
echo "  ║    编辑 $INSTALL_DIR/.env                    ║"
echo "  ║    设置 CGROUP_ENABLED=true                   ║"
echo "  ║    可调整 CPU/内存/PID 限制                   ║"
echo "  ║    需确保 cgroups v2 已挂载:                 ║"
echo "  ║      mount | grep cgroup2                     ║"
echo "  ║    未挂载请执行:                              ║"
echo "  ║      sudo mkdir -p /sys/fs/cgroup             ║"
echo "  ║      sudo mount -t cgroup2 none /sys/fs/cgroup ║"
echo "  ╚══════════════════════════════════════════════╝"
echo ""
echo "  查看状态:  sudo systemctl status mcp-server"
echo "  查看日志:  sudo journalctl -u mcp-server -f"
echo "  安装路径:  $INSTALL_DIR"
echo ""