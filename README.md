# 麒麟智能运维 Agent (kylin-agent)

赛题 A2：面向麒麟操作系统的安全智能运维 Agent。

## 架构概览

```
┌─ 用户浏览器 ──────────────────────────────────────────┐
│  前端: Vue 3 + Vite (端口 5173 dev / Nginx :443 prod)  │
└──────────────────────┬────────────────────────────────┘
                       │ HTTPS / WSS
┌─ 控制节点 ───────────────────────────────────────────┐
│  Nginx :443 → 静态文件 + API 反向代理 + WSS 升级       │
│  Backend :8000 → FastAPI + 多 Agent 编排 + LLM 路由    │
│  Redis :6379 → 会话缓存 + 确认状态存储 + 任务持久化    │
│  SQLite → 审计日志 (哈希链防篡改)                      │
└──────────────────────┬────────────────────────────────┘
                       │ HTTPS + Bearer Token
┌─ 麒麟 V11 目标机 (LoongArch) ────────────────────────┐
│  MCP Server :8001 → 系统信息 / 服务管理 / 日志读取 / … │
│  权限: agent-read (只读) / agent-op (sudo 白名单)      │
└──────────────────────────────────────────────────────┘
```

**核心安全设计：**
- LLM 不做决策执行，只做意图理解和辅助推理
- 所有系统操作通过 MCP Server 在目标机本地执行
- 三级 RBAC (READ → OP → ADMIN) + 危险模式拦截
- 多 Token 分级认证
- 审计链 SHA256 哈希防篡改

## 项目结构

```
kylin-agent/
├── backend/                     # Python + FastAPI 后端 (控制节点)
│   ├── config.py                # 全局配置 (环境变量驱动)
│   ├── diagnose.py              # 系统诊断工具 (CLI)
│   ├── run.py                   # uvicorn 启动入口
│   ├── requirements.txt         # Python 依赖
│   ├── .env.example             # 环境变量模板
│   └── app/
│       ├── main.py              # FastAPI 应用工厂 + 路由注册
│       ├── api/                 # REST + WebSocket 接口层
│       │   ├── chat.py          # WebSocket 聊天 (安全检测 → LLM → 审计)
│       │   ├── sessions.py      # 会话 CRUD
│       │   ├── monitor.py       # SSE 系统监控流
│       │   ├── audit.py         # 审计日志查询
│       │   ├── actions.py       # 修复动作 API
│       │   └── config.py        # 白名单配置 API
│       ├── services/            # Agent 服务层
│       │   ├── agent_harness.py     # Agent 编排执行器
│       │   ├── orchestrator.py      # 多 Agent 编排循环
│       │   ├── intent_agent.py      # 意图识别
│       │   ├── diagnose_agent.py    # 诊断 Agent
│       │   ├── fix_planner_agent.py # 修复规划 Agent
│       │   ├── reporter_agent.py    # 报告生成 Agent
│       │   ├── safety_guard.py      # 输入安全护栏
│       │   ├── action_service.py    # 修复动作服务
│       │   ├── knowledge_service.py # 知识库匹配模块
│       │   └── ...                  # 连接管理 / 状态存储 / LLM 客户端等
│       ├── core/                # 安全与基础组件
│       │   ├── security.py      # 风险分级 (reject/confirm/allow)
│       │   ├── prompt_guard.py  # Prompt 注入检测 (五层)
│       │   ├── security_rules.py# 安全规则数据唯一来源（含旧版 RBAC 默认白名单）
│       │   ├── auth.py          # Token 认证 (多 token 分级)
│       │   ├── redis_client.py  # Redis 封装 (fakeredis fallback)
│       │   └── database.py      # 数据库引擎 (SQLite/PostgreSQL)
│       ├── mcp/                 # MCP 客户端
│       │   ├── client.py        # JSON-RPC 2.0 客户端
│       │   └── tools.py         # 6 个工具定义 + JSON Schema
│       ├── llm/                 # LLM 模块
│       │   ├── deepseek.py      # DeepSeek API (流式/非流式)
│       │   └── router.py        # 意图路由
│       ├── audit/               # 审计模块
│       │   ├── models.py        # SQLite 表 + 哈希链防篡改
│       │   └── logger.py        # 审计写入/查询
│       └── schemas/             # Pydantic 数据模型
│
├── mcp-server/                  # MCP Server (部署在麒麟目标机)
│   ├── server.py                # MCP 服务入口
│   ├── config.py                # MCP Server 配置
│   ├── sandbox.py               # 命令执行沙箱 (cgroups 资源隔离)
│   ├── resource_limiter.py      # cgroups v2 资源限制器
│   ├── requirements.txt         # Python 依赖 (psutil, python-dotenv)
│   ├── kylin-wizard.sh          # 配置向导 (端口/Token/防火墙)
│   └── plugins/
│       ├── sys_info.py          # 系统信息采集
│       ├── service_mgr.py       # systemd 服务管理
│       ├── log_reader.py        # 系统日志读取
│       ├── net_monitor.py       # 网络监控
│       ├── cmd_exec.py          # 命令执行 (RBAC 管控)
│       ├── file_guard.py        # 文件监视
│       └── metrics_store.py     # 指标存储
│
├── frontend/                    # Vue 3 + Vite 前端
│   └── src/
│       ├── api/                 # HTTP + WebSocket 客户端
│       ├── components/          # ChatPanel, SysMonitor, AuditTable 等
│       ├── stores/              # Pinia 状态管理
│       ├── router/              # Vue Router
│       └── views/               # HomeView, AuditView, MonitorView
│
├── deploy/                      # 部署脚本 (三端独立)
│   ├── backend/
│   │   ├── install.sh           # Backend 一键安装
│   │   ├── uninstall.sh         # Backend 一键卸载
│   │   └── kylin-agent.service  # Backend systemd 服务
│   ├── frontend/
│   │   ├── install.sh           # Frontend + Nginx 一键安装
│   │   ├── uninstall.sh         # Frontend + Nginx 一键卸载
│   │   └── nginx-kylin-agent.conf # Nginx 反向代理配置
│   └── mcp-server/
│       ├── install.sh           # 麒麟目标机一键安装
│       ├── uninstall.sh         # 麒麟目标机一键卸载
│       ├── mcp-server.service   # MCP Server systemd 服务
│       ├── agent-op.sudoers     # sudoers 白名单
│       └── loongarch-check.sh   # LoongArch 架构兼容性检查
│
├── docs/                        # 项目文档
└── README.md                    # 本文件
```

## 快速开始 (开发环境)

### 前置要求

- Python >= 3.10
- Node.js >= 18
- Redis (可选，代码内置 fakeredis fallback)
- DeepSeek API Key (可选，`LLM_ENABLED=false` 可走规则版 fallback)

### 1. 启动后端

```bash
cd backend

# 创建虚拟环境
python3 -m venv .venv
source .venv/bin/activate

# 安装依赖
pip install -r requirements.txt

# 配置环境变量 (从模板复制)
cp .env.example .env
# 编辑 .env，至少填写 DEEPSEEK_API_KEY (如果启用 LLM)

# 启动 (默认 127.0.0.1:8000)
python run.py
```

后端默认监听 `http://localhost:8000`，健康检查：

```bash
curl http://localhost:8000/health
# → {"status":"ok","version":"0.1.0"}
```

### 2. 启动前端

```bash
cd frontend
npm install
npm run dev
```

前端默认监听 `http://localhost:5173`，Vite 会自动将 `/api` 和 `/ws` 代理到后端 8000 端口。

### 3. (可选) 启动 Mock MCP Server

```bash
cd mcp-server
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python server.py
# 监听 127.0.0.1:8001
```

## 生产部署 (麒麟 V11)

### 部署架构

三个组件**可独立部署**：

| 组件 | 部署位置 | 安装脚本 |
|------|---------|----------|
| Backend | 控制节点 (x86_64 / ARM) | `sudo ./deploy/backend/install.sh` |
| Frontend + Nginx | 控制节点 (x86_64 / ARM) | `sudo ./deploy/frontend/install.sh` |
| MCP Server | 麒麟 V11 目标机 (LoongArch) | `sudo ./deploy/mcp-server/install.sh` |

### Backend 部署

```bash
sudo ./deploy/backend/install.sh

# 编辑配置
vim /opt/kylin-agent/backend/.env

# 启动服务
sudo systemctl start kylin-agent

# 查看日志
sudo journalctl -u kylin-agent -f
```

安装后路径：`/opt/kylin-agent/backend/`

### Frontend + Nginx 部署

```bash
# 需要先构建前端产物
cd frontend && npm run build && cd ..

sudo ./deploy/frontend/install.sh
```

安装后路径：`/opt/kylin-agent/frontend/dist/`
Nginx 配置：`/etc/nginx/sites-available/kylin-agent`

### 麒麟目标机部署

```bash
sudo ./deploy/mcp-server/install.sh
# 安装前自动运行 loongarch-check.sh 架构检查

# 查看状态
sudo systemctl status mcp-server

# 运行配置向导 (修改端口/Token/防火墙)
cd /opt/mcp-server && sudo bash kylin-wizard.sh
```

安装后路径：`/opt/mcp-server/`

### 卸载

```bash
# Backend 卸载
sudo ./deploy/backend/uninstall.sh

# Frontend + Nginx 卸载
sudo ./deploy/frontend/uninstall.sh

# MCP Server 卸载
sudo ./deploy/mcp-server/uninstall.sh
```

## API 接口

### REST API

| 方法 | 路径 | 说明 |
|------|------|------|
| `GET` | `/health` | 健康检查 |
| `WS` | `/ws/chat/{session_id}` | WebSocket 聊天 (安全检测 → LLM → 审计) |
| `GET` | `/api/sessions` | 列出所有会话 |
| `POST` | `/api/sessions` | 创建新会话 |
| `GET` | `/api/monitor/stream` | SSE 系统监控流 |
| `GET` | `/api/audit` | 审计日志查询 (分页: `?limit=&offset=`) |
| `GET` | `/api/config/whitelist` | 查看三级白名单 |
| `PUT` | `/api/config/whitelist` | 更新白名单 |
| `POST` | `/api/actions` | 修复动作 |

### 认证

支持两种 Token 模式：

1. **单 Token**：`API_TOKEN=my-secret` → 所有匹配均为 ADMIN
2. **多 Token 分级**：`API_TOKENS=read:rk_xxx,op:op_yyy,admin:adm_zzz`
   - `read` — 只读权限
   - `op` — 操作权限
   - `admin` — 管理权限

## 配置说明

完整配置项见 `backend/.env.example`。关键配置：

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `LLM_ENABLED` | `false` | LLM 主开关，false 走规则版 fallback |
| `LLM_PROVIDER` | `deepseek` | deepseek / local_openai_compatible |
| `DEEPSEEK_API_KEY` | — | DeepSeek API Key |
| `MCP_MODE` | `mock` | mock (独立开发) / real (对接麒麟目标机) |
| `MCP_SERVER_URL` | `http://192.168.56.101:8001` | MCP Server 地址 |
| `MCP_AUTH_TOKEN` | — | MCP Server Bearer Token |
| `REDIS_URL` | `redis://localhost:6379/0` | Redis 连接地址 |
| `API_TOKEN` | (空) | 单 Token 认证 (空=不启用) |
| `API_TOKENS` | (空) | 多 Token 分级认证 |

## 安全模型

```
用户输入
  ↓
第1层: 安全护栏 (safety_guard.py)
  ├─ 长度检查 (MAX_INPUT_LENGTH)
  ├─ 高危正则黑名单 (rm -rf /, mkfs, dd, fork bomb 等)
  ├─ Prompt 注入检测 (五层: 控制字符 → Unicode混淆 → 重复字符 → 关键词 → 语义边界)
  └─ 中危关键词 (kill -9, chmod 777 等)
       ↓ 返回: reject / confirm / allow
  ↓
第2层: 意图识别 (intent_agent.py)
  ↓
第3层: 多 Agent 编排 (orchestrator.py)
  ├─ DiagnoseAgent → 根因分析
  ├─ FixPlannerAgent → 修复方案生成
  └─ ReporterAgent → 报告生成
       ↓ 修复动作
  ↓
第4层: 工具调用安全裁决 (safety_guard.py + security_rules.py)
  ├─ 工具分发检查 + 高危模式拦截
  ├─ 危险模式拦截 (;, &&, |, `, $() 等)
  └─ 角色分级: viewer → operator → admin（medium 需确认，high 恒拒绝）
       ↓
第5层: MCP Server 执行 (麒麟目标机)
  ├─ agent-read: 只读 (sys_info, log_reader, net_monitor)
  └─ agent-op: sudo 白名单 (systemctl status/start/stop/restart/reload)
       ↓
第6层: cgroup 资源隔离 (mcp-server/sandbox.py)
  ├─ CPU 配额限制 (默认 50%)
  ├─ 内存上限 (默认 256MB)
  ├─ PID 数量限制 (防 fork bomb)
  └─ 可通过 .env 配置: CGROUP_ENABLED=true
       ↓
第7层: 审计记录 (哈希链防篡改)
  ├─ SQLite audit_chain 表
  └─ SHA256 链式哈希: hash(prev_hash + current_data)
```

## 技术栈

| 层级 | 技术 |
|------|------|
| 后端框架 | FastAPI + Uvicorn |
| LLM | DeepSeek API (可切换 OpenAI-compatible) |
| 实时通信 | WebSocket + SSE |
| 缓存/状态 | Redis (fakeredis fallback) |
| 审计持久化 | SQLite + aiosqlite |
| MCP 协议 | JSON-RPC 2.0 over HTTP |
| 前端 | Vue 3 + Vite + Pinia |
| 部署 | systemd + venv + Nginx (原生) |
| 目标平台 | 麒麟 V11 + LoongArch |

## 诊断工具

Backend 提供了独立 CLI 诊断脚本 `backend/diagnose.py`，可离线检测后端环境，**不依赖后端 API 服务**。

### 功能

| 检测项 | 说明 |
|--------|------|
| 依赖检测 | 解析 `requirements.txt`，检查每个包是否已安装，缺失项给出安装建议 |
| 监听地址 | 查看当前 `APP_HOST:APP_PORT` 配置，支持交互式修改 `.env` |
| 启动状态 | 端口监听检测、`/health` 端点探测、Redis 连通性、SQLite 可读写性、systemd 服务状态 |
| 网络连通性 | MCP Server、DeepSeek API、前端 Nginx 的 HTTP 可达性检测，含延迟测量和故障建议 |

### 使用方式

```bash
cd backend

# 完整检测（含依赖检测 + 交互式修改监听地址）
python diagnose.py

# 快速检测（跳过依赖检测）
python diagnose.py --quick

# 非交互模式（不提示修改监听地址）
python diagnose.py --non-interactive

# 快速 + 非交互（适合 CI/脚本调用）
python diagnose.py --quick --non-interactive
```

### 输出示例

```
  Kylin Agent Backend 诊断工具

[1/4] 依赖检测
  ✓ fastapi (0.110.0)
  ✓ uvicorn (0.29.0)
  ...

[2/4] 监听地址
  当前配置: 0.0.0.0:8000

[3/4] Backend 启动状态
  ✓ 端口监听 — 端口 8000 正在监听
  ✓ Health API — /health 返回 200
  ✗ Redis — Redis 不可达 (localhost:6379)
    → 建议: systemctl start redis
  ✓ SQLite — audit.db 可读写
  ✗ Systemd 服务 — systemd 服务状态: inactive
    → 建议: systemctl start kylin-agent

[4/4] 网络连通性
  ✗ MCP Server (http://192.168.1.37:8001) — HTTP 502 Bad Gateway
  ✓ DeepSeek API (https://api.deepseek.com) — 130ms
  ✓ 前端 (http://127.0.0.1:80) — 6ms

  诊断完成: 通过 6/9
  失败: 3
```

### 部署后使用

生产环境部署 Backend 后，可直接使用诊断脚本排查问题：

```bash
cd /opt/kylin-agent/backend
python diagnose.py --non-interactive
```

## 开发

### 运行测试

```bash
cd backend
pip install pytest
pytest tests/ -v
```

### 代码风格

项目使用 `.editorconfig` 统一缩进风格，建议安装对应 IDE 插件。

## License

MIT