# 麒麟智能运维 Agent — 后端系统架构

> 生成日期：2026-06-22
> 项目：赛题 A2 - 安全智能运维 Agent 后端服务

## 目录

- [1. 系统分层](#1-系统分层)
- [2. 入口与配置](#2-入口与配置)
- [3. API 层](#3-api-层)
- [4. 服务层](#4-服务层)
- [5. LLM 层](#5-llm-层)
- [6. MCP 层](#6-mcp-层)
- [7. 基础设施层](#7-基础设施层)
- [8. 审计层](#8-审计层)
- [9. 数据模型层](#9-数据模型层)
- [10. 端到端数据流](#10-端到端数据流)
- [11. 模块清单](#11-模块清单)

---

## 1. 系统分层

```
backend/
├── config.py              # 全局配置中心
└── app/
    ├── main.py            # FastAPI 应用入口
    ├── api/               # ★ 接口层 — 对外暴露 HTTP/WS 端点
    ├── services/          # ★ 服务层 — 业务编排 & 连接管理
    ├── llm/               # ★ LLM 层 — DeepSeek 调用 & 多模型路由
    ├── mcp/               # ★ MCP 层 — 远程 MCP Server 通信
    ├── core/              # ★ 基础设施层 — 安全/RBAC/Redis/Prompt 防护
    ├── audit/             # ★ 审计层 — SQLite 日志持久化
    └── schemas/           # ★ 数据模型层 — Pydantic 模型
```

---

## 2. 入口与配置

| 文件 | 职责 |
|---|---|
| `backend/config.py` | 全局配置单例，从环境变量读取 DeepSeek API Key、MCP Server URL、Redis URL、数据库路径等全部配置项，模块级 `settings` 对象供全局使用 |
| `backend/app/main.py` | FastAPI 应用骨架：注册所有路由、CORS 中间件、启动时初始化 SQLite 数据库、`/health` 健康检查端点 |

---

## 3. API 层 — 5 个端点模块

| 文件 | 路由 | 职责 |
|---|---|---|
| `chat.py` | `WS /ws/chat/{session_id}` | 核心实时对话通道。处理 `chat`/`confirm`/`ping` 三类客户端消息，调用 `mock_orchestrate()` 或真实 `LLMRouter`，按 v1.0 协议返回 `status`/`risk_alert`/`chunk`/`tool_call`/`done`/`error`/`pong` 七种消息 |
| `audit.py` | `GET /api/audit` | 审计日志分页查询，返回 `{total, items}`，支持 `limit`/`offset` 参数 |
| `monitor.py` | `GET /api/monitor/stream` (SSE) / `GET /api/monitor/metrics` (REST) | 系统监控：SSE 流实时推送指标（含 CPU/内存/磁盘/网络），REST 快照接口用于降级轮询 |
| `config.py` | `GET /api/config/whitelist` / `PUT /api/config/whitelist` | 白名单配置读写：`commands` 列表 + `blocked_patterns` 全局拦截规则，支持 SQLite 持久化 |
| `sessions.py` | `GET /api/sessions` / `POST /api/sessions` / `GET /api/sessions/{id}/messages` | 会话管理（前端暂未深用）：创建会话、列会话、恢复历史消息 |

---

## 4. 服务层

| 文件 | 核心函数/类 | 职责 |
|---|---|---|
| `orchestrator.py` | `mock_orchestrate()` / `is_medium_risk_command()` / `is_high_risk_command()` | Day-2 Mock 编排器。通过关键词匹配模拟 LLM→MCP 链路，生成 `status`→`tool_call`→`chunk`→`done` 消息序列。风险命令检查独立函数供 `chat.py` 调用 |
| `connection_manager.py` | `ConnectionManager` 类 | WebSocket 连接管理器。跟踪 `session_id → WebSocket` 映射，支持广播、断开、活跃会话遍历 |

---

## 5. LLM 层

| 文件 | 核心类/函数 | 职责 |
|---|---|---|
| `deepseek.py` | `DeepSeekClient` — `chat_stream()` 异步生成器 | DeepSeek API 客户端。封装 OpenAI 兼容的流式聊天补全调用，支持工具定义注入、流式输出 token，兼具错误处理与重试 |
| `router.py` | `LLMRouter` — `route()` | 意图识别 + 工具分发路由器。分析用户输入，判断意图（sys_info/service_mgr/log_reader/net_monitor/cmd_exec），分派给对应 MCP 工具，组装 prompt，调用 DeepSeek 流式生成最终回复 |

---

## 6. MCP 层

| 文件 | 核心类/函数 | 职责 |
|---|---|---|
| `client.py` | `MCPClient` — `call_tool()` / `get_system_metrics()` / `list_tools()` | HTTP JSON-RPC 客户端。通过 Bearer Token 认证向麒麟 V11 MCP Server 发 `tools/call` 请求，含超时/错误处理 |
| `executor.py` | `Executor` — `execute()` | 命令执行调度器。工具白名单校验 + RBAC 权限检查后委托给 `MCPClient`，拦截未授权工具调用 |
| `tools.py` | `get_tool_names()` / `get_tool_schema()` | MCP 工具目录。定义 6 个工具（sys_info/service_mgr/log_reader/net_monitor/cmd_exec/file_guard）的 JSON Schema 参数模型 |

---

## 7. 基础设施层

| 文件 | 核心类/函数 | 职责 |
|---|---|---|
| `security.py` | `validate_token()` / 依赖注入 | API Token 认证。Bearer Token 校验，环境变量 `API_TOKEN` 为空时跳过认证（向后兼容） |
| `rbac.py` | `check_command_permission()` / `load/save_config_from_db()` | 角色权限控制。基于白名单的 `agent-read`/`agent-op`/`agent-admin` 三级权限校验 + SQLite 持久化配置 |
| `prompt_guard.py` | `analyze_prompt()` / `DANGEROUS_PATTERNS` | Prompt 输入防护。检测注入攻击、危险模式（`rm -rf`、管道执行等），返回风险等级 `high`/`medium`/`low` |
| `redis_client.py` | `get_redis()` / `cache_*()` | Redis 客户端封装。`aioredis` 连接管理，提供 session/配置缓存工具函数 |

---

## 8. 审计层

| 文件 | 职责 |
|---|---|
| `models.py` | SQLAlchemy ORM 模型。`AuditRecord` 表定义（trace_id/timestamp/user_input/intent/risk_level/mcp_tool/command/raw_output/llm_reasoning/final_response），+ `init_db()` 数据库初始化 |
| `logger.py` | 审计日志写入器。`AuditLogger.log()` 将完整审计记录写入 SQLite，生成 `trace_id` |

---

## 9. 数据模型层

| 文件 | 职责 |
|---|---|
| `models.py` | Pydantic 请求/响应模型。定义 `AuditRecordOut`/`AuditListResponse`、`WhitelistUpdate`/`WhitelistCommandEntry`、`SystemMetrics` 及其子模型（`CPUMetrics`/`MemoryMetrics`/`DiskMetrics`/`NetworkMetrics`），是前后端接口契约的单一数据源 |

---

## 10. 端到端数据流

```
用户输入 → WebSocket (chat.py)
  → PromptGuard 风险检测 (prompt_guard.py)
    → [high] 直接拒绝 → risk_alert 返回前端
    → [medium] 发送 risk_alert → 等待 confirm → 继续
    → [low] 直接放行
  → RBAC 检查 (rbac.py)
  → LLM Router 意图识别 + 工具分发 (llm/router.py)
    → DeepSeek 流式调用 (llm/deepseek.py)
    → MCP Executor 权限校验 (mcp/executor.py)
    → MCP Client JSON-RPC 调用麒麟 V11 (mcp/client.py)
  → 流式结果返回前端 (chunk → tool_call → done)
  → 审计日志写入 (audit/logger.py → audit/models.py → SQLite)
```

---

## 11. 模块清单

| 编号 | 层 | 文件 | 职责概要 |
|---|---|---|---|
| 1 | 配置 | `backend/config.py` | 全局 Settings 单例，环境变量读取 |
| 2 | 入口 | `backend/app/main.py` | FastAPI 注册路由/CORS/数据库初始化/健康检查 |
| 3 | API | `app/api/chat.py` | WebSocket 聊天 — 消息路由与协议实现 |
| 4 | API | `app/api/audit.py` | REST 审计日志分页查询 |
| 5 | API | `app/api/monitor.py` | SSE 监控流 + REST 指标快照 |
| 6 | API | `app/api/config.py` | 白名单 CRUD，SQLite 持久化 |
| 7 | API | `app/api/sessions.py` | 会话 CRUD + 历史消息恢复 |
| 8 | 服务 | `app/services/orchestrator.py` | Day-2 Mock 编排器，关键词匹配模拟链路 |
| 9 | 服务 | `app/services/connection_manager.py` | WebSocket 连接生命周期管理 |
| 10 | LLM | `app/llm/deepseek.py` | DeepSeek API 流式调用客户端 |
| 11 | LLM | `app/llm/router.py` | LLM Router — 意图识别 + 工具分发 |
| 12 | MCP | `app/mcp/client.py` | MCP JSON-RPC HTTP 客户端 |
| 13 | MCP | `app/mcp/executor.py` | 执行调度器 — 白名单 + RBAC 拦截 |
| 14 | MCP | `app/mcp/tools.py` | MCP 工具目录 & JSON Schema 定义 |
| 15 | 基础设施 | `app/core/security.py` | API Token Bearer 认证 |
| 16 | 基础设施 | `app/core/rbac.py` | 三级角色权限控制 + 配置持久化 |
| 17 | 基础设施 | `app/core/prompt_guard.py` | Prompt 注入检测 & 危险模式拦截 |
| 18 | 基础设施 | `app/core/redis_client.py` | Redis 连接管理 & 缓存工具 |
| 19 | 审计 | `app/audit/models.py` | SQLAlchemy ORM 审计记录模型 + DB 初始化 |
| 20 | 审计 | `app/audit/logger.py` | 审计日志写入，trace_id 生成 |
| 21 | 数据模型 | `app/schemas/models.py` | Pydantic 请求/响应模型，前后端契约 |
