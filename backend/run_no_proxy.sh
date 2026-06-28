#!/usr/bin/env bash
# ============================================================
# 后端启动脚本（跳过 HTTP 代理）
#
# 宿主机可能配置了 http_proxy/https_proxy 环境变量（如 Clash），
# 这些代理会影响 Python httpx 请求，导致无法连接麒麟虚拟机中
# 的 MCP Server。本脚本启动后端时取消代理环境变量。
#
# 使用: ./run_no_proxy.sh
# ============================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

echo "[run_no_proxy] 正在启动后端服务..."

# 取消 HTTP 代理环境变量，避免影响与虚拟机内 MCP Server 的通信
export http_proxy=""
export https_proxy=""
export HTTP_PROXY=""
export HTTPS_PROXY=""
# 设置 no_proxy 为通配符，双重保险
export no_proxy="*"
export NO_PROXY="*"

# 显示当前代理状态
echo "[run_no_proxy] http_proxy=${http_proxy:-<空>}"
echo "[run_no_proxy] https_proxy=${https_proxy:-<空>}"

# 检查 .env 是否存在
if [ ! -f ".env" ]; then
    echo "[run_no_proxy] 警告: .env 文件不存在，将使用默认配置"
    echo "[run_no_proxy] 提示: 请从 .env.example 复制并修改配置"
fi

# 执行原启动脚本（传递所有参数）
exec python3 run.py "$@"
