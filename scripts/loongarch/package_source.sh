#!/bin/bash
# ============================================================
# Kylin Agent - LoongArch 源码打包脚本（用于跨架构分发）
# ============================================================
# 用途: 在 x86_64 开发机上打包源码 + 构建脚本，
#      传输到龙芯机器后执行 build.sh 进行原生编译
#
# 用法: ./scripts/loongarch/package_source.sh [输出文件名]
# ============================================================
set -euo pipefail

# --- 颜色输出 ---
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

log_info()  { echo -e "${GREEN}[INFO]${NC}  $*"; }
log_warn()  { echo -e "${YELLOW}[WARN]${NC}  $*"; }
log_step()  { echo -e "\n${CYAN}===[ $* ]===${NC}"; }

PROJECT_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
TIMESTAMP=$(date +%Y%m%d-%H%M%S)
PKG_DIR="${PROJECT_ROOT}/kylin-agent-loongarch-pkg"
ARCHIVE_NAME="${1:-kylin-agent-loongarch-src-${TIMESTAMP}.tar.gz}"

log_info "项目根目录: ${PROJECT_ROOT}"
log_info "输出目录:    ${PKG_DIR}"

# 清理并重建打包目录
rm -rf "$PKG_DIR"
mkdir -p "$PKG_DIR"

# ============================================================
# 1. 复制构建脚本
# ============================================================
log_step "1/6 复制构建脚本"
mkdir -p "${PKG_DIR}/scripts/loongarch"
cp "${PROJECT_ROOT}/scripts/loongarch/build.sh" "${PKG_DIR}/scripts/loongarch/"
chmod +x "${PKG_DIR}/scripts/loongarch/build.sh"
log_info "✓ build.sh 已复制"

# ============================================================
# 2. 复制 backend/ 源码
# ============================================================
log_step "2/5 复制 backend/ 源码"
mkdir -p "${PKG_DIR}/backend"

# 使用 rsync 排除开发文件
rsync -a \
    --exclude='.env' \
    --exclude='.venv/' \
    --exclude='venv/' \
    --exclude='__pycache__/' \
    --exclude='*.pyc' \
    --exclude='*.pyo' \
    --exclude='*.egg-info/' \
    --exclude='.pytest_cache/' \
    --exclude='tests/' \
    --exclude='logs/' \
    --exclude='data/' \
    --exclude='.git/' \
    --exclude='.gitignore' \
    "${PROJECT_ROOT}/backend/" "${PKG_DIR}/backend/"

# 复制 .env.example
if [ -f "${PROJECT_ROOT}/backend/.env.example" ]; then
    cp "${PROJECT_ROOT}/backend/.env.example" "${PKG_DIR}/backend/"
fi

BACKEND_COUNT=$(find "${PKG_DIR}/backend" -type f -name '*.py' | wc -l)
log_info "✓ backend/ 源码复制完成 (${BACKEND_COUNT} .py 文件)"

# ============================================================
# 3. 复制 mcp-server/ 源码
# ============================================================
log_step "3/5 复制 mcp-server/ 源码"
mkdir -p "${PKG_DIR}/mcp-server"

rsync -a \
    --exclude='.env' \
    --exclude='.venv/' \
    --exclude='venv/' \
    --exclude='__pycache__/' \
    --exclude='*.pyc' \
    --exclude='*.pyo' \
    --exclude='*.egg-info/' \
    --exclude='.pytest_cache/' \
    --exclude='tests/' \
    --exclude='.git/' \
    --exclude='.gitignore' \
    "${PROJECT_ROOT}/mcp-server/" "${PKG_DIR}/mcp-server/"

if [ -f "${PROJECT_ROOT}/mcp-server/.env.example" ]; then
    cp "${PROJECT_ROOT}/mcp-server/.env.example" "${PKG_DIR}/mcp-server/"
fi

MCP_COUNT=$(find "${PKG_DIR}/mcp-server" -type f -name '*.py' | wc -l)
log_info "✓ mcp-server/ 源码复制完成 (${MCP_COUNT} .py 文件)"

# ============================================================
# 4. 复制 frontend/ 源码
# ============================================================
log_step "4/5 复制 frontend/ 源码"
mkdir -p "${PKG_DIR}/frontend"

rsync -a \
    --exclude='node_modules/' \
    --exclude='dist/' \
    --exclude='.env' \
    --exclude='.env.local' \
    --exclude='.git/' \
    --exclude='.gitignore' \
    "${PROJECT_ROOT}/frontend/" "${PKG_DIR}/frontend/"

# 复制环境配置
for envf in .env.production .env.development.local; do
    if [ -f "${PROJECT_ROOT}/frontend/${envf}" ]; then
        cp "${PROJECT_ROOT}/frontend/${envf}" "${PKG_DIR}/frontend/"
    fi
done

log_info "✓ frontend/ 源码复制完成"

# ============================================================
# 5. 生成 README 和构建说明
# ============================================================
log_step "5/6 生成构建说明"

cat > "${PKG_DIR}/BUILD_LOONGARCH.txt" << 'EOF'
╔══════════════════════════════════════════════════════════════╗
║    Kylin Agent - LoongArch 源码构建指南                       ║
╠══════════════════════════════════════════════════════════════╣
║                                                              ║
║  本包包含 Kylin Agent 全部源码 + 一键编译脚本。              ║
║                                                              ║
║  目标平台: 麒麟服务器版 V11 (Swan25) + LoongArch             ║
║                                                              ║
║  前置条件:                                                    ║
║    - Python >= 3.10 (sudo apt install python3 python3-venv)  ║
║    - pip3 (sudo apt install python3-pip)                     ║
║    - gcc, make (sudo apt install gcc make)                   ║
║    - Node.js >= 18 (前端构建需要，可选)                      ║
║      麒麟 V11 安装 Node.js:                                  ║
║        方法1: nvm 安装                                       ║
║          curl -o- https://raw.githubusercontent.com/         ║
║            nvm-sh/nvm/v0.39.0/install.sh | bash              ║
║          nvm install 18                                     ║
║        方法2: 二进制安装                                     ║
║          从 nodejs.org 下载 Linux LoongArch 版本             ║
║                                                              ║
║  一键编译 (推荐):                                             ║
║    sudo bash scripts/loongarch/build.sh                      ║
║                                                              ║
║  分步编译:                                                    ║
║    # 只编译后端 (PyInstaller)                                 ║
║    sudo bash scripts/loongarch/build.sh --backend-only       ║
║                                                              ║
║    # 只编译 MCP Server                                       ║
║    sudo bash scripts/loongarch/build.sh --mcp-only           ║
║                                                              ║
║    # 只构建前端                                              ║
║    sudo bash scripts/loongarch/build.sh --frontend-only      ║
║                                                              ║
║    # 使用 Nuitka 编译 (性能更好，但编译更慢)                  ║
║    sudo bash scripts/loongarch/build.sh --use-nuitka         ║
║                                                              ║
║    # 清理构建产物                                            ║
║    sudo bash scripts/loongarch/build.sh --clean              ║
║                                                              ║
║  输出目录: dist/loongarch/                                    ║
║    - kylin-agent-backend    Backend 可执行文件               ║
║    - kylin-mcp-server       MCP Server 可执行文件            ║
║    - frontend/              前端静态文件                    ║
║    - INSTALL_LOONGARCH.txt  部署说明                        ║
║                                                              ║
║  打包分发:                                                    ║
║    编译完成后会自动生成 .tar.gz 压缩包                       ║
║    或手动:                                                    ║
║    cd dist && tar -czf kylin-agent-loongarch.tar.gz loongarch/║
║                                                              ║
║  故障排查:                                                    ║
║    - PyInstaller 打包失败: 检查 Python 版本 >= 3.10          ║
║    - 模块找不到: 运行 build.sh 输出最后20行查看错误           ║
║    - 前端构建失败: 检查 Node.js >= 18                        ║
║    - 内存不足: PyInstaller 需要较大内存，建议 >= 4GB         ║
║                                                              ║
║  传输到目标机器:                                              ║
║    scp kylin-agent-loongarch-src-*.tar.gz root@target:/tmp/  ║
║    ssh root@target                                           ║
║    cd /tmp && tar -xzf kylin-agent-loongarch-src-*.tar.gz    ║
║    cd kylin-agent-loongarch-pkg-*                            ║
║    sudo bash scripts/loongarch/build.sh                      ║
║                                                              ║
╚══════════════════════════════════════════════════════════════╝
EOF

log_info "✓ BUILD_LOONGARCH.txt 已生成"

# ============================================================
# 6. 生成 tar.gz 压缩包
# ============================================================
log_step "6/6 生成分发包"

cd "$PROJECT_ROOT"
tar -czf "$ARCHIVE_NAME" "$(basename "$PKG_DIR")" 2>/dev/null

if [ -f "$ARCHIVE_NAME" ]; then
    SIZE=$(du -h "$ARCHIVE_NAME" | cut -f1)
    log_info "✓ 分发包: ${PROJECT_ROOT}/${ARCHIVE_NAME} (${SIZE})"
else
    log_warn "tar 生成失败，可手动打包: tar -czf kylin-agent-loongarch-pkg.tar.gz kylin-agent-loongarch-pkg/"
fi

# ============================================================
# 摘要
# ============================================================
echo ""
echo "=============================================="
echo "  源码打包完成"
echo "=============================================="
echo ""
echo "  打包目录: ${PKG_DIR}/"
echo "  ──────────────────────────────"
echo "  ├── scripts/loongarch/build.sh    一键编译脚本"
echo "  ├── backend/                      后端源码"
echo "  ├── mcp-server/                   MCP Server 源码"
echo "  ├── frontend/                     前端源码"
echo "  └── BUILD_LOONGARCH.txt           构建指南"
echo ""
if [ -f "${PROJECT_ROOT}/${ARCHIVE_NAME}" ]; then
echo "  压缩包:   ${PROJECT_ROOT}/${ARCHIVE_NAME}"
echo ""
fi
echo "  传输到龙芯机器后执行:"
echo "    cd kylin-agent-loongarch-pkg"
echo "    sudo bash scripts/loongarch/build.sh"
echo ""
echo "  或使用压缩包传输:"
echo "    scp ${ARCHIVE_NAME} root@target:/tmp/"
echo ""
