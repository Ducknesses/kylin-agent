# 后端技术债跟踪总表

> 最后更新：2026-06-22
> 整合来源：Day 1 技术债记录、Day 2 技术债记录、后端架构诊断报告

---

## 使用说明

每条技术债记录包含以下字段：

| 字段 | 说明 |
| --- | --- |
| **编号** | 唯一标识，格式 `TD-XX`（Technical Debt） |
| **来源** | Day 1 / Day 2 / 架构诊断 |
| **问题描述** | 技术债的具体表现 |
| **是否解决** | ✅ 已解决 / ❌ 未解决 / ⏳ 部分解决 |
| **优先级** | P0（阻塞后续开发）/ P1（近期必须解决）/ P2（可延后）/ P3（低优先级） |
| **验收标准** | 可以关闭此问题的具体条件 |
| **关联文件** | 相关代码位置 |

---

## 未解决问题清单

### TD-01：chat.py 未接入真实 LLM / MCP 链路

| 来源 | 优先级 | 状态 |
| --- | --- | --- |
| Day 2 / 架构诊断 | **P0** | ❌ 未解决 |

**问题描述**：
`chat.py` 当前直接调用 `mock_orchestrate()` 处理所有聊天消息，不调用 `app.llm.router.LLMRouter`，也不调用 `app.mcp.executor.Executor`。真实 DeepSeek + MCP Server 链路完全未接入。系统仅在 Day-2 关键词匹配模式下运转。

**验收标准**：
- [ ] `chat.py` 在配置项 `USE_REAL_LLM=true` 时走 `LLMRouter.route()` → `DeepSeekClient.chat_stream()` + `Executor.execute()` 链路
- [ ] `chat.py` 在 `USE_REAL_LLM=false` 时回退到 `mock_orchestrate()`
- [ ] 真实链路下 `status` / `chunk` / `tool_call` / `done` 消息均正确发送
- [ ] 审计日志 `log_chain` 在真实链路中正确记录 `trace_id` / `user_input` / `final_response`
- [ ] `pytest tests/` 全部通过

**关联文件**：
- `backend/app/api/chat.py` — 需增加真实链路分支
- `backend/app/llm/router.py` — LLMRouter 入口
- `backend/app/llm/deepseek.py` — DeepSeekClient
- `backend/app/mcp/executor.py` — Executor
- `backend/app/services/orchestrator.py` — mock 保留为 fallback

---

### TD-02：安全检测职责重叠，分散在多处

| 来源 | 优先级 | 状态 |
| --- | --- | --- |
| Day 2 / 架构诊断 | **P1** | ❌ 未解决 |

**问题描述**：
安全逻辑目前分散在 4 个文件中：

| 文件 | 职责 |
| --- | --- |
| `app/core/security.py` | 长度检测 / 黑名单正则 / 关键词分级 → `risk_classify()` |
| `app/core/prompt_guard.py` | Prompt Injection 检测 → `detect_injection()` |
| `app/core/rbac.py` | 命令白名单匹配 / 权限级别 → `check_command_permission()` |
| `app/services/orchestrator.py` | 中危关键词补充判定 → `is_medium_risk_command()` |

**具体重叠**：
1. `security.py._check_blacklist` 与 `rbac.py.check_command_permission` 编译了相同危险正则（`rm -rf`、管道执行、重定向覆写），违反 DRY 原则
2. `security.py._check_keywords` 与 `prompt_guard.py.detect_injection` 功能边界模糊，`risk_classify()` 链式调用两者
3. `risk_classify()` 内部已做 medium/high 等级判定，但 `chat.py` 又调用 `is_medium_risk_command()` 做二次判定

**验收标准**：
- [ ] 危险模式正则提取到 `core/` 公共模块（如 `dangerous_patterns.py`），`security.py` 和 `rbac.py` 共享同一套定义
- [ ] `risk_classify()` 返回值包含 `risk_level` + `reason` + `confirm_id`，`chat.py` 不再需要调用 `is_medium_risk_command()` 做二次判定
- [ ] `prompt_guard.detect_injection()` 与 `security._check_keywords()` 职责划分清晰，或有文档说明边界
- [ ] 旧函数未被删除时添加 `# TODO: deprecate` 注释

**关联文件**：
- `backend/app/core/security.py`
- `backend/app/core/prompt_guard.py`
- `backend/app/core/rbac.py`
- `backend/app/services/orchestrator.py`
- `backend/app/api/chat.py`

---

### TD-03：ConnectionManager 职责混杂

| 来源 | 优先级 | 状态 |
| --- | --- | --- |
| 架构诊断 | **P2** | ❌ 未解决 |

**问题描述**：
`ConnectionManager` 同时管理两种无关状态：
1. WebSocket 连接生命周期 — `connect()` / `disconnect()` / `send_json()`
2. 确认流程状态机 — `set_pending()` / `get_pending()` / `pop_pending()` / `has_pending()`

违反单一职责原则。且 `is_connected()` / `send_json_ws()` 定义后从未被调用（`chat.py` 使用自定义 `_send()`）。

**验收标准**：
- [ ] 拆分为 `ConnectionManager`（仅管理 WS 连接）和 `ConfirmManager`（仅管理挂起确认状态）
- [ ] `chat.py` 中分别持有两个 Manager 实例，不再耦合
- [ ] `is_connected()` / `send_json_ws()` 若保留则在 `chat.py` 中实际使用；否则删除
- [ ] `pytest tests/test_ws_chat.py` 全部通过

**关联文件**：
- `backend/app/services/connection_manager.py`
- `backend/app/api/chat.py`

---

### TD-04：死代码未清理

| 来源 | 优先级 | 状态 |
| --- | --- | --- |
| 架构诊断 | **P3** | ❌ 未解决 |

**问题列表**：

| 文件 | 函数 | 说明 |
| --- | --- | --- |
| `services/orchestrator.py` | `is_high_risk_command()` | 定义但无任何 import 或调用 |
| `core/prompt_guard.py` | `sanitize_input()` | 定义但无外部调用方 |
| `core/rbac.py` | `get_user_level()` | executor.py 中 import 但未实际执行 |
| `services/connection_manager.py` | `is_connected()` / `send_json_ws()` | chat.py 用自定义 `_send()` 代替 |

**验收标准**：
- [ ] 未使用的函数标注 `# TODO: remove after Day-3 validation` 或直接删除
- [ ] 所有保留的函数有明确的调用链
- [ ] `pytest` 全绿

**关联文件**：
- `backend/app/services/orchestrator.py`
- `backend/app/core/prompt_guard.py`
- `backend/app/core/rbac.py`
- `backend/app/services/connection_manager.py`

---

### TD-05：`agent_harness.py` 未创建 / 缺少统一编排入口

| 来源 | 优先级 | 状态 |
| --- | --- | --- |
| Day 2 | **P1** | ❌ 未解决 |

**问题描述**：
统一的工具调用入口、LLM 调用入口、MCP Client 调度入口尚未创建。当前 Mock 数据直接在 `orchestrator.py` 中硬编码。后续接入真实链路时需要一个中心编排模块来协调 `LLMRouter` → `DeepSeek` + `Executor` 的完整流程。

**验收标准**：
- [ ] `agent_harness.py`（或等价模块）创建并实现 `orchestrate(user_input, session_id) -> AsyncIterator[dict]` 接口
- [ ] 内部调用 `LLMRouter.route()`，接收流式输出并 yield 标准化消息
- [ ] `chat.py` 通过该编排入口调度，不直接调用 `mock_orchestrate()` 或 `LLMRouter`
- [ ] mock 模式也通过同一接口的 mock 实现提供

**关联文件**：
- 待新建：`backend/app/services/agent_harness.py`（或 `backend/app/services/orchestrator.py` 重构）
- `backend/app/api/chat.py`

---

### TD-06：一键修复 FixOption 功能未实现

| 来源 | 优先级 | 状态 |
| --- | --- | --- |
| Day 2 | **P2** | ❌ 未解决 |

**问题描述**：
API 规范中预留的修复建议 / 一键修复按钮数据结构尚未落地。当前中危操作仅支持 `approve` / `reject` 二选一，无固定修复方案选项。

**验收标准**：
- [ ] `risk_alert` 消息在中危场景下可携带 `fix_options` 字段（可选列表）
- [ ] 前端 `RiskAlert.vue` 展示修复选项按钮
- [ ] 用户选择修复选项后通过 `confirm` 消息回传，后端执行对应修复

**关联文件**：
- `backend/app/api/chat.py`
- `backend/app/services/orchestrator.py`
- `frontend/src/components/RiskAlert.vue`

---

### TD-07：MCP `result.blocked=true` 场景已覆盖

| 来源 | 优先级 | 状态 |
| --- | --- | --- |
| Day 2 | **P2** | ✅ 已解决 |

**问题描述**：
MCP Server 可能在 HTTP 200 下返回 `result.blocked == true`，表示工具调用已被执行器安全策略拦截。后端必须识别该字段，不能把 blocked 结果当成成功执行。

**解决方式**：
在 fix/technical_debt_001 (Part 2C) 中，`MCPClient.call_tool()` 新增 `result.blocked` 检测：在 JSON-RPC error 检查之后、成功返回之前，判断 `isinstance(result, dict) and result.get("blocked") is True`，若为真则返回 `{"success": False, "blocked": True, "error": ..., "result": result}`。Executor 层为纯透传，无需修改。

**验收证据**：
- `test_mcp_client.py` 新增 5 个 blocked 测试，全部通过
- `test_ws_chat.py` 仍 16 passed
- `grep -R blocked backend/app/mcp backend/tests` 可见 client.py 中的处理逻辑和测试覆盖

**关联文件**：
- `backend/app/mcp/client.py` — blocked 检测逻辑（行 73-86）
- `backend/app/mcp/executor.py` — 纯透传，无需修改
- `backend/tests/test_mcp_client.py` — 新增 5 个 blocked 场景测试

---

### TD-08：ConnectionManager 使用单进程内存状态

| 来源 | 优先级 | 状态 |
| --- | --- | --- |
| Day 2 | **P2** | ❌ 未解决 |

**问题描述**：
当前 `pending_confirm` 与连接状态均保存在模块级内存字典中，多 worker（如 `uvicorn --workers 4`）部署时不同进程之间状态不共享，确认流程会失效。

**验收标准**：
- [ ] 确认状态存储切换到 Redis 或数据库
- [ ] 多 worker 场景下 confirm/approve/reject 流程正常
- [ ] 不引入新的外部依赖（可复用已有 `redis_client.py`）

**关联文件**：
- `backend/app/services/connection_manager.py`
- `backend/app/core/redis_client.py`

---

### TD-09：`_mcp_metrics_generator()` 未接入 SSE 路由

| 来源 | 优先级 | 状态 |
| --- | --- | --- |
| Day 2 | **P2** | ❌ 未解决 |

**问题描述**：
`monitor.py` 中 `_mcp_metrics_generator()` 已实现对 MCP Server 远程指标的采集和类型收窄，但 SSE 路由 `/api/monitor/stream` 当前实际使用的是本地 `psutil` 采集（`_generator()`）。远程采集函数未被任何路由引用。

**验收标准**：
- [ ] `/api/monitor/stream` 支持通过配置切换本地 psutil（`_generator`）和远程 MCP（`_mcp_metrics_generator`）两种采集源
- [ ] MCP 远程指标失败时有重试和降级策略
- [ ] SSE 输出字段在两种模式下保持一致

**关联文件**：
- `backend/app/api/monitor.py`

---

### TD-10：`GET /api/audit/logs` legacy 路由已删除

| 来源 | 优先级 | 状态 |
| --- | --- | --- |
| Day 1 / Day 2 | **P3** | ✅ 已解决 |

**问题描述**：
正式规范使用 `GET /api/audit`，但 `/api/audit/logs` 曾作为兼容路由保留。

**解决方式**：
在 fix/technical_debt_001 (Part 2B) 中直接删除 legacy `/api/audit/logs` 路由及 `get_audit_logs_legacy()` 处理函数，统一使用 `GET /api/audit`。正式接口支持 `limit` / `offset` / `start_date` / `end_date` 参数，返回 `{records, total}` 结构。

**验收证据**：
- `grep -R "/api/audit/logs\|@router.get(\"/logs\"" backend/app backend/tests` 零匹配
- `pytest tests/test_ws_chat.py` 全部通过

**关联文件**：
- `backend/app/api/audit.py` — legacy 路由已删除

---

### TD-11：审计日志时间过滤参数未充分验证

| 来源 | 优先级 | 状态 |
| --- | --- | --- |
| Day 1 | **P3** | ⏳ 部分解决 |

**问题描述**：
`query_audit()` 和 `count_audit()` 已实现 `start_date` / `end_date` 时间过滤逻辑，但因本地审计数据量少（Mock 阶段），未做大规模时间范围查询验证。

**验收标准**：
- [ ] 在至少 1000 条审计记录的数据库上验证时间范围查询性能
- [ ] 边界条件（`start_date` 晚于 `end_date`、无效 ISO 格式）有合理错误响应
- [ ] 分页 + 时间过滤组合查询结果正确

**关联文件**：
- `backend/app/audit/logger.py`

---

### TD-21：PostgreSQL 数据库适配

| 来源 | 优先级 | 状态 |
| --- | --- | --- |
| 生产化补全第 4 项 | **P0** | ✅ 已解决 |

**问题描述**：
项目仅支持 SQLite，所有数据访问层使用原始 `aiosqlite.connect()` 硬编码，无法切换到 PostgreSQL 以适应生产环境。

**解决方式**：
引入 SQLAlchemy 2.0 异步引擎作为统一数据库抽象层，通过 `DATABASE_URL` 环境变量切换 SQLite/PostgreSQL：
- 新增 `app/core/database.py` 引擎工厂 + `app/models/` 4 个 ORM 模型
- 改造 `SQLiteMessageRepository`、`AuditService`、`audit/models.py`、`audit/logger.py` 全部改用 SQLAlchemy `AsyncSession`
- `config.py` 新增 `DATABASE_URL` 配置项，默认回退 `SQLITE_DB`（向后兼容）
- 235 个非 LLM 测试全部通过，零回归

**后续**：
- [ ] PostgreSQL 真实连接验证（当前环境无 PG 实例）→ 详见 **TD-22**
- [ ] Repository 命名去 SQLite 化 → 详见 **TD-23**
- [ ] Session Factory 公共接口封装 → 详见 **TD-24**

**关联文件**：
- `backend/config.py` — DATABASE_URL 配置
- `backend/app/core/database.py` — 引擎工厂（新增）
- `backend/app/models/` — ORM 模型（新增）
- `backend/app/repositories/sqlite.py` — 改用 AsyncSession
- `backend/app/services/audit_service.py` — 改用 ORM
- `backend/app/audit/models.py` — 移除 raw DDL
- `backend/app/audit/logger.py` — 委托 AuditService
- `backend/app/main.py` — init_db → init_engine
- `backend/docs/postgresql_adaptation_technical_debt.md` — 详细记录

---

### TD-22：PostgreSQL 真实环境测试缺失

| 来源 | 优先级 | 状态 |
| --- | --- | --- |
| TD-21 子项 | **P2** | ❌ 未处理 |

**问题描述**：
数据库适配已通过 SQLAlchemy 支持 SQLite/PostgreSQL 双后端，但所有自动化测试均运行在 SQLite 临时数据库上，缺少真实 PostgreSQL 集成验证。

**后续方案**：
Docker Compose 增加 PG 测试容器 + CI 增加 PG 测试任务。

**关联文件**：
- `backend/docs/postgresql_adaptation_technical_debt.md` — 技术债 1

---

### TD-23：Repository 命名历史遗留

| 来源 | 优先级 | 状态 |
| --- | --- | --- |
| TD-21 子项 | **P3** | ❌ 未处理 |

**问题描述**：
`SQLiteMessageRepository` 类名和 `sqlite.py` 文件名仍带 "SQLite"，但已通过 SQLAlchemy 支持多数据库，名称与实际抽象层次不符。

**后续方案**：
重命名为 `DatabaseMessageRepository` / `message_repository.py`。

**关联文件**：
- `backend/app/repositories/sqlite.py` / `__init__.py`
- `backend/app/dependencies.py`

---

### TD-24：Session Factory 封装优化

| 来源 | 优先级 | 状态 |
| --- | --- | --- |
| TD-21 子项 | **P3** | ❌ 未处理 |

**问题描述**：
Repository/Service 直接引用 `database.py` 的私有变量 `_async_session_factory`，降低了模块封装性。

**后续方案**：
增加 `get_session_factory()` 公共接口。

**关联文件**：
- `backend/app/core/database.py`
- `backend/app/repositories/sqlite.py`
- `backend/app/services/audit_service.py`

---

### TD-12：`audit.db` 从 Git 跟踪已移除

| 来源 | 优先级 | 状态 |
| --- | --- | --- |
| Day 1 | **P3** | ✅ 已解决 |

**问题描述**：
`backend/data/audit.db` 曾被 Git 跟踪，需从索引中移除。

**解决方式**：
`git rm --cached backend/data/audit.db` 已执行（在 Day 1 PR Review 阻塞修复中完成），`.gitignore` 已包含 `*.db` / `*.db-journal` / `*.db-wal` / `*.db-shm` / `*.sqlite` / `*.sqlite3` / `backend/data/audit.db` 多重排除规则。

**验收证据**：
- `git ls-files backend/data/audit.db` 无输出（2026-06-22 验证）
- `git status` 不显示 `audit.db` 为已修改文件

**关联文件**：
- `backend/data/audit.db`
- `.gitignore`

---

## 已解决问题清单

| 编号 | 来源 | 问题摘要 | 解决方式 |
| --- | --- | --- | --- |
| ~~TD-01-old~~ | Day 1 | WebSocket 路径 `/ws/chat` 与规范不一致 | Day 2 统一为 `/ws/chat/{session_id}`，旧路由删除 |
| ~~TD-02-old~~ | Day 1 | 后端 `type=reject` 消息已废弃 | Day 2 移除，改为 `decision=reject` 仅表示用户取消 |
| ~~TD-03-old~~ | Day 1 | Pylance 可选类型标注报错 | 全部改为 `str \| None` 等 Python 3.10+ 联合类型 |
| ~~TD-04-old~~ | Day 1 | LLM 配置错误信息暴露 `DEEPSEEK_API_KEY` | 脱敏为用户友好文案，日志保留内部信息 |
| ~~TD-05-old~~ | Day 1 | 旧审计数据库含敏感实现细节 | 备份旧库，重建新库，`.gitignore` 加排除规则 |
| ~~TD-06-old~~ | Day 1 | PR1 前后端接口不一致（13 项差异） | 全部对齐到 API v1.0 规范 |
| ~~TD-07-old~~ | Day 1 | WebSocket confirm_id 校验顺序错误 | 改为先校验再 pop，错误 confirm 不丢失 pending |
| ~~TD-08-old~~ | Day 1 | 白名单持久化失败仍返回成功 | `save_config()` 改为抛异常，`update_whitelist()` 返回 500 |
| ~~TD-09-old~~ | Day 1 | SSE 异常返回缺少核心字段 | 异常 fallback 补全 8 个核心字段 |
| ~~TD-10-old~~ | Day 1 | 网络速率可能出现负数 | 增加 `max(0, delta)` 非负保护 |
| ~~TD-11-old~~ | Day 1 | 审计模块注释含误导性"思维链"表述 | 全部修正为"安全审计链路" |
| ~~TD-12-old~~ | Day 1 | Pydantic Schema 默认值 `[]` 与缺 `Literal` 约束 | 改为 `Field(default_factory=list)` + `Literal` 枚举 |
| ~~TD-13-old~~ | Day 1 | 会话历史接口返回普通 dict | 改为返回 `SessionMessagesOut` 模型实例 |
| ~~TD-14-old~~ | Day 1 | `router.py` 类型标注 `Dict`/`List` 混用 | 统一为 Python 3.10+ `dict`/`list` |
| ~~TD-15-old~~ | Day 1 | `.gitignore` 末尾缺换行符 | 补充换行（fix/technical_debt_001 Part 2B 重新确认并补充） |
| ~~TD-16-old~~ | Day 2 | Mock 编排器匹配顺序错误 | 调整 CPU → 重启 nginx → nginx 状态顺序 |
| ~~TD-17-old~~ | Day 2 | monitor.py disk 字段类型收窄 | `isinstance` 判断后安全访问 |
| ~~TD-18~~ | Day 1 / Day 2 | `/api/audit/logs` legacy 路由 | fix/technical_debt_001 Part 2B 直接删除，统一使用 `GET /api/audit` |
| ~~TD-19~~ | Day 1 | `audit.db` Git 跟踪 | `git rm --cached` 已执行，`.gitignore` 多重排除规则覆盖 |
| ~~TD-20~~ | Day 2 | MCP `result.blocked=true` 未处理 | fix/technical_debt_001 Part 2C：MCPClient 识别 blocked 并转换为失败结构 |
| ~~TD-21~~ | 生产化 P4 | 仅支持 SQLite，无法切换 PostgreSQL | feat/postgresql-storage：引入 SQLAlchemy 2.0 + ORM 模型，DATABASE_URL 统一切换 |

---

## 优先级汇总

| 优先级 | 数量 | 编号 |
| --- | --- | --- |
| **P0** | 1 | TD-01（真实 LLM/MCP 链路未接入） |
| **P1** | 2 | TD-02（安全检测重叠）、TD-05（agent_harness 未创建） |
| **P2** | 4 | TD-03（ConnectionManager 拆分）、TD-06（FixOption）、TD-08（多 worker 状态）、TD-09（MCP 监控路由）、TD-22（PG 真实测试） |
| **P3** | 4 | TD-04（死代码）、TD-11（时间过滤验证）、TD-23（Repository 命名）、TD-24（Session Factory 封装） |

**总计：11 项未解决，21 项已解决**

---

> 本文档随每次技术债清理更新，与 `day1_technical_debt.md` / `day2_technical_debt.md` 配合使用。新发现的问题直接从诊断报告中录入此表，已解决的问题从历史文档迁移至"已解决"分区。