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
