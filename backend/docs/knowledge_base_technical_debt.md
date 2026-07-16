# 知识库匹配模块 技术债记录

> 分支: `feat/knowledge-base-agent`
> 提交: `a142594` — `feat: 新增知识库匹配模块并接入诊断流程`
> 创建日期: 2026-07-16

---

## 知识库匹配模块

### 1. 问题背景

DiagnoseAgent 初始版本主要依赖工具执行结果和 LLM 推理生成诊断计划与报告。

**缺少的能力：**

- 历史故障经验库
- 常见问题（FAQ）库
- 已验证的解决方案模板

**导致的后果：**

诊断结果完全基于实时工具观测 + LLM 推理，缺少可追溯的知识依据。当系统遇到已知故障模式时，无法直接引用历史解决方案。

---

### 2. 修复方案

**新增 `KnowledgeBaseService`**，采用 SQLite 结构化知识库存储，支持三种条目类型：

| 类型 | 用途 |
|------|------|
| `faq` | 常见问题 |
| `fault_pattern` | 故障模式 |
| `solution` | 已验证的解决方案 |

**接入 DiagnoseAgent 诊断流程：**

```
用户输入
    ↓
IntentAgent（意图识别）
    ↓
DiagnoseAgent（诊断规划 + 工具执行）
    ↓
KnowledgeBaseService.search(intent, observations, user_input)
    ↓
知识增强诊断报告（ReporterAgent 追加「知识依据」章节）
```

**核心设计原则：**

- 使用项目已有 SQLAlchemy async session，不创建新数据库连接
- 知识库不可用时 fallback 到原有逻辑（不抛异常）
- 接口设计预留 VectorRetriever 替换空间
- 所有公开方法异常时返回安全默认值

---

### 3. 当前实现边界

**已实现：**

- ✅ SQLite 知识库（`knowledge_items` 表，通过 ORM 自动建表）
- ✅ 关键词匹配检索（基于 keywords / title / symptoms 字段）
- ✅ 统一 `search()` 接口（输入 user_input + intent + observations → 输出结构化结果）
- ✅ DiagnoseAgent 集成（`search_knowledge()` 方法）
- ✅ Orchestrator 流程接入（工具执行后、报告生成前查询）
- ✅ ReporterAgent 知识依据输出（报告末尾追加 `### 知识依据` 章节）
- ✅ RAG 扩展接口预留（KnowledgeBaseService 接口稳定，未来替换 Retriever 不影响调用方）

**未实现（有意延后）：**

- ❌ Embedding 向量化
- ❌ Vector Database（如 ChromaDB / Milvus）
- ❌ 语义检索 / RAG 完整管线
- ❌ 知识库管理 API（CRUD REST 接口）
- ❌ 知识条目批量导入
- ❌ 知识条目自动从历史对话中提取

**未来升级路径：**

当前 `KnowledgeBaseService` 的内部检索器可视为 `KeywordRetriever`。未来只需实现 `VectorRetriever`（相同接口签名），替换内部实现即可，`DiagnoseAgent` / `Orchestrator` / `ReporterAgent` 无需修改。

---

### 4. 已知风险

#### 风险 1：LLM 生成路径未注入知识结果

**现状：**

LLM 生成诊断报告时（`ReporterAgent.generate_with_llm`），知识库匹配结果仅追加在 Markdown 报告末尾作为「知识依据」展示，未注入 LLM 的 system prompt / user prompt 上下文。

**影响：** 低。

**原因：** 当前验收要求仅要求诊断结果包含知识依据，未要求 LLM 基于知识库内容推理。未来可将 `knowledge_result` 注入 LLM prompt，让 LLM 引用知识条目进行更精准的诊断。

**缓解措施：** 接口已预留 `knowledge_result` 参数传递到 `generate_with_llm()`，实现成本低。

---

#### 风险 2：关键词匹配为内存遍历

**现状：**

`_match_items()` 方法在内存中遍历所有知识条目，对每条执行关键词/title/symptoms 的字符串包含检查。

**影响：** 低（当前规模）。

**原因：** 知识条目数量预计在数百条以内，O(n) 遍历可接受。当条目增长到数千条以上时，需要优化。

**未来优化方向：**

- SQL 层预过滤（`WHERE keywords LIKE '%xxx%'` 或全文索引）
- 倒排索引（内存 map: keyword → [item_ids]）
- 向量检索（替换为 VectorRetriever）

---

#### 风险 3：关键词匹配精度有限

**现状：**

匹配逻辑基于精确子串匹配（`kw.lower() in search_text`），不考虑：
- 同义词（如 "502" 与 "Bad Gateway"）
- 中文分词（如 "CPU 使用率" 与 "处理器负载"）
- 拼写错误容错

**影响：** 中。

**原因：** 纯关键词匹配可能漏掉语义相近但不包含相同关键词的查询。用户需要输入较精确的术语才能命中。

**未来优化方向：** 替换为 VectorRetriever + Embedding 语义检索。

---

#### 风险 4：`_ensure_tables()` 每次调用建表

**现状：**

`_fetch_all()` 和 `add_item()` 每次调用都执行 `Base.metadata.create_all()`（幂等）。

**影响：** 低。

**原因：** 生产环境 `init_engine()` 已在启动时建表，此调用为空操作（幂等）。仅在测试环境自动兜底。`create_all` 内部检查表是否存在，已存在的表不会重复创建。

**优化方向：** 未来可添加 `_tables_ensured` 标志避免重复调用。

---

### 5. 验收记录

| 项目 | 结果 |
|------|------|
| **新增测试** | `test_knowledge_service.py` — 16 passed |
| **核心回归** | `test_diagnose_agent.py` + `test_reporter_agent.py` + `test_agent_context.py` + `test_orchestrator_agent_flow.py` — 124 passed |
| **py_compile** | 全部 10 个文件通过 AST 语法检查 |
| **代码审查** | 通过 |
| **修改文件** | 3 新增 + 7 修改，+663/-16 行 |
| **违反禁止项** | 无（未修改 HTTPS/WSS/Redis/cgroups/PostgreSQL/RBAC/SafetyGuard/MCP） |

---

### 6. 修改文件清单

| 操作 | 文件 | 说明 |
|------|------|------|
| 新增 | `app/models/knowledge.py` | KnowledgeItem ORM 模型 |
| 新增 | `app/services/knowledge_service.py` | KnowledgeBaseService |
| 新增 | `tests/test_knowledge_service.py` | 16 项测试 |
| 修改 | `app/models/__init__.py` | 注册 KnowledgeItem |
| 修改 | `app/services/agent_context.py` | 添加 knowledge_result 字段 |
| 修改 | `app/services/diagnose_agent.py` | 添加 search_knowledge() |
| 修改 | `app/services/orchestrator.py` | 流程接入 + knowledge_service 注入 |
| 修改 | `app/services/reporter_agent.py` | 追加知识依据章节 |
| 修改 | `app/dependencies.py` | 注册 KnowledgeBaseService 共享实例 |
| 修改 | `app/api/chat.py` | 注入 knowledge_service 到 Orchestrator |
