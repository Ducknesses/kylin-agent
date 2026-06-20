# 后端现有代码框架通读报告

> 生成时间：2026-06-16
> 分析范围：`backend/` 全部 Python 源码
> 状态：只读分析，未做任何修改

---

## 1. 项目整体判断

**结论：当前项目已是一个可运行的 FastAPI 后端框架，且具备 Mock 模式运行能力。**

| 维度 | 判断 |
| --- | --- |
| 是否 FastAPI 项目 | 是。`backend/app/main.py` 包含完整的 `FastAPI()` 实例化、路由注册、CORS、lifespan 生命周期管理。 |
| 入口文件 | `backend/run.py` → `uvicorn app.main:app` |
| 启动方式 | `python run.py` 或 `uvicorn app.main:app --host 0.0.0.0 --port 8000` |
| 最低可运行状态 | 在未配置 `DEEPSEEK_API_KEY` / Redis / MCP Server 的情况下，`/health` 和审计查询等不依赖外部服务的接口可正常返回；聊天接口会返回 "API Key 未配置" 错误消息而非崩溃。 |
| Mock 能力 | 已具备：Redis 有 fakeredis fallback；monitor 接口使用硬编码 mock 数据；chat 可在无 LLM Key 时返回友好错误。 |

---

## 2. 当前目录结构说明

```
backend/
├── run.py                  # 启动脚本
├── config.py               # 全局配置类 Settings
├── requirements.txt        # Python 依赖
├── data/
│   └── audit.db            # SQLite 审计数据库（已存在，20 KiB）
├── docs/                   # 文档目录（本报告所在）
└── app/
    ├── __init__.py
    ├── main.py             # FastAPI 入口，路由注册，lifespan
    ├── api/                # REST + WebSocket 接口层
    ├── audit/              # 审计日志模块
    ├── core/               # 安全护栏 + RBAC + Redis 客户端
    ├── llm/                # DeepSeek LLM 客户端 + 意图路由
    ├── mcp/                # MCP Client + Executor + Tool 定义
    └── schemas/            # Pydantic 请求/响应模型
```

### 2.1 `backend/app/api/` — REST & WebSocket 接口层

| 文件 | 职责 |
| --- | --- |
| `chat.py` | WebSocket `/ws/chat/{session_id}` 核心聊天流程：接收消息 → 安全检测 → LLM 流式回复 → 审计记录。阶段 1 已实现完整链。 |
| `sessions.py` | 会话管理 REST：`GET /api/sessions`、`POST /api/sessions`，数据存储在 Redis（带 fakeredis fallback）。 |
| `monitor.py` | SSE 流式系统指标 `/api/monitor/stream`，阶段 1 使用硬编码 mock 数据，每 3 秒推送。 |
| `audit.py` | 审计日志查询 `GET /api/audit`，支持分页参数 `limit`/`offset`。 |
| `config.py` | 白名单查看 `GET /api/config/whitelist` 和更新 `PUT /api/config/whitelist`，PUT 为阶段 2 预留接口。 |

### 2.2 `backend/app/audit/` — 审计模块

| 文件 | 职责 |
| --- | --- |
| `models.py` | SQLite 表定义（`audit_chain`）+ `init_db()` 初始化 + `_compute_hash()` / `get_last_hash()` 哈希链防篡改。 |
| `logger.py` | `log_chain()` 写入审计记录；`query_audit()` 分页查询。 |

### 2.3 `backend/app/core/` — 核心安全与状态

| 文件 | 职责 |
| --- | --- |
| `security.py` | `risk_classify()` 三层安全检测：长度 → 高危正则黑名单 → Prompt Injection → 中危关键词，返回 reject/confirm/allow。 |
| `prompt_guard.py` | `detect_injection()` 五层注入检测：控制字符 → Unicode 混淆 → 重复字符 → 关键词 → 语义边界；`sanitize_input()` 输入净化。 |
| `rbac.py` | `COMMAND_WHITELIST` 三级权限白名单（READ/OP/ADMIN）+ `check_command_permission()` 命令校验 + `get_user_level()`（阶段 1 固定返回 READ）。 |
| `redis_client.py` | Redis 客户端封装：真实 Redis 优先，连接失败自动 fallback 到 fakeredis。提供 session CRUD。 |

### 2.4 `backend/app/llm/` — LLM 模块

| 文件 | 职责 |
| --- | --- |
| `deepseek.py` | `chat_with_llm()` DeepSeek API 流式/非流式调用 + `analyze_root_cause()` 根因分析。包含 SYSTEM_PROMPT、超时、异常处理。 |
| `router.py` | `parse_intent()` 意图解析（JSON 输出）+ `route_request()` 总路由（启发式判断根因分析 vs 工具调用）。 |

### 2.5 `backend/app/mcp/` — MCP 模块

| 文件 | 职责 |
| --- | --- |
| `client.py` | `MCPClient` 类：JSON-RPC 2.0 客户端，`call_tool()` + `get_system_metrics()` + `list_tools()`，含超时和连接异常处理。 |
| `executor.py` | `Executor` 类：工具名白名单校验 → `cmd_exec` 额外 RBAC 校验 → 调用 MCPClient。 |
| `tools.py` | `TOOL_DEFINITIONS` 字典（6 个工具定义 + JSON Schema）+ `get_tool_names()` / `get_tool_schema()` 辅助函数。 |

### 2.6 `backend/app/schemas/` — Pydantic 数据模型

| 文件 | 职责 |
| --- | --- |
| `models.py` | 全部请求/响应 Pydantic 模型：`SessionCreate`, `SessionOut`, `ChatMessage`, `AuditRecordOut`, `WhitelistUpdate`, `SystemMetrics`。 |

### 2.7 `backend/data/audit.db` — SQLite 数据库文件

| 属性 | 值 |
| --- | --- |
| 文件大小 | 20 KiB（已存在，含历史数据） |
| 表结构 | `audit_chain`（13 列 + 2 索引） |
| 风险 | 1. **已入库 Git**（`.gitignore` 仅有 `*.pyc`，未排除 `*.db`）；2. 每次启动 `init_db()` 使用 `CREATE TABLE IF NOT EXISTS` 不会重置，但若 schema 变更需手动迁移。 |

---

## 3. 文件级功能说明

### 3.1 `backend/run.py`

- **功能**：uvicorn 启动脚本，读取 `config.settings` 配置 host/port/reload/log_level。
- **状态**：完整实现。
- **依赖**：`uvicorn`，`config.settings`。
- **风险**：无。

### 3.2 `backend/config.py`

- **功能**：`Settings` 类，所有配置项从环境变量读取并提供默认值。单例 `settings = Settings()` 供全项目使用。
- **状态**：完整实现，覆盖了当前所需的全部配置项。
- **依赖**：`os`，`typing.Optional`（未使用，可清理）。
- **风险**：`Optional` import 未使用（无功能影响）。

### 3.3 `backend/requirements.txt`

- **功能**：Python 依赖声明。
- **状态**：完整。包含 fastapi、uvicorn[standard]、redis、aiosqlite、httpx、pydantic、python-multipart。
- **风险**：`fakeredis` 未列入依赖清单（代码中有 fallback 逻辑，若真实 Redis 不可用且 fakeredis 未安装会 `raise`，见 [问题-3](#问题-3)）。

### 3.4 `backend/app/main.py`

- **功能**：FastAPI 应用工厂。lifespan 中初始化 SQLite；注册 CORS（全开）；挂载 5 个 router；定义 `/health`。
- **状态**：完整实现，可直接运行。
- **依赖**：`app.api.*`，`app.audit.models.init_db`，`config.settings`，`fastapi`。
- **风险**：1. CORS `allow_origins=["*"]` 仅适合开发阶段，注释已标注；2. `chat.router` 挂载在 `/ws` 前缀下，但 `chat.py` 内的路由是 `/chat/{session_id}` → 实际路径为 `/ws/chat/{session_id}`。

### 3.5 `backend/app/api/chat.py`

- **功能**：WebSocket `/ws/chat/{session_id}`。接收 JSON `{"content": "..."}` → `risk_classify()` → 高危/中危/低危三条分支 → LLM 流式回复 → `log_chain()` 审计。
- **状态**：**部分实现**。阶段 1 实现了完整的风险检测 + LLM 流式 + 审计链，但：
  - 中危路径未实现 "等待 confirm" 交互逻辑（直接返回提示）；
  - 未实际调用 MCP Executor（注释写明 "阶段2接入"）；
  - 连接管理为内存 `Dict`，未接入 Redis Pub/Sub。
- **依赖**：`app.core.security.risk_classify`，`app.llm.deepseek.chat_with_llm`，`app.audit.logger.log_chain`，`app.mcp.executor.Executor`（已导入但阶段1未使用）。
- **风险**：`Executor` 已 import 但未使用 → 无功能影响，但可能触发 linter 警告。

### 3.6 `backend/app/api/sessions.py`

- **功能**：`GET /api/sessions`（列出所有会话）、`POST /api/sessions`（创建会话）。
- **状态**：完整实现。session 数据写入 Redis（带 fakeredis fallback），过期时间 3600s。
- **依赖**：`app.core.redis_client`，`app.schemas.models.SessionCreate/SessionOut`。
- **风险**：无 DELETE/PUT 接口（但不属于 Day 1 范围）。

### 3.7 `backend/app/api/monitor.py`

- **功能**：`GET /api/monitor/stream` SSE 流式推送。阶段 1 使用硬编码 mock 数据；代码中保留了注释掉的真实 MCP 调用。
- **状态**：**部分实现（Mock 可用）**。真实 MCP 数据通路已预留注释，切换只需取消注释。
- **依赖**：`app.mcp.client.MCPClient`（仅 import，阶段1未使用）。
- **风险**：无。

### 3.8 `backend/app/api/audit.py`

- **功能**：`GET /api/audit?limit=50&offset=0` 分页查询审计日志，返回 `List[AuditRecordOut]`。
- **状态**：完整实现。
- **依赖**：`app.audit.logger.query_audit`，`app.schemas.models.AuditRecordOut`。
- **风险**：`List` 从 typing 导入但未在路由装饰器中使用（FastAPI 自动处理，无功能影响）。

### 3.9 `backend/app/api/config.py`

- **功能**：`GET /api/config/whitelist`（读取三级白名单）、`PUT /api/config/whitelist`（阶段 2 预留，当前仅返回提示不实际修改）。
- **状态**：GET 完整实现；PUT 为占位。
- **依赖**：`app.core.rbac.COMMAND_WHITELIST`，`app.schemas.models.WhitelistUpdate`。
- **风险**：无。

### 3.10 `backend/app/schemas/models.py`

- **功能**：6 个 Pydantic v2 BaseModel：`SessionCreate`, `SessionOut`, `ChatMessage`, `AuditRecordOut`, `WhitelistUpdate`, `SystemMetrics`。
- **状态**：完整实现。字段与 API 返回一一对应。
- **依赖**：`pydantic`。
- **风险**：`datetime` import 未直接使用（仅在类型注解中用到 `Optional`、`List`，无功能影响）。

### 3.11 `backend/app/audit/models.py`

- **功能**：SQLite 表定义（`audit_chain` 含 13 字段 + 哈希链防篡改）、`init_db()` 初始化、`_compute_hash()` SHA256 链式哈希、`get_last_hash()`。
- **状态**：完整实现。
- **依赖**：`aiosqlite`，`config.settings`。
- **风险**：无。

### 3.12 `backend/app/audit/logger.py`

- **功能**：`log_chain()` 写入审计记录（全字段）、`query_audit()` 分页查询。
- **状态**：完整实现。
- **依赖**：`app.audit.models._compute_hash/get_last_hash`，`aiosqlite`，`config.settings`。
- **风险**：`datetime` import 仅在 `log_chain` 内用于生成时间戳，已使用；无风险。

### 3.13 `backend/app/core/security.py`

- **功能**：`risk_classify()` 四层安全检测：长度(2000) → 高危正则(6个) → Prompt Injection → 中危关键词(9个)。返回 `{level, reason, action}`。
- **状态**：完整实现。
- **依赖**：`config.settings.MAX_INPUT_LENGTH`，`app.core.prompt_guard.detect_injection`。
- **风险**：见 [问题-1](#问题-1)（检测顺序可能导致 Prompt Injection 检测被中危关键词屏蔽）和 [问题-2](#问题-2)（中危关键词列表缺少命令链检测）。

### 3.14 `backend/app/core/prompt_guard.py`

- **功能**：`detect_injection()` 五层注入检测 + `sanitize_input()` 输入净化。
- **状态**：完整实现。
- **依赖**：`re`，`logging`。
- **风险**：关键词列表偏静态，无法防御变体攻击（如"忽 略 之 前 指 令"）。属于已知局限，非当前阶段问题。

### 3.15 `backend/app/core/rbac.py`

- **功能**：三级权限白名单（READ 13 条、OP 2 条、ADMIN 4 条）+ 危险模式拦截（`;`, `&&`, `|`, `` ` ``, `$(` 等 9 个）+ `check_command_permission()` + `get_user_level()` 阶段 1 固定返回 READ。
- **状态**：完整实现（权限模型完整，实际用户认证待接入）。
- **依赖**：`re`，`logging`。
- **风险**：`get_user_level()` 固定返回 `READ` → 所有用户视作最低权限，OP/ADMIN 白名单条目当前不可达。属于阶段 1 设计意图，非 bug。

### 3.16 `backend/app/core/redis_client.py`

- **功能**：Redis 客户端封装，真实 Redis 优先 → 连接失败自动 fallback 到 fakeredis。提供 `set_session`/`get_session`/`list_sessions`/`delete_session`。
- **状态**：完整实现。
- **依赖**：`redis`（真实），`fakeredis`（fallback），`config.settings.REDIS_URL`。
- **风险**：fakeredis 未在 `requirements.txt` 中声明，可能导致 fallback 时 `ImportError` 抛出而非优雅降级。

### 3.17 `backend/app/llm/deepseek.py`

- **功能**：`chat_with_llm()` 流式/非流式调用 DeepSeek Chat API + `analyze_root_cause()` 根因分析专用接口。含 SYSTEM_PROMPT、timeout(30s connect 10s)、HTTP 异常处理、API Key 缺失友好提示。
- **状态**：完整实现。
- **依赖**：`httpx`，`config.settings`。
- **风险**：无。

### 3.18 `backend/app/llm/router.py`

- **功能**：`parse_intent()` 调用 LLM 做意图分类（JSON 输出）→ `route_request()` 启发式 + LLM 双路由。
- **状态**：完整实现。
- **依赖**：`app.llm.deepseek.chat_with_llm/analyze_root_cause`。
- **风险**：`route_request()` 的启发式关键词硬编码为中文，英文运维场景需补充。

### 3.19 `backend/app/mcp/client.py`

- **功能**：`MCPClient` 类：JSON-RPC 2.0 协议、自增 request id、`call_tool()` / `get_system_metrics()` / `list_tools()`。超时时间 `COMMAND_TIMEOUT + 5s`、connect 5s。
- **状态**：完整实现（客户端层）。
- **依赖**：`httpx`，`config.settings`。
- **风险**：**无 Bearer Token**（Authorization header 未设置），若 MCP Server 要求认证将失败。见 [问题-4](#问题-4)。

### 3.20 `backend/app/mcp/executor.py`

- **功能**：`Executor` 类：工具名白名单校验（`get_tool_names()`）→ `cmd_exec` 额外 RBAC 校验（`check_command_permission()`）→ 转发 `MCPClient.call_tool()`。
- **状态**：完整实现。但阶段 1 的 WebSocket chat 流程中未实际调用（仅 import）。
- **依赖**：`app.core.rbac`，`app.mcp.client.MCPClient`，`app.mcp.tools.get_tool_names`。
- **风险**：无独立风险；依赖链完整。

### 3.21 `backend/app/mcp/tools.py`

- **功能**：6 个 MCP 工具定义（`sys_info`, `service_mgr`, `log_reader`, `net_monitor`, `cmd_exec`, `file_guard`）含 JSON Schema + 辅助函数。
- **状态**：完整实现。
- **依赖**：无外部依赖。
- **风险**：`TOOL_COMMAND_TYPES` 映射定义在 `executor.py` 而非 `tools.py`，存在职责分散 —— 工具元数据分两个文件维护，未来可能不一致。见 [问题-5](#问题-5)。

---

## 4. 与目标架构的模块映射

| 目标设计模块 | 当前可能对应文件 | 状态 | 说明 |
| --- | --- | --- | --- |
| FastAPI main.py | `backend/app/main.py` | 已实现 | 完整入口：lifespan、CORS、路由注册、/health |
| REST API | `backend/app/api/*.py`（chat 除外） | 已实现 | sessions、monitor、audit、config 四个 REST router |
| WebSocket chat | `backend/app/api/chat.py` | 部分实现 | 基础链路完整；中危确认交互、MCP 调用待阶段 2 |
| Pydantic Schemas | `backend/app/schemas/models.py` | 已实现 | 6 个 BaseModel 覆盖当前全部接口 |
| Config | `backend/config.py` | 已实现 | 环境变量驱动的 Settings 类 |
| Logging | `backend/app/audit/logger.py` + Python logging | 已实现 | 结构化审计日志 + 标准 logging |
| SafetyGuard | `backend/app/core/security.py` | 已实现 | `risk_classify()` 三层检测 |
| Prompt Guard | `backend/app/core/prompt_guard.py` | 已实现 | `detect_injection()` 五层检测 + `sanitize_input()` |
| RBAC | `backend/app/core/rbac.py` | 已实现 | 三级权限白名单 + 危险模式拦截 |
| Redis Session | `backend/app/core/redis_client.py` | 已实现 | Redis + fakeredis fallback |
| MCPClient | `backend/app/mcp/client.py` | 已实现 | JSON-RPC 2.0 客户端 |
| ToolRegistry | `backend/app/mcp/tools.py` | 已实现 | 6 个工具定义 + get_tool_names/schema |
| AgentHarness | — | 缺失 | 目标架构中负责编排 LLM→Tool→Response 循环的模块未创建。当前 chat.py 中内联了简化版流程，但未抽象为独立 Harness。 |
| Orchestrator | `backend/app/llm/router.py`（部分） | 部分实现 | `route_request()` 提供启发式路由，但缺少完整的 LLM→Execute→Observe→Respond 循环编排 |
| LLM Client | `backend/app/llm/deepseek.py` | 已实现 | DeepSeek Chat API 流式/非流式 + 根因分析 |
| AuditService | `backend/app/audit/logger.py` + `models.py` | 已实现 | `log_chain()` 写入 + `query_audit()` 查询 + 哈希链防篡改 |
| Audit Models | `backend/app/audit/models.py` | 已实现 | `audit_chain` 表 + `init_db()` |
| Mock REST | `backend/app/api/monitor.py`（hardcoded 数据）+ `redis_client.py`（fakeredis）+ `chat.py`（API Key 缺失时返回错误） | 部分实现 | 各模块分散实现 Mock fallback，未统一抽象 |
| Tests | — | 缺失 | 项目无 `tests/` 目录 |
| Docs | `backend/docs/`（本报告） | 占位 | 仅包含本结构审查报告 |

---

## 5. 当前已有接口盘点

| 方法 | 路径 | 文件 | 返回格式 | Mock | 与前端约定 |
| --- | --- | --- | --- | --- | --- |
| `GET` | `/health` | `main.py:48` | `{"status":"ok","version":"0.1.0"}` | 否（始终可用） | 标准健康检查 |
| `WS` | `/ws/chat/{session_id}` | `chat.py:24` | JSON `{type, content, trace_id}` | 部分（API Key 缺失时返回错误不崩溃） | type: chunk/done/error/risk_alert/status |
| `GET` | `/api/monitor/stream` | `monitor.py:31` | SSE `data: {cpu_percent, load_avg, memory_percent, disk_percent, timestamp}` | **是**（硬编码数据） | SSE 流 |
| `GET` | `/api/config/whitelist` | `config.py:14` | `{"permissions": {"agent-read": [...], "agent-op": [...], "agent-admin": [...]}}` | 否（读内存常量） | 待确认 |
| `PUT` | `/api/config/whitelist` | `config.py:22` | `{"message":"...","current_count":N}` | 是（不实际修改） | 占位接口 |
| `GET` | `/api/audit?limit=&offset=` | `audit.py:15` | `[{trace_id, timestamp, user_input, intent, risk_level, mcp_tool, command, raw_output, llm_reasoning, final_response}]` | 否（读 SQLite） | 待确认 |
| `GET` | `/api/sessions` | `sessions.py:16` | `[{id, title, created_at}]` | 部分（fakeredis 可独立运行） | 待确认 |
| `POST` | `/api/sessions` | `sessions.py:27` | `{id, title, created_at}` | 部分（fakeredis 可独立运行） | 待确认 |

**接口完整性检查结果：**

| 预期接口 | 状态 |
| --- | --- |
| `GET /health` | ✅ 已实现 |
| `WebSocket /ws/chat` | ✅ 已实现（阶段 1） |
| `GET /api/monitor/metrics` | ⚠️ 命名不一致：实际为 `/api/monitor/stream`（SSE），非 `/api/monitor/metrics` |
| `GET /api/config/whitelist` | ✅ 已实现 |
| `GET /api/audit/logs` | ⚠️ 命名不一致：实际为 `/api/audit`，非 `/api/audit/logs` |
| session 相关接口 | ✅ `GET /api/sessions` + `POST /api/sessions` |

---

## 6. 当前安全链路判断

| 安全能力 | 状态 | 说明 |
| --- | --- | --- |
| 高危命令拦截 | ✅ 已实现 | `security.py` 6 个高危正则（rm -rf /、mkfs、dd、覆盖 /etc/passwd、fork bomb、chmod -R 777 /） |
| Prompt 注入检测 | ✅ 已实现 | `prompt_guard.py` 5 层检测（控制字符、零宽字符、重复字符、14 个关键词、语义边界） |
| RBAC viewer/operator/admin | ✅ 已实现 | `rbac.py` 三级白名单 + `check_command_permission()`；但 `get_user_level()` 阶段 1 固定返回 READ |
| MCP 工具调用前的安全检查 | ✅ 已实现 | `executor.py` 先校验工具名白名单 → `cmd_exec` 做 RBAC 校验 → 调用 MCPClient |
| LLM 是否可能绕过安全模块 | ⚠️ 存在风险 | 当前 `chat.py` 流程：`risk_classify()` → 低危时直接 `chat_with_llm()` 流式返回。**LLM 响应用户的文本未经安全模块回检**——若 LLM 被注入攻击诱导生成包含系统命令的输出文本，前端展示后用户可能被误导。注意：LLM 不直接执行命令（由 MCP Server 执行），此风险为输出内容风险，非执行风险。 |
| 是否存在直接执行系统命令的风险 | ⚠️ 存在风险 | `mcp/tools.py` 定义了 `cmd_exec` 工具，该工具由 MCP Server（C 端）负责实际执行。当前 B 端通过 `executor.py` → `MCPClient.call_tool()` 调用 C 端，**B 端自身不执行系统命令**。但 `cmd_exec` 工具的存在意味着：一旦 MCPClient 连接成功且 RBAC 校验通过，命令将被发送到 C 端执行。这符合设计（B 是调度层），但需确保 C 端也有对应的安全校验。 |

**风险记录（详见第 9 节问题清单）：**

1. **[问题-1]** `security.py` 检测顺序：Prompt Injection 在正则黑名单之后但在中危关键词之前，可能导致注入被中危逻辑降级处理。
2. **[问题-2]** `security.py` 中危关键词列表不含命令链特征（`;`, `&&`, `|`），这些由 `rbac.py` 拦截，但两处职责边界需明确。

---

## 7. 当前 MCP 链路判断

| 能力 | 状态 | 说明 |
| --- | --- | --- |
| 已有 MCP Client | ✅ 是 | `mcp/client.py` `MCPClient` 类 |
| 支持 Mock 模式 | ⚠️ 部分 | `monitor.py` 中硬编码了 mock 数据，但 `MCPClient` 本身没有内置 mock 模式——调用会真实请求 `MCP_SERVER_URL`，若 C 端未就绪会抛 `ConnectError` |
| 支持 JSON-RPC 2.0 | ✅ 是 | `"jsonrpc": "2.0"` + 自增 `id` + `method` + `params` |
| 有 Bearer Token | ❌ 否 | `MCPClient` 的 HTTP 请求未设置 `Authorization` header |
| 有 timeout 和异常处理 | ✅ 是 | `httpx.Timeout(COMMAND_TIMEOUT+5s, connect=5s)` + `TimeoutException`/`ConnectError`/通用 `Exception` |
| 是否可能直接执行命令 | ❌ B 端不直接执行 | 命令通过 JSON-RPC 发送到 C 端（MCP Server），B 端仅做校验和转发 |
| `mcp/executor.py` 职责 | 命令调度器 | 工具白名单校验 → RBAC 权限校验 → 转发 MCPClient |
| `mcp/tools.py` 职责 | 工具注册表 | 6 个工具定义 + JSON Schema + 查询辅助函数 |

---

## 8. 当前审计链路判断

| 审计能力 | 状态 | 说明 |
| --- | --- | --- |
| `audit.db` 是否被代码使用 | ✅ 是 | `audit/models.py` 的 `init_db()` 创建表；`audit/logger.py` 通过 `aiosqlite` 读写 |
| `audit/logger.py` 负责写入审计 | ✅ 是 | `log_chain()` 写入全部字段 |
| `audit/models.py` 定义审计结构 | ✅ 是 | `audit_chain` 表 13 列 |
| 记录 `trace_id` | ✅ 是 | `chat.py` 中 `str(uuid.uuid4())[:16]` |
| 记录 `session_id` | ⚠️ 否 | `log_chain()` 参数列表和表结构均无 `session_id` 字段。`trace_id` 每次请求生成，不与 session 关联。 |
| 记录 `user_input` | ✅ 是 | 必填字段 |
| 记录 `intent` | ✅ 是 | 可选字段，当前 chat.py 调用时未传入（`intent=None`） |
| 记录 `risk_level` | ✅ 是 | 必填字段 |
| 记录 `tool_call` | ✅ 是 | `mcp_tool` + `command` 两个字段 |
| 记录 `final_response` | ✅ 是 | 可选字段 |
| **是否存在记录原始模型思维链的风险** | ⚠️ 存在字段但阶段 1 未使用 | `llm_reasoning` 字段已定义，注释为 "LLM 推理过程"，当前 `chat.py` 调用 `log_chain()` 时未传入此参数（默认 `None`）。该字段的存在本身是设计意图的体现——如果未来填入 LLM 原始输出，则与 "不记录模型原始思维链" 的设计要求冲突。**当前实际未记录**，但字段定义未加限制。 |
| 哈希链防篡改 | ✅ 是 | `_compute_hash()` 串联 `prev_hash` 形成 SHA256 链 |

---

## 9. 发现的问题清单

### 问题-1：安全检测顺序可能导致 Prompt Injection 降级处理

- **编号**：ISSUE-001
- **描述**：`security.py` `risk_classify()` 中 Prompt Injection 检测（`detect_injection`）置为高危并 `action=reject`，但在它之后的中危关键词检测（`_check_keywords`）可能匹配到包含中危关键词的注入文本，导致原本应 `reject` 的注入被降级为 `confirm`。**实际代码流程是顺序 `return`，先到先得**——Prompt Injection 在关键词之前，所以此问题**不成立**。代码正确。
- **产生原因**：初读时对顺序 return 语义的误判。
- **影响范围**：无。代码逻辑正确：黑名单 (reject) → Prompt Injection (reject) → 中危关键词 (confirm) → 低危 (allow)。
- **建议解决方案**：无需修复。当前顺序合理。
- **当前是否已修复**：N/A（非问题）
- **当前是否验收成功**：N/A

### 问题-2：中危关键词列表与 RBAC 危险模式存在职责重叠

- **编号**：ISSUE-002
- **描述**：`security.py` 的 `MEDIUM_RISK_KEYWORDS` 包含 `chmod 777`、`kill -9` 等；`rbac.py` 的 `DANGEROUS_PATTERNS` 包含 `;`, `&&`, `|`, `` ` ``, `$(` 等。两条拦截链独立运行：`risk_classify()` 在输入阶段拦截中危关键词 → `check_command_permission()` 在命令执行阶段拦截危险模式。当前两条链互补且不重复，但缺乏显式文档说明各自的职责边界。
- **产生原因**：两个模块独立开发，自然形成了输入层 + 执行层双层防护。
- **影响范围**：低。双层防护是合理设计，但需文档化。
- **建议解决方案**：在 `security.py` 和 `rbac.py` 的 docstring 中注明职责边界：`security.py` = 输入层（用户意图判断），`rbac.py` = 执行层（命令级白名单）。
- **当前是否已修复**：否
- **当前是否验收成功**：否

### 问题-3：fakeredis 未列入 requirements.txt

- **编号**：ISSUE-003
- **描述**：`redis_client.py` 在真实 Redis 连接失败时 `import fakeredis` 做 fallback。但 `requirements.txt` 中未声明 `fakeredis` 依赖。若环境无 Redis 且未手动安装 fakeredis，首次调用 Redis 操作会抛出 `ImportError`（代码中 `raise` 而非捕获）。
- **产生原因**：fakeredis 作为可选 fallback 被遗漏。
- **影响范围**：无 Redis 环境下的 sessions 接口将不可用。
- **建议解决方案**：将 `fakeredis>=2.0.0` 加入 `requirements.txt`。
- **当前是否已修复**：否
- **当前是否验收成功**：否

### 问题-4：MCPClient 缺少 Bearer Token 认证

- **编号**：ISSUE-004
- **描述**：`mcp/client.py` `MCPClient` 的所有 HTTP 请求未设置 `Authorization` header。目标架构要求 MCP 通信使用 Bearer Token；若 C 端 MCP Server 已实现 Token 校验，B 端所有 MCP 调用将返回 401。
- **产生原因**：阶段 1 未接入真实 MCP Server，认证待阶段 2 实现。
- **影响范围**：连接真实 MCP Server 时全部 `call_tool()` 失败。
- **建议解决方案**：在 `config.py` 添加 `MCP_AUTH_TOKEN` 环境变量 → `MCPClient.__init__` 接受 token 参数 → 请求时设置 `Authorization: Bearer {token}`。
- **当前是否已修复**：否
- **当前是否验收成功**：否

### 问题-5：工具元数据分散在两个文件

- **编号**：ISSUE-005
- **描述**：工具定义（名称、描述、参数 Schema）在 `mcp/tools.py` 的 `TOOL_DEFINITIONS`；工具到命令类型的映射（`TOOL_COMMAND_TYPES`）在 `mcp/executor.py`。未来新增工具需同时修改两个文件，容易遗漏导致不一致。
- **产生原因**：执行器的命令类型分类逻辑与工具定义分开编写。
- **影响范围**：低。当前 6 个工具映射正确；未来扩展时可能产生不一致。
- **建议解决方案**：将 `TOOL_COMMAND_TYPES` 移至 `mcp/tools.py`，或将其合并到 `TOOL_DEFINITIONS` 的每个工具条目中（增加 `category` 字段）。
- **当前是否已修复**：否
- **当前是否验收成功**：否

### 问题-6：audit.db 已入库 Git 仓库

- **编号**：ISSUE-006
- **描述**：`backend/data/audit.db` (20 KiB) 已存在于工作目录中。项目 `.gitignore` 仅包含 `*.pyc`，未排除 `*.db`。若该文件已被 `git add` 或 commit，二进制数据库文件将污染 Git 历史。
- **产生原因**：`.gitignore` 未覆盖 SQLite 数据库文件。
- **影响范围**：Git 仓库体积、diff 可读性、多环境部署时数据库冲突。
- **建议解决方案**：在 `.gitignore` 中添加 `*.db` 和 `*.db-journal` / `*.db-wal`；若已入库，执行 `git rm --cached backend/data/audit.db` 移除跟踪。
- **当前是否已修复**：否
- **当前是否验收成功**：否

### 问题-7：审计表缺少 session_id 字段

- **编号**：ISSUE-007
- **描述**：`audit_chain` 表定义和 `log_chain()` 函数均无 `session_id` 字段。当前 `chat.py` 的 WebSocket 路由包含 `session_id` 参数，但审计记录中仅保存 `trace_id`（每次请求生成）。这意味着无法通过审计日志回溯某次会话的完整操作链。
- **产生原因**：`log_chain()` 签名未包含 `session_id`。
- **影响范围**：审计日志的会话维度查询能力缺失。
- **建议解决方案**：1. `audit_chain` 表增加 `session_id TEXT` 列；2. `log_chain()` 增加 `session_id` 参数；3. `chat.py` 调用时传入 `session_id`。
- **当前是否已修复**：否
- **当前是否验收成功**：否

### 问题-8：`config.py` 中 `Optional` import 未使用

- **编号**：ISSUE-008
- **描述**：`backend/config.py` 第 3 行 `from typing import Optional`，但文件中未使用 `Optional` 类型注解。
- **产生原因**：开发过程中预留类型注解后未清理。
- **影响范围**：极低。不影响功能，仅 linter 可能警告。
- **建议解决方案**：移除 `Optional` import。
- **当前是否已修复**：否
- **当前是否验收成功**：否

---

## 10. Day 1 范围内的下一步建议

按优先级排序：

| 优先级 | 建议 | 关联问题 |
| --- | --- | --- |
| P0 | 在 `.gitignore` 中添加 `*.db` / `*.db-journal` / `*.db-wal`，并从 Git 跟踪中移除 `backend/data/audit.db` | ISSUE-006 |
| P0 | 将 `fakeredis` 加入 `requirements.txt` | ISSUE-003 |
| P1 | 确认前端接口约定：`/api/monitor/metrics` vs `/api/monitor/stream`、`/api/audit/logs` vs `/api/audit`——若前端已按当前路径开发则无需变更，记录即可 | 接口盘点 |
| P1 | 确认 `config.py` 中所有环境变量的默认值是否符合开发/测试环境需求（尤其是 `MCP_SERVER_URL=http://192.168.56.101:8001`） | — |
| P1 | 确认 `requirements.txt` 是否需要锁定版本（当前使用 `>=` 下限，可能导致跨环境不一致） | — |
| P2 | 清理 `backend/config.py` 中未使用的 `Optional` import | ISSUE-008 |
| P2 | 在 `security.py` 和 `rbac.py` 顶部 docstring 中注明各自的职责边界 | ISSUE-002 |
| P2 | 考虑将 `TOOL_COMMAND_TYPES` 从 `executor.py` 移至 `tools.py` | ISSUE-005 |
| P2 | 确认 `backend/data/` 目录是否需要加入 `.gitkeep`（当前仅 `audit.db`，若移除后目录为空） | — |

**Day 1 范围外（明确排除，不在本轮执行）：**

- 不实现 WebSocket 完整流式逻辑、中危确认交互。
- 不实现 SafetyGuard / AgentHarness / MCPClient / Orchestrator 主流程。
- 不实现一键修复。
- 不编写 tests。
- 不设计完整 Bearer Token 认证流程（仅建议在 config 中预留变量）。

---

## 11. Git 建议

| 项目 | 建议 |
| --- | --- |
| 分支名 | `feature/backend-foundation` |
| commit 信息 | `docs(backend): add backend structure review` |
| 涉及文件 | 仅 `backend/docs/backend_structure_review.md`（本报告） |
| 不应提交 | `backend/data/audit.db`（建议先加入 `.gitignore` 并 `git rm --cached`） |

---

> **报告结束。** 当前后端框架结构完整、分层清晰，具备 Mock 独立运行能力。安全链路（输入检测 → Prompt Guard → RBAC → MCP 调度 → 审计记录）已形成闭环。主要待办集中在依赖声明、Git 规范、接口路径对齐、以及阶段 2 功能模块的补全。
