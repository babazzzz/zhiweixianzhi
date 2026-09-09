# AGENTS.md — 智维先知项目宪法

> **Codex必读文档**。每次会话开始时自动读取本文件以理解项目上下文。

## 项目身份

- **项目名称**：智维先知 — CNC设备预测性维护系统
- **项目阶段**：从Coze版迁移至LangGraph企业级多智能体架构
- **目标用户**：CNC设备运维工程师、生产线管理者
- **核心价值**：从"会说话的报表"进化为"7×24自主监控+预测+决策"的工业Agent

## 关键文档（必读）

在开始任何任务前，**必须按顺序阅读以下文档**：

| 优先级 | 文档 | 用途 |
|--------|------|------|
| P0 | `系统架构设计文档.md` | 系统架构、6个Agent、MCP、Skills、Loops设计 |
| P0 | `企业级Agent建设规划.md` | 战略规划、数据架构、Agent成熟度模型 |
| P0 | `模型选型与部署方案.md` | LLM路由策略、Ollama+DeepSeek混合方案 |
| P1 | `Codex开发指南.md` | 12个分阶段Prompt模板、本项目开发顺序 |
| P1 | `问题日志.md` | 原项目15项缺陷 + 新系统20项预期问题（设计规避） |
| P2 | `设备说明书.txt` | CNC设备规格、传感器定义 |
| P2 | `维护手册.txt` | 维护SOP、故障处理流程 |
| P2 | `案例知识库.txt` | 历史故障案例与处理方案 |
| P2 | `设备成本与产量.txt` | 维护成本数据 |
| P3 | `data_sample.csv` | 现有数据格式参考（前100行） |

## 技术栈（不可更改）

### 后端
- **LLM编排**：LangGraph（StateGraph + Send API）
- **LLM接口**：LangChain（ChatOllama + ChatOpenAI兼容）
- **API服务**：FastAPI + WebSocket/SSE
- **异步**：asyncio（LangGraph原生支持）
- **数据库**：
  - 时序数据 → TimescaleDB（PostgreSQL扩展）
  - 向量检索 → ChromaDB
  - 关系数据 → PostgreSQL
- **仿真器**：Python 3.13，基于物理模型（Paris疲劳/Arrhenius/Taylor刀具寿命）

### 前端
- React 18 + TypeScript
- Vite 构建
- shadcn/ui 组件库（中性配色、1px边框、8pt网格、radius 10px）
- Recharts / ECharts（图表）
- Tailwind CSS（样式）
- WebSocket客户端

### LLM
- **本地（Ollama）**：zhiwei-monitor / zhiwei-knowledge / zhiwei-reflection（基于qwen2.5:7b）+ bge-m3（嵌入）
- **云端（DeepSeek API）**：deepseek-chat（用于Diagnostic / Decision / Prediction / Report）
- **统一接口**：已实现 `src/llm/client.py`（UnifiedLLMClient），调用方法见下方

## 已实现模块（可直接使用）

```
src/config/model_routing.py   # 7个Agent的模型路由配置（含System Prompt）
src/llm/client.py             # 统一LLM客户端（Ollama+DeepSeek双通道）
.env.example                  # 环境变量模板（复制为.env后填Key）
test_models.py                # 本地模型连通性测试脚本
```

调用示例：

```python
from src.llm.client import UnifiedLLMClient
from src.config.model_routing import get_agent_config

client = UnifiedLLMClient()
config = get_agent_config("monitor")    # 获取Monitor Agent的配置

result = client.chat("monitor", [
    {"role": "user", "content": "温度78°C，判断是否异常"}
])
# result.content — 模型回复文本
# 自动路由到本地 zhiwei-monitor 或云端 DeepSeek
```

## 项目目录结构（待建）

```
智维先知/
├── AGENTS.md                          # 本文件
├── README.md                          # 项目说明
├── .env / .env.example                # 环境变量
├── data/                              # 数据层
│   ├── raw/                           # 原始仿真数据（Parquet/CSV）
│   ├── processed/                     # 清洗后数据
│   ├── chroma_db/                     # ChromaDB持久化目录
│   └── data_sample.csv                # 数据样本（前100行）
├── src/
│   ├── simulator/                     # CNC仿真器（Phase 1）
│   │   ├── physics_models.py          # Paris/Arrhenius/Taylor模型
│   │   ├── sensor_models.py           # 18维传感器仿真
│   │   ├── fault_injector.py          # 故障注入器
│   │   └── run_simulation.py          # 仿真入口
│   ├── database/                      # 数据库模块（Phase 1）
│   │   ├── timescale_client.py        # 时序库连接
│   │   ├── postgres_client.py         # 关系库连接
│   │   ├── chroma_client.py           # 向量库连接
│   │   └── schemas.py                 # 表结构定义
│   ├── knowledge/                     # 知识库（Phase 2）
│   │   ├── fmeca_parser.py            # FMECA表解析
│   │   ├── fault_graph.py             # 因果推理图（有向图）
│   │   ├── vector_store.py            # ChromaDB封装
│   │   └── graph_store.py             # NetworkX图存储
│   ├── mcp_servers/                   # MCP工具服务器（Phase 2）
│   │   ├── timeseries_server.py       # 时序数据MCP
│   │   ├── knowledge_server.py        # 知识检索MCP
│   │   ├── analytics_server.py        # 统计分析MCP
│   │   ├── physics_server.py          # 物理模型MCP
│   │   ├── relational_server.py       # 关系数据MCP
│   │   └── notify_server.py           # 通知推送MCP
│   ├── skills/                        # Skills能力封装（Phase 3）
│   │   ├── anomaly_detect.py          # 异常检测
│   │   ├── evidence_chain.py          # 证据链构建
│   │   ├── rul_weibull.py             # RUL预测（Weibull）
│   │   ├── fault_graph.py             # 故障图推理
│   │   ├── case_match.py              # 案例匹配
│   │   ├── cost_benefit.py            # 成本效益分析
│   │   └── report_gen.py              # 报告生成
│   ├── agents/                        # 多智能体（Phase 3）
│   │   ├── state.py                   # 全局State定义
│   │   ├── orchestrator.py            # Supervisor编排
│   │   ├── monitor_agent.py           # 监控Agent
│   │   ├── diagnostic_agent.py        # 诊断Agent（ReAct）
│   │   ├── prediction_agent.py        # 预测Agent
│   │   ├── decision_agent.py          # 决策Agent（Planning）
│   │   ├── knowledge_agent.py         # 知识Agent
│   │   ├── report_agent.py            # 报告Agent
│   │   └── reflection_agent.py        # 自检Agent（Reflection）
│   ├── graph/                         # LangGraph图定义（Phase 3）
│   │   ├── main_graph.py              # 顶层StateGraph
│   │   ├── diagnostic_subgraph.py     # 诊断子图（ReAct循环）
│   │   └── reflection_subgraph.py     # 自检子图
│   ├── config/                        # 配置（已实现）
│   │   └── model_routing.py
│   ├── llm/                           # LLM客户端（已实现）
│   │   └── client.py
│   └── api/                           # FastAPI（Phase 4）
│       ├── main.py
│       ├── routes/
│       └── websocket.py
├── web/                               # 前端（Phase 4）
│   ├── src/
│   ├── package.json
│   └── vite.config.ts
├── tests/                             # 测试
│   ├── unit/
│   ├── integration/
│   └── e2e/
└── outputs/                           # 产出物
    ├── reports/                       # 诊断报告
    └── charts/                        # 图表
```

## 硬性约束（违反任何一项 = 设计失败）

### 数据约束
- ❌ **禁止使用 random/randint 生成任何"假数据"** — 所有故障必须是物理退化模型推演结果
- ❌ **禁止硬编码 P5/P95 等统计量** — 必须从真实数据计算
- ❌ **禁止突变式故障注入** — 故障必须有7-30天的退化过程
- ✅ 仿真器规格：50台设备 × 180天 × 18维传感器 × 1Hz采样
- ✅ 故障率：3-5%（接近真实工业场景）
- ✅ 18维传感器：温度×5、振动×4、电气×3、力学×3、辅助×3

### 代码约束
- ❌ **禁止使用 `any` 类型**（TypeScript）— 所有变量必须有明确类型
- ❌ **禁止硬编码 API Key** — 全部从 .env 读取
- ❌ **禁止跳过 Reflection 自检** — 每个诊断必须经过5项自检
- ✅ 类型注解必须完整（Python用TypeHint，TypeScript用TS类型）
- ✅ 函数必须有 docstring
- ✅ 所有LLM调用必须记录到日志（输入、输出、耗时、token数）
- ✅ Agent节点失败必须重试，最多3次（Reflection Loop）

### UI约束（shadcn/ui规范）
- 中性配色：`#FAFAFA`背景、`#E4E4E7`边框、`#18181B`主文字
- 1px边框、低对比度、8pt网格
- 圆角统一：`radius: 10px`
- 字体：Inter / 系统字体
- ❌ 禁止花哨渐变、阴影、动画
- ❌ 禁止 Mock 数据 — 图表必须基于真实API数据

### 业务约束
- ✅ 证据链 E1-E7 必须完整：触发条件、传感证据、相似案例、规则匹配、知识图谱、统计基线、维修建议
- ✅ RUL预测必须给 P10/P50/P90 三档置信区间，禁止只给单点
- ✅ 决策必须包含：建议、窗口期、停机损失、备件清单、工时估算

## 开发顺序（严格遵守，禁止跳序）

```
Phase 1 — 数据基础
  1.1  Schema 定义（timescale_client / postgres_client / chroma_client）
  1.2  CNC仿真器（physics_models / sensor_models / fault_injector）
  1.3  数据加载器（仿真数据 → TimescaleDB）

Phase 2 — 知识与工具层
  2.1  FMECA 解析器（FMECA表 → 结构化规则库）
  2.2  故障传播图（节点 + 边，构建NetworkX有向图）
  2.3  向量知识库（设备手册/案例/标准 → ChromaDB）
  2.4  6个 MCP Server 实现

Phase 2.5 — 模型路由（已完成）
  src/config/model_routing.py + src/llm/client.py

Phase 3 — Agent编排
  3.1  State 定义（state.py）
  3.2  LangGraph 主图（main_graph.py）
  3.3  异常检测 Agent（monitor_agent.py + anomaly_detect skill）
  3.4  诊断 Agent（diagnostic_agent.py + evidence_chain skill，ReAct循环）
  3.5  预测 Agent（prediction_agent.py + rul_weibull skill）
  3.6  决策 Agent（decision_agent.py + cost_benefit skill，Planning循环）
  3.7  知识/报告/自检 Agent

Phase 4 — 集成与展示
  4.1  FastAPI 接口（GET/POST/WebSocket）
  4.2  前端仪表板（React + shadcn/ui）
  4.3  端到端测试
```

每个 Phase 完成后必须验证通过才能进入下一阶段。验证标准见 `Codex开发指南.md` 中每个 Prompt 的"完成定义"。

## 与Codex沟通的Prompt规范

每个Prompt必须包含4个要素：

```
【读什么】列出要读的文档路径
【做什么】明确要实现的功能（一个具体文件/一个具体功能）
【约束】列出相关硬性约束（从本文件"硬性约束"部分摘取）
【输出格式】函数签名、返回值、docstring、测试用例
```

模板示例：

```
请阅读 docs/系统架构设计文档.md 中的"3.1 异常检测Agent"章节，
阅读 src/config/model_routing.py 了解Agent配置接口，
阅读 维护手册.txt 了解CNC主轴相关规则。

实现 src/skills/anomaly_detect.py 的 isolated_forest 方法。

约束：
- 输入：18维传感器数据numpy数组
- 输出：异常分数（0-1）、异常维度列表、置信度
- 训练集：基于仿真器生成的正常数据（不少于10000个样本）
- 禁止使用 random 硬编码阈值
- 必须包含 docstring 和类型注解

完成后给出：
- 完整的Python代码
- 一个使用示例
- 单元测试用例（pytest格式）
```

## 反馈与升级

- 遇到不明确的需求 → 优先查询现有文档，再问用户
- 遇到方案冲突 → 在docs下记录 issue，等待用户决策
- 遇到技术难点 → 拆解为子问题分阶段解决
- 不确定时倾向保守方案（先跑通，再优化）

---

*最后更新：2026-08-09*
*本文件为项目宪法，修改前请充分评估影响*