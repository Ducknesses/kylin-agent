#!/bin/bash
# ============================================================
# Kylin Agent 部署文件提取脚本
# ============================================================
# 用法: ./extract_deploy.sh [目标目录名]
# 默认输出: ./kylin-agent-deploy/
# ============================================================
# 功能:
#   提取项目正常运行/部署所需的所有文件，排除:
#   - Git 历史 (.git)
#   - 测试文件 (*/tests/)
#   - 文档 (*/docs/)
#   - Python 缓存 (__pycache__, *.pyc)
#   - Node 模块 (node_modules/)
#   - 前端构建产物 (dist/，应由 npm run build 生成)
#   - 环境秘密 (.env，保留 .env.example)
#   - 数据库文件 (*.db, *.sqlite*)
#   - IDE/编辑器配置 (.vscode, .idea)
#   - 临时/备份文件
# ============================================================

set -e

# ---- 参数处理 ----
TARGET="${1:-kylin-agent-deploy}"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$SCRIPT_DIR"

# ---- 安全检查 ----
if [ "$(realpath "$PROJECT_ROOT")" = "$(realpath "$TARGET")" ]; then
    echo "[ERROR] 目标目录不能与项目根目录相同！"
    exit 1
fi

echo "=============================================="
echo "  Kylin Agent 部署文件提取"
echo "=============================================="
echo ""
echo "  源目录:   $PROJECT_ROOT"
echo "  目标目录: $PROJECT_ROOT/$TARGET"
echo ""

# ---- 1. 清理旧目录 ----
if [ -d "$TARGET" ]; then
    echo "[1/4] 清理旧目录 $TARGET ..."
    rm -rf "$TARGET"
fi

# ---- 2. 创建目标目录 ----
echo "[2/4] 创建目标目录结构..."
mkdir -p "$TARGET"

# ---- 3. 复制文件 (使用 rsync 进行高效过滤) ----
echo "[3/4] 提取部署文件..."

# 定义排除模式（与 .gitignore 保持一致 + 额外排除）
RSYNC_EXCLUDES=(
    # ── Git ──
    --exclude='.git'
    --exclude='.gitignore'
    --exclude='.gitattributes'

    # ── Python 编译缓存 ──
    --exclude='__pycache__/'
    --exclude='*.pyc'
    --exclude='*.pyo'
    --exclude='*.pyd'

    # ── Python 虚拟环境 ──
    --exclude='.venv/'
    --exclude='venv/'
    --exclude='env/'
    --exclude='*.egg-info/'

    # ── Node 模块与构建产物 ──
    --exclude='node_modules/'
    --exclude='dist/'
    --exclude='.node/'

    # ── 环境秘密文件 (保留 .env.example 模板) ──
    --exclude='.env'
    --exclude='.env.development.local'
    --exclude='.env.production'

    # ── 数据库与数据文件 ──
    --exclude='*.db'
    --exclude='*.db-journal'
    --exclude='*.db-wal'
    --exclude='*.db-shm'
    --exclude='*.sqlite'
    --exclude='*.sqlite3'
    --exclude='backup/'

    # ── 测试文件 ──
    --exclude='tests/'
    --exclude='test_*.py'
    --exclude='*_test.py'
    --exclude='__pycache__/'
    --exclude='.pytest_cache/'

    # ── 文档 ──
    --exclude='docs/'
    --exclude='*.md'
    --exclude='*.docx'

    # ── IDE / 编辑器配置 ──
    --exclude='.vscode/'
    --exclude='.idea/'
    --exclude='*.swp'
    --exclude='*.swo'
    --exclude='*~'

    # ── 日志 ──
    --exclude='*.log'

    # ── 临时文件 ──
    --exclude='tmp/'
    --exclude='*.tmp'
    --exclude='*.Identifier'

    # ── 项目配置 (不需要部署的) ──
    --exclude='.editorconfig'
    --exclude='.clinerules'
    --exclude='.reasonix/'
    --exclude='reasonix.toml'

    # ── 测试/开发用脚本 ──
    --exclude='ws_test.py'
    --exclude='mock_server.py'

    # ── 本项目提取脚本自身 ──
    --exclude='extract_deploy.sh'
    --exclude='kylin-agent-deploy/'
)

# 使用 rsync 执行复制
rsync -a \
    "${RSYNC_EXCLUDES[@]}" \
    --prune-empty-dirs \
    "$PROJECT_ROOT/" \
    "$PROJECT_ROOT/$TARGET/"

echo "  ✓ 文件提取完成"

# ---- 4. 生成清单报告 ----
echo "[4/4] 生成文件清单..."

cat > "$PROJECT_ROOT/$TARGET/MANIFEST.txt" << 'MANIFEST_EOF'
============================================================
  Kylin Agent 部署包 - 文件清单
============================================================

包含内容说明:

  backend/            后端 FastAPI 应用 (Python)
  frontend/           前端 Vue 3 + Vite 应用
  mcp-server/         MCP Server 守护进程 (Python)
  deploy/             一键安装/卸载脚本

不包含内容:

  .env               环境秘密文件 (部署时从 .env.example 创建)
  node_modules/      前端依赖 (通过 npm install 安装)
  dist/              前端构建产物 (通过 npm run build 生成)
  tests/             测试文件
  docs/              文档
  __pycache__/       Python 编译缓存
  *.db               数据库文件
  .git               Git 历史

部署流程:

  1. Backend (控制节点):
     sudo ./deploy/backend/install.sh

  2. Frontend (控制节点, Nginx):
     sudo ./deploy/frontend/install.sh

  3. MCP Server (目标机, 麒麟 V11):
     sudo ./deploy/mcp-server/install.sh

提示:

  - 部署前请创建并编辑 .env 配置文件 (参考 .env.example)
  - Backend 需要 Python 3.10+
  - Frontend 构建需要 Node.js 18+
  - MCP Server 需要 Python 3.x + 麒麟 V11 环境
MANIFEST_EOF

# ---- 5. 统计信息 ----
FILE_COUNT=$(find "$PROJECT_ROOT/$TARGET" -type f | wc -l)
DIR_COUNT=$(find "$PROJECT_ROOT/$TARGET" -type d | wc -l)
TOTAL_SIZE=$(du -sh "$PROJECT_ROOT/$TARGET" | cut -f1)

echo ""
echo "=============================================="
echo "  ✓ 提取完成！"
echo "=============================================="
echo ""
echo "  输出目录: $PROJECT_ROOT/$TARGET"
echo "  文件数量: $FILE_COUNT"
echo "  目录数量: $DIR_COUNT"
echo "  总大小:   $TOTAL_SIZE"
echo ""
echo "  文件清单: $TARGET/MANIFEST.txt"
echo ""

# ---- 6. (可选) 创建压缩包 ----
read -p "是否创建 tar.gz 压缩包? [y/N] " -r COMPRESS
echo ""
if [[ "$COMPRESS" =~ ^[Yy]$ ]]; then
    ARCHIVE_NAME="kylin-agent-deploy-$(date +%Y%m%d-%H%M%S).tar.gz"
    echo "正在创建压缩包: $ARCHIVE_NAME"
    tar -czf "$ARCHIVE_NAME" -C "$PROJECT_ROOT" "$TARGET/"
    ARCHIVE_SIZE=$(du -sh "$ARCHIVE_NAME" | cut -f1)
    echo ""
    echo "  ✓ 压缩包已创建: $PROJECT_ROOT/$ARCHIVE_NAME"
    echo "  压缩包大小: $ARCHIVE_SIZE"
fi

echo ""
echo "  后续部署步骤:"
echo "    cd $TARGET"
echo "    cp backend/.env.example backend/.env    # 并编辑配置"
echo "    sudo ./deploy/backend/install.sh       # 安装后端"
echo "    sudo ./deploy/frontend/install.sh      # 安装前端"
echo "    # MCP Server 复制到目标机后安装"
echo ""