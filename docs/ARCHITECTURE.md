# 智维先知 — 系统架构文档（ARCHITECTURE）

> 适用版本：8 项模块优化全部落地后的架构（skills 注册表化 / 聊天 Agent / 混合 RAG /
> 四层记忆 / 数据层实时化 / 审批联动生成维护报告）。源码即事实，本文件与
> `README.md`、`项目详细报告.md` 保持一致。
>
> 阅读顺序：架构总览 → 三条 LangGraph 图 → 模型路由 → Skills 体系 → 混合 RAG →
> 四层记忆 → 可观测性 → 数据层 → API / 前端。

---

## 0. 一句话架构

> **LangGraph 把"设备监控 → 诊断 → 知识检索 → RUL 预测 → 决策 → 报告 → 反思"编排成
> 有向图；确定性 skills 承载算法；ChromaDB + BM25 做混合 RAG 喂养 LLM；四层记忆跨会话
> 沉淀；FastAPI 既当 API 网关又当"进程内 harness"来驱动自主循环与实时数据。**

### 0.1 分层全景

```
┌────────────────────────── 前端 web/（React + Vite）──────────────────────────┐
│  overview · machines · diagnostics · agents · notifications · chat · logs     │
│  reports · topology · knowledge · operations      （1280px 锁定布局）           │
└───────────────┬──────────────────────────────────────────────────────────────┘
                │  HTTP /api/*   +   WebSocket /ws/*
┌───────────────▼──────────────────────────────────────────────────────────────┐
│                       FastAPI   src/api/main.py  （50 个端点）                  │
│   trace 中间件  ·  lifespan 装配（建图/自主循环/实时引擎/Skills 发现/聊天图）      │
└───────┬───────────────┬───────────────┬───────────────┬──────────────────────┘
        │               │               │               │
┌───────▼──────┐ ┌──────▼───────┐ ┌─────▼──────┐ ┌─────▼──────────┐
│ 图A 诊断监督图 │ │ 图C 聊天图    │ │ Autonomous  │ │  RealtimeEngine│
│ src/graph/   │ │ chat_agent   │ │ Loop(自主)  │ │  (数据实时推进) │
│ main_graph   │ │ (7节点)       │ │            │ │                │
└──────┬───────┘ └──────┬───────┘ └─────┬──────┘ └────────┬───────┘
       │  函数级调用      │  确定性工具    │                 │
┌──────▼─────────────────▼──────────────▼─────────────────▼──────────────┐
│                        能力 / 数据 / 基础设施层                            │
│   Skills 体系(8子包)   MemoryManager(四层)   HybridRAG(BM25+bge-m3+RRF)   │
│   UnifiedLLMClient     SQLite(9+表)          观测(日志三件套)              │
└───────────────────────────────────────────────────────────────────────────┘
```

---

## 1. 三条 LangGraph 图（核心）

项目**不是一张大图**，而是按场景拆成三条独立的 `StateGraph`，各自编译、各自有
入口。理解这点是看懂整个系统的钥匙。

| 图 | 文件 | State | 节点链 | 循环/反思 | checkpointer |
|---|---|---|---|---|---|
| **A 诊断监督图**（API 实装）| `src/graph/main_graph.py` | `DiagnosticState` | monitor→diagnostic→knowledge→prediction→decision→report→reflection | 条件边 `reflection → retry/end`，最多重试 3 次 | `MemorySaver` |
| **B 分析图**（CLI 学习用）| `src/graph/build.py` | `AgentState(MessagesState)` | supervisor→data_pipeline→analysis→diagnosis→decision→self_check→rag→report | 条件边 `self_check → fail 回 analysis / pass 进 rag` | `MemorySaver` |
| **C 聊天图**（问答）| `src/agents/chat_agent.py` | `ChatState(TypedDict)` | classify→dispatch→compose | 无（线性）| 无（会话记忆走 SQLite）|

### 1.1 图 A：企业诊断监督图

```python
# src/graph/main_graph.py（伪代码 + 真实行号）
workflow = StateGraph(DiagnosticState)
workflow.add_node("monitor", monitor)        # 绑定 DB 的依赖注入闭包
workflow.add_node("diagnostic", diagnostic)
workflow.add_node("knowledge", knowledge)    # 用 RAG 检索故障先例
workflow.add_node("prediction", prediction)  # 威布尔 RUL
workflow.add_node("decision", decision)      # 分级处置
workflow.add_node("report", report_node)     # 拼装报告
workflow.add_node("reflection", reflection_node)
workflow.add_edge(START, "monitor")
workflow.add_conditional_edges("monitor", route_monitor,
                               {"diagnostic": "diagnostic", "end": END})   # L61
workflow.add_edge("diagnostic", "knowledge")  # … 依此类推 …
workflow.add_conditional_edges("reflection", route_reflection,
                               {"retry": "diagnostic", "end": END})        # L67
return workflow.compile(checkpointer=MemorySaver())                          # L68
```

**关键教学点：**

1. **State 是共享的"黑板"**。`DiagnosticState(TypedDict, total=False)`（`src/agents/state.py`），
   节点间通过它传数据。用 `Annotated[list, operator.add]` 实现"追加合并"、用
   `Annotated[list[BaseMessage], add_messages]` 实现"消息累积"。
2. **有条件边 = LangGraph 的"循环"手段**。`route_reflection` 返回 `"retry"` 就会绕回
   `diagnostic` 节点再跑一遍，直到 `reflection_passed=True` 或 `retry_count>=max_retries(3)`
   才 `END`。**这是"for 循环"，不是 ReAct**。
3. **`compile(checkpointer=MemorySaver())`**：MemorySaver 存每一轮图运行的状态，可按
   `thread_id` 断点续跑。但它是**进程内存**，重启即失——生产应换 SqliteSaver/Postgres。

### 1.2 图 B：分析图（学习/CLI 版）

用 `MessagesState`（自带 `add_messages`），节点经 `wrap_node()` 包裹自动把 numpy 转
原生类型。`self_check` 节点做质量自检，不通过就回 `analysis` 重做（最多 3 轮），
这就是"**反射-重试循环**"在图层面的落点。

### 1.3 图 C：聊天图（第三条图）

```python
# src/agents/chat_agent.py（伪代码）
graph = StateGraph(ChatState)
graph.add_node("classify", self._node_classify)   # 意图识别：规则优先 + DeepSeek 兜底
graph.add_node("dispatch", self._node_dispatch)   # 纯确定性：SQL / RAG，不走 LLM
graph.add_node("compose", self._node_compose)     # LLM 组织答案；失败模板兜底
graph.set_entry_point("classify")
graph.add_edge("classify", "dispatch")
graph.add_edge("dispatch", "compose")
graph.add_edge("compose", END)
return graph.compile()
```

意图路由到工具：
`device_info/records` → SQL 查库；`maintenance_sop` → RAG 查手册+FMECA；`knowledge_search`
→ 全库混合检索；`smalltalk` → 直接 compose。

---

## 2. 模型调用与路由（谁能被谁调、用什么模型）

### 2.1 双 Provider + 环境变量驱动的路由表 `src/config/model_routing.py`

```python
OLLAMA_MONITOR_MODEL = os.getenv("OLLAMA_MONITOR_MODEL", "qwen2.5:7b")
DEEPSEEK_MODEL       = os.getenv("DEEPSEEK_MODEL", "deepseek-chat")
EMBEDDING_MODEL      = os.getenv("OLLAMA_EMBEDDING_MODEL", "bge-m3")

AGENT_ROUTING = {  # agent → provider/model/温度/上下文窗口/…
  "monitor":   AgentModelConfig("ollama",  "qwen2.5:7b", 0.1, 512, 4096,  ...),
  "diagnostic":AgentModelConfig("deepseek","deepseek-chat", 0.3, 2048, 32768, ...),
  "prediction":AgentModelConfig("deepseek", ...),  "decision": ...,
  "knowledge": AgentModelConfig("ollama", ...),     "report": ..., "reflection": ...,
  "chat":      AgentModelConfig("ollama", "qwen2.5:7b", ...),
}
```

**原则**：轻量判断（monitor/knowledge/reflection/chat）走本地 Ollama `qwen2.5:7b`；
重推理（diagnostic/prediction/decision/report）走云端 DeepSeek。凭据**只来自环境变量**
（`.env` 的 `DEEPSEEK_API_KEY`），绝不写死在代码。

### 2.2 统一客户端 `src/llm/client.py`（httpx 直连，无需 OpenAI SDK）

- `UnifiedLLMClient`：Ollama 走 `POST {base}/api/chat`；DeepSeek 走
  `POST {base}/chat/completions`；embed 走 Ollama `api/embed`（bge-m3）。
- `deepseek_enabled` 属性决定是否可用（有 key 才 True）。
- 自动**降级链**：DeepSeek 无 key → 本地；诊断 LLM 增强由 `ENABLE_LLM_ENRICHMENT`
  门控（默认关，走确定性规则）；聊天 compose 异常 → `_template_answer()` 模板兜底；
  检索层 Ollama 挂 → 纯 BM25。**保证演示永远不白屏。**

> ⚠️ 项目里还有一套旧调用面 `src/llm/__init__.py::get_agent_llm`（`langchain_ollama.ChatOllama`），
> 供 supervisor.py 等 demo 节点用；新图节点走 `UnifiedLLMClient`。两套并存，教学时注意区分。

---

## 3. Skills 体系（"工具/能力"的工程化封装）

### 3.1 为什么做成注册表

目标：让"系统有哪些能力"可列举、可审计、可被前端与 agent 动态调用，而不是散落的
平铺函数。每个 skill = 一个**包**，固定 4 个文件：

```
src/skills/<id>/
├── __init__.py   只导出一个实例 SKILL = XxxSkill()
├── skill.py      XxxSkill(BaseSkill)，实现 run()
├── schema.py     pydantic 输入/输出模型（对外数据契约）
└── SKILL.md      人读文档：用途/入参/出参/示例/确定性声明/副作用
```

当前 8 个子包：`anomaly_detect / rul_weibull / evidence_chain / cost_benefit /
case_match / diagnostic_report / fault_graph_trace / maintenance_report`。

### 3.2 三件套契约（`src/skills/base.py`）

```python
class BaseSkill:
    meta: ClassVar[SkillMeta]           # id/name/category/deterministic/requires_llm/…
    input_schema: ClassVar[type[BaseModel]]
    output_schema: ClassVar[type[BaseModel]]
    def run(self, payload): ...          # 子类实现真实算法
    def to_langchain_tool(self) -> StructuredTool: ...  # 暴露给 ReAct 型 agent
```

### 3.3 三种调用方式（按场景选）

| 方式 | 代码 | 何时用 |
|---|---|---|
| ① 注册表统一入口 | `SkillRegistry.run("maintenance_report", {...})` | API / 审批链 / 需审计 |
| ② 直接 execute | `SkillRegistry.get(id).execute(**kw)` | 内部代码，仍走 pydantic 校验 |
| ③ LangChain Tool | `skill.to_langchain_tool()` | ReAct 型 agent（当前仅在 analysis 节点用本地 @tool）|

`SkillRegistry.run()` 是唯一会写 `tool_calls.jsonl` 审计的入口。注册是**自动发现**：
`discover()` 用 `pkgutil.iter_modules` 扫 `src/skills/`，凡导出 `SKILL` 实例即登记，
单个包失败只跳过不拖垮启动。

> **诚实说明**：`as_langchain_tools()` 目前**只定义未被调用**。真实接线有两条——
> 诊断节点用**函数级 import**（`diagnostic_agent.py` `from src.skills.case_match import match_cases`），
> 审批/API 用**注册表级** `SkillRegistry.run()`。ReAct 目前只存在于 `analysis` 节点内部
> 的一组本地 `@tool`（`src/agents/analysis.py`），不属于 skills 注册表。

---

## 4. 混合 RAG（知识库召回）

### 4.1 链路

```
data/knowledge/*.txt(唯一源)
   → src/knowledge/chunking.py  结构感知切分（标题路径前置正文）
   → src/knowledge/index_builder.py  build():
        ├─ BM25Index → data/index/bm25.pkl（稀疏）
        ├─ DenseIndex → data/chroma_db（稠密，bge-m3 1024 维）
        └─ fingerprint.json（sha256，源未变则跳过重嵌）
   → src/knowledge/retrieval.py  HybridRetriever.search(query):
        稠密 top3k + 稀疏 top3k → RRF 融合 → 元数据过滤 → top_k
```

### 4.2 为什么用 RRF 而不是分数加权（`retrieval.py` 头部注释）

BM25 分数无界、向量余弦 ∈[-1,1]，**量纲不可比**，直接加权会偏袒某一路。RRF 只取
**排名**：`score = Σ w/(RRF_K + rank)`（默认 `RRF_K=60`, `bm25_weight=1.0`,
`dense_weight=0.9`），对单通道失效更鲁棒。同分时优先 BM25 命中术语多的文档。

### 4.3 提升召回率的手段（上下文工程的工程侧）

1. **标题路径前置**：把「维护手册 > 故障类型1：轴承磨损 > 标准维修步骤」写进每个 chunk
   正文，稠密/稀疏双通道都受益。
2. **同义词双向扩展**（`chunking.py` `SYNONYM_GROUPS`）：`发热/温度/高温/temperature` 同组，
   只扩 BM25 侧，避免稀释稠密语义。
3. **切分策略分而治之**：案例按「【案例NNN】」整块、手册按「### 故障类型N」、说明书按
   「一、~六、」章节、成本表不切分走结构化解析。
4. **元数据过滤**：`fault_code`/`machine_id`/`doc_type` 供诊断时精准过滤。
5. **指纹增量重建**：源文件 sha256 未变则直接 `_load_from_disk`，启动秒级，不重嵌。

### 4.4 评估脚本 `scripts/rag_tools.py`

内置 12 条带预期命中词的查询，算 `recall@k`。实测 **recall@5 = 1.0（12/12）**，
其中 11 条 top1 命中。用法：

```bash
.venv/Scripts/python.exe scripts/rag_tools.py build   # 重建索引（含嵌入）
.venv/Scripts/python.exe scripts/rag_tools.py eval    # 召回率评估
```

---

## 5. 四层记忆

`langgraph.checkpoint.sqlite` 因缺 aiosqlite 不可用 → 用自建 SQLite 表承载。
`MemoryManager`（`src/memory/manager.py`）是门面。

| 层 | 存什么 | 存哪 | 不混乱的保证 |
|---|---|---|---|
| ① 会话记忆 | 聊天多轮 role/content/ts + 滚动摘要 | `conversation_threads` + `conversation_messages` | 按 thread_id 隔离；满 12 条自动摘要 |
| ② 情节记忆 | 诊断/工单/审核事件流水 | `agent_activity` + `diagnostics` | 只追加不修改，event_type 固定 |
| ③ 设备画像 | 健康轨迹/故障史/维修效果/人工反馈 | `long_term_memory`（kind=machine 等）| 由 `refresh_machine_profile()` 从 DB 沉淀，去重 |
| ④ 语义记忆 | 知识库向量 + 文本 | Chroma `knowledge` + `data/index` | 只读于运行期，重建走脚本 |

```python
# src/memory/manager.py
MEMORY_KINDS = ("machine", "fact", "preference", "lesson")
# open_thread / append_message(滚动摘要) / remember(去重) / recall(pinned优先) /
# build_context(注入) / refresh_machine_profile(情节→画像沉淀)
```

> 图 A、图 B 用 `MemorySaver()` 做**运行期**状态缓存；图 C 不用，会话记忆交给
> MemoryManager + SQLite。**MemorySaver 重启即失**，长期记忆在 SQLite，二者分工明确。

---

## 6. 可观测性（日志三件套 + trace_id）

`src/observability/` 落盘到 `data/logs/`：

| 文件 | 内容 |
|---|---|
| `app.log` | TSV 前缀（时间/级别/组件/trace_id）+ JSON，5MB×5 轮转 |
| `llm_audit.jsonl` | provider/model/tokens/latency/prompt 摘要/error |
| `tool_calls.jsonl` | caller/tool/args/latency/deterministic/output 摘要 |

**trace_id 贯穿**：API 中间件每请求 `trace_id = X-Trace-Id or new_trace_id()`，响应头回写；
`traced(component, action)` 上下文管理器嵌套复用同一 trace_id。前端日志页可按
trace_id 聚合 app+llm+tools 三类日志，一键追一条请求的完整调用链
（`GET /api/logs/trace/{trace_id}`）。

---

## 7. 数据层：确定性与断点续写

| 问题 | 答案 |
|---|---|
| 数据哪来？ | `src/simulator/` 用 Paris/Arrhenius/Taylor 退化模型 + `fault_injector` 6 台预置渐进故障，`sensor_models.deterministic_noise` 用多频正弦生成**可复现噪声**（非 random） |
| 每次运行重跑吗？ | 否。历史 67200 行一次性生成（INSERT OR IGNORE）落库后固定；重启不重造 |
| 实时推进 | `RealtimeEngine`（`src/data/realtime.py`）按真实时钟每拍推进所有设备，`workload_for` 纯时间推导工况（白/夜班、换刀、保养窗口）调制读数 |
| **断点续写** | 游标来源三级：① `data/sim_clock.json`（持久化文件，抗 DB 清空）→ ② DB `MAX(timestamp)` → ③ 启动时刻。每拍写回文件，重启从断点续写、历史不被覆盖 |
| 溯源 | 每批实时推进写 `data_provenance`，前端数据页可展示"谁/何时/什么模型算出" |

---

## 8. API 与前端

### 8.1 FastAPI（`src/api/main.py`，**50 个端点**）

分组：system(2) · monitoring(1) · machines(4) · diagnostics(4，含 SSE) · knowledge(4) ·
operations(2) · agent/自主(7) · observability(5) · skills(3) · chat(3) · memory(4) ·
reports(2) · data(3) + WebSocket(2：`/ws/realtime`,`/ws/agent`)。

**lifespan 装配**（启动顺序）：`bootstrap_database()` → `FaultGraph()` → 编译图 A →
`ActivityBus` → 按 `AUTONOMOUS_LOOP_ENABLED` 起自主循环 → 按 `REALTIME_ENABLED` 起
实时引擎 → `SkillRegistry.discover()` → 装配聊天 Agent。

### 8.2 前端（`web/src/`，React + Vite）

- `App.tsx` 薄壳 + 10 个导航页（overview/machines/diagnostics/agents/chat/logs/reports/
  topology/knowledge/operations），agents 带待审徽标，`WebSocket("ws://…/ws/realtime")`
  实时 + 每 10s 轮询待审通知。
- **尺寸锁定**：`body` 100vh 外壳 + 固定 232px 侧栏 + 主区内部滚动 + 内容区 `1280px`
  限宽居中；`@media(max-width:1250px)` 降级两列——消除"忽大忽小"。

---

## 9. 关于 MCP 与 "harness"

**如实说明**：本项目**没有接入 MCP（Model Context Protocol），也没有第三方 agent harness**。
代码里的"工具/能力"由 `SkillRegistry` 在进程内自动发现，并经 FastAPI `/api/skills` 暴露；
所谓"执行外壳（harness）"就是 **FastAPI app + `AutonomousLoop` + `ActivityBus`**：
- `AutonomousLoop` 用 `asyncio` `while not stop_event` 按 30s 节拍驱动图 A（扫描→诊断）；
- `ActivityBus` 在进程内广播事件；
- 图 A 的编译实例作为"智能体运行时"被 API 持有（`app.state.graph`）。

若要把这套能力开放成 MCP server（教学扩展方向）：可用 `fastmcp` 把每个 skill 包成
`@mcp.tool()`，对外暴露 `list_tools`/`call_tool`，让任意 MCP 客户端（Claude Desktop、
自研 Agent）按标准协议调用——这本质是把当前 `/api/skills` 的 REST 契约翻译成 MCP
JSON-RPC 工具发现协议。

---

## 10. 快速导航

| 想深入哪块 | 看文件 |
|---|---|
| LangGraph 图编排 | `src/graph/main_graph.py`、`src/graph/build.py`、`src/agents/chat_agent.py` |
| 模型路由/客户端 | `src/config/model_routing.py`、`src/llm/client.py` |
| Skills 注册表 | `src/skills/base.py` + 每个子包 |
| 混合 RAG | `src/knowledge/{chunking,retrieval,index_builder}.py` |
| 记忆 | `src/memory/manager.py` + connection 里 memory 相关方法 |
| 可观测性 | `src/observability/` |
| 数据/仿真/实时 | `src/data/`、`src/simulator/` |
| API | `src/api/main.py` |
| 测试 | `tests/`（pytest） |
