# 工具注册与调用系统重构计划

> 分析日期: 2026-07-24 | 分析范围: backend + frontend + mcp-server

---

## 一、当前架构总览

```
┌─────────────────────────────────────────────────────────────────────┐
│                        tool_definitions.json                        │
│  静态 JSON: sys_info, service_mgr, log_reader, cmd_exec, ...        │
│  定义: name, params, default_risk, action_field, audit_policy       │
└────────────────────────────────┬────────────────────────────────────┘
                                 │ 加载
                                 ▼
┌─────────────────────────────────────────────────────────────────────┐
│                       ToolRegistry._registry                        │
│  静态工具定义 {name → ToolSpec}                                       │
│  不包含 server_id                                                    │
└─────────────────────────────────────────────────────────────────────┘
                                 ▲
                                 │ register_from_server(server_id, tools)
┌────────────────────────────────┴────────────────────────────────────┐
│                   ToolRegistry._tools (动态 MCP 工具)                 │
│  从 MCP 服务器发现: {name → MCPTool}                                 │
│  缺少 risk_level / audit_policy / action_field                       │
└─────────────────────────────────────────────────────────────────────┘
```

**核心问题**：工具定义被分成两个孤立的存储，中间靠 `_static_tool_to_server` 映射桥接。动态工具只能获得默认的 `low` 风险等级和 `full` 审计模式。

---

## 二、核心文件清单

| 文件 | 角色 | 行数 |
|---|---|---|
| `backend/app/services/tool_registry.py` | 工具注册表核心（双存储模型） | 845 |
| `backend/data/tool_definitions.json` | 静态工具定义配置文件 | ~150 |
| `backend/app/services/agent_harness.py` | 统一工具调用入口 | 324 |
| `backend/app/services/action_service.py` | 修复操作安全回查+执行层 | 552 |
| `backend/app/services/mcp_server_manager.py` | MCP 服务器生命周期管理 | 315 |
| `backend/app/mcp/client.py` | MCP JSON-RPC 客户端 | ~350 |
| `backend/app/api/actions.py` | 工具定义管理 API | 225 |
| `backend/app/services/orchestrator.py` | Agent 编排器（含硬编码工具列表） | ~250 |
| `frontend/src/components/ToolsSettings.vue` | 前端工具管理界面 | 188 |

---

## 三、需要重构的 6 个核心问题

### 🔴 问题 1：静态工具定义与 MCP 服务器强分离，无法为动态工具提供完整元信息

**现状**：
- `tool_definitions.json` 中定义的工具（如 `service_mgr`、`sys_info`）有完整的 `default_risk`、`action_field`、`audit_policy`
- 但 JSON 中的工具没有 `server_id`，需要通过 `_static_tool_to_server` 映射表来查找（`tool_registry.py:304-306`, `597行`）
- 从 MCP 动态发现的工具有 `server_id`，但缺少 `risk_level` / `audit_policy` 等元信息
- `get_tool_info()`（`tool_registry.py:520-550`）对动态工具硬编码 `risk_level: "low"`，无法自定义

**影响**：新增 MCP 插件（如 `net_monitor.py`）后，工具可以被发现和调用，但风险等级固定为 `low`、审计策略固定为 `full`，用户无法在前端进行配置。

**涉及代码位置**：
- `tool_registry.py:292-306` — `__init__` 双存储初始化
- `tool_registry.py:520-550` — `get_tool_info` 动态工具硬编码
- `tool_registry.py:697-710` — `get_default_risk` 动态工具固定返回 "low"
- `tool_registry.py:712-720` — `get_risk_for_action` 无动态工具 action 覆盖

---

### 🔴 问题 2：前端只管理静态工具，看不到动态 MCP 工具

**现状**：
- `ToolsSettings.vue` 调用 `GET /api/tools/definitions` → `tool_registry.get_all_tool_definitions()`
- `get_all_tool_definitions()`（`tool_registry.py:416-421`）**只遍历 `self._registry`（静态 JSON），完全忽略 `self._tools`（动态工具）**
- 用户无法在前端看到 MCP 动态发现的工具，更无法配置其风险等级和审计策略

**影响**：前端工具管理功能只覆盖 7 个预定义工具，MCP 服务器连接后动态注入的工具成为"幽灵工具"。

**涉及代码位置**：
- `tool_registry.py:416-421` — `get_all_tool_definitions` 只看 `_registry`
- `frontend/src/components/ToolsSettings.vue:106-116` — 前端只调用 `/tools/definitions`

---

### 🟡 问题 3：新增工具需要同时修改 3 个地方，分散且易遗漏

当前新增一个 MCP 插件（如 `mcp-server/plugins/xxxx.py`）需要：
1. 在 `mcp-server/server.py` 中注册工具处理函数
2. 在 `tool_definitions.json` 中添加工具定义（可选，但不加就没有审计/风险控制）
3. 在 `orchestrator.py` 中更新 Agent 的 system prompt 中的工具列表（`_DIAGNOSE_TOOLS` 硬编码）
4. 在各 Agent 的 system prompt 中添加工具使用说明

**影响**：工具注册流程分散，容易遗漏步骤 2-4，导致工具可用但缺乏安全管控。

---

### 🟡 问题 4：Agent system prompt 中硬编码工具列表

**现状**：
- `orchestrator.py` 中有**硬编码的工具分配**（如 `sys_info`, `service_mgr`, `log_reader` 分配给诊断 Agent）
- 各 Agent（`diagnose_agent.py`, `fix_planner_agent.py`, `intent_agent.py`, `reporter_agent.py`）的 system prompt 中包含**硬编码的工具名称和用途描述**
- 这些硬编码不会随工具注册表动态变化

**影响**：新增工具不会出现在 LLM 的 prompt 中，LLM 不知道有新工具可用。虽然 `get_openai_functions()` 能动态生成 function calling schema，但 system prompt 中的工具使用指南（指南式描述）是静态的。

**涉及代码位置**：
- `orchestrator.py` — `_DIAGNOSE_TOOLS` / `_FIX_TOOLS` 等常量
- `diagnose_agent.py` — system prompt 中的工具列表段落
- `fix_planner_agent.py` — system prompt 中的工具列表段落
- `intent_agent.py` — system prompt 中的工具列表段落
- `reporter_agent.py` — system prompt 中的工具列表段落

---

### 🟡 问题 5：动态工具缺乏审计策略和风险覆盖机制

**现状**：
- `get_default_risk()`（`tool_registry.py:697-710`）对动态工具返回固定 `"low"`
- `get_risk_for_action()`（`tool_registry.py:712-720`）对动态工具回退到 `get_default_risk`，无法按 action 区分
- `build_audit_metadata()`（`tool_registry.py:806-845`）对动态工具回退到 `full` 模式
- `update_tool_risk()` 和 `update_tool_audit_policy()` 只操作 `_registry`，无法修改动态工具的属性

**影响**：所有动态发现的 MCP 工具在安全层面都处于最低管控水平。

---

### 🟢 问题 6：缺少统一的工具管理 API

**现状**：
- API 提供了 `/api/tools/definitions`（读写静态工具的风险和审计策略）
- API 提供了 `/api/mcp/servers`（注册/管理 MCP 服务器）
- 但**没有 API 能够**：
  - 列出所有工具（静态 + 动态）并显示它们的来源
  - 为动态工具设置风险等级和审计策略
  - 查看工具的可用性状态（MCP 服务器是否在线）
  - 批量导入/导出工具定义

---

## 四、重构方案

### 🎯 目标架构

```
┌──────────────────────────────────────────────────────────────────┐
│                    统一工具注册表 (UnifiedToolRegistry)             │
│                                                                   │
│  ┌─────────────────────────────────────────────────────────────┐ │
│  │  ToolDefinition (单一模型)                                    │ │
│  │  - name, description, params (完整 Schema)                    │ │
│  │  - default_risk, action_field, action_risk_overrides         │ │
│  │  - audit_policy (mode, safe_fields, summary_builder)          │ │
│  │  - source: "static" | "mcp"                                   │ │
│  │  - server_id: str (MCP 工具关联的服务器，静态为空或 mcp 名)    │ │
│  │  - status: "available" | "unavailable"                        │ │
│  └─────────────────────────────────────────────────────────────┘ │
│                                                                   │
│  持久化: SQLite + JSON 双写，管理端通过 API 读写                  │
│  发现: MCP 服务器连接时自动注册，断开时标记为 unavailable          │
└──────────────────────────────────────────────────────────────────┘
```

---

### 📋 具体重构任务清单

#### Phase 1: 数据模型统一（backend/core）

**任务 1.1**: 合并 `ToolSpec` 和 `MCPTool` 为统一的 `ToolDefinition` 模型
- 文件: `backend/app/services/tool_registry.py`
- 具体变更:
  - 新增 `UnifiedToolDefinition` dataclass，包含 `ToolSpec` 的全部字段 + `source`、`server_id`、`status`
  - 兼容 `get_openai_functions()` 格式输出
  - 同时支持静态定义和动态发现

**任务 1.2**: 重构 ToolRegistry 为单一注册表
- 文件: `backend/app/services/tool_registry.py`
- 具体变更:
  - 废弃 `_registry` / `_tools` 双存储，改为统一的 `_definitions: Dict[str, UnifiedToolDefinition]`
  - `register_from_server()` 时：如果 JSON 中已有同名工具的元信息定义，合并（使用 JSON 中的 risk/audit 覆盖默认值）；如果没有，用默认 `low`/`full` 创建
  - `unregister_server()` 时：标记工具为 `unavailable` 而非删除，保留用户配置

**任务 1.3**: 支持对动态工具的 risk/audit 配置持久化
- 文件: `backend/app/services/tool_registry.py`
- 具体变更:
  - `update_tool_risk()` / `update_tool_audit_policy()` 扩展到统一模型
  - 动态工具的配置修改持久化到 `tool_definitions.json`（或独立存储），MCP 重连后恢复

---

#### Phase 2: API 层扩展

**任务 2.1**: `GET /api/tools/definitions` 返回全部工具（静态 + 动态）
- 文件: `backend/app/api/actions.py`, `backend/app/services/tool_registry.py`
- 具体变更:
  - 修改 `get_all_tool_definitions()` 返回 `_registry + _tools`
  - 每条记录包含 `source`, `server_id`, `status` 字段
  - 前端可据此判断工具来源和可用性

**任务 2.2**: 新增 `POST /api/tools/definitions/refresh` 手动触发工具重新发现
- 文件: `backend/app/api/actions.py`
- 功能: 调用 `mcp_server_manager.connect_all_and_discover()` 并返回发现结果

**任务 2.3**: `PUT /api/tools/definitions/{tool_name}/risk` 和 `/audit` 支持动态工具
- 文件: `backend/app/api/actions.py`, `backend/app/services/tool_registry.py`
- 功能: 移除只操作 `_registry` 的限制，扩展对动态工具的修改能力

---

#### Phase 3: Agent 动态工具提示

**任务 3.1**: 消除 `orchestrator.py` 中硬编码的工具列表
- 文件: `backend/app/services/orchestrator.py`
- 具体变更:
  - `_DIAGNOSE_TOOLS` 等常量替换为从 `ToolRegistry` 动态查询
  - 根据工具的 `description` 和 `source` 自动生成 Agent system prompt 中的工具说明段
  - 每次 Agent 轮次前刷新工具列表（已通过 `get_openai_functions()` 实现）

**任务 3.2**: Agent system prompt 模板化
- 文件: `backend/app/services/diagnose_agent.py`, `fix_planner_agent.py`, `intent_agent.py`, `reporter_agent.py`
- 具体变更:
  - 将工具列表部分抽取为 `{available_tools_section}` 模板变量
  - 由 orchestrator 在构建 Agent 时注入动态工具列表

---

#### Phase 4: 前端增强

**任务 4.1**: ToolsSettings.vue 显示全部工具（静态 + 动态）
- 文件: `frontend/src/components/ToolsSettings.vue`
- 具体变更:
  - 新增 `来源` 列（静态定义 / MCP 服务器名）
  - 新增 `状态` 列（available / unavailable）
  - 动态工具同样可配置风险等级和审计策略

**任务 4.2**: 新增工具注册引导流程
- 文件: `frontend/src/components/ToolsSettings.vue`（或新组件）
- 功能:
  - MCP 服务器连接成功后，引导用户为新发现的工具设置风险等级和审计策略
  - 提供"一键应用默认安全策略"功能

---

#### Phase 5: MCP 插件端优化

**任务 5.1**: MCP 工具自描述增强
- 文件: `mcp-server/server.py`, `mcp-server/plugins/*.py`
- 具体变更:
  - 每个 MCP 插件输出更丰富的元信息（建议加入 `suggested_risk`、`category` 等字段）
  - `server.py` 在 `list_tools()` 时携带这些扩展属性

---

## 五、影响范围评估

| 影响文件 | 变更程度 | 说明 |
|---|---|---|
| `backend/app/services/tool_registry.py` | 🔴 重写 | 核心重构，统一双存储模型 |
| `backend/data/tool_definitions.json` | 🟡 扩展 | 新增 `server_id`、`source` 字段 |
| `backend/app/api/actions.py` | 🟡 修改 | 扩展返回格式，新增 refresh 接口 |
| `backend/app/services/agent_harness.py` | 🟢 小幅 | 适配新的统一查询接口 |
| `backend/app/services/action_service.py` | 🟢 小幅 | 适配新的 `build_audit_metadata` |
| `backend/app/services/orchestrator.py` | 🟡 修改 | 消除硬编码工具列表 |
| `backend/app/services/diagnose_agent.py` | 🟡 修改 | system prompt 动态化 |
| `backend/app/services/fix_planner_agent.py` | 🟡 修改 | system prompt 动态化 |
| `backend/app/services/intent_agent.py` | 🟢 小幅 | 如有硬编码需清理 |
| `backend/app/services/reporter_agent.py` | 🟢 小幅 | 如有硬编码需清理 |
| `backend/app/services/mcp_server_manager.py` | 🟢 小幅 | 返回更丰富的发现结果 |
| `frontend/src/components/ToolsSettings.vue` | 🟡 修改 | 展示全部工具 + 来源/状态 |
| `mcp-server/server.py` | 🟢 可选 | 扩展 `list_tools` 元信息 |
| `backend/tests/test_tool_registry.py` | 🟡 更新 | 适配新接口 |

---

## 六、实施优先级建议

| 优先级 | Phase | 原因 |
|---|---|---|
| P0 | Phase 1 (数据模型统一) | 是一切后续工作的基础 |
| P0 | Phase 2 (API 扩展) | 动态工具的配置入口 |
| P1 | Phase 4 (前端增强) | 用户可见性，与 Phase 2 配套 |
| P2 | Phase 3 (Agent 动态提示) | LLM 能发现新工具 |
| P3 | Phase 5 (MCP 插件优化) | 锦上添花，非阻塞 |

---

## 七、风险与注意事项

1. **向后兼容**：`get_openai_functions()` 输出格式不变，LLM 调用不受影响
2. **持久化安全**：`tool_definitions.json` 扩展字段需处理旧版格式的自动迁移
3. **并发安全**：MCP 服务器重连与用户手动刷新可能有竞态，需加锁
4. **降级策略**：MCP 服务器离线时，工具标记为 `unavailable` 而非删除，LLM 调用时返回友好错误
5. **测试覆盖**：重构后需确保所有现有测试通过，新增覆盖动态工具场景的测试

---

## 八、预期的 UnifiedToolDefinition 数据结构

```python
@dataclass
class UnifiedToolDefinition:
    """统一的工具定义（合并 ToolSpec + MCPTool）"""
    name: str
    description: str
    params: Mapping[str, ToolParamSpec]
    default_risk: RiskLevel                          # low / medium / high
    action_field: str | None = None
    action_risk_overrides: Mapping[str, RiskLevel]   # {action_value: risk}
    audit_policy: AuditPolicy
    source: Literal["static", "mcp"]                 # 工具来源
    server_id: str = ""                              # MCP 服务器 ID
    status: Literal["available", "unavailable"] = "available"
```

### 新旧接口兼容映射

```python
# 旧接口 → 新实现
def get_all_tool_definitions(self) -> List[Dict]:
    """现在返回 _registry + _tools 的全部工具"""
    result = []
    for name, defn in self._definitions.items():
        d = defn.to_dict()
        d["source"] = defn.source
        d["server_id"] = defn.server_id
        d["status"] = defn.status
        result.append(d)
    return result

def get_openai_functions(self) -> List[Dict]:
    """不变：始终返回完整工具列表的 OpenAI function calling 格式"""
    # 内部从统一 _definitions 构建，输出格式不变
    ...