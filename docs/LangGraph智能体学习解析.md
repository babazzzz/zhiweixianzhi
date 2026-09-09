# 智维先知 — LangGraph 智能体学习解析

> 面向 LangGraph 初学者的项目级代码导读  
> 重点：Agent 搭建、Skills 技能层、记忆系统、RAG  
> 项目路径：`d:\daima\ziweixianzhi_langgraph`

---

## 目录

1. [先建立全局认知：这个项目在做什么](#1-先建立全局认知这个项目在做什么)
2. [LangGraph 入门：三个核心概念](#2-langgraph-入门三个核心概念)
3. [两套系统：教学版 vs 企业版](#3-两套系统教学版-vs-企业版)
4. [系统 A（CLI 教学版）完整走读](#4-系统-acli-教学版完整走读)
5. [系统 B（企业 API 版）完整走读](#5-系统-b企业-api-版完整走读)
6. [Agent 搭建：从函数到图节点](#6-agent-搭建从函数到图节点)
7. [Skills 技能层：为什么要有、怎么写](#7-skills-技能层为什么要有怎么写)
8. [记忆系统：四种「记忆」在本项目中的含义](#8-记忆系统四种记忆在本项目中的含义)
9. [RAG：三种检索策略对比](#9-rag三种检索策略对比)
10. [条件边与 Loop 工程：自验收循环](#10-条件边与-loop-工程自验收循环)
11. [LLM 路由：什么时候用模型、什么时候不用](#11-llm-路由什么时候用模型什么时候不用)
12. [学习路径建议：按什么顺序读代码](#12-学习路径建议按什么顺序读代码)
13. [动手实验清单](#13-动手实验清单)

---

## 1. 先建立全局认知：这个项目在做什么

### 1.1 业务目标

**智维先知**是一个 CNC 机床**预测性维护（Predictive Maintenance, PdM）**系统：

| 输入 | 处理 | 输出 |
|------|------|------|
| 传感器时序数据（温度、电流、振动等） | 异常检测 → 故障诊断 → 寿命预测 → 维护决策 | 诊断报告、维护工单、成本对比 |

一句话：**在设备还没坏之前，提前发现风险并给出可执行的维修建议。**

### 1.2 为什么用 LangGraph 而不是普通 Python 脚本？

普通脚本：`A() → B() → C()`，线性、难循环、难可视化。

LangGraph 把流程建模为**有向图**：

```
节点 = 一个 Agent（一个函数）
边   = 执行顺序
条件边 = 根据状态决定下一步（通过/回退/结束）
State = 所有节点共享的「黑板」
```

本项目利用 LangGraph 的核心能力：

1. **流水线编排**：Monitor → Diagnostic → … → Report
2. **质量门循环**：Reflection 不通过 → 回退 Diagnostic 重试（最多 3 次）
3. **检查点记忆**：`MemorySaver` + `thread_id` 保存执行快照
4. **流式输出**：`astream` 把每个节点的中间结果推给前端

### 1.3 项目最重要的设计决策

| 决策 | 原因 |
|------|------|
| **确定性算法优先，LLM 可选** | 工业场景不能靠模型「猜」；算法可复现、可审计 |
| **Agent 负责编排，Skills 负责计算** | 职责分离：Agent 读 State、调 Skill、写 State |
| **E1–E7 证据链** | 每条结论都能追溯到数据来源，避免黑盒 |
| **两套系统共存** | CLI 版教 LangGraph 基础；API 版教企业级架构 |

---

## 2. LangGraph 入门：三个核心概念

在读任何源码之前，先理解 LangGraph 的三要素。本项目两个系统的代码都围绕这三点展开。

### 2.1 State（状态）— 共享黑板

State 是一个 **TypedDict**（带类型注解的字典），所有节点读它、写它。

```python
# 伪代码：LangGraph 的执行方式
state = {"device_ids": ["CNC_007"], "messages": []}
state = supervisor_agent(state)      # 只返回要更新的字段
state = data_pipeline_agent(state)   # LangGraph 自动 merge 进 state
state = analysis_agent(state)
# ...
```

**关键：字段的「合并策略」**

| 写法 | 含义 | 本项目示例 |
|------|------|-----------|
| 普通字段 | 后写覆盖前写 | `diagnosis_result`, `health_score` |
| `Annotated[list, operator.add]` | 列表追加合并 | `events`（企业版） |
| `Annotated[list, add_messages]` | 消息追加 | `messages`（聊天历史） |

教学版 State 定义见 `src/state.py`：

```55:69:src/state.py
class AgentState(MessagesState):
    """
    ==========================================
    全局状态定义（继承自 MessagesState）
    ==========================================
    MessagesState 是 LangGraph 内置的状态基类，
    它已经包含了 messages 字段（用于存储聊天消息），
    我们在这里扩展项目所需的额外字段。
    ...
    """
```

企业版 State 定义见 `src/agents/state.py`：

```12:40:src/agents/state.py
class DiagnosticState(TypedDict, total=False):
    """Serializable state for one machine diagnostic run."""

    machine_id: str
    trigger_timestamp: str
    sensor_data: dict[str, float]
    history: list[dict[str, float]]
    health_score: float
    ...
    events: Annotated[list[dict[str, object]], operator.add]
    messages: Annotated[list[BaseMessage], add_messages]
```

**学习要点**：把 State 想象成一张不断被填写的表格。每个 Agent 只负责填自己那几列。

---

### 2.2 Node（节点）— 一个 Agent = 一个函数

LangGraph 里，**节点就是一个普通 Python 函数**：

```python
def my_agent(state: AgentState) -> dict:
    # 1. 从 state 读取输入
    data = state.get("raw_data")
    # 2. 做业务逻辑
    result = do_something(data)
    # 3. 返回要更新的字段（不是整个 state！）
    return {"analysis_result": result}
```

企业版用 `async def`，因为 FastAPI 和 LLM 调用都是异步的：

```python
async def monitor_node(state, database) -> dict:
    readings = database.get_readings(state["machine_id"])
    result = detect_anomaly(...)  # 调用 Skill
    return {**result.as_dict(), "events": [...]}
```

**为什么返回 dict 而不是 return state？**  
LangGraph 会自动把返回值 **merge** 进全局 State。这样每个节点只声明「我改了什么」，代码更清晰。

---

### 2.3 Edge（边）— 控制执行顺序

| 类型 | API | 作用 |
|------|-----|------|
| 普通边 | `graph.add_edge("A", "B")` | A 完成后必定执行 B |
| 条件边 | `graph.add_conditional_edges(...)` | 根据函数返回值选下一路径 |
| 入口/出口 | `START`, `END` | 图的开始和结束 |

条件边是本项目 **Loop 工程**（自验收循环）的核心，后面第 10 节详讲。

---

## 3. 两套系统：教学版 vs 企业版

**同一个仓库，两条完全独立的执行路径。** 学习时建议先 CLI 版，再企业版。

```
┌─────────────────────────────────────────────────────────────────┐
│                        同一仓库                                  │
├────────────────────────────┬────────────────────────────────────┤
│  系统 A — CLI 教学版        │  系统 B — 企业 API 版               │
├────────────────────────────┼────────────────────────────────────┤
│  入口: python src/main.py  │  入口: uvicorn backend.app:app     │
│  图: src/graph/build.py    │  图: src/graph/main_graph.py       │
│  State: src/state.py       │  State: src/agents/state.py        │
│  8 个节点                  │  7 个节点                          │
│  数据: CSV (5维)           │  数据: SQLite (22维, 67200条)      │
│  算法: src/tools/          │  算法: src/skills/                 │
│  RAG: 内嵌关键词           │  RAG: SQLite + ChromaDB + 因果图   │
│  LLM: ReAct 为主           │  LLM: 可选增强，算法兜底            │
└────────────────────────────┴────────────────────────────────────┘
```

| 对比项 | 系统 A | 系统 B |
|--------|--------|--------|
| 学习 LangGraph 基础 | ⭐⭐⭐ 注释多、概念全 | ⭐⭐ 更贴近生产 |
| 学习 Skills 分层 | ⭐ 算法在 tools/ | ⭐⭐⭐ 独立 skills/ |
| 学习 RAG | ⭐⭐ 简单关键词 | ⭐⭐⭐ 多层知识 |
| 学习记忆/检查点 | ⭐⭐ MemorySaver | ⭐⭐⭐ + 数据库持久化 |

---

## 4. 系统 A（CLI 教学版）完整走读

### 4.1 执行入口

```48:50:src/main.py
    graph = build_analysis_graph()
    result = run_analysis(devices, graph, args.thread)
```

`run_analysis` 做三件事：

1. `create_initial_state(device_ids)` — 初始化黑板
2. `config = {"configurable": {"thread_id": thread_id}}` — 绑定会话 ID（给 MemorySaver 用）
3. `graph.invoke(initial_state, config)` — 跑完整张图

---

### 4.2 状态图结构（8 节点）

```
START
  ↓
[Supervisor]        ← 接收设备列表，创建任务
  ↓
[Data Pipeline]     ← 加载 CSV，计算 22 维特征
  ↓
[Analysis]    ←──────┐  ← ReAct + 算法兜底
  ↓                  │
[Diagnosis]          │  ← E1-E7 证据链 + 故障匹配
  ↓                  │
[Decision]           │  ← 成本分析 + 工单
  ↓                  │
[Self-Check] ────────┘  ← 5 项质检，不通过回退
  ↓ (pass)
[RAG]               ← 关键词检索案例
  ↓
[Report]            ← 拼接 Markdown 报告
  ↓
END
```

构建代码在 `src/graph/build.py`：

```226:277:src/graph/build.py
    graph = StateGraph(AgentState)

    graph.add_node("supervisor", wrap_node(supervisor_agent))
    graph.add_node("data_pipeline", wrap_node(data_pipeline_agent))
    graph.add_node("analysis", wrap_node(analysis_agent))
    ...
    graph.add_edge(START, "supervisor")
    graph.add_edge("supervisor", "data_pipeline")
    ...
    graph.add_conditional_edges(
        "self_check",
        route_after_check,
        {"pass": "rag", "fail": "analysis"}
    )
    graph.add_edge("rag", "report")
    graph.add_edge("report", END)

    compiled = graph.compile(checkpointer=MemorySaver())
```

**逐行理解：**

| 代码 | 含义 |
|------|------|
| `StateGraph(AgentState)` | 声明 State 的类型结构 |
| `add_node(name, fn)` | 注册节点，name 是路由用的字符串 |
| `add_edge(A, B)` | A 完成后执行 B |
| `add_conditional_edges` | 调用 `route_after_check(state)`，返回值决定下一节点 |
| `compile(checkpointer=MemorySaver())` | 编译成可执行图，并启用内存检查点 |

`wrap_node` 的作用：把 numpy 类型转成 Python 原生类型，避免 JSON 序列化失败。

---

### 4.3 各 Agent 节点详解（系统 A）

#### 4.3.1 Supervisor — 调度员

**文件**：`src/agents/supervisor.py`

**做什么**：把用户给的 `device_ids` 转成 `analysis_tasks` 列表。

**核心逻辑**（门槛版）：

```python
# 输入：state["device_ids"] = ["CNC_007", "CNC_012"]
# 输出：
return {
    "analysis_tasks": [
        {"device_id": "CNC_007", "type": "full_analysis", "status": "pending"},
        ...
    ],
    "current_device": "CNC_007",  # 当前分析第一台
    "messages": [HumanMessage(...)]  # 追加到聊天历史
}
```

**为什么需要它？**  
在多 Agent 架构里，Supervisor 模式很常见：一个「项目经理」分解任务。本项目中它较简单，主要是**演示 LangGraph 多节点流水线**的第一站。

**LLM 的作用**：生成一句确认话术；LLM 挂了也不影响后续流程。

---

#### 4.3.2 Data Pipeline — 数据预处理

**文件**：`src/agents/data_pipeline.py`

**数据流**：

```
CSV 文件
  → load_machine_logs()           # src/tools/data_loader.py
  → get_device_data(df, id)       # 按设备提取时序
  → compute_all_devices_features() # src/tools/feature_engineering.py
  → state.raw_data + state.feature_vectors
```

**为什么单独做一个节点？**  
数据加载和特征工程耗时、逻辑独立。拆成节点后：

- 可以单独测试
- 未来可换成数据库/API 而不动下游 Agent
- 符合 LangGraph「一个节点一件事」的原则

---

#### 4.3.3 Analysis — ReAct 分析（★ 重点）

**文件**：`src/agents/analysis.py`

这是系统 A 里**最接近「智能体」**的节点，使用了 LangGraph 预置的 ReAct Agent。

**ReAct 是什么？**

```
Thought（思考）→ Action（选工具）→ Observation（看结果）→ 再思考 → ...
```

**工具定义**（用 `@tool` 装饰器）：

```60:85:src/agents/analysis.py
@tool
def analyze_temperature(data: list[float]) -> dict:
    """分析温度数据，检测温度异常和趋势。..."""
    zscore = zscore_anomaly_detection(data)
    cusum = cusum_detection(data)
    return {"anomaly_count": zscore["anomaly_count"], ...}

@tool
def estimate_rul(temperature_data: list[float], operating_hours: float = 100) -> dict:
    """基于威布尔分布估计设备剩余寿命(RUL)..."""
    return weibull_rul_estimation(temperature_data, operating_hours)

TOOLS = [analyze_temperature, analyze_current, estimate_rul, comprehensive_device_analysis]
```

**创建 ReAct Agent**：

```102:118:src/agents/analysis.py
    llm = get_agent_llm()
    agent = create_react_agent(model=llm, tools=TOOLS, prompt=REACT_SYSTEM_PROMPT)

    try:
        result = agent.invoke({"messages": [("user", f"请全面分析设备 {device_id} ...")]})
        conclusion = result["messages"][-1].content
    except Exception as e:
        conclusion = "降级算法模式（ReAct不可用）"

    # 无论 ReAct 是否成功，都跑确定性算法
    algo_result = comprehensive_analysis(time_series)
```

**为什么这样设计？**

| 设计 | 原因 |
|------|------|
| ReAct + 工具 | 教你怎么用 LangGraph/LangChain 的工具调用 |
| 算法始终执行 | LLM 不可用时系统仍能出结果（**降级策略**） |
| 工具内部调 `algorithms.py` | 工具是 LLM 的「手」，算法是「肌肉」 |

**学习 ReAct 的最小心智模型**：

1. 你定义若干 `@tool` 函数
2. `create_react_agent` 把 LLM + 工具绑在一起
3. LLM 自己决定调用哪个工具、调用几次
4. 最终结果在 `result["messages"]` 里

---

#### 4.3.4 Diagnosis — 故障诊断

**文件**：`src/agents/diagnosis.py`

**输入**：`analysis_result` + 原始时序  
**输出**：`diagnosis_result`（风险等级、E1-E7 证据链、Top-3 故障匹配）

**E1-E7 证据链**（诊断框架）：

| 证据 | 含义 | 数据来源 |
|------|------|----------|
| E1 | 温度异常 | 时序中 >55℃ 的比例 |
| E2 | 电流异常 | 时序中 >15A 的比例 |
| E3 | 电压稳定性 | 电压标准差 |
| E4 | 转速波动 | 转速标准差 |
| E5 | 综合异常 | Isolation Forest / 贝叶斯融合分 |
| E6 | 寿命预测 | 威布尔 RUL / 故障概率 |
| E7 | 趋势分析 | 温度线性拟合斜率 |

**故障匹配**（`match_fault_type`）：遍历 `FAULT_MODELS`（10 种故障），按证据链分数加权，返回 Top-3。

**为什么用规则匹配而不是纯 LLM？**  
工业诊断需要**可解释**：工程师要知道「因为 E1 温度高 + E2 电流高，所以匹配轴承磨损」，而不是模型黑盒输出。

---

#### 4.3.5 Decision — 维护决策

**文件**：`src/agents/decision.py`

根据 `risk_level` 查 `RISK_LEVELS` 配置 → 算预防性 vs 纠正性维护成本 → 生成 SOP 工单。

核心公式（简化）：

```
预防成本 = 检查费(2000) + 必要时换件(12000)
纠正成本 = 紧急维修(25000) + 停机损失(小时×5000) + 换件
节省     = 纠正 - 预防
```

---

#### 4.3.6 Self-Check — 自验收（Loop 入口）

**文件**：`src/agents/self_check.py`

5 项纯代码检查（不依赖 LLM）：

1. 结构完整性 — 必填字段是否存在
2. 逻辑一致性 — risk_level 和 risk_score 是否匹配
3. 数值合理性 — 分数是否在 [0,1]
4. 证据链完整性 — E1-E7 是否齐全
5. 故障匹配质量 — 是否匹配到有意义的故障

输出 `verification_status`：`pass` / `fail` / `max_retry`

---

#### 4.3.7 RAG — 案例检索（系统 A 版）

**文件**：`src/agents/rag.py`

见第 9 节 RAG 详解。

---

#### 4.3.8 Report — 报告生成

**文件**：`src/graph/build.py` 中的 `report_generator`

纯 Python 字符串拼接，不调用 LLM → **格式稳定、可预测**。

---

## 5. 系统 B（企业 API 版）完整走读

### 5.1 执行入口

FastAPI 启动时编译图：

```54:59:src/api/main.py
async def lifespan(application: FastAPI):
    application.state.database = bootstrap_database()
    application.state.fault_graph = FaultGraph()
    application.state.graph = build_graph(application.state.database, application.state.fault_graph)
```

触发诊断：

```201:202:src/api/main.py
    state = await app.state.graph.ainvoke(
        {"machine_id": request.machine_id, "retry_count": 0, ...},
        {"configurable": {"thread_id": thread_id}}
    )
```

诊断结果**持久化到 SQLite** 的 `diagnostics` 表 → 这是**情节记忆（Episodic Memory）**。

---

### 5.2 状态图结构（7 节点）

```
START
  ↓
[Monitor]              ← 读 SQLite 传感器 → detect_anomaly()
  ↓ is_anomaly=True          ↓ False
[Diagnostic]               END（正常设备不诊断）
  ↓
[Knowledge]            ← FMECA + 案例 + 因果图
  ↓
[Prediction]           ← predict_rul() 威布尔区间
  ↓
[Decision]             ← analyze_cost_benefit() 三方案
  ↓
[Report]               ← generate_report()
  ↓
[Reflection] ──pass──→ END
  └──fail──→ [Diagnostic]（最多 3 次）
```

构建代码：

```52:68:src/graph/main_graph.py
    workflow = StateGraph(DiagnosticState)
    workflow.add_node("monitor", monitor)
    workflow.add_node("diagnostic", diagnostic)
    ...
    workflow.add_edge(START, "monitor")
    workflow.add_conditional_edges("monitor", route_monitor, {"diagnostic": "diagnostic", "end": END})
    ...
    workflow.add_conditional_edges("reflection", route_reflection, {"retry": "diagnostic", "end": END})
    return workflow.compile(checkpointer=MemorySaver())
```

---

### 5.3 依赖注入：为什么用闭包包装节点？

企业版节点需要 `Database`、`FaultGraph` 等依赖，但 LangGraph 的 `add_node` 只接受 `(state) -> dict`。

**解决方案**：在 `build_graph` 里用闭包绑定依赖：

```27:35:src/graph/main_graph.py
    async def monitor(state: DiagnosticState) -> dict[str, object]:
        return await monitor_node(state, database)

    async def diagnostic(state: DiagnosticState) -> dict[str, object]:
        return await diagnostic_node(state, database, causal_graph)
```

这是 LangGraph 项目里的常见模式：**图构建层负责 wiring，Agent 层负责业务**。

---

### 5.4 各 Agent 节点详解（系统 B）

| 节点 | 文件 | 调用 Skill | LLM |
|------|------|-----------|-----|
| Monitor | `monitor_agent.py` | `detect_anomaly` | 无 |
| Diagnostic | `diagnostic_agent.py` | `build_evidence_chain`, `match_cases` | 可选（`ENABLE_LLM_ENRICHMENT=1`） |
| Knowledge | `knowledge_agent.py` | `match_cases`, `FaultGraph.backward_trace` | 无 |
| Prediction | `prediction_agent.py` | `predict_rul` | 无 |
| Decision | `decision_agent.py` | `analyze_cost_benefit` | 无 |
| Report | `report_agent.py` | `generate_report` | 无 |
| Reflection | `reflection_agent.py` | 纯代码 5 项检查 | 无 |

**企业版核心哲学**：**Agent = 编排者，Skill = 执行者，LLM = 可选润色。**

即使 Ollama/DeepSeek 全部离线，Monitor → Report 仍能跑通。

---

## 6. Agent 搭建：从函数到图节点

这一节总结「如何自己搭一个 LangGraph Agent」，对照本项目代码。

### 6.1 最小 Agent 模板

```python
# Step 1: 定义 State
class MyState(TypedDict):
    input: str
    output: str
    messages: Annotated[list, add_messages]

# Step 2: 写节点函数
def my_agent(state: MyState) -> dict:
    result = do_work(state["input"])
    return {"output": result, "messages": [HumanMessage(content="done")]}

# Step 3: 建图
graph = StateGraph(MyState)
graph.add_node("work", my_agent)
graph.add_edge(START, "work")
graph.add_edge("work", END)
app = graph.compile()
```

本项目的 Supervisor 就是这个模板的实例。

---

### 6.2 带条件边的 Agent 链

```python
def route(state) -> str:
    if state["ok"]:
        return "next"
    return "retry"

graph.add_conditional_edges("check", route, {"next": "report", "retry": "analyze"})
```

对应 `route_after_check`（系统 A）和 `route_reflection`（系统 B）。

---

### 6.3 ReAct Agent（工具调用型）

系统 A 的 Analysis 节点展示了 LangGraph 预置 ReAct：

```python
from langgraph.prebuilt import create_react_agent

agent = create_react_agent(model=llm, tools=TOOLS, prompt=SYSTEM_PROMPT)
result = agent.invoke({"messages": [("user", "分析这台设备")]})
```

**注意**：`create_react_agent` 返回的是**子图**，可以：

- 作为独立 Agent 在节点里 `invoke`（本项目做法）
- 或 `add_node("analysis", agent)` 直接嵌入主图

本项目选择「节点内 invoke」是因为还要合并确定性算法结果。

---

### 6.4 异步 Agent + FastAPI

企业版全部 `async def`，配合：

```python
state = await graph.ainvoke(initial, config)
async for chunk in graph.astream(initial, config, stream_mode="values"):
    ...
```

`astream` 用于 SSE 流式推送（`src/api/main.py` 的 `/api/diagnostics/stream`）。

---

### 6.5 Agent 与 Skill 的分工（★ 架构重点）

```
┌──────────────┐     调用      ┌──────────────┐
│    Agent     │ ────────────→ │    Skill     │
│  (编排/读写   │               │  (纯函数/    │
│   State)     │ ←──────────── │   确定性)    │
└──────────────┘     返回结果   └──────────────┘
       │
       ↓ 可选
┌──────────────┐
│     LLM      │  仅用于语言生成/校验，不参与核心计算
└──────────────┘
```

| 层 | 职责 | 可测试性 | 示例 |
|----|------|----------|------|
| Agent | 读 State、调 Skill、写 State、记录 events | 集成测试 | `monitor_node` |
| Skill | 输入数据 → 输出结构化结果 | 单元测试 | `detect_anomaly` |
| LLM | 自然语言、可选 JSON 校验 | 需 mock | `_optional_llm_refinement` |

---

## 7. Skills 技能层：为什么要有、怎么写

### 7.1 什么是 Skill？

在本项目中，**Skill = 不依赖 LLM 的纯 Python 函数**，输入明确、输出结构化、行为可复现。

目录：`src/skills/`

| 文件 | 函数 | 作用 |
|------|------|------|
| `anomaly_detect.py` | `detect_anomaly()` | 三信号融合异常检测 |
| `rul_weibull.py` | `predict_rul()` | 威布尔 RUL 区间 P10/P50/P90 |
| `evidence_chain.py` | `build_evidence_chain()` | E1-E7 可追溯证据 |
| `cost_benefit.py` | `analyze_cost_benefit()` | 三方案成本对比 |
| `case_match.py` | `match_cases()` | 历史案例检索 |
| `fault_graph_skill.py` | `reason_fault()` | 因果图正/反向溯源 |
| `report_gen.py` | `generate_report()` | Markdown 报告 |

---

### 7.2 Skill 的标准写法

每个 Skill 遵循相同模式：

```python
@dataclass(frozen=True)
class AnomalyResult:
    health_score: float
    is_anomaly: bool
    ...

    def as_dict(self) -> dict:
        return asdict(self)

def detect_anomaly(latest, history) -> AnomalyResult:
    # 纯计算，不调 LLM
    ...
    return AnomalyResult(...)
```

**为什么用 dataclass？**

- 输出契约清晰（IDE 有类型提示）
- `as_dict()` 方便 merge 进 LangGraph State
- `frozen=True` 防止结果被意外修改

---

### 7.3 核心 Skill 逐行解析

#### 7.3.1 `detect_anomaly` — 异常检测

**文件**：`src/skills/anomaly_detect.py`

**问题**：怎么判断一台设备「不正常」？

**做法**：对每个传感器维度，融合三种信号：

```
最终分数 = max(工程阈值分, Z-score分, 趋势分)
```

**基线策略（重要修复）**：

```45:48:src/skills/anomaly_detect.py
    samples = list(history)
    # Split: first 40% = baseline (healthy), latest reading = observation target
    baseline_count = max(10, int(len(samples) * 0.4))
    baseline_samples = samples[:baseline_count]
```

- 用历史**前 40%** 作为「健康期」算基线
- 用**最新一条**读数对比基线
- 这样渐进退化（仿真器注入的故障）能被检出，而不是被全历史均值稀释

**阈值表示例**（工程经验硬编码）：

```python
THRESHOLDS = {
    "op_temperature": (70.0, 90.0, 60.0, 105.0),  # 正常下限, 正常上限, 告警下限, 告警上限
    ...
}
```

**严重度判定**：

```python
if len(dims) >= 3 or anomaly_score >= 0.78:
    severity = "critical"
elif dims or anomaly_score >= 0.40:
    severity = "warning"
else:
    severity = "normal"
```

**为什么不用 LLM 判断异常？**  
异常检测需要稳定阈值；LLM 对数值的推理不可靠且不可复现。

---

#### 7.3.2 `predict_rul` — 剩余寿命预测

**文件**：`src/skills/rul_weibull.py`

**输出**：不是单点 RUL，而是**区间**：

| 分位 | 含义 | 本项目约定 |
|------|------|-----------|
| P10 | 乐观（寿命较长） | 最大 |
| P50 | 中位估计 | 中间 |
| P90 | 保守（寿命较短） | 最小 |

```python
# 约束：P10 > P50 > P90（代码里强制保证）
if p10 <= p50:
    p10 = p50 + 1
```

**输入信号**：

- 温度/电流的相对斜率 → `degradation_rate`
- `health_score` + `anomaly_score` → 风险系数
- 威布尔参数 β（形状）、η（尺度）→ 中位 RUL

**为什么用区间而不是单点？**  
工业维护需要「最坏情况规划」：P90 用于排期，P10 用于乐观估算。

---

#### 7.3.3 `build_evidence_chain` — 证据链

**文件**：`src/skills/evidence_chain.py`

企业版 E1-E7 **与系统 A 含义不同**，每条证据标注**数据来源**：

| ID | source | 内容 |
|----|--------|------|
| E1 | sensor_readings | 当前异常维度 |
| E2 | rolling_baseline | 基线偏离评分 |
| E3 | trend_analysis | 渐进 vs 突发 |
| E4 | case_library | 相似历史案例 |
| E5 | fmeca_rules | FMECA 规则匹配 |
| E6 | fault_graph | 因果图后向溯源 |
| E7 | maintenance_records | 维护记录对照 |

**设计原则**：`content` 字符串里的每个数字都来自传入参数，**禁止编造**。

---

#### 7.3.4 `analyze_cost_benefit` — 成本决策

**文件**：`src/skills/cost_benefit.py`

对比三种方案：

1. **立即维修** — 停机 + 直接成本
2. **计划窗口维修** — 直接成本 + 风险成本（72h 内故障概率 × 故障损失）
3. **减载运行 20%** — 低直接成本 + 较高风险成本 + 产能损失

选 `total_cost` 最低的方案作为推荐。

---

### 7.4 Skills 与 tools/ 的区别

| | `src/tools/`（系统 A） | `src/skills/`（系统 B） |
|--|----------------------|------------------------|
| 调用者 | CLI Agent 直接 import | 企业 Agent 通过 Skill 层 |
| 数据 | CSV 5 维 | SQLite 22 维 |
| 输出 | dict | dataclass + `as_dict()` |
| 设计 | 教学向，注释丰富 | 生产向，契约严格 |

学习 Skills 分层时，**以 `src/skills/` 为准**；`tools/` 是同一算法的教学版实现。

---

## 8. 记忆系统：四种「记忆」在本项目中的含义

AI Agent 语境下的「记忆」常被混用。本项目实际上有 **4 层**，对应不同代码：

### 8.1 工作记忆（Working Memory）— State 本身

**是什么**：图执行过程中，State 里临时存放的中间结果。

**代码**：`DiagnosticState` / `AgentState` 的所有字段。

**生命周期**：一次 `invoke` 从开始到结束。除非用 checkpointer 保存。

**示例字段**：

- `sensor_data`, `history` — 当前诊断用的数据窗口
- `evidence_chain`, `rul`, `decision` — 各节点产出
- `messages` — LLM 对话历史（`add_messages` 追加）

---

### 8.2 检查点记忆（Checkpoint Memory）— MemorySaver

**是什么**：LangGraph 在每个节点执行后保存 State 快照，可通过 `thread_id` 恢复。

**代码**：

```python
compiled = graph.compile(checkpointer=MemorySaver())
config = {"configurable": {"thread_id": "session-001"}}
graph.invoke(state, config)
```

**用途**：

- 同一会话重入（中断恢复）
- 调试：查看某 thread 的历史 checkpoint
- 人机协同：暂停等人工确认后继续

**局限**：`MemorySaver` 存在**进程内存**里，重启丢失。生产环境应换 `SqliteSaver` / Postgres checkpointer。

---

### 8.3 情节记忆（Episodic Memory）— SQLite 案例与诊断历史

**是什么**：「过去发生过什么」——历史故障案例、历次诊断结果。

**代码**：

| 表 | 内容 | 访问方式 |
|----|------|----------|
| `case_library` | 4+ 条标注案例 | `database.search_cases()` |
| `diagnostics` | 每次诊断完整 State | `database.save_diagnostic()` |
| `maintenance_records` | 维护工单 | `database.create_maintenance_order()` |

诊断完成后持久化：

```203:206:src/api/main.py
    database.save_diagnostic(diagnostic_id, request.machine_id, "completed",
        str(state.get("severity", "normal")), float(state.get("confidence", 0.0)), payload)
```

**Agent 如何使用**：Diagnostic 节点通过 `match_cases()` 检索相似案例，写入 E4 证据。

---

### 8.4 语义记忆（Semantic Memory）— 向量库 + 知识图谱

**是什么**：「世界知识/long-term facts」——故障模式、因果关系、维护手册。

**三层实现**：

| 层 | 技术 | 文件 |
|----|------|------|
| 结构化规则 | SQLite FMECA 表 | `src/database/`, `src/knowledge/fmeca.py` |
| 因果图谱 | NetworkX 有向图 | `src/knowledge/fault_graph.py` |
| 向量检索 | ChromaDB + bge-m3 | `src/knowledge/vector_store.py` |

**FaultGraph 示例**（症状 → 故障 → 后果）：

```
temperature_rise → bearing_wear → spindle_failure → machine_downtime
     (0.72)           (0.78)          (0.95)
```

`backward_trace("bearing_wear")` 回答：「观察到轴承磨损，可能的上游原因是什么？」

---

### 8.5 记忆层次对照表

| 记忆类型 | 存什么 | 存在哪 | 本项目 Agent 怎么用 |
|----------|--------|--------|---------------------|
| 工作记忆 | 当前诊断中间态 | State | 每个节点读写 |
| 检查点 | 执行快照 | MemorySaver | thread_id 恢复 |
| 情节记忆 | 历史案例/诊断 | SQLite | case_match, save_diagnostic |
| 语义记忆 | 规则/图谱/向量 | FMECA/NetworkX/Chroma | knowledge_agent, vector_store |

---

## 9. RAG：三种检索策略对比

RAG = Retrieval-Augmented Generation（检索增强生成）

**核心流程**：

```
用户/Agent 问题 → 检索相关知识 → 把知识塞进上下文 → LLM/规则生成答案
```

本项目有 **3 种检索实现**，复杂度递增：

### 9.1 策略一：内嵌关键词匹配（系统 A）

**文件**：`src/agents/rag.py`

**知识库**：17 条文本硬编码在 `_get_knowledge_base()`（7 案例 + 10 故障模式）

**检索算法**：

```python
# 1. 把查询拆成关键词集合
query_keywords = set(re.split(r'[,\s;，；]+', query.lower()))

# 2. 与每条知识的 keywords 求交集
match_count = len(query_keywords & item_keywords)

# 3. 按 match_count 排序，取 top_k
```

**为什么不用向量库？**

- 17 条数据，关键词匹配足够
- 更快、更可控、更可解释
- 不依赖 Ollama embedding 服务

**Agent 怎么用**：

```python
query = fault_name + fault_id + risk_level + 异常证据维度
matches = keyword_search(query, knowledge_base, top_k=3)
state["rag_context"] = 拼接案例文本
```

---

### 9.2 策略二：SQLite 结构化检索（系统 B 案例库）

**文件**：`src/skills/case_match.py` → `database.search_cases()`

按故障码 + 文本相似度从 `case_library` 表检索。

Diagnostic Agent 调用：

```python
cases = match_cases(database, query, fault_code, 3)
evidence = build_evidence_chain(..., cases, ...)
```

这是 **RAG 的「R」**，但 **「G」由确定性模板完成**（证据链文本拼接，不是 LLM 自由生成）。

---

### 9.3 策略三：ChromaDB 向量检索（可选）

**文件**：`src/knowledge/vector_store.py`

```python
class VectorStore:
    async def add_documents(self, collection, docs):
        embeddings = [await self._client.embed(text) for text in texts]  # bge-m3
        chroma_collection.upsert(ids=..., embeddings=embeddings, ...)

    def search(self, collection, query, top_k=5):
        # 优先 Chroma 语义搜索
        # 失败则降级到词袋 overlap
```

**降级设计**：Chroma 不可用 → 内存词袋匹配 → 系统仍可用。

**何时用向量 RAG？**

- 文档量大（手册、SOP、历史报告 > 100 条）
- 查询是自然语言，关键词难覆盖
- 需要语义相似而非字面匹配

---

### 9.4 三种 RAG 对比

| | 关键词（rag.py） | SQLite（case_match） | 向量（vector_store） |
|--|-----------------|---------------------|---------------------|
| 数据规模 | ~20 条 | 数十~数百条 | 数百~数万条 |
| 依赖 | 无 | SQLite | Chroma + bge-m3 |
| 可解释性 | ⭐⭐⭐ | ⭐⭐ | ⭐ |
| 语义理解 | ⭐ | ⭐⭐ | ⭐⭐⭐ |
| 本项目状态 | CLI 默认 | 企业版默认 | 已实现，可选启用 |

---

## 10. 条件边与 Loop 工程：自验收循环

Loop 工程 = **质量门 + 条件回退**，是 LangGraph 相比普通脚本的核心优势之一。

### 10.1 系统 A 的 Self-Check Loop

```
Decision → Self-Check
              │
    pass ──────┼──→ RAG → Report → END
              │
    fail ──────┼──→ Analysis（重新分析）
              │
  max_retry ───┴──→ RAG（带警告放行）
```

路由函数：

```68:97:src/graph/build.py
def route_after_check(state: AgentState) -> str:
    status = state.get("verification_status", "fail")
    if status == "pass":
        return "pass"
    elif status == "max_retry":
        return "pass"  # 超过 3 次也放行，但不阻塞
    else:
        return "fail"  # 回退到 analysis
```

**iteration_count** 在 Self-Check 节点每轮 +1，达到 `max_self_check_retries=3` 时置 `max_retry`。

---

### 10.2 系统 B 的 Reflection Loop

```
Report → Reflection
            │
  passed ────┼──→ END
            │
  failed ────┼──→ Diagnostic（retry_count + 1）
            │
  ≥ max ─────┴──→ END（warning_max_retries）
```

```77:80:src/graph/main_graph.py
def route_reflection(state: DiagnosticState) -> str:
    return "end" if bool(state.get("reflection_passed")) or int(state.get("retry_count", 0)) >= int(state.get("max_retries", 3)) else "retry"
```

Reflection 检查项（`reflection_agent.py`）：

1. 数据完整性 — sensor_data 和 history 非空
2. 逻辑一致性 — severity=normal 时 anomaly_score 不能太高
3. 证据链闭合 — E1-E7 全部存在
4. 预测合理性 — P10 > P50 > P90
5. 物理约束 — 温度 ≤180℃，degradation_rate ≤0.005

---

### 10.3 为什么需要 Loop？

| 没有 Loop | 有 Loop |
|-----------|---------|
| 分析错了直接出报告 | 质检发现问题，自动重跑 |
| 用户看到矛盾结论 | 最多 3 轮自我修正 |
| 无法做 Agent 可靠性工程 | 工业级「质量门」模式 |

这是从 Demo 到**企业级 Agent** 的关键差异之一。

---

## 11. LLM 路由：什么时候用模型、什么时候不用

### 11.1 模型路由表

**文件**：`src/config/model_routing.py`

| Agent | 模型 | 温度 | 用途 |
|-------|------|------|------|
| monitor | Ollama zhiwei-monitor | 0.1 | 轻量监控（实际未强依赖） |
| diagnostic | DeepSeek | 0.3 | 可选 JSON 校验 |
| prediction | DeepSeek | 0.2 | 配置存在，实际 Skill 计算 |
| decision | DeepSeek | 0.4 | 配置存在，实际 Skill 计算 |
| knowledge | Ollama zhiwei-knowledge | 0.1 | 配置存在，实际 DB 检索 |
| report | DeepSeek | 0.5 | 配置存在，实际模板生成 |
| reflection | Ollama zhiwei-reflection | 0.0 | 配置存在，实际代码检查 |

**发现**：路由表定义了「理想架构」，但当前实现里**多数 Agent 已用 Skill 兜底**，LLM 只在 `ENABLE_LLM_ENRICHMENT=1` 时参与 Diagnostic 校验。

---

### 11.2 UnifiedLLMClient

**文件**：`src/llm/client.py`

统一封装 Ollama + DeepSeek：

```python
result = await client.chat("diagnostic", [{"role": "user", "content": "..."}])
# 自动按 AGENT_ROUTING 选 provider/model/temperature
# 写 audit 日志到 data/logs/llm_audit.jsonl
```

**设计原则**：

- API Key 只从环境变量读，不进代码
- 3 次指数退避重试
- JSON 解析容错（去 markdown 代码块）

---

### 11.3 系统 A vs 系统 B 的 LLM 策略

| | 系统 A | 系统 B |
|--|--------|--------|
| LLM 角色 | ReAct 主导分析 | 可选润色 |
| 无 LLM 时 | 降级到 algorithms | 完全可运行 |
| 推荐学习 | 理解工具调用 | 理解确定性优先 |

---

## 12. 学习路径建议：按什么顺序读代码

### 阶段 1：LangGraph 三要素（1-2 天）

1. `src/state.py` — 理解 State 字段和 merge 策略
2. `src/graph/build.py` — 理解建图、边、条件边、MemorySaver
3. `src/agents/supervisor.py` — 最简单的节点
4. `src/main.py` — 跑通 `python src/main.py --devices CNC_007`

### 阶段 2：ReAct 与工具（1 天）

5. `src/agents/analysis.py` — ReAct + @tool
6. `src/tools/algorithms.py` — 工具背后的算法

### 阶段 3：Loop 与 RAG（1 天）

7. `src/agents/self_check.py` + `route_after_check`
8. `src/agents/rag.py` — 关键词 RAG
9. `src/agents/diagnosis.py` — 证据链

### 阶段 4：企业版 Skills 架构（2-3 天）

10. `src/agents/state.py` — 企业 State
11. `src/skills/anomaly_detect.py` — 第一个 Skill
12. `src/agents/monitor_agent.py` — Agent 调 Skill 范式
13. `src/graph/main_graph.py` — 异步图 + 依赖注入
14. `src/skills/evidence_chain.py` + `diagnostic_agent.py`

### 阶段 5：记忆与知识（1-2 天）

15. `src/database/connection.py` — SQLite 情节记忆
16. `src/knowledge/fault_graph.py` — 因果图谱
17. `src/knowledge/vector_store.py` — 向量 RAG
18. `src/api/main.py` — FastAPI 触发图 + 持久化

---

## 13. 动手实验清单

### 实验 1：改条件边，观察 Loop

修改 `src/config/__init__.py`：

```python
SYSTEM_CONFIG = {..., "max_self_check_retries": 1}
```

运行 CLI，观察 Self-Check 回退行为。

### 实验 2：关掉 LLM 跑企业诊断

不设置 `DEEPSEEK_API_KEY`，不设置 `ENABLE_LLM_ENRICHMENT`：

```bash
curl -X POST http://localhost:8000/api/diagnostics/trigger \
  -H "Content-Type: application/json" \
  -d '{"machine_id": "CNC_007"}'
```

验证：无 LLM 仍能返回完整 evidence_chain + rul + decision。

### 实验 3：调异常检测灵敏度

修改 `src/skills/anomaly_detect.py` 第 74-77 行阈值，重建后对比 CNC_007 的 severity。

### 实验 4：打印 LangGraph 流式事件

```bash
curl -N -X POST http://localhost:8000/api/diagnostics/stream \
  -H "Content-Type: application/json" \
  -d '{"machine_id": "CNC_007"}'
```

观察每个节点写入 `events` 字段的内容。

### 实验 5：单独测试 Skill（不跑图）

```python
from src.skills.anomaly_detect import detect_anomaly
from src.database.connection import Database

db = Database()
history = [dict(r["metrics"]) for r in db.get_readings("CNC_007", 240)]
latest = history[-1]
print(detect_anomaly(latest, history))
```

这是理解 **Agent vs Skill 分离** 最快的办法。

---

## 附录 A：关键文件速查

| 想了解… | 读这个文件 |
|---------|-----------|
| LangGraph 怎么建图（教学） | `src/graph/build.py` |
| LangGraph 怎么建图（企业） | `src/graph/main_graph.py` |
| State 怎么定义 | `src/state.py`, `src/agents/state.py` |
| ReAct Agent | `src/agents/analysis.py` |
| Skills 是什么 | `src/skills/*.py` |
| 异常检测算法 | `src/skills/anomaly_detect.py` |
| RUL 预测 | `src/skills/rul_weibull.py` |
| 关键词 RAG | `src/agents/rag.py` |
| 向量 RAG | `src/knowledge/vector_store.py` |
| 因果图谱 | `src/knowledge/fault_graph.py` |
| 检查点记忆 | `MemorySaver` in `build.py` / `main_graph.py` |
| 情节记忆 | `src/database/connection.py` |
| LLM 路由 | `src/config/model_routing.py` |
| API 入口 | `src/api/main.py` |

---

## 附录 B：一句话总结每个模块

| 模块 | 一句话 |
|------|--------|
| `graph/` | 把 Agent 函数连成有向图，加条件边实现 Loop |
| `agents/` | 每个函数是一个节点：读 State → 调 Skill/LLM → 写 State |
| `skills/` | 确定性算法，Agent 的「手」 |
| `tools/` | 教学版算法库，对应 skills 的简化实现 |
| `knowledge/` | 长期知识：规则 + 图谱 + 向量 |
| `database/` | 持久化：传感器 + 案例 + 诊断历史 |
| `llm/` | 统一 LLM 客户端，可选而非必须 |
| `simulator/` | 物理仿真生成 22 维训练/测试数据 |
| `api/` | HTTP 入口，触发 LangGraph 并持久化结果 |
| `web/` | React 前端展示 |

---

## 附录 C：从本项目到「自己搭 Agent」的 checklist

- [ ] 定义 TypedDict State（明确每个字段谁写、怎么 merge）
- [ ] 每个节点一个函数，返回 dict 而非整个 state
- [ ] 确定性逻辑放 Skill/tool，Agent 只做编排
- [ ] 用 `add_conditional_edges` 实现质检回退
- [ ] `compile(checkpointer=...)` 启用会话记忆
- [ ] 小数据用关键词 RAG，大数据再上向量库
- [ ] LLM 做增强不做唯一路径，始终准备降级方案
- [ ] 重要结论要有 evidence/source 追溯（如 E1-E7）

---

*文档版本：2026-08-15 · 基于项目源码逐模块分析编写*
