#!/bin/bash
# ============================================================
# Kylin Agent - 部署资源提取脚本
# 将 deploy/ 配置 + 三个组件的源码一并提取，可直接拷贝到
# 麒麟虚拟机按原流程安装。
#
# 用法:
#   ./scripts/extract_deploy.sh [输出目录]
#   默认输出目录: ./kylin-agent-deploy-YYYYMMDD-HHMMSS
#
# 提取内容:
#   - deploy/           安装/卸载/服务/nginx 配置
#   - backend/          Backend 源码（排除 tests/logs/venv 等）
#   - frontend/         前端源码（排除 node_modules/dist 等）
#   - mcp-server/       MCP Server 源码（排除 tests/venv 等）
#   - README.md
#   - DEPLOY_MANIFEST.txt  部署指引清单
#
# 安装流程（在目标机器上）:
#   cd deploy/mcp-server && sudo bash install.sh      # MCP Server
#   cd deploy/backend     && sudo bash install.sh      # Backend
#   cd deploy/frontend    && sudo bash install.sh      # Frontend + Nginx
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
log_error() { echo -e "${RED}[ERROR]${NC} $*"; }
log_step()  { echo -e "${CYAN}[STEP]${NC}  $*"; }

# --- 参数 ---
TIMESTAMP=$(date +%Y%m%d-%H%M%S)
OUTPUT_DIR="${1:-./kylin-agent-deploy-${TIMESTAMP}}"

# --- 安全：防止覆盖非空目录 ---
if [ -d "$OUTPUT_DIR" ] && [ "$(ls -A "$OUTPUT_DIR" 2>/dev/null)" ]; then
    log_warn "输出目录 '$OUTPUT_DIR' 已存在且非空"
    read -rp "是否清空并覆盖？[y/N] " confirm
    case "$confirm" in
        [yY]|[yY][eE][sS])
            rm -rf "$OUTPUT_DIR"
            ;;
        *)
            log_error "已取消"
            exit 1
            ;;
    esac
fi

PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DEPLOY_SRC="${PROJECT_ROOT}/deploy"

log_info "项目根目录: ${PROJECT_ROOT}"
log_info "输出目录:    ${OUTPUT_DIR}"

# ============================================================
# Step 1: 复制 deploy/ 配置（排除敏感/元数据文件）
# ============================================================
log_step "1/6 复制 deploy/ 部署配置..."

mkdir -p "$OUTPUT_DIR/deploy"

rsync -a \
    --exclude='certs/kylin-agent.crt' \
    --exclude='certs/kylin-agent.key' \
    --exclude='*.gitkeep' \
    --exclude='*Zone.Identifier' \
    "${DEPLOY_SRC}/" "${OUTPUT_DIR}/deploy/"

log_info "deploy/ 复制完成 (无需证书文件，目标机器自行生成)"

# ============================================================
# Step 2: 复制 backend/ 源码（排除开发/构建产物）
# ============================================================
log_step "2/6 复制 backend/ 源码..."

BACKEND_SRC="${PROJECT_ROOT}/backend"
BACKEND_DST="${OUTPUT_DIR}/backend"
mkdir -p "$BACKEND_DST"

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
    --exclude='*Zone.Identifier' \
    "${BACKEND_SRC}/" "${BACKEND_DST}/"

# 复制 .env.example，不复制真实 .env
if [ -f "${BACKEND_SRC}/.env.example" ]; then
    cp "${BACKEND_SRC}/.env.example" "${BACKEND_DST}/"
fi

log_info "backend/ 源码复制完成"

# ============================================================
# Step 3: 复制 frontend/ 源码（排除构建产物）
# ============================================================
log_step "3/6 复制 frontend/ 源码..."

FRONTEND_SRC="${PROJECT_ROOT}/frontend"
FRONTEND_DST="${OUTPUT_DIR}/frontend"
mkdir -p "$FRONTEND_DST"

rsync -a \
    --exclude='node_modules/' \
    --exclude='dist/' \
    --exclude='.env' \
    --exclude='.env.local' \
    --exclude='.git/' \
    --exclude='.gitignore' \
    --exclude='*Zone.Identifier' \
    "${FRONTEND_SRC}/" "${FRONTEND_DST}/"

# 复制 .env 环境配置模板（如有）
for envf in .env.production .env.development.local; do
    if [ -f "${FRONTEND_SRC}/${envf}" ]; then
        cp "${FRONTEND_SRC}/${envf}" "${FRONTEND_DST}/"
    fi
done

log_info "frontend/ 源码复制完成"

# ============================================================
# Step 4: 复制 mcp-server/ 源码（排除开发产物）
# ============================================================
log_step "4/6 复制 mcp-server/ 源码..."

MCP_SRC="${PROJECT_ROOT}/mcp-server"
MCP_DST="${OUTPUT_DIR}/mcp-server"
mkdir -p "$MCP_DST"

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
    --exclude='*Zone.Identifier' \
    "${MCP_SRC}/" "${MCP_DST}/"

# 复制 .env.example，不复制真实 .env
if [ -f "${MCP_SRC}/.env.example" ]; then
    cp "${MCP_SRC}/.env.example" "${MCP_DST}/"
fi

log_info "mcp-server/ 源码复制完成"

# ============================================================
# Step 5: 复制 README 并生成部署清单
# ============================================================
log_step "5/6 生成部署清单..."

README="${PROJECT_ROOT}/README.md"
if [ -f "$README" ]; then
    cp "$README" "$OUTPUT_DIR/"
    log_info "README.md 已复制"
fi

cat > "${OUTPUT_DIR}/DEPLOY_MANIFEST.txt" << 'MANIFEST_EOF'
╔══════════════════════════════════════════════════════════════╗
║          Kylin Agent 部署资源清单                            ║
╠══════════════════════════════════════════════════════════════╣
║                                                              ║
║  目录结构与项目源码一致，可直接拷贝到目标机器安装。          ║
║                                                              ║
║  安装步骤（按顺序）:                                         ║
║                                                              ║
║  [1] MCP Server（被管节点）                                  ║
║      cd deploy/mcp-server && sudo bash install.sh            ║
║      卸载: sudo bash uninstall.sh                            ║
║      配置: sudo bash wizard.sh                               ║
║                                                              ║
║  [2] Backend（控制节点）                                     ║
║      cd deploy/backend && sudo bash install.sh               ║
║      卸载: sudo bash uninstall.sh                            ║
║                                                              ║
║  [3] Frontend + Nginx（控制节点）                            ║
║      cd deploy/frontend && sudo bash install.sh              ║
║      卸载: sudo bash uninstall.sh                            ║
║                                                              ║
║  系统要求:                                                    ║
║    - LoongArch / x86_64 架构                                 ║
║    - Python >= 3.10                                          ║
║    - Node.js >= 18（前端构建）                                ║
║    - Nginx（前端部署）                                        ║
║                                                              ║
║  ⚠ 注意: 环境变量文件 (.env) 需手动创建或安装脚本自动生成    ║
║     backend/  提供了 .env.example                            ║
║     mcp-server/ 提供了 .env.example                          ║
║                                                              ║
╚══════════════════════════════════════════════════════════════╝
MANIFEST_EOF

log_info "DEPLOY_MANIFEST.txt 已生成"

# ============================================================
# Step 6: 输出摘要
# ============================================================
log_step "6/6 输出摘要..."

echo ""
echo "============================================"
echo "  提取完成！"
echo "============================================"
echo ""
echo "  输出目录: ${OUTPUT_DIR}"
echo ""

# 按一级目录汇总文件数
echo "  目录结构概要:"
find "$OUTPUT_DIR" -maxdepth 1 -type d | sort | while read -r d; do
    dn=$(basename "$d")
    if [ "$dn" = "$(basename "$OUTPUT_DIR")" ]; then continue; fi
    fc=$(find "$d" -type f 2>/dev/null | wc -l)
    ds=$(du -sh "$d" 2>/dev/null | cut -f1)
    printf "    %-20s  %3s 文件   %s\n" "$dn/" "$fc" "$ds"
done

# 额外文件
EXTRA=$(find "$OUTPUT_DIR" -maxdepth 1 -type f | wc -l)
if [ "$EXTRA" -gt 0 ]; then
    printf "    (根目录文件)        %3s 个\n" "$EXTRA"
fi

TOTAL_FILES=$(find "$OUTPUT_DIR" -type f | wc -l)
TOTAL_SIZE=$(du -sh "$OUTPUT_DIR" 2>/dev/null | cut -f1)

echo ""
echo "  总文件数: ${TOTAL_FILES}"
echo "  总大小:   ${TOTAL_SIZE}"
echo ""
log_info "可直接拷贝此目录到目标机器，然后按 DEPLOY_MANIFEST.txt 操作"
echo ""
echo "  打包命令:"
echo "    tar -czf kylin-agent-deploy-${TIMESTAMP}.tar.gz -C $(dirname "$OUTPUT_DIR") $(basename "$OUTPUT_DIR")"