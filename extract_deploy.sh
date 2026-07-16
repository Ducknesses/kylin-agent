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
#   - 测试文件 (tests/, test_*.py, *_test.py)
#   - 文档 (*.md, *.docx)
#   - Python 缓存 (__pycache__, *.pyc, .pytest_cache)
#   - Node 模块 (node_modules/)
#   - 前端构建产物 (dist/)
#   - 环境秘密 (.env，保留 .env.example)
#   - 数据库文件 (*.db, *.sqlite*)
#   - SSL/TLS 证书 (deploy/nginx/certs/*.crt, *.key)
#   - IDE/编辑器配置 (.vscode, .idea)
#   - 日志文件 (*.log)
#   - Windows Zone.Identifier 文件
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
    echo "[1/6] 清理旧目录 $TARGET ..."
    rm -rf "$TARGET"
else
    echo "[1/6] 无需清理"
fi

# ---- 2. 创建目标目录 ----
echo "[2/6] 创建目标目录结构..."
mkdir -p "$TARGET"

# ---- 3. 复制文件 (使用 rsync 进行高效过滤) ----
echo "[3/6] 提取部署文件..."

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
    --exclude='.pytest_cache/'

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

    # ── Windows Zone.Identifier (WSL 跨文件系统产生) ──
    --exclude='*.Identifier'

    # ── SSL/TLS 证书 (部署时自行生成) ──
    --exclude='deploy/nginx/certs/*.crt'
    --exclude='deploy/nginx/certs/*.key'
    --exclude='deploy/nginx/certs/*.pem'
    --exclude='deploy/nginx/certs/*.csr'

    # ── 临时文件 ──
    --exclude='tmp/'
    --exclude='*.tmp'

    # ── 项目配置 (不需要部署的) ──
    --exclude='.editorconfig'
    --exclude='.clinerules'
    --exclude='.reasonix/'
    --exclude='reasonix.toml'

    # ── 仅开发/测试用脚本 ──
    --exclude='ws_test.py'
    --exclude='mock_server.py'

    # ── 本项目提取脚本自身及输出目录 ──
    --exclude='extract_deploy.sh'
    --exclude='kylin-agent-deploy/'
    --exclude='*.tar.gz'
)

# 使用 rsync 执行复制
rsync -a \
    "${RSYNC_EXCLUDES[@]}" \
    --prune-empty-dirs \
    "$PROJECT_ROOT/" \
    "$PROJECT_ROOT/$TARGET/"

echo "  ✓ 文件提取完成"

# ---- 4. 生成清单报告 ----
echo "[4/6] 生成文件清单..."

cat > "$PROJECT_ROOT/$TARGET/MANIFEST.txt" << 'MANIFEST_EOF'
============================================================
  Kylin Agent 部署包 - 文件清单
============================================================

包含内容说明:

  backend/            后端 FastAPI 应用 (Python)
  frontend/           前端 Vue 3 + Vite 应用
  mcp-server/         MCP Server 守护进程 (Python)
  deploy/             一键安装/卸载脚本 & 配置文件
    ├── backend/      Backend systemd 服务 & 安装脚本
    ├── frontend/     Frontend Nginx 配置 & 安装脚本
    ├── mcp-server/   MCP Server systemd 服务 & 安装脚本
    └── nginx/        Nginx 站点配置模板

不包含内容:

  .env               环境秘密文件 (部署时从 .env.example 创建)
  node_modules/      前端依赖 (通过 npm install 安装)
  dist/              前端构建产物 (通过 npm run build 生成)
  tests/             测试文件
  docs/              文档
  __pycache__/       Python 编译缓存
  *.db               数据库文件
  *.log              日志文件
  *.Identifier       Windows Zone.Identifier 文件
  deploy/nginx/certs/* SSL/TLS 证书 (部署时自行生成或放置)
  .git               Git 历史
  .vscode/           IDE 配置

部署流程:

  1. Backend (控制节点):
     sudo ./deploy/backend/install.sh

  2. Frontend (控制节点, Nginx):
     sudo ./deploy/frontend/install.sh

  3. Nginx 配置 (控制节点):
     sudo cp deploy/nginx/kylin-agent.conf /etc/nginx/sites-available/
     sudo ln -s /etc/nginx/sites-available/kylin-agent.conf /etc/nginx/sites-enabled/
     sudo nginx -t && sudo systemctl reload nginx

  4. MCP Server (目标机, 麒麟 V11 / LoongArch):
     sudo ./deploy/mcp-server/install.sh

提示:

  - 部署前请创建并编辑 .env 配置文件:
      Backend:  cp backend/.env.example backend/.env
      MCP Server: cp mcp-server/.env.example mcp-server/.env

  - Backend 需要 Python 3.10+
  - Frontend 构建需要 Node.js 18+
  - MCP Server 需要 Python 3.x + 麒麟 V11 环境

  - SSL 证书:
      自签名: openssl req -x509 -nodes -days 365 -newkey rsa:2048 \
               -keyout deploy/nginx/certs/kylin-agent.key \
               -out deploy/nginx/certs/kylin-agent.crt
      正式证书: 将 .crt 和 .key 放入 deploy/nginx/certs/ 目录

  - 数据库将在首次启动时自动创建 (backend/data/)
MANIFEST_EOF

# ---- 5. 统计信息 ----
echo "[5/6] 统计文件..."

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
echo "[6/6] 压缩选项"
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
echo ""
echo "  ── 后端 ──"
echo "    cp backend/.env.example backend/.env    # 并编辑配置"
echo "    sudo ./deploy/backend/install.sh"
echo ""
echo "  ── 前端 ──"
echo "    sudo ./deploy/frontend/install.sh"
echo ""
echo "  ── Nginx ──"
echo "    sudo cp deploy/nginx/kylin-agent.conf /etc/nginx/sites-available/"
echo "    sudo ln -s /etc/nginx/sites-available/kylin-agent.conf /etc/nginx/sites-enabled/"
echo "    # 如有 SSL 证书，放置到 deploy/nginx/certs/ 后 reload nginx"
echo "    sudo nginx -t && sudo systemctl reload nginx"
echo ""
echo "  ── MCP Server (复制到目标机后) ──"
echo "    cp mcp-server/.env.example mcp-server/.env  # 并编辑配置"
echo "    sudo ./deploy/mcp-server/install.sh"
echo ""