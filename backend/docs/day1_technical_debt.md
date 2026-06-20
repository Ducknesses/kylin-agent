# Day 1 技术债记录

> 最后更新：2026-06-16
> 关联分支：feature/backend-foundation

---

## Part：API 文档接口重构

### 本轮目标

以 API 文档为唯一正式对外接口标准，重构与文档不一致的旧接口。旧接口来自早期框架快速搭建实现，与正式 API 文档存在路径/参数/返回格式上的差异。本轮选择**直接重构到统一接口**而不是长期兼容双接口，目的是降低后续人工 code review 成本和避免前后端对接混乱。

### 本轮已处理

| 接口 / 项 | 旧形态 | 新形态 | 状态 |
| --- | --- | --- | --- |
| WebSocket | `/ws/chat/{session_id}` | `/ws/chat`（session_id 从 JSON body 读取） | 已完成 |
| 监控 | `/api/monitor/stream`（唯一） | `/api/monitor/metrics`（正式）+ `/stream`（非正式保留） | 已完成 |
| 审计 | `/api/audit?limit=&offset=` | `/api/audit/logs?page=&limit=&start_date=&end_date=` | 已完成 |
| 白名单 | `/api/config/whitelist`（路径不变，改返回格式） | `{code, data: {commands, blocked_patterns}}` | 已完成 |
| MCP Bearer Token | 无 | `MCP_AUTH_TOKEN` 配置项 + `Authorization: Bearer` 请求头 | 已完成 |
| fakeredis 依赖 | 未声明 | `requirements.txt` 已加 `fakeredis>=2.0.0` | 已完成 |
| SQLite .gitignore | 无排除规则 | `.gitignore` 已加 `*.db` / `*.db-journal` / `*.db-wal` / `*.db-shm` | 已完成 |
| `.env.example` | 无 | 根目录已创建 | 已完成 |
| `data/.gitkeep` | 无 | 已创建 | 已完成 |

### 本轮遗留问题

1. **`audit.db` 从 Git 跟踪移除需用户手动执行**

   `backend/data/audit.db` 已被 `.gitignore` 排除，但如果该文件之前已被 `git add` 跟踪，需要用户手动执行：

   ```bash
   git rm --cached backend/data/audit.db
   ```

   原因：`.gitignore` 只能阻止未跟踪文件的添加，无法自动移除已跟踪文件。`git rm --cached` 只移除 Git 索引记录，不会删除本地数据库文件。

2. **`/api/monitor/stream` 仍作为非正式接口存在**

   删除条件：前端全部切换到 `/api/monitor/metrics`，后端自测脚本不再依赖 SSE 流。

3. **`/ws/chat/{session_id}` 仍作为 deprecated 路由存在**

   删除条件：前端确认不再使用路径参数形式，所有调试脚本已切换。

4. **`/api/audit` 仍作为 deprecated 路由存在**

   删除条件：确认无旧调用方，前端审计页已切换到 `/api/audit/logs`。

5. **`start_date` / `end_date` 参数已支持但未经端到端验证**

   `query_audit()` 和 `count_audit()` 已实现时间过滤逻辑，前端可传入 ISO-8601 时间字符串。但因本地审计数据量少（Mock 阶段），未做大规模时间范围查询验证。

6. **`PUT /api/config/whitelist` 仍是阶段 2 预留占位**

   当前不实际修改运行时常量，返回提示消息。

### 技术债原因

旧接口来自项目早期阶段，当时 API 文档尚未最终定稿，开发时以"能让各端快速跑通"为目标。随着 API 文档正式发布，旧接口与新契约的差异逐渐成为协作摩擦点。本轮在 Day 1 范围内统一接口，避免后续代码中混用新旧路径导致调试困难和 code review 负担。

### 后续删除条件

保留的 deprecated 路由均为**同一 handler 内部转发**，不维护独立业务逻辑。删除条件统一为：

- 前端全部切换到 API 文档正式接口。
- 后端自测脚本不再依赖旧路径。
- 联调确认零调用后删除。

### 验收情况

| 接口 | 验收状态 | 备注 |
| --- | --- | --- |
| `GET /health` | 需要人工验证 | 未改动，预期正常 |
| `GET /api/monitor/metrics` | 需要人工验证 | 新增，Mock 数据 |
| `GET /api/audit/logs` | 需要人工验证 | 新增，带分页 |
| `GET /api/config/whitelist` | 需要人工验证 | 改返回格式 |
| `WS /ws/chat` | 需要人工验证 | 重构路径和消息格式 |
| `WS /ws/chat` reject 响应 | 需要人工验证 | 发送高危命令验证 |
| `requirements.txt` | 需要人工验证 | 检查 fakeredis 声明 |
| `.gitignore` | 需要人工验证 | 检查 *.db 规则 |
| `git rm --cached audit.db` | 需要人工执行 | 见遗留问题 #1 |

---

> 本文档随 Day 1 接口重构创建，后续每个 Day 可追加新 Part。

---

## Part：Pylance 可选类型标注修复

> 日期：2026-06-16
> 关联分支：feature/backend-foundation

### 问题描述

多个可选参数标注为 `str` / `MCPClient`，但默认值为 `None`，导致 Pylance 报 "None 不可分配给指定类型"。

### 产生原因

接口响应字段、查询参数或辅助函数参数本身允许为空，但类型标注没有写成可选类型。Python 3.10.12 支持 `类型 | None` 联合类型写法。

### 本轮修复

使用 Python 3.10.12 支持的 `类型 | None` 写法修复可选参数标注。

| 文件 | 函数/方法 | 修改 |
| --- | --- | --- |
| `app/api/chat.py` | `_send()` | `content/message/reason/risk_level/trace_id: str = None` → `str \| None = None`；`payload: dict` → `dict[str, Any]`；添加 `-> None` 返回类型和 `Any` 导入 |
| `app/audit/logger.py` | `query_audit()` | `start_date/end_date: str = None` → `str \| None = None` |
| `app/audit/logger.py` | `count_audit()` | `start_date/end_date: str = None` → `str \| None = None` |
| `app/mcp/client.py` | `MCPClient.__init__()` | `base_url: str = None` → `str \| None = None` |
| `app/mcp/executor.py` | `Executor.__init__()` | `client: MCPClient = None` → `MCPClient \| None = None` |

### 本轮未处理

- 不处理与类型标注无关的功能问题。
- 不修改接口路径。
- 不引入新架构。
- 不改变业务逻辑。
- 未使用 `Optional[str]` 风格（文件规模小，统一使用 Python 3.10+ 联合类型）。

### 验收方式

- Pylance 不再提示 "None 不可分配给 str/dict/list/int/bool"。
- 后端仍可启动。
- `/health` 正常返回。
- `/ws/chat`、`/api/monitor/metrics`、`/api/audit/logs`、`/api/config/whitelist` 的接口契约不变。

---

## Part：LLM 配置错误信息脱敏

> 日期：2026-06-16
> 关联分支：feature/backend-foundation

### 问题描述

未配置 DeepSeek API Key 时，LLM 客户端 (`deepseek.py`) 将包含具体环境变量名 `DEEPSEEK_API_KEY` 的错误信息直接 yield 给业务层。由于 `chat.py` 将 LLM 返回的 chunks 原样发送给前端并写入审计日志 `final_response`，导致内部配置细节暴露给外部用户。

### 产生原因

`chat_with_llm()` 在检测到 API Key 缺失时，yield 的文本包含具体环境变量名和模型服务实现细节，该文本逐层透传至 WebSocket 响应和审计日志。

### 风险评估

- 当前未泄露真实 API Key 值。
- 暴露了内部环境变量名 (`DEEPSEEK_API_KEY`) 和具体模型服务实现细节 (`DeepSeek API Key`)。
- 若未来异常处理中打印 request headers、config 对象或真实密钥，可能扩大为密钥泄露风险。

### 本轮修复

- `deepseek.py:39`：用户可见 yield 文本从 `[错误] DeepSeek API Key 未配置，请在环境变量中设置 DEEPSEEK_API_KEY` 改为 `[错误] 智能分析服务暂不可用，请检查后端环境配置`。
- 后端日志保留 `logger.error("DeepSeek API Key 未配置")`，仅影响开发排查，不进入用户响应和审计记录。

### 本轮未处理

- 不修改审计表结构。
- 不引入统一 ErrorHandler。
- 不重构 LLM Client 架构。
- 不修改接口路径和 WebSocket 协议。
- MCP 客户端的 `Authorization` header 构造代码保持不变（仅为内部调用逻辑，不面向用户）。

### 验收方式

- WebSocket 响应不包含 `DEEPSEEK_API_KEY` 或 `DeepSeek API Key`。
- `/api/audit/logs` 的 `final_response` 不包含敏感环境变量名。
- 后端启动正常。
- `/health` 正常返回。

---

## Part：本地旧审计数据库清理

> 日期：2026-06-20
> 关联分支：feature/backend-foundation

### 问题描述

LLM 配置错误信息脱敏修复后，新审计记录已不再暴露 `DEEPSEEK_API_KEY` 等内部环境变量名，但本地开发数据库 `backend/data/audit.db` 中仍保留修复前的历史测试记录（含 `[错误] DeepSeek API Key 未配置，请在环境变量中设置 DEEPSEEK_API_KEY`），导致 `/api/audit/logs` 查询结果中仍能看到旧的敏感实现细节。

### 产生原因

审计日志持久化保存在 SQLite 数据库中。代码修复只能影响后续新写入记录，不能自动修改历史记录。

### 本轮处理

- 未直接修改单条审计记录，避免破坏审计链路语义。
- 将旧的本地开发数据库备份到 `backend/data/backup/audit.db.bak_20260620_0155`。
- 让后端重新初始化生成新的开发数据库。
- `.gitignore` 新增 `*.sqlite`、`*.sqlite3`、`backend/data/backup/` 排除规则。

### 风险说明

- 当前处理仅适用于本地开发测试环境。
- 生产环境不能随意删除或重置审计数据库。生产环境应通过数据脱敏迁移脚本或审计留痕流程处理历史敏感记录。

### 本轮未处理

- 不修改审计表结构。
- 不手动 UPDATE / DELETE 单条审计记录。
- 不删除备份数据库（留存备查）。
- 不修改接口路径和 WebSocket 协议。

### 验收方式

- 后端可在缺少 `audit.db` 的情况下正常启动并自动创建新数据库。
- `/health` 正常返回。
- 新的 `/api/audit/logs` 结果不再包含 `DEEPSEEK_API_KEY`。
- `audit.db` 和备份数据库未被加入 Git（备份目录已被 `.gitignore` 排除，但 `audit.db` 本身已被 Git 跟踪，需手动 `git rm --cached`）。

---

## Part：PR1 前后端 API 统一规范修复

> 日期：2026-06-20
> 关联分支：feature/backend-foundation

### 背景

PR review 后确认，旧 Day 1 后端接口与前端实际调用存在多处不一致。最新 `api_specification_1.pdf` 已作为新的统一接口规范。

### 本轮修复

| 接口 | 旧形态 | 新形态 |
| --- | --- | --- |
| WebSocket | `/ws/chat` 正式，/chat/{session_id} deprecated | `/ws/chat/{session_id}` 正式，/chat legacy |
| WS 前端消息 | 仅 chat | chat / confirm / ping |
| WS 后端消息 | chunk / done / reject / error | status / risk_alert / chunk / tool_call / done / error / pong |
| WS 高危响应 | `{"type":"reject"}` | `{"type":"risk_alert","level":"high",...}` |
| WS 中危响应 | 无 confirm 流程 | risk_alert + confirm_id + pending_confirm + approve/reject |
| 审计 | `GET /api/audit/logs` 返回 code/data | `GET /api/audit` 返回 {total, items} |
| 审计字段 | 缺 raw_output/llm_reasoning | 包含全部 10 个字段 |
| 监控 REST | `GET /api/monitor/metrics` 返回 code/data + mock | 嵌套结构，真实 psutil 数据，无 code/data |
| 监控 SSE | `GET /api/monitor/stream` 嵌套 SSE | 扁平结构 SSE，无 code/data |
| 白名单 GET | `{code, data: {commands:[{pattern,risk}]}}` | `{commands:[{pattern,role,risk}], blocked_patterns}` |
| 白名单 PUT | WhitelistUpdate.commands 为 list[str] | WhitelistUpdate.commands 为 list[WhitelistCommandEntry] |
| 白名单持久化 | 无 | SQLite app_config 表 |
| 会话历史 | 无 | `GET /api/sessions/{session_id}/messages` |
| Schema 模型 | 旧注释、扁平 SystemMetrics | ChatMessage 按方向分述、嵌套指标模型、SSEMetrics |

### 本轮不处理

- 后端-MCP `/mcp/v1/tools/call` 对齐（下一轮）
- `params.args` 改 `params.arguments`（下一轮）
- 完整 AgentHarness / Orchestrator
- 真实 MCP tool_call 执行链路（当前仅占位消息类型）
- 消息历史持久化（当前返回空列表）

### 风险说明

部分接口契约相较 Day 1 旧文档发生变化。后续统一以最新 PDF 为准。
旧接口如保留，仅作为 legacy 兼容入口，不再作为正式契约。
`tool_call` 消息类型已支持，但真实 MCP 调用链路下一轮处理。

### 验收方式

- WebSocket `/ws/chat/{session_id}` 可连接
- ping 返回 pong
- 高危输入返回 risk_alert
- `/api/audit?limit=20&offset=0` 返回 total/items
- `/api/monitor/stream` 返回扁平 SSE
- `/api/monitor/metrics` 返回嵌套指标
- `/api/config/whitelist` GET/PUT 不再 422
- `/api/sessions/{session_id}/messages` 可访问
- 后端启动无 Traceback

---

## Part：WebSocket confirm_id 校验与 pending 状态修复

> 日期：2026-06-20
> 关联分支：feature/backend-foundation

### 问题描述

confirm 分支原先使用 `pending_confirm.pop(session_id, None)` 在校验 decision 和 confirm_id 之前就删除挂起操作。错误 confirm 消息（非法 decision、缺少 confirm_id、confirm_id 不匹配）也会清空待确认操作，导致用户无法重试。

### 产生原因

初版 confirm 流程只按 session_id 查找挂起操作，未完整实现最新前后端 API v1.0 中 confirm_id 的确认语义。

### 本轮修复

- 改为先 `pending_confirm.get(session_id)` 读取挂起操作而不删除。
- 仅当 `decision == "approve"` 或 `decision == "reject"` 且 `confirm_id` 匹配时才执行 `pop`。
- 缺少 confirm_id、confirm_id 不匹配、decision 非法时均返回 error 并保留 pending。

### 验收方式

- 错误 decision 不会清空 pending。
- 缺少 confirm_id 不会清空 pending。
- confirm_id 不匹配不会清空 pending。
- reject 会清空 pending 并返回 status：操作已取消。
- approve 会清空 pending 并继续调用现有 `_process_low_risk`。

---

## Part：白名单配置持久化失败处理修复

> 日期：2026-06-20
> 关联分支：feature/backend-foundation

### 问题描述

`save_config()` 在 SQLite 写入失败时只记录日志，不向调用方抛出异常。`update_whitelist()` 在持久化失败后仍然更新内存缓存并返回「白名单已更新」，导致前端误认为保存成功但数据库实际未写入。

### 产生原因

配置持久化 helper 为了容错吞掉了异常，但 PUT 接口属于用户显式保存操作，失败时应明确返回错误。

### 本轮修复

- `save_config()`：记录日志后重新抛出异常。
- `update_whitelist()`：捕获异常并返回 HTTP 500（`detail="白名单配置持久化失败"`）。
- 仅在所有 `save_config()` 成功后更新运行时缓存 `_runtime_commands` / `_runtime_blocked`。
- `get_whitelist()`：同时检查 `_runtime_commands` 和 `_runtime_blocked` 缓存状态。
- role 字段确认输出为 `agent-read` / `agent-op` / `agent-admin` 字符串（Permission 类成员本身就是规范字符串）。

### 验收方式

- PUT 成功时返回 `message/saved_commands/saved_blocked_patterns`。
- SQLite 写入失败时不会返回「白名单已更新」。
- GET `/api/config/whitelist` 返回的 commands 每项包含 `pattern/role/risk`。
- role 字段为 `agent-read` / `agent-op` / `agent-admin` 之一。
- 重启后端后 GET 仍返回 PUT 写入的内容，确认持久化生效。

---

## Part：监控 SSE 异常结构与网络速率非负保护

> 日期：2026-06-20
> 关联分支：feature/backend-foundation

### 问题描述

`/api/monitor/stream` 在采集异常时只返回 `{"error": "采集失败"}`，导致前端图表组件缺少 `cpu_percent` / `memory_percent` / `disk_percent` 等核心字段。同时网络速率基于两次采样差值计算，在网卡计数器重置或运行环境变化时理论上可能出现负数。

### 产生原因

初版监控接口优先完成结构对齐，异常分支和边界采样场景处理较简单。

### 本轮修复

- SSE 异常分支改为返回与正常数据一致的核心字段，并额外携带 `error` 字段。
- 网络速率计算增加 `max(0, delta)` 保护，避免出现负数。

### 验收方式

- `/api/monitor/metrics` 仍返回嵌套结构。
- `/api/monitor/stream` 正常推送仍返回扁平结构。
- SSE 异常 fallback 包含 `cpu_percent` / `load_avg` / `memory_percent` / `disk_percent` / `net_in_kbps` / `net_out_kbps` / `timestamp`。
- `rx_kbps` / `tx_kbps` / `net_in_kbps` / `net_out_kbps` 不应为负数。
