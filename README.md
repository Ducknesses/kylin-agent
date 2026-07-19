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

## 安装教程

### 前置要求

- Python >= 3.10
- Node.js >= 18
- Redis（可选，代码内置 fakeredis fallback；生产环境建议安装 `sudo apt install redis-server`）
- DeepSeek API Key（可选，`LLM_ENABLED=false` 可走规则版 fallback）
- 麒麟 V11 目标机（LoongArch 架构，仅 MCP Server 需要）

### 部署架构

三个组件**可独立部署**，支持开发环境和生产环境两种模式：

| 组件 | 开发环境 | 生产环境部署位置 | 生产环境安装脚本 |
|------|---------|-----------------|-----------------|
| Backend | `python run.py`（127.0.0.1:8000） | 控制节点 (x86_64 / ARM) | `sudo ./deploy/backend/install.sh` |
| Frontend | `npm run dev`（127.0.0.1:5173） | 控制节点 (x86_64 / ARM) + Nginx | `sudo ./deploy/frontend/install.sh` |
| MCP Server | `python server.py`（127.0.0.1:8001） | 麒麟 V11 目标机 (LoongArch) | `sudo ./deploy/mcp-server/install.sh` |

---

### 一、开发环境安装

适用于本地开发和调试，三端均在同一台机器上运行。

#### 1. 启动后端

```bash
cd backend

# 创建虚拟环境
python3 -m venv .venv
source .venv/bin/activate

# 安装依赖
pip install -r requirements.txt

# 配置环境变量（从模板复制）
cp .env.example .env
# 编辑 .env，至少填写 DEEPSEEK_API_KEY（如果启用 LLM）

# 启动（默认 127.0.0.1:8000）
python run.py
```

验证后端是否正常：

```bash
curl http://localhost:8000/health
# → {"status":"ok","version":"0.1.0"}
```

#### 2. 启动前端

```bash
cd frontend
npm install
npm run dev
```

前端默认监听 `http://localhost:5173`，Vite 会自动将 `/api` 和 `/ws` 代理到后端 8000 端口。

#### 3.（可选）启动 Mock MCP Server

```bash
cd mcp-server
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python server.py
# 监听 127.0.0.1:8001
```

---

### 二、生产环境部署

#### 1. Backend 部署（控制节点）

```bash
# 一键安装（需要 root 权限）
sudo ./deploy/backend/install.sh
```

安装脚本会自动完成：
- 检查 Python 3.10+ 环境
- 创建虚拟环境 `/opt/kylin-agent/backend/venv`
- 安装 Python 依赖（`fastapi`, `uvicorn`, `websockets` 等）
- 复制项目文件到 `/opt/kylin-agent/backend/`
- 创建 `kylin-agent` systemd 服务

安装后配置：

```bash
# 编辑配置文件
sudo vim /opt/kylin-agent/backend/.env

# 关键配置项：
# MCP_SERVER_URL=http://<麒麟目标机IP>:8001
# MCP_AUTH_TOKEN=<与 MCP Server 一致的 Token>
# MCP_MODE=real   # 从 mock 改为 real
```

启动与状态检查：

```bash
# 启动服务
sudo systemctl start kylin-agent

# 设置开机自启
sudo systemctl enable kylin-agent

# 查看运行状态
sudo systemctl status kylin-agent

# 查看实时日志
sudo journalctl -u kylin-agent -f

# 健康检查
curl http://127.0.0.1:8000/health
```

安装后路径：`/opt/kylin-agent/backend/`

#### 2. Frontend + Nginx 部署（控制节点）

```bash
# 先构建前端产物
cd frontend && npm install && npm run build && cd ..

# 一键安装（需要 root 权限）
sudo ./deploy/frontend/install.sh
```

安装脚本会自动完成：
- 安装 Nginx 并配置反向代理
- 复制前端静态文件到 `/opt/kylin-agent/frontend/dist/`
- 生成自签名 SSL 证书（`/etc/nginx/certs/kylin-agent.{crt,key}`）
- 配置 HTTP → HTTPS 自动跳转
- 自动处理 SELinux 策略（如适用）

安装后路径：`/opt/kylin-agent/frontend/dist/`
Nginx 配置：`/etc/nginx/sites-available/kylin-agent`（Debian/Ubuntu）或 `/etc/nginx/conf.d/kylin-agent.conf`（RHEL/Kylin）

验证前端：

```bash
# 访问 https://<控制节点IP>
# 默认将 /api 代理到 127.0.0.1:8000，/ws 代理到 127.0.0.1:8000

# 检查 Nginx 状态
sudo systemctl status nginx
```

> **注意：** 请确保 Backend 已安装并运行在 `127.0.0.1:8000`，否则前端将无法正常访问后端 API。

#### 3. MCP Server 部署（麒麟 V11 目标机）

```bash
# 一键安装（安装前自动运行 LoongArch 架构兼容性检查）
sudo ./deploy/mcp-server/install.sh
```

安装脚本会自动完成：
- LoongArch 架构兼容性检查
- 安装 Python 依赖（`psutil`, `python-dotenv`）
- 创建专用系统用户 `agent-read`（只读）和 `agent-op`（操作）
- 配置 sudoers 白名单（`/etc/sudoers.d/agent-op`）
- 创建 `mcp-server` systemd 服务

安装后配置：

```bash
# 运行配置向导（修改端口/Token/防火墙规则）
cd /opt/mcp-server && sudo bash wizard.sh
```

向导会交互式引导你设置：
- MCP Server 监听端口（默认 8001）
- 监听地址（默认 0.0.0.0）
- 认证 Token
- 防火墙规则（自动放行对应端口）

> **重要：** 请使用 `wizard.sh` 向导修改配置，不要直接用文本编辑器修改 `/opt/mcp-server/.env` 文件，否则 MCP Server 可能无法正确解析配置。

启动与状态检查：

```bash
# 查看运行状态
sudo systemctl status mcp-server

# 查看实时日志
sudo journalctl -u mcp-server -f

# 验证 MCP Server 是否可达（从控制节点执行）
curl http://<麒麟目标机IP>:8001/health
```

安装后路径：`/opt/mcp-server/`

#### 4. 卸载

```bash
# Backend 卸载
sudo ./deploy/backend/uninstall.sh

# Frontend + Nginx 卸载
sudo ./deploy/frontend/uninstall.sh

# MCP Server 卸载
sudo ./deploy/mcp-server/uninstall.sh
```

---

### 三、安装后验证清单

完成所有组件部署后，按以下顺序验证：

1. **Backend 健康检查**：`curl http://127.0.0.1:8000/health`
2. **MCP Server 状态**：`sudo systemctl status mcp-server`
3. **后端到 MCP 连通性**：`curl http://<麒麟目标机IP>:8001/health`（从控制节点执行）
4. **Nginx 状态**：`sudo systemctl status nginx`
5. **前端访问**：浏览器打开 `https://<控制节点IP>`
6. **运行诊断工具**：`cd /opt/kylin-agent/backend && python diagnose.py --non-interactive`

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

## 常见问题与解决方法

### 1. MCP Server 不可访问

#### 1.1 检查 MCP Server 的依赖项是否正确安装

MCP Server 依赖 `psutil` 和 `python-dotenv`。如果依赖缺失，服务可能无法正常启动或读取配置。

```bash
# 切换到 MCP Server 安装目录
cd /opt/mcp-server

# 使用虚拟环境的 pip 重新安装依赖
sudo ./venv/bin/pip install --upgrade pip
sudo ./venv/bin/pip install -r requirements.txt

# 验证关键依赖是否安装成功
sudo ./venv/bin/python -c "import psutil; import dotenv; print('依赖检查通过')"
```

如果提示 `No module named 'psutil'` 或 `No module named 'dotenv'`，说明依赖未安装到 MCP Server 的虚拟环境中，需要重新执行安装脚本：

```bash
sudo /opt/mcp-server/venv/bin/pip install psutil python-dotenv
sudo systemctl restart mcp-server
```

#### 1.2 检查 `.env` 文件的端口和监听地址

MCP Server 的端口和监听地址通过 `/opt/mcp-server/.env` 文件配置。

> **重要：** 请务必使用配置向导 `wizard.sh` 来修改端口和地址，**不要直接用文本编辑器修改 `.env` 文件**。直接编辑可能导致编码或换行符格式异常，使 MCP Server 无法正确读取配置。

```bash
cd /opt/mcp-server && sudo bash wizard.sh
```

向导会交互式引导你设置：
- **监听端口**（默认 8001）
- **监听地址**（默认 0.0.0.0，监听所有网络接口）
- **认证 Token**
- **防火墙规则**（自动放行对应端口）

修改完成后，重启 MCP Server 使配置生效：

```bash
sudo systemctl restart mcp-server
```

#### 1.3 检查防火墙是否放行 MCP Server 端口

如果 MCP Server 在麒麟目标机上运行但控制节点无法访问，通常是防火墙未放行端口。

**使用 firewalld（RHEL/Kylin 默认）：**

```bash
# 查看当前防火墙规则
sudo firewall-cmd --list-all

# 添加 MCP Server 端口（默认 8001）
sudo firewall-cmd --zone=public --add-port=8001/tcp --permanent

# 重载防火墙规则
sudo firewall-cmd --reload

# 验证端口已放行
sudo firewall-cmd --list-ports
```

**使用 ufw（Ubuntu/Debian）：**

```bash
sudo ufw allow 8001/tcp
sudo ufw reload
sudo ufw status
```

**使用 iptables（通用）：**

```bash
sudo iptables -A INPUT -p tcp --dport 8001 -j ACCEPT
sudo iptables-save > /etc/iptables/rules.v4  # Debian/Ubuntu
# 或
sudo service iptables save                     # RHEL/CentOS
```

#### 1.4 检查 MCP Server 是否正确安装

使用 systemd 检查 MCP Server 的运行状态：

```bash
# 查看服务状态（是否 active/running）
sudo systemctl status mcp-server

# 如果状态显示 inactive 或 failed，查看启动日志
sudo journalctl -u mcp-server -n 50 --no-pager

# 常见启动失败原因：
# - 端口被占用：检查是否有其他进程占用 8001 端口
#   sudo ss -tlnp | grep 8001
# - Python 依赖缺失：参考 1.1 节重新安装依赖
# - .env 文件格式异常：参考 1.2 节用 wizard.sh 重新配置
# - 权限不足：检查 /opt/mcp-server 目录属主是否为 agent-read
#   ls -la /opt/mcp-server/
```

如果服务未安装（`Unit mcp-server.service could not be found.`），需要重新运行安装脚本：

```bash
sudo ./deploy/mcp-server/install.sh
```

#### 1.5 检查 Backend 的 MCP 配置项

Backend 与 MCP Server 通信需要正确的配置和一致的 Token。

**检查 Backend 配置：**

```bash
# 查看 Backend 的 MCP 相关配置
grep -E 'MCP_' /opt/kylin-agent/backend/.env

# 应确保以下配置正确：
# MCP_MODE=real                           # 必须为 real（不是 mock）
# MCP_SERVER_URL=http://<麒麟目标机IP>:8001  # MCP Server 的实际地址
# MCP_AUTH_TOKEN=<your_token>              # 必须与 MCP Server 的 Token 一致
```

**检查 Token 是否一致：**

分别查看两边的 Token 配置：

```bash
# 在麒麟目标机上查看 MCP Server 的 Token
grep 'AUTH_TOKEN' /opt/mcp-server/.env

# 在控制节点上查看 Backend 的 Token
grep 'MCP_AUTH_TOKEN' /opt/kylin-agent/backend/.env
```

确保两边的 Token 值完全一致。修改任意一边后都需要重启对应服务。

**检查 dotenv 是否已安装且 `.env` 是否被正常读取：**

```bash
# 查看 MCP Server 日志，检查启动时是否成功加载 .env
sudo journalctl -u mcp-server -n 100 --no-pager | grep -i -E 'dotenv|env|config|token'

# 正常情况应看到类似输出：
# Loaded config from /opt/mcp-server/.env
# AUTH_TOKEN configured: yes

# 如果看到 'dotenv module not found' 错误，需安装 python-dotenv：
sudo /opt/mcp-server/venv/bin/pip install python-dotenv
sudo systemctl restart mcp-server
```

修改 Backend 配置后重启后端服务：

```bash
sudo systemctl restart kylin-agent
sudo journalctl -u kylin-agent -f  # 查看启动日志确认无配置错误
```

---

### 2. 指令不在白名单

#### 2.1 在前端检查并添加白名单

1. 登录前端管理界面（需要 ADMIN 权限）
2. 进入 **配置管理** / **白名单设置** 页面
3. 查看当前三级白名单（READ / OP / ADMIN）
4. 如果所需指令不在白名单中，点击添加按钮将该指令加入对应权限级别的白名单
5. 保存配置后立即生效

也可以通过 API 直接管理白名单：

```bash
# 查看当前白名单
curl -H "Authorization: Bearer <ADMIN_TOKEN>" \
  http://127.0.0.1:8000/api/config/whitelist

# 更新白名单（需要 ADMIN Token）
curl -X PUT -H "Authorization: Bearer <ADMIN_TOKEN>" \
  -H "Content-Type: application/json" \
  -d '{"level":"op", "commands":["systemctl status <service>","systemctl restart <service>"]}' \
  http://127.0.0.1:8000/api/config/whitelist
```

#### 2.2 检查 `/opt/mcp-server/whitelist_rules.json` 白名单文件

MCP Server 端也有本地的白名单配置文件，用于在目标机侧进行指令拦截验证。

```bash
# 在麒麟目标机上查看 whitelist_rules.json
cat /opt/mcp-server/whitelist_rules.json

# 如果所需指令不在文件中，编辑添加
sudo vim /opt/mcp-server/whitelist_rules.json

# 编辑后重启 MCP Server 使配置生效
sudo systemctl restart mcp-server
```

白名单格式参考（`whitelist_rules.json` 中的 `ALLOWED_COMMANDS` 列表）：

```json
{
  "ALLOWED_COMMANDS": [
    "systemctl status <service>",
    "systemctl start <service>",
    "systemctl stop <service>",
    "df -h",
    "free -m"
  ]
}
```

#### 2.3 检查执行的命令是否命中拦截项

MCP Server 的安全沙箱（`sandbox.py`）会拦截危险命令模式，即使指令在白名单中，以下特征也会被拦截：

- **Shell 元字符**：`;`、`&&`、`||`、`|`（管道）、`` ` ``（反引号）、`$()`、`${}`
- **危险命令关键字**：`rm -rf`、`mkfs`、`dd if=`、`> /dev/`（重定向覆盖）、`chmod 777`
- **路径遍历**：`../`、`/etc/passwd`、`/etc/shadow`
- **fork bomb**：`:(){ :|:& };:` 等递归函数模式

如果指令被拦截，建议：
1. 检查指令中是否包含上述危险特征
2. 将复合指令拆分为单步操作（避免使用 `&&` 或 `;` 串联）
3. 通过 LLM 对话自然语言描述需求，由系统生成安全可执行的动作步骤
4. 如需执行特例指令，联系管理员在 `sandbox.py` 的 `DANGER_PATTERNS` 中进行白名单例外配置

---

### 3. 前端无法访问到后端

#### 3.1 检查后端是否正确运行

**检查服务状态：**

```bash
# 查看 Backend systemd 服务状态
sudo systemctl status kylin-agent

# 如果服务未运行，启动它
sudo systemctl start kylin-agent
sudo systemctl enable kylin-agent  # 设置开机自启

# 查看启动日志排查错误
sudo journalctl -u kylin-agent -n 50 --no-pager
```

**检查端口是否在监听：**

```bash
# 确认 8000 端口是否被 Backend 占用
sudo ss -tlnp | grep 8000
# 期望输出：LISTEN 0 2048 127.0.0.1:8000 或 0.0.0.0:8000

# 直接测试 /health 端点
curl http://127.0.0.1:8000/health
# 期望返回：{"status":"ok","version":"0.1.0"}
```

**常见启动失败原因与解决：**

| 症状 | 原因 | 解决方法 |
|------|------|---------|
| `ImportError: No module named 'fastapi'` | Python 依赖未安装 | `cd /opt/kylin-agent/backend && sudo ./venv/bin/pip install -r requirements.txt` |
| `Address already in use` | 8000 端口被占用 | `sudo ss -tlnp \| grep 8000` 找到占用进程并终止 |
| `Connection refused`（Redis） | Redis 未运行 | `sudo apt install redis-server && sudo systemctl start redis`（或使用内置 fakeredis fallback） |
| `ModuleNotFoundError`（其他模块） | 部署文件不完整 | 重新运行 `sudo ./deploy/backend/install.sh` |

**后端监听端口占用排查：**

如果 Backend 启动后立即退出，且日志中出现 `Address already in use` 或端口无响应，说明监听端口可能被其他进程占用。

```bash
# 1. 查找占用 8000 端口的进程
sudo ss -tlnp | grep 8000
# 或使用 lsof
sudo lsof -i :8000

# 示例输出：
# LISTEN  0  2048  0.0.0.0:8000  0.0.0.0:*  users:(("python3",pid=12345,fd=5))
# → PID 为 12345 的 python3 进程占用了 8000 端口
```

如果占用进程不是 kylin-agent 服务（例如旧的僵尸进程、其他 Python 程序），需要手动终止：

```bash
# 2. 查看进程详细信息，确认可以安全终止
ps aux | grep <PID>

# 3. 终止占用进程
sudo kill -15 <PID>       # 优雅终止（首选）
# 如果进程无响应，使用强制终止
sudo kill -9 <PID>        # 强制终止（仅在必要时使用）

# 4. 再次确认端口已释放
sudo ss -tlnp | grep 8000
# 期望输出：空（端口不再被占用）

# 5. 重新启动 Backend
sudo systemctl restart kylin-agent
sudo systemctl status kylin-agent
```

如果端口仍被占用（可能是 systemd 管理的旧实例残留），彻底清理：

```bash
# 停掉所有相关进程
sudo systemctl stop kylin-agent
sudo fuser -k 8000/tcp   # 强制释放端口

# 确认端口干净
sudo ss -tlnp | grep 8000

# 重新启动
sudo systemctl start kylin-agent
```

如果希望修改 Backend 的监听端口（例如 8000 被其他核心服务占用），编辑配置：

```bash
# 编辑 Backend 的 .env 文件
sudo vim /opt/kylin-agent/backend/.env

# 修改监听端口（示例改为 8008）
# APP_HOST=127.0.0.1
# APP_PORT=8008

# 同时更新 Nginx 反向代理配置中的代理目标端口
sudo vim /etc/nginx/sites-available/kylin-agent
# 或
sudo vim /etc/nginx/conf.d/kylin-agent.conf
# 将 proxy_pass http://kylin_backend; 中 upstream 的端口同步修改

# 重载配置
sudo nginx -t && sudo systemctl reload nginx
sudo systemctl restart kylin-agent
```

**运行诊断工具进行全面检测：**

```bash
cd /opt/kylin-agent/backend
python diagnose.py --non-interactive
```

#### 3.2 检查后端的依赖项是否正确安装

```bash
# 切换到 Backend 安装目录
cd /opt/kylin-agent/backend

# 重新安装所有依赖
sudo ./venv/bin/pip install --upgrade pip
sudo ./venv/bin/pip install -r requirements.txt

# 验证核心依赖
sudo ./venv/bin/python -c "
import fastapi
import uvicorn
import websockets
import aiosqlite
print('fastapi:', fastapi.__version__)
print('uvicorn:', uvicorn.__version__)
print('所有依赖检查通过')
"

# 重启服务
sudo systemctl restart kylin-agent
```

#### 3.3 检查前端的连接设置

在前端界面中检查连接配置：

1. 打开前端页面，进入 **设置** / **连接设置**
2. 检查 **后端 API 地址** 是否正确：
   - 开发环境：通常为 `http://localhost:8000`（Vite 代理自动转发）
   - 生产环境：通常与前端同域，Nginx 将 `/api` 代理到后端
3. 检查 **WebSocket 地址** 是否正确：
   - 开发环境：`ws://localhost:8000/ws/chat/`
   - 生产环境：`wss://<你的域名>/ws/chat/`

也可以检查 Nginx 反向代理配置是否正确：

```bash
# 查看 Nginx 配置
cat /etc/nginx/sites-available/kylin-agent | grep -A5 'proxy_pass'
# 或
cat /etc/nginx/conf.d/kylin-agent.conf | grep -A5 'proxy_pass'

# 确认代理指向 127.0.0.1:8000（与 Backend 监听地址一致）
# 确认 WebSocket 升级配置包含 Upgrade 和 Connection 头
```

#### 3.4 检查系统代理是否影响连接

系统级 HTTP/HTTPS 代理可能导致前端到后端的请求被错误路由：

```bash
# 检查系统环境变量
env | grep -i proxy

# 检查是否有全局代理设置
cat /etc/environment | grep -i proxy

# 如果有代理配置，确保 127.0.0.1 和 localhost 在 NO_PROXY 中
# 例如：export NO_PROXY=localhost,127.0.0.1,.local

# 如果使用反向代理（Nginx），通常不需要额外配置
# 如果直接访问后端，确保代理不会干扰本地 loopback 请求
```

#### 3.5 检查防火墙是否放行相关端口

**前端端口（HTTPS 443）：**

```bash
# firewalld
sudo firewall-cmd --zone=public --add-service=https --permanent
sudo firewall-cmd --reload

# ufw
sudo ufw allow 443/tcp
sudo ufw reload

# 验证
sudo firewall-cmd --list-services  # firewalld
sudo ufw status                    # ufw
```

**后端端口（8000）通常不需要对外开放**，因为 Nginx 在本地通过 `127.0.0.1` 代理。仅在前端直接连接后端时需要放行：

```bash
# 一般情况下后端端口不需要开放到外部
# 后端绑定 127.0.0.1 仅本地访问
grep 'APP_HOST' /opt/kylin-agent/backend/.env
# 建议值：APP_HOST=127.0.0.1（仅本地访问，通过 Nginx 代理外部请求）
```

---

### 4. 执行指令返回 Permission Denied

#### 4.1 检查 sudoers 权限配置

MCP Server 在执行特权操作时，使用 `agent-read` 或 `agent-op` 用户通过 `sudo` 提权。如果 `/etc/sudoers.d/agent-op` 配置缺失或错误，将导致 `Permission denied` 错误。

```bash
# 在麒麟目标机上检查 sudoers 文件是否存在
sudo cat /etc/sudoers.d/agent-op

# 期望内容应包含：
# agent-read ALL=(root) NOPASSWD: /usr/bin/systemctl status *, /usr/bin/systemctl start *, ...
# agent-op ALL=(ALL) NOPASSWD: /usr/bin/systemctl status *, /usr/bin/systemctl start *, ...

# 如果文件不存在或内容不完整，重新安装 sudoers 配置
sudo cp deploy/mcp-server/agent-op.sudoers /etc/sudoers.d/agent-op
sudo chmod 440 /etc/sudoers.d/agent-op
sudo chown root:root /etc/sudoers.d/agent-op

# 验证 sudoers 语法是否正确（非常重要！错误的 sudoers 可能导致系统无法使用 sudo）
sudo visudo -c

# 测试 agent-read 用户是否可以使用 sudo
sudo -u agent-read sudo -n systemctl status mcp-server
```

#### 4.2 检查命令是否在 sudoers 白名单内

即使用户有 sudo 权限，也只能执行 sudoers 文件中明确允许的命令模式。sudoers 白名单通过正则表达式限制命令：

- 允许的服务名称匹配模式：`[a-zA-Z0-9@._-]*`（仅限字母数字和 `@._-`）
- 允许的命令：`systemctl status|start|stop|restart|reload <服务名>`
- 路径限制：必须使用 `/usr/bin/systemctl` 完整路径

如果执行的命令不在白名单内（如路径不同、参数不符），将返回权限拒绝。

#### 4.3 检查文件系统权限

MCP Server 执行文件操作时需要对应的文件系统权限：

```bash
# 检查 /opt/mcp-server 目录权限
ls -la /opt/mcp-server/
# agent-read 用户应对该目录有读取和执行权限

# 检查操作目标文件/目录权限
# 例如：如果命令需要读取 /var/log/syslog
ls -la /var/log/syslog
# agent-read 用户已加入 systemd-journal 组，具有日志读取权限

# 如果特定文件不可读，可以临时授权测试
# sudo setfacl -m u:agent-read:r /path/to/file  （需要 acl 包）
```

#### 4.4 检查沙箱（sandbox）限制

MCP Server 的 `sandbox.py` 在 sudo 层面之上还有一层白名单拦截。即使 sudoers 配置正确，如果命令不在沙箱的 `ALLOWED_COMMANDS` 列表中，也会被拦截并返回权限拒绝。

```bash
# 查看 MCP Server 日志中是否有 sandbox 拦截记录
sudo journalctl -u mcp-server -n 100 --no-pager | grep -i -E 'blocked|denied|whitelist|sandbox'

# 常见 sandbox 拦截原因：
# - 命令不在 ALLOWED_COMMANDS 中（需要添加白名单，参考 2.2 节）
# - 命令包含危险模式（shell 元字符、管道、重定向等，参考 2.3 节）
# - cgroups 资源限制触发（CPU/内存/PID 超限，检查 /opt/mcp-server/.env 中 CGROUP_* 配置）
```

#### 4.5 检查执行用户的资源限制

Linux 系统级别的资源限制（ulimit）也可能导致 Permission denied 或 Operation not permitted：

```bash
# 查看 agent-read 用户的资源限制
sudo -u agent-read ulimit -a

# 如果遇到 "fork: retry: Resource temporarily unavailable"
# 检查当前进程数和系统限制
ps aux | grep agent-read | wc -l
cat /proc/sys/kernel/pid_max

# 调整 cgroups 限制（如果启用了 cgroups 资源隔离）
grep 'CGROUP_' /opt/mcp-server/.env
# CGROUP_ENABLED=true
# CGROUP_CPU_LIMIT=50          # CPU 使用率上限（%）
# CGROUP_MEMORY_LIMIT=256      # 内存上限（MB）
# CGROUP_MAX_PIDS=64           # 最大进程数

# 如果操作需要更多资源，适当调整限制后重启 MCP Server
sudo systemctl restart mcp-server
```

---

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
