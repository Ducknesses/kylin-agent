#!/bin/bash
# ============================================================
# Kylin Agent - LoongArch 一键编译打包脚本
# ============================================================
# 目标平台: 麒麟服务器版 V11 (Swan25) + LoongArch
# 用法:     sudo bash scripts/loongarch/build.sh [选项]
#
# 选项:
#   --backend-only    只构建 Backend
#   --mcp-only        只构建 MCP Server
#   --frontend-only   只构建 Frontend
#   --use-nuitka      优先使用 Nuitka (需要 GCC + python3-dev)
#   --use-pyinstaller 使用 PyInstaller (默认)
#   --clean           清理所有构建产物
#   --help            显示帮助
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
log_step()  { echo -e "\n${CYAN}===[ $* ]===${NC}"; }

# --- 默认配置 ---
PROJECT_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
BUILD_DIR="${PROJECT_ROOT}/build/loongarch"
OUTPUT_DIR="${PROJECT_ROOT}/dist/loongarch"
PACKAGER="pyinstaller"   # pyinstaller | nuitka
BUILD_BACKEND=true
BUILD_MCP=true
BUILD_FRONTEND=true
DO_CLEAN=false

# --- 解析参数 ---
while [[ $# -gt 0 ]]; do
    case "$1" in
        --backend-only)   BUILD_MCP=false; BUILD_FRONTEND=false; shift ;;
        --mcp-only)       BUILD_BACKEND=false; BUILD_FRONTEND=false; shift ;;
        --frontend-only)  BUILD_BACKEND=false; BUILD_MCP=false; shift ;;
        --use-nuitka)     PACKAGER="nuitka"; shift ;;
        --use-pyinstaller) PACKAGER="pyinstaller"; shift ;;
        --clean)          DO_CLEAN=true; shift ;;
        --help)
            head -20 "$0" | grep '^#' | sed 's/^# \{0,1\}//'
            exit 0
            ;;
        *) log_error "未知选项: $1"; exit 1 ;;
    esac
done

echo "=============================================="
echo "  Kylin Agent - LoongArch 编译打包"
echo "  目标平台: 麒麟 V11 (Swan25) + LoongArch"
echo "  打包工具: ${PACKAGER}"
echo "=============================================="

# ============================================================
# 环境检查
# ============================================================
check_environment() {
    log_step "环境检查"

    # 1. 检查是否为 root（安装系统包需要）
    if [ "$(id -u)" -ne 0 ]; then
        log_error "请使用 sudo 运行: sudo bash scripts/loongarch/build.sh"
        exit 1
    fi

    # 2. 检查 CPU 架构
    ARCH=$(uname -m)
    log_info "CPU 架构: $ARCH"
    case "$ARCH" in
        loongarch64|loongarch)
            log_info "✓ 检测到 LoongArch 原生架构"
            ;;
        *)
            log_warn "当前架构为 $ARCH，非 LoongArch。交叉编译仅限 Frontend。"
            log_warn "Python 二进制打包 (PyInstaller/Nuitka) 需要在 LoongArch 机器上执行。"
            if [ "$BUILD_BACKEND" = true ] || [ "$BUILD_MCP" = true ]; then
                log_warn "将跳过 Python 二进制打包，仅生成 Frontend 和脚本。"
                BUILD_BACKEND=false
                BUILD_MCP=false
            fi
            ;;
    esac

    # 3. 检查 Python 版本
    if ! command -v python3 &>/dev/null; then
        log_error "python3 未安装！请执行: sudo apt install python3 python3-venv python3-pip python3-dev"
        exit 1
    fi
    PY_VERSION=$(python3 --version 2>&1)
    log_info "Python: $PY_VERSION"

    PY_MAJOR=$(python3 -c 'import sys; print(sys.version_info.major)')
    PY_MINOR=$(python3 -c 'import sys; print(sys.version_info.minor)')
    if [ "$PY_MAJOR" -lt 3 ] || { [ "$PY_MAJOR" -eq 3 ] && [ "$PY_MINOR" -lt 10 ]; }; then
        log_error "Python 版本需 >= 3.10，当前: ${PY_MAJOR}.${PY_MINOR}"
        exit 1
    fi

    # 4. 检查 Node.js (仅 Frontend 需要)
    if [ "$BUILD_FRONTEND" = true ]; then
        if ! command -v node &>/dev/null; then
            log_warn "Node.js 未安装，将跳过 Frontend 构建"
            log_warn "安装方法: sudo apt install nodejs npm  或使用 nvm"
            BUILD_FRONTEND=false
        else
            NODE_VERSION=$(node --version 2>&1)
            log_info "Node.js: $NODE_VERSION"
        fi
    fi
}

# ============================================================
# 安装系统依赖
# ============================================================
install_system_deps() {
    log_step "安装系统编译依赖"

    # 麒麟 V11 使用 apt (Debian-based)
    local DEPS="gcc g++ make python3-dev python3-venv python3-pip"
    local optional_deps="libpq-dev libffi-dev libssl-dev"

    log_info "安装必要依赖: $DEPS"
    apt-get update -qq
    apt-get install -y -qq $DEPS 2>&1 | tail -3

    # 尝试安装可选依赖
    for dep in $optional_deps; do
        if ! dpkg -l "$dep" &>/dev/null; then
            apt-get install -y -qq "$dep" 2>/dev/null || log_warn "可选依赖 $dep 安装失败（可忽略）"
        fi
    done

    log_info "✓ 系统依赖安装完成"
}

# ============================================================
# 安装 Python 打包工具
# ============================================================
install_packager() {
    log_step "安装 Python 打包工具: ${PACKAGER}"

    case "$PACKAGER" in
        pyinstaller)
            pip3 install --upgrade pyinstaller 2>&1 | tail -2
            ;;
        nuitka)
            # Nuitka 需要 C 编译器
            pip3 install --upgrade nuitka 2>&1 | tail -2
            # 也安装 PyInstaller 作为备选
            pip3 install --upgrade pyinstaller 2>&1 | tail -2
            ;;
    esac

    log_info "✓ 打包工具安装完成"
}

# ============================================================
# 准备虚拟环境和依赖
# ============================================================
prepare_venv() {
    local COMPONENT="$1"   # backend | mcp-server
    local VENV_DIR="${BUILD_DIR}/venv_${COMPONENT}"

    log_info "准备 ${COMPONENT} 虚拟环境..."

    # 清理旧虚拟环境
    rm -rf "$VENV_DIR"
    python3 -m venv "$VENV_DIR"
    source "${VENV_DIR}/bin/activate"

    # 升级 pip
    pip install --upgrade pip 2>&1 | tail -1

    # 安装组件依赖
    local REQ_FILE="${PROJECT_ROOT}/${COMPONENT}/requirements.txt"
    if [ -f "$REQ_FILE" ]; then
        log_info "安装 ${COMPONENT} 依赖..."
        pip install -r "$REQ_FILE" 2>&1 | grep -E "(Successfully|ERROR|error)" || true
    fi

    # 额外安装打包工具到虚拟环境
    pip install pyinstaller 2>&1 | tail -1

    deactivate
    log_info "✓ ${COMPONENT} 虚拟环境准备完成"
}

# ============================================================
# 用 PyInstaller 打包 Python 组件
# ============================================================
build_with_pyinstaller() {
    local COMPONENT="$1"      # backend | mcp-server
    local ENTRY_POINT="$2"    # run.py | server.py
    local OUTPUT_NAME="$3"    # kylin-agent-backend | kylin-mcp-server
    local VENV_DIR="${BUILD_DIR}/venv_${COMPONENT}"
    local SRC_DIR="${PROJECT_ROOT}/${COMPONENT}"

    log_step "PyInstaller 打包: ${COMPONENT} → ${OUTPUT_NAME}"

    source "${VENV_DIR}/bin/activate"

    # 进入源码目录（确保 import 路径正确）
    cd "$SRC_DIR"

    # 构建 PyInstaller 命令
    # --onefile: 打包成单个文件
    # --name: 输出文件名
    # --distpath: 二进制输出目录
    # --workpath: 临时编译目录
    # --specpath: .spec 文件目录
    # --hidden-import: 强制导入隐式依赖模块
    # --add-data: 包含数据文件 (.env, 配置等)
    # --collect-all: 收集包的所有子模块

    local COLLECT_MODULES=()
    local HIDDEN_IMPORTS=()

    case "$COMPONENT" in
        backend)
            COLLECT_MODULES=("fastapi" "uvicorn" "websockets" "pydantic" "redis" "sqlalchemy" "httpx")
            HIDDEN_IMPORTS=(
                "uvicorn.loops.auto"
                "uvicorn.loops.asyncio"
                "uvicorn.protocols.http.auto"
                "uvicorn.protocols.http.h11_impl"
                "uvicorn.protocols.http.httptools_impl"
                "uvicorn.protocols.websockets.auto"
                "uvicorn.protocols.websockets.wsproto_impl"
                "uvicorn.protocols.websockets.websockets_impl"
                "aiosqlite"
                "fakeredis"
                "httptools"
                "dotenv"
                "asyncpg"
            )
            ;;
        mcp-server)
            COLLECT_MODULES=("psutil")
            HIDDEN_IMPORTS=(
                "psutil"
                "dotenv"
            )
            ;;
    esac

    # 组装 --collect-all 参数
    local COLLECT_ARGS=""
    for mod in "${COLLECT_MODULES[@]}"; do
        COLLECT_ARGS="$COLLECT_ARGS --collect-all $mod"
    done

    # 组装 --hidden-import 参数
    local HIDDEN_ARGS=""
    for mod in "${HIDDEN_IMPORTS[@]}"; do
        HIDDEN_ARGS="$HIDDEN_ARGS --hidden-import=$mod"
    done

    # 数据文件：只包含 .env.example（真实 .env 不应打包）
    local DATA_ARGS=""
    if [ -f "${SRC_DIR}/.env.example" ]; then
        DATA_ARGS="--add-data=.env.example:."
    fi

    # 对于 backend，还需要包含 app/ 子目录的 Python 模块
    # PyInstaller 通过入口脚本分析导入链，大部分模块会自动发现
    # 但如果使用了动态 import (importlib)，需要手动添加

    # 子目录作为隐式包添加（确保 app 下所有模块都被收集）
    if [ "$COMPONENT" = "backend" ]; then
        HIDDEN_ARGS="$HIDDEN_ARGS --hidden-import=app"
        HIDDEN_ARGS="$HIDDEN_ARGS --hidden-import=app.main"
        HIDDEN_ARGS="$HIDDEN_ARGS --hidden-import=app.dependencies"
        HIDDEN_ARGS="$HIDDEN_ARGS --hidden-import=config"

        # 收集所有 app.api 模块
        for pyfile in "$SRC_DIR"/app/api/*.py; do
            if [ -f "$pyfile" ] && [ "$(basename "$pyfile")" != "__init__.py" ]; then
                modname="app.api.$(basename "$pyfile" .py)"
                HIDDEN_ARGS="$HIDDEN_ARGS --hidden-import=$modname"
            fi
        done

        # 收集所有 app.services 模块
        for pyfile in "$SRC_DIR"/app/services/*.py; do
            if [ -f "$pyfile" ] && [ "$(basename "$pyfile")" != "__init__.py" ]; then
                modname="app.services.$(basename "$pyfile" .py)"
                HIDDEN_ARGS="$HIDDEN_ARGS --hidden-import=$modname"
            fi
        done

        # 收集所有 app.core 模块
        for pyfile in "$SRC_DIR"/app/core/*.py; do
            if [ -f "$pyfile" ] && [ "$(basename "$pyfile")" != "__init__.py" ]; then
                modname="app.core.$(basename "$pyfile" .py)"
                HIDDEN_ARGS="$HIDDEN_ARGS --hidden-import=$modname"
            fi
        done

        # 收集 repositories, models, schemas, llm, mcp, audit
        for subdir in repositories models schemas llm mcp audit; do
            for pyfile in "$SRC_DIR"/app/${subdir}/*.py; do
                if [ -f "$pyfile" ] && [ "$(basename "$pyfile")" != "__init__.py" ]; then
                    modname="app.${subdir}.$(basename "$pyfile" .py)"
                    HIDDEN_ARGS="$HIDDEN_ARGS --hidden-import=$modname"
                fi
            done
        done
    fi

    if [ "$COMPONENT" = "mcp-server" ]; then
        HIDDEN_ARGS="$HIDDEN_ARGS --hidden-import=config"
        HIDDEN_ARGS="$HIDDEN_ARGS --hidden-import=resource_limiter"
        HIDDEN_ARGS="$HIDDEN_ARGS --hidden-import=sandbox"
        HIDDEN_ARGS="$HIDDEN_ARGS --hidden-import=plugins"

        # 收集 plugins 子模块
        for pyfile in "$SRC_DIR"/plugins/*.py; do
            if [ -f "$pyfile" ] && [ "$(basename "$pyfile")" != "__init__.py" ]; then
                modname="plugins.$(basename "$pyfile" .py)"
                HIDDEN_ARGS="$HIDDEN_ARGS --hidden-import=$modname"
            fi
        done
    fi

    mkdir -p "$OUTPUT_DIR"

    log_info "执行 PyInstaller 打包（这可能需要几分钟）..."

    # 执行 PyInstaller
    # strip: 去除符号表减小体积
    # --noconfirm: 自动覆盖上次构建
    pyinstaller \
        --onefile \
        --strip \
        --noconfirm \
        --name="${OUTPUT_NAME}" \
        --distpath="${OUTPUT_DIR}" \
        --workpath="${BUILD_DIR}/pyinstaller_${COMPONENT}" \
        --specpath="${BUILD_DIR}" \
        $COLLECT_ARGS \
        $HIDDEN_ARGS \
        $DATA_ARGS \
        "${ENTRY_POINT}" \
        2>&1 | tail -20

    # 验证输出
    BINARY="${OUTPUT_DIR}/${OUTPUT_NAME}"
    if [ -f "$BINARY" ]; then
        chmod +x "$BINARY"
        local BINARY_SIZE=$(du -h "$BINARY" | cut -f1)
        log_info "✓ ${OUTPUT_NAME} 构建成功 ($BINARY_SIZE)"
        log_info "  输出: ${BINARY}"
    else
        log_error "✗ ${OUTPUT_NAME} 构建失败！"
        deactivate
        return 1
    fi

    deactivate
}

# ============================================================
# 用 Nuitka 打包 Python 组件 (实验性)
# ============================================================
build_with_nuitka() {
    local COMPONENT="$1"
    local ENTRY_POINT="$2"
    local OUTPUT_NAME="$3"
    local VENV_DIR="${BUILD_DIR}/venv_${COMPONENT}"
    local SRC_DIR="${PROJECT_ROOT}/${COMPONENT}"

    log_step "Nuitka 打包: ${COMPONENT} → ${OUTPUT_NAME}"

    source "${VENV_DIR}/bin/activate"

    cd "$SRC_DIR"
    mkdir -p "$OUTPUT_DIR"

    # Nuitka 编译选项:
    # --standalone: 独立可分发目录（包含所有依赖）
    # --onefile: 单文件（需要更长时间）
    # --follow-imports: 跟踪所有导入
    # --include-package: 显式包含包
    # --include-data-dir: 包含数据目录
    # --static-libpython=no: 不静态链接 libpython（避免 license 问题）
    # jobs=N: 并行编译线程数

    # 麒麟 V11 LoongArch 上 Nuitka 可能更慢但二进制性能更好
    # 先用 --standalone 确保能构建，再考虑 --onefile

    local INCLUDE_PACKAGES=""
    case "$COMPONENT" in
        backend)
            INCLUDE_PACKAGES="--include-package=app --include-package=config"
            INCLUDE_PACKAGES="$INCLUDE_PACKAGES --include-package=fastapi --include-package=uvicorn --include-package=pydantic"
            ;;
        mcp-server)
            INCLUDE_PACKAGES="--include-package=config --include-package=plugins"
            INCLUDE_PACKAGES="$INCLUDE_PACKAGES --include-package=resource_limiter --include-package=sandbox"
            ;;
    esac

    # 数据文件
    local DATA_ARGS=""
    if [ -f "${SRC_DIR}/.env.example" ]; then
        DATA_ARGS="--include-data-file=${SRC_DIR}/.env.example=.env.example"
    fi

    log_info "执行 Nuitka 编译（这可能需要 10-20 分钟，取决于系统性能）..."

    # 使用 timeout 防止无限等待
    timeout 1200 python3 -m nuitka \
        --standalone \
        --follow-imports \
        --include-package=psutil \
        $INCLUDE_PACKAGES \
        $DATA_ARGS \
        --output-dir="${OUTPUT_DIR}" \
        --output-filename="${OUTPUT_NAME}" \
        --remove-output \
        "${ENTRY_POINT}" \
        2>&1 | tail -30

    local NUITKA_EXIT=$?

    # 验证输出
    if [ $NUITKA_EXIT -eq 0 ] && [ -d "${OUTPUT_DIR}/${ENTRY_POINT%.*}.dist" ]; then
        local DIST_DIR="${OUTPUT_DIR}/${ENTRY_POINT%.*}.dist"
        log_info "✓ Nuitka 编译成功"
        log_info "  输出目录: ${DIST_DIR}"
        log_info "  入口: ${DIST_DIR}/${ENTRY_POINT}"
    elif [ $NUITKA_EXIT -eq 124 ]; then
        log_error "Nuitka 编译超时（20分钟），回退到 PyInstaller"
        deactivate
        build_with_pyinstaller "$COMPONENT" "$ENTRY_POINT" "$OUTPUT_NAME"
        return
    else
        log_warn "Nuitka 编译失败（退出码: $NUITKA_EXIT），回退到 PyInstaller"
        deactivate
        build_with_pyinstaller "$COMPONENT" "$ENTRY_POINT" "$OUTPUT_NAME"
        return
    fi

    deactivate
}

# ============================================================
# 打包 Python 组件（根据配置选择工具）
# ============================================================
build_python_component() {
    local COMPONENT="$1"      # backend | mcp-server
    local ENTRY_POINT="$2"    # run.py | server.py
    local OUTPUT_NAME="$3"    # kylin-agent-backend | kylin-mcp-server

    if [ ! -d "${PROJECT_ROOT}/${COMPONENT}" ]; then
        log_warn "${COMPONENT}/ 目录不存在，跳过"
        return
    fi

    prepare_venv "$COMPONENT"

    case "$PACKAGER" in
        nuitka)
            build_with_nuitka "$COMPONENT" "$ENTRY_POINT" "$OUTPUT_NAME"
            ;;
        pyinstaller)
            build_with_pyinstaller "$COMPONENT" "$ENTRY_POINT" "$OUTPUT_NAME"
            ;;
    esac
}

# ============================================================
# 构建前端
# ============================================================
build_frontend() {
    log_step "构建前端 (Vue.js + Vite)"

    local FRONTEND_DIR="${PROJECT_ROOT}/frontend"
    if [ ! -d "$FRONTEND_DIR" ]; then
        log_warn "frontend/ 目录不存在，跳过"
        return
    fi

    cd "$FRONTEND_DIR"

    # 安装 Node 依赖
    # 麒麟 V11 上 npm 可能较慢，设置国内镜像源
    log_info "配置 npm 镜像源..."
    npm config set registry https://registry.npmmirror.com 2>/dev/null || true

    log_info "安装 npm 依赖..."
    timeout 180 npm install 2>&1 | tail -5

    # 构建生产包
    log_info "构建前端静态文件..."
    npm run build 2>&1 | tail -10

    if [ -d "${FRONTEND_DIR}/dist" ]; then
        # 复制 dist 到输出目录
        mkdir -p "${OUTPUT_DIR}/frontend"
        cp -r "${FRONTEND_DIR}/dist"/* "${OUTPUT_DIR}/frontend/"
        local DIST_SIZE=$(du -sh "${OUTPUT_DIR}/frontend" | cut -f1)
        log_info "✓ 前端构建完成 ($DIST_SIZE)"
        log_info "  输出: ${OUTPUT_DIR}/frontend/"
    else
        log_error "✗ 前端构建失败！dist/ 目录未生成"
        return 1
    fi
}

# ============================================================
# 生成部署配置
# ============================================================
generate_deploy_config() {
    log_step "生成部署配置文件"

    mkdir -p "$OUTPUT_DIR"

    # 生成安装说明
    cat > "${OUTPUT_DIR}/INSTALL_LOONGARCH.txt" << 'EOF'
╔══════════════════════════════════════════════════════════════╗
║     Kylin Agent - LoongArch 二进制部署指南                    ║
╠══════════════════════════════════════════════════════════════╣
║                                                              ║
║  本目录包含针对麒麟 V11 (Swan25) + LoongArch 的编译产物。    ║
║                                                              ║
║  文件清单:                                                    ║
║    kylin-agent-backend      Backend 可执行文件               ║
║    kylin-mcp-server         MCP Server 可执行文件            ║
║    frontend/                前端静态文件 (nginx 部署)        ║
║                                                              ║
║  部署步骤:                                                    ║
║                                                              ║
║  [1] Backend 部署                                             ║
║      sudo cp kylin-agent-backend /opt/kylin-agent/backend/   ║
║      cd /opt/kylin-agent/backend/                            ║
║      cp .env.example .env   # 编辑配置文件                   ║
║      sudo ./kylin-agent-backend                              ║
║                                                              ║
║  [2] MCP Server 部署                                          ║
║      sudo cp kylin-mcp-server /opt/mcp-server/               ║
║      cd /opt/mcp-server/                                     ║
║      cp .env.example .env   # 编辑配置文件                   ║
║      sudo ./kylin-mcp-server                                 ║
║                                                              ║
║  [3] Frontend 部署 (需要 Nginx)                               ║
║      sudo cp -r frontend/* /var/www/kylin-agent/             ║
║      sudo cp deploy/nginx/kylin-agent.conf /etc/nginx/       ║
║          sites-available/                                    ║
║      sudo ln -s /etc/nginx/sites-available/kylin-agent.conf  ║
║          /etc/nginx/sites-enabled/                           ║
║      sudo systemctl reload nginx                             ║
║                                                              ║
║  systemd 服务 (推荐生产使用):                                 ║
║    参考 deploy/backend/kylin-agent.service                   ║
║    参考 deploy/mcp-server/mcp-server.service                 ║
║                                                              ║
║  注意事项:                                                    ║
║    - 二进制文件仅适用于 LoongArch + 麒麟 V11                  ║
║    - .env 配置文件需手动创建 （.env.example 已打包在内）     ║
║    - Python 依赖已静态打包，无需安装 Python 运行时           ║
║    - 但需要系统 C 库（glibc ≥ 2.28）                         ║
║    - 如有 C 扩展依赖（如 httptools），可能在运行时报错       ║
║      此时请在目标机上安装 python3 并用 pip 安装回退          ║
║                                                              ║
║  故障排查:                                                    ║
║    - 缺少 .so 文件：ldd ./kylin-agent-backend | grep "not"   ║
║    - 导入错误：./kylin-agent-backend --help                   ║
║    - 端口占用：检查 APP_PORT 配置                            ║
║                                                              ║
╚══════════════════════════════════════════════════════════════╝
EOF

    log_info "✓ 部署配置已生成: ${OUTPUT_DIR}/INSTALL_LOONGARCH.txt"
}

# ============================================================
# 清理构建产物
# ============================================================
clean_build() {
    log_step "清理构建产物"
    rm -rf "${BUILD_DIR}"
    rm -rf "${OUTPUT_DIR}"
    log_info "✓ 构建产物已清理"
}

# ============================================================
# 生成摘要
# ============================================================
print_summary() {
    echo ""
    echo "=============================================="
    echo "  编译完成！"
    echo "=============================================="
    echo ""
    echo "  输出目录: ${OUTPUT_DIR}"
    echo ""
    echo "  产物清单:"
    echo "  ───────────────────────────────────────────"
    if [ -f "${OUTPUT_DIR}/kylin-agent-backend" ]; then
        printf "    %-30s  %s\n" "kylin-agent-backend" "$(du -h "${OUTPUT_DIR}/kylin-agent-backend" | cut -f1)"
    fi
    if [ -f "${OUTPUT_DIR}/kylin-mcp-server" ]; then
        printf "    %-30s  %s\n" "kylin-mcp-server" "$(du -h "${OUTPUT_DIR}/kylin-mcp-server" | cut -f1)"
    fi
    if [ -d "${OUTPUT_DIR}/frontend" ]; then
        printf "    %-30s  %s\n" "frontend/" "$(du -sh "${OUTPUT_DIR}/frontend" | cut -f1)"
    fi
    echo ""
    echo "  打包分发:"
    echo "    cd ${OUTPUT_DIR}/.. && tar -czf kylin-agent-loongarch-$(date +%Y%m%d).tar.gz loongarch/"
    echo ""

    # 默认打包
    cd "${OUTPUT_DIR}/.."
    local ARCHIVE="kylin-agent-loongarch-$(date +%Y%m%d-%H%M%S).tar.gz"
    tar -czf "$ARCHIVE" loongarch/ 2>/dev/null
    if [ -f "$ARCHIVE" ]; then
        log_info "已自动打包: $(pwd)/${ARCHIVE} ($(du -h "$ARCHIVE" | cut -f1))"
    fi
}

# ============================================================
# 主流程
# ============================================================

main() {
    # 清理
    if [ "$DO_CLEAN" = true ]; then
        clean_build
        return
    fi

    # 环境检查
    check_environment

    # 安装依赖
    install_system_deps
    install_packager

    # 创建构建和输出目录
    mkdir -p "$BUILD_DIR" "$OUTPUT_DIR"

    # 构建 Python 组件
    if [ "$BUILD_BACKEND" = true ]; then
        build_python_component "backend" "run.py" "kylin-agent-backend"
    fi

    if [ "$BUILD_MCP" = true ]; then
        build_python_component "mcp-server" "server.py" "kylin-mcp-server"
    fi

    # 构建前端
    if [ "$BUILD_FRONTEND" = true ]; then
        build_frontend
    fi

    # 生成部署配置
    generate_deploy_config

    # 打印摘要
    print_summary
}

main