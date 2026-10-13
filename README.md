# 智维先知

> 基于 **LangGraph** 的 CNC 设备预测性维护系统：把「设备健康监控 → 故障诊断 → 剩余寿命预测 → 维护决策 → 报告生成 → 质量自检 → 自然语言问答」串成一个可无人值守运行的闭环。
> 后端 FastAPI + SQLite，前端 React 18，数据由确定性物理仿真器驱动。

![Python](https://img.shields.io/badge/Python-3.12-3776AB?logo=python&logoColor=white)
![FastAPI](https://img.shields.io/badge/FastAPI-0.141-009485?logo=fastapi&logoColor=white)
![React](https://img.shields.io/badge/React-18-61DAFB?logo=react&logoColor=black)
![LangGraph](https://img.shields.io/badge/LangGraph-1.2-1C3C3C)
![Tests](https://img.shields.io/badge/tests-44%20passed-brightgreen)
![License](https://img.shields.io/badge/license-MIT-blue)

---

## 目录

| 章 | 内容 |
| --- | --- |
| 1 | [这是什么 / 解决什么问题](#1-这是什么--解决什么问题) |
| 2 | [技术栈速览](#2-技术栈速览) |
| 3 | [快速启动](#3-快速启动) |
| 4 | [架构概览](#4-架构概览) |
| 5 | [数据从哪来](#5-数据从哪来) |
| 6 | [目录结构导览](#6-目录结构导览) |
| 7 | [接口与前端速览](#7-接口与前端速览) |
| 8 | [常用配置速查](#8-常用配置速查) |
| 9 | [系统边界与客观局限](#9-系统边界与客观局限) |
| 10 | [文档导航](#10-文档导航) |

---

## 1. 这是什么 / 解决什么问题

**智维先知**是一个面向 CNC 数控加工中心的设备预测性维护系统。它持续读取设备遥测，在设备**尚未损坏时**判断它的健康状态、定位可能的故障类型、估算还能用多久，并给出「现在修还是再等等」的成本对比，最后把结论整理成一份可追溯的报告。

工业现场的预测性维护有四个典型难点，系统的设计直接对应它们：

| 难点 | 系统的做法 |
| --- | --- |
| **健康退化难发现**——单点阈值报警要么太晚要么太吵 | 用**同侪包络**判据替代绝对阈值：把每台设备放进全厂同型号健康设备的群体里比较，只有「显著离群」才算异常。全厂一起缓慢老化不会被当成 100 台同时故障 |
| **故障类型难定位**——轴承磨损和散热故障都会推高温度 | 用**同侪偏差**找专属征兆（冷却水温只有散热故障才会偏离），按征兆**独特性**排序做故障隔离 |
| **维护时机难决策**——「现在停机修」和「再跑几天」代价不同 | 输出 **P10/P50/P90** 三档剩余寿命，并对「立即维修 / 计划窗口维修 / 减载运行 20%」三种方案用**同一套成本口径**定价，取总成本最低者 |
| **结论无法追责**——运维决策必须能解释为什么 | 每次诊断构造 **E1–E7** 七条可逐条回溯的证据链，报告里每个数字都注明数据出处 |

系统的三层结构：

| 层 | 职责 |
| --- | --- |
| **数据层** | 确定性物理仿真（Paris 疲劳 / Arrhenius 热老化 / Taylor 刀具磨损）、工况调制、时序落库、数据血缘 |
| **Agent 引擎层** | LangGraph 状态图（7 个诊断节点 + 3 个聊天节点）、8 个技能包、知识层（FMECA / 因果图 / 案例 / 混合 RAG）、四层记忆、混合 LLM 路由、自主循环 |
| **应用层** | 51 个 HTTP/WS 接口、10 页 React 控制台、JWT 三角色权限、报告导出与 IM 推送 |

一条重要的运行前提：**系统默认不调用大模型也能完整运行**——诊断、预测、决策、报告、自检全部是确定性算法与数据库实况，LLM 只作为可选的措辞润色与候选校验。

---

## 2. 技术栈速览

| 层 | 技术 |
| --- | --- |
| 运行环境 | Python 3.12（项目在 `.venv` 中验证） |
| Agent 编排 | **LangGraph** 1.2+（`StateGraph` / 条件边 / `MemorySaver` 检查点）+ langchain-core |
| 后端 | FastAPI 0.128+ · Uvicorn · Pydantic 2 · python-dotenv · **PyJWT**（HS256 认证） |
| 存储 | **SQLite（WAL 模式）** 14 张业务表 · pickle 稀疏索引 · ChromaDB（可选稠密索引） |
| 检索 | BM25（`k1=1.5, b=0.75`）+ bge-m3 稠密（**1024 维**）+ **RRF**（`K=60`，权重 1.0 / 0.9） |
| 图算法 | NetworkX（故障因果传播图，14 节点 / 12 条边） |
| 数值 | NumPy · Pandas · SciPy · scikit-learn |
| 前端 | React 18 · TypeScript 5 · Vite 5 · Tailwind CSS 3 · **echarts 5** · lucide-react 图标 |
| LLM（可选） | Ollama 本地 `qwen2.5:7b`（monitor / knowledge / reflection / chat）+ `bge-m3`（嵌入）；DeepSeek 云端 `deepseek-chat`（diagnostic / prediction / decision / report） |
| 测试 | pytest（**44 项**，约 4 s，7 个测试文件 + `conftest.py`） |

---

## 3. 快速启动

### 3.1 准备环境

```bash
# 1) 创建虚拟环境（Python 3.12）
python -m venv .venv
.venv\Scripts\python.exe -m pip install -U pip

# 2) 安装依赖（国内建议用清华源）
.venv\Scripts\pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple

# 3) 复制环境变量模板（不含任何密钥）
copy .env.example .env
```

**4) 启动本地 Ollama（可选，但稠密检索依赖它）**

```bash
# 保持常驻，默认监听 127.0.0.1:11434
ollama serve

# 嵌入模型：稠密检索通道必需，缺失时 RAG 自动退化为纯 BM25
ollama pull bge-m3

# 本地对话模型：monitor / knowledge / reflection / chat 四个节点的本地通道
# （不装则这些节点走 DeepSeek 云端；DeepSeek 未配置时相关节点降级为确定性模板）
ollama pull qwen2.5:7b
```

启动后可用 `curl http://127.0.0.1:11434/api/tags` 确认模型已就位；后端 `/health` 的 `llm.ollama` 与 `rag.dense_enabled` 也会反映其可用性。模型名与地址在 `.env` 的 `OLLAMA_*` 配置项中调整。

### 3.2 生成数据库（首次 clone 必须执行）

> **仓库里不包含 `.db` 文件。** 首次运行前必须先本地生成数据库。
> ⚠️ `regenerate` 会先删除再重建整个数据库文件——仅在首次生成或确需全量重建时执行；日常局部重算用 `scripts/recompute_fault_readings.py`。

```bash
.venv\Scripts\python.exe -m src.database.regenerate
```

这一步会：建表 → 从 `data/raw/MACHINE_SUMMARY_DATA._2025.csv` 载入 100 台设备主数据 → upsert 9 条 FMECA 规则与 4 条案例 → 用仿真器生成 **100 台 × 14 天 × 30 分钟** 的 22 维遥测（共 **67202 条**，`data/zhiwei.db` 约 62.25 MB）。耗时约 2–3 分钟，期间会按每 10 台打印进度。

### 3.3 启动后端

```bash
.venv\Scripts\uvicorn src.api.main:app --host 127.0.0.1 --port 8000
```

> **必须绑定 `127.0.0.1`，不要用 `0.0.0.0`。** 前端的 WebSocket 与 CORS 白名单都按 `localhost` / `127.0.0.1` 配置。

启动时 `lifespan` 会自动：装载数据库 → 构建因果图 → 编译 7 节点诊断图 → 创建 `ActivityBus` 与 `AutonomousLoop` → 发现 8 个技能 → **播种初始管理员账号**（幂等，凭据取自 `.env` 的 `ADMIN_USERNAME` / `ADMIN_PASSWORD`）。**自主循环与实时引擎默认关闭**，属正常现象。

后端日志写在项目根目录 `_backend.log`；结构化日志写在 `data/logs/`。

### 3.4 启动前端

```bash
cd web
npm install
npm run dev
```

Vite 固定监听 `http://127.0.0.1:5173`（见 `web/vite.config.ts`）。首次打开会跳转登录页，用 `.env` 里的管理员账号登录；右上角可切换浅色 / 暗色主题。

### 3.5 可选：构建知识索引

```bash
# 构建 BM25 + 稠密索引（稠密部分需要本地 Ollama 提供 bge-m3）
.venv\Scripts\python.exe scripts/rag_tools.py build

# 评估检索效果（15 条查询的 recall@5，当前 15/15）
.venv\Scripts\python.exe scripts/rag_tools.py eval
```

首次提问或首次调用知识检索时，系统也会自动构建索引（`KnowledgeIndex.ensure()`），因此这一步不是必须的。索引落在 `data/index/`（`bm25.pkl` + `chunks.json` + `fingerprint.json`）。**稠密通道依赖本地 Ollama 提供 bge-m3**：Ollama 未启动时嵌入调用失败，检索自动退化为纯 BM25，源文档的改动也不会写进向量库——改过 `data/knowledge/` 下的文档后，请确认 Ollama 在运行再执行 `build`。

### 3.6 访问地址

| 入口 | 地址 | 说明 |
| --- | --- | --- |
| 前端控制台 | http://127.0.0.1:5173 | 10 个页面（需登录） |
| API 文档（Swagger） | http://127.0.0.1:8000/docs | 49 个 REST 接口 |
| 服务元信息 | http://127.0.0.1:8000/ | 名称 / 版本 / 状态 |
| 健康检查 | http://127.0.0.1:8000/health | 数据、LLM 与知识索引状态（`rag.dense_enabled` 可确认 Ollama 稠密通道是否在服务） |
| 全厂 KPI | http://127.0.0.1:8000/api/overview | 设备 / 诊断 / 工单汇总（需令牌） |

### 3.7 跑测试

```bash
.venv\Scripts\python.exe -m pytest tests/ -q
# 期望输出：44 passed
```

测试全部跑在临时文件库上，不触碰 `data/zhiwei.db`，也不触发任何网络调用；其中 `tests/test_auth.py` 覆盖口令哈希、JWT 签发校验、用户 CRUD、角色依赖与管理员播种。

### 3.8 演示前预检（可选）

```bash
# 只读：复刻生产路径全量跑 100 台，打印「6/6 诊断准确率 + 0/94 健康机误报」
.venv\Scripts\python.exe scripts/verify_diagnosis_accuracy.py
```

---

## 4. 架构概览

### 4.1 分层图

```
┌───────────────────────── 应用层 ─────────────────────────┐
│  React 18 + TS + Vite（10 页控制台 + 登录页）             │
│  FastAPI 51 个接口：49 REST（含 1 SSE）+ 2 WebSocket      │
│  JWT 认证：viewer 只读 / operator 写 / admin 管理          │
└───────────────────────────┬──────────────────────────────┘
                            │ HTTP / WS / SSE
┌───────────────────── Agent 引擎层 ───────────────────────┐
│  main_graph（7 节点诊断图）     chat_agent（3 节点聊天图） │
│  技能层：SkillRegistry + 8 个技能包                        │
│  知识层：FMECA 9 条 / 因果图 14 节点 / 案例 4 条 / 混合 RAG │
│  记忆层：会话 / 情节 / 设备画像 / 语义                     │
│  LLM 层：Ollama（本地）+ DeepSeek（云端），可关            │
│  自治层：AutonomousLoop + ActivityBus + 通知审批链          │
└───────────────────────────┬──────────────────────────────┘
                            │
┌───────────────────────── 数据层 ─────────────────────────┐
│  确定性物理仿真器（Paris / Arrhenius / Taylor）           │
│  工况调制（班次 / 换刀 / 保养窗口）                        │
│  SQLite(WAL) 14 张表  +  ChromaDB（可选稠密索引）          │
└──────────────────────────────────────────────────────────┘
```

### 4.2 主诊断图：7 个节点 + 2 条条件边

```
START ─▶ monitor ─┬─ 有异常 ─▶ diagnostic ─▶ knowledge ─▶ prediction ─▶ decision ─▶ report ─▶ reflection ─┬─ 通过 ─▶ END
                  └─ 无异常 ─▶ END                                                                        └─ 不通过 ─▶ diagnostic（最多重试 3 次）
```

| 节点 | 做什么 |
| --- | --- |
| **monitor** | 读最近 240 条读数，用**同侪包络**判据做多信号异常检测，输出健康分 / 异常分 / 异常维度 |
| **diagnostic** | 算同侪偏差 → 按 7 条规则做故障码隔离 → 构造 E1–E7 证据链 → 算置信度 |
| **knowledge** | 取 FMECA 规则 + 相似案例 + 因果图**后向溯源**路径 |
| **prediction** | 威布尔 RUL → P10/P50/P90 三档区间 + 退化率 + 动态监测频率 |
| **decision** | 三方案成本对比（立即维修 / 计划窗口 / 减载运行 20%）→ 推荐总成本最低者 |
| **report** | 纯确定性 Markdown 拼装（不调用大模型） |
| **reflection** | 5 项质量门（数据完整性 / 逻辑一致性 / 证据链闭合 / 预测合理性 / 物理约束）→ 通过放行，否则回退重诊 |

两条条件边的精确判定：

```python
route_monitor    → "diagnostic" if is_anomaly 且无 error else "end"
route_reflection → "end" if 自检通过 或 重试次数已达上限(3) else "retry"（回 diagnostic）
```

### 4.3 聊天图：3 个节点

```
classify ─▶ dispatch ─▶ compose ─▶ END
```

| 节点 | 做什么 |
| --- | --- |
| **classify** | 规则优先的意图分类（6 类：`machine_info` / `machine_sop` / `machine_records` / `knowledge_search` / `general` / `chitchat`），抽出 `CNC_078` 形式的设备号；仅在规则未打准且云端可用时才让 LLM 兜底 |
| **dispatch** | 走**确定性** SQL / RAG 取数据（不依赖 LLM） |
| **compose** | 让 LLM 把工具结果组织成人话；LLM 不可用时用模板兜底，**聊天窗口永远有答案** |

分工原则：**主图产生结论，聊天图解释结论**——聊天图不参与任何判定。

### 4.4 依赖注入与检查点

`build_graph(database, fault_graph)` 用闭包把仓储层与因果图注入节点包装函数，节点自身保持「只依赖 State」的纯函数形态。图以 `MemorySaver` 编译，按 `thread_id` 保存中间状态——`POST /api/diagnostics/stream` 正是靠它把每个节点的中间状态以 SSE 推给前端的推理控制台。

---

## 5. 数据从哪来

### 5.1 规模

| 项 | 值 |
| --- | --- |
| 数据库文件 | `data/zhiwei.db`，约 **62.25 MB** |
| 业务表 | **14 张** |
| 设备 | **100 台**（LINE-01 ~ LINE-05，各 20 台） |
| 遥测读数 | **67202 条**（100 × 14 天 × 48 步/天 + 2 条实时 tick） |
| 时间范围 | `2025-07-01 00:00` → `2025-07-15 00:00` |
| 采样间隔 | 30 分钟 |
| 每条读数维度 | **22 维** |
| 注入故障设备 | **6 台 / 100 台**（6%） |

### 5.2 14 张表

| 表 | 行数 | 说明 |
| --- | --- | --- |
| `machines` | 100 | 设备主数据 |
| `sensor_readings` | 67202 | 22 维遥测时序 |
| `users` | 1 | 登录账号（用户名 / 口令哈希 / 角色） |
| `fmeca_rules` | 9 | 结构化故障规则（fault_code 1–9） |
| `case_library` | 4 | 历史案例 |
| `diagnostics` | 87 | 诊断快照 |
| `maintenance_records` | 45 | 维护工单 |
| `notifications` | 45 | 待审 / 已审通知 |
| `agent_activity` | 460 | Agent 行为审计 |
| `conversation_threads` / `conversation_messages` | 5 / 42 | 会话记忆 |
| `long_term_memory` | 1 | 长期记忆 |
| `maintenance_reports` | 3 | 维护报告 |
| `data_provenance` | 2 | 数据血缘 |

### 5.3 数据怎么生成的

**① 确定性物理退化模型**（`src/simulator/physics_models.py`，无随机数）

| 模型 | 用途 |
| --- | --- |
| Paris 裂纹扩展 | 轴承磨损：`crack = 0.02 + 1e-5 × cycles × stress³`，按 0.25 归一 |
| Arrhenius 热老化 | 电机绝缘：以 70℃ 为锚点的相对老化速率 |
| Taylor 刀具寿命 | 刀具磨损：`life = 200 × (v/f)^-0.25 / 硬度因子` |

仪表噪声用**双频正弦叠加**代替随机采样：`scale × (0.65×sin(step×0.071) + 0.35×sin(step×0.013+1.7))`，同一 `step` 必然得到同一个值。

**② 8 个基线参数 → 22 维遥测**（`src/simulator/sensor_models.py`）

仿真器内部维护 **8 个基线参数**（温度 / 电流 / 电压 / 转速 / 振动 / 冷却水温 / 冷却压力 / 油压），按机号确定性漂移；再由**传感器传递函数**派生出 **22 维遥测**（含 `motor_power`、`spindle_load`、`bearing_temp` 等派生量）。故障设备按 `强度 = 渐进曲线 × 严重度` 在同一套公式上叠加征兆。

**③ 6 类注入故障**（`src/simulator/fault_injector.py`）

| 设备 | 故障 | onset 天 | 前兆天数 | 严重度 |
| --- | --- | --- | --- | --- |
| CNC_007 | 电源模块故障 | 3.0 | 2.0 | 0.80 |
| CNC_018 | 轴承磨损 | 5.0 | 3.0 | 0.85 |
| CNC_025 | 温度过高 | 7.0 | 2.0 | 0.70 |
| CNC_036 | 散热系统故障 | 4.0 | 2.0 | 0.75 |
| CNC_052 | 电机过载 | 9.0 | 2.5 | 0.65 |
| CNC_078 | 转子故障 | 10.0 | 3.0 | 0.75 |

> ⚠️ **数据为仿真生成，不是真实产线数据。** 所有准确率表述都应理解为「在已知真值的仿真数据上的自测口径」。详见第 9 章。

### 5.4 实时推进（默认关闭）

`RealtimeEngine`（`src/data/realtime.py`）按真实时钟每 **5 秒**把每台设备推进 **30 分钟**的仿真时间，追加**带工况调制**的新读数。时钟游标三级恢复（`data/sim_clock.json` → 数据库 `MAX(timestamp)` → 启动时刻），保证只追加、不改写历史；每 10 拍写一条数据血缘记录。开启方式见第 8 章。追加读数的 `health_score` 为空（仿真器不产出该字段），健康分按中性 0.8 处理，详见第 9 章第 10 条。

---

## 6. 目录结构导览

### 6.1 `src/` —— 14 个子目录（89 个 .py）

| 目录 | 职责 | 关键文件 |
| --- | --- | --- |
| `src/` | 全局单点 | `severity.py`（**严重度唯一口径**）· `main.py`（CLI 入口，教学版）· `state.py`（教学版状态类） |
| `agents/` | 智能体节点 | `state.py`（`DiagnosticState` 26 字段）· `monitor_agent.py` · `diagnostic_agent.py` · `knowledge_agent.py` · `prediction_agent.py` · `decision_agent.py` · `report_agent.py` · `reflection_agent.py` · `chat_agent.py` · `autonomous_loop.py` |
| `api/` | HTTP 服务 | `main.py`（51 个接口 + lifespan + trace 中间件）· `security.py`（**JWT 认证 / 三角色依赖 / WS 令牌校验**） |
| `config/` | 配置 | `__init__.py`（路径 / 22 维列名 / 认证与管理员配置 / 教学版常量）· `model_routing.py`（LLM 路由表 + 提示词框架） |
| `data/` | 数据链路 | `workload.py`（工况调制）· `realtime.py`（实时推进引擎） |
| `database/` | 仓储层 | `connection.py`（14 张表 DDL + 查询，含 `users` 表与账号 CRUD）· `schemas.py` · `init_db.py`（FMECA 9 条 / 案例 4 条）· `regenerate.py`（**全量重建**） |
| `graph/` | 图定义 | `main_graph.py`（**主诊断图**）· `edges.py` · `build.py`（教学版 8 节点图） |
| `knowledge/` | 知识层 | `fault_graph.py`（因果图 14 节点）· `chunking.py`（结构感知分块）· `retrieval.py`（BM25 + 稠密 + RRF）· `index_builder.py`（136 chunk 索引） |
| `llm/` | LLM 客户端 | `client.py`（`UnifiedLLMClient`：chat / embed / health_check / parse_json） |
| `memory/` | 记忆 | `manager.py`（`MemoryManager` 门面） |
| `observability/` | 可观测性 | `logger.py`（3 份日志 + trace 传播 + 读取 API） |
| `simulator/` | 仿真器 | `physics_models.py` · `sensor_models.py`（22 维传递函数）· `fault_injector.py` · `cnc_simulator.py` |
| `skills/` | 技能层 | `base.py`（`BaseSkill` / `SkillMeta` / **`SkillRegistry`**）+ 8 个子包 |
| `tools/` | 教学版工具 | `algorithms.py` · `data_loader.py` · `feature_engineering.py` · `type_converter.py` |

**8 个技能子包**（每个 = `__init__.py` + `skill.py` + `schema.py` + `SKILL.md`）：

| id | 分类 | 作用 |
| --- | --- | --- |
| `anomaly_detect` | analysis | 同侪包络 + 阈值 + 趋势的三信号异常检测 |
| `rul_weibull` | prediction | 威布尔 RUL → P10/P50/P90 |
| `evidence_chain` | analysis | E1–E7 证据链 + 加权聚合置信度 |
| `cost_benefit` | operations | 三方案成本对比 + What-if |
| `case_match` | knowledge | 案例库词法检索 |
| `fault_graph_trace` | analysis | 因果图 forward / backward 遍历 |
| `diagnostic_report` | report | 确定性 Markdown 报告 |
| `maintenance_report` | operations | 审批后生成维护报告并落库 |

### 6.2 `scripts/` —— 9 个脚本

| 脚本 | 说明 |
| --- | --- |
| `verify_diagnosis_accuracy.py` | **只读** 演示前预检：6/6 诊断 + 0/94 误报 |
| `audit_artifact_consistency.py` | **只读** 审计「真值 vs 诊断 vs 待审通知 vs 报告」一致性 |
| `rag_tools.py build\|eval` | 构建 / 评估知识索引（recall@5） |
| `repair_data.py`（`--dry-run`） | 回填缺失的 `health_score`，幂等 |
| `repair_chat_chitchat_answers.py`（`--dry-run`） | 清洗历史会话里「检索原文冒充答案」的助手回复 |
| `fix_stale_notifications.py`（`--dry-run`） | 修正待审通知里写错的故障名（保留审批状态） |
| `recompute_fault_readings.py` | **定向**重算指定设备读数（自动备份整库） |
| `refresh_fault_diagnosis.py` | 重跑指定设备诊断并重建其维护报告 |
| `_doc_facts.py` | **只读** 输出文档所需的全部实测数字 |

> 所有脚本都必须用项目 venv 运行：`.venv\Scripts\python.exe scripts/xxx.py`。
> **局部重算绝不要用 `src/database/regenerate.py`**——它会 `unlink` 整个库文件；用 `recompute_fault_readings.py`。

### 6.3 `web/` —— 前端

| 路径 | 职责 |
| --- | --- |
| `src/App.tsx` | 应用外壳：侧栏 + 顶栏 + 10 页条件渲染（**无 react-router**，用 `useState<Page>`）；登录守卫与角色下发 |
| `src/api.ts` / `src/types.ts` | 接口封装（自动注入 `Authorization` 头、401 广播登出）/ TS 类型 |
| `src/auth.tsx` | 登录态上下文：token 持久化、启动验活、角色判定（`canWrite` / `canAdmin`） |
| `src/pages/login.tsx` | 登录页 |
| `src/pages/fleet.tsx` | `OverviewPage` · `MachinesPage` |
| `src/pages/agents.tsx` | `DiagnosticsPage` · `AgentMonitorPage` |
| `src/pages/chat.tsx` | `ChatPage` · `ReportsPage` · `LogsPage` |
| `src/pages/knowledge.tsx` | `KnowledgePage` · `TopologyPage` · `OperationsPage` |
| `src/components/ui.tsx` | 13 个通用原子组件（`RiskBadge` / `HealthBar` / `EvidenceChain` / `DecisionPanel` …） |
| `src/components/charts.tsx` | `Sparkline` / `MiniBar` 迷你图（主题感知） |
| `src/theme.ts` | 浅色 / 暗色主题切换 + 图表调色板 |
| `src/reportExport.ts` | Markdown → HTML **单一转换器**，MD / Word / 打印三种导出共用 |
| `src/TrendChart.tsx` | echarts 趋势图（22 维中文名映射） |
| `src/styles.css` | 设计系统（CSS 变量令牌 / 侧栏 232px / 内容 1280px / 1px 边框 / radius 10px） |

### 6.4 `data/` 与 `docs/`

| 路径 | 内容 |
| --- | --- |
| `data/zhiwei.db` | SQLite 主库 |
| `data/knowledge/*.txt` | 4 份工程文档：维护手册 / 设备说明书 / 案例知识库 / 设备成本与产量 |
| `data/index/` | `bm25.pkl` · `chunks.json` · `fingerprint.json` |
| `data/chroma_db/` | Chroma 稠密索引（可选） |
| `data/logs/` | `app.log` · `llm_audit.jsonl` · `tool_calls.jsonl` |
| `data/raw/` | 竞赛 CSV（教学版数据源） |
| `docs/` | 架构与教学文档、`zhiwei-submit/`（单文件提交版 Demo）、`archive/` |

---

## 7. 接口与前端速览

### 7.1 接口总数：51

| 类别 | 数量 |
| --- | --- |
| REST 业务接口 | **49**（含 1 个 SSE：`POST /api/diagnostics/stream`） |
| WebSocket | **2**（`/ws/realtime`、`/ws/agent`） |
| 合计 | **51** |

> 不含 FastAPI 自动生成的 `/docs`、`/openapi.json` 等文档路由。

### 7.2 分组要点

| 分组 | 接口数 | 代表接口 |
| --- | --- | --- |
| 系统 | 2 | `GET /health` |
| 认证 | 4 | `POST /api/auth/login` · `GET /api/auth/me` · `GET/POST /api/auth/users` |
| 总览与设备 | 6 | `GET /api/overview` · `POST /api/diagnostics/trigger` |
| 诊断 | 3 | `POST /api/diagnostics/stream`（SSE 流式） |
| 知识 | 4 | `POST /api/knowledge/search`（混合检索）· `GET /api/topology` |
| 运维工单 | 2 | `POST /api/operations/orders` |
| Agent 自治 | 7 | `POST /api/agent/scan` · `POST /api/notifications/{id}/review`（审批联动） |
| 可观测 | 5 | `GET /api/logs/trace/{trace_id}`（按 trace 聚合全链路） |
| 技能 | 3 | `GET /api/skills` · `POST /api/skills/{id}/run` |
| 聊天 | 3 | `POST /api/chat` |
| 记忆 | 4 | `POST /api/memory/profile/{machine_id}/refresh` |
| 维护报告 | 3 | `POST /api/reports/{id}/push`（飞书 / 企业微信 / 钉钉） |
| 数据层 | 3 | `GET /api/data/provenance` |

### 7.3 前端 10 个页面

| # | 页面 | 页面内能做什么 |
| --- | --- | --- |
| — | **登录页** | 用户名 + 密码登录，角色权限说明 |
| 1 | **总览** | 全厂 KPI 卡片（含健康分布直方图与产线迷你图）、健康矩阵、产线筛选、最近诊断与工单 |
| 2 | **设备** | 设备列表与健康条、单机详情、历史趋势图 |
| 3 | **诊断中心** | 手动触发诊断、SSE 实时推理时间线、证据链与决策面板 |
| 4 | **智能问答** | 多会话聊天、引用来源卡片、新建对话 |
| 5 | **维护报告** | 报告列表与预览、导出 MD / Word / 打印存 PDF、IM 推送 |
| 6 | **Agent 监控** | WebSocket 实时活动流、自主循环开关、待审通知与审批 |
| 7 | **运行日志** | 应用日志 / LLM 审计 / 工具调用三个视图，10 秒自动刷新 |
| 8 | **关联图谱** | 设备 → 产线 → 故障因果图（含边概率） |
| 9 | **知识库** | FMECA 规则折叠表、案例卡片、混合检索实验台 |
| 10 | **运维工单** | 工单列表与人工建单 |

前端特性：全局数据流在 `App.tsx` 统一拉取 `overview` + `machines`，通过 `ws://127.0.0.1:8000/ws/realtime` 推送触发刷新；侧栏「Agent 监控」带**待审通知数量徽标**（10 秒轮询）；浅色 / 暗色双主题（`data-theme` + localStorage 持久化）；页面级错误边界防止单页异常白屏。

### 7.4 登录认证与三角色

| 项 | 说明 |
| --- | --- |
| 登录 | `POST /api/auth/login`（用户名 + 密码 → JWT，HS256） |
| 令牌使用 | 后续请求带 `Authorization: Bearer <token>`；WebSocket 用查询参数 `?token=<token>`（浏览器无法给 WS 设请求头） |
| 当前用户 | `GET /api/auth/me`（前端启动验活与路由守卫用） |
| 账号管理 | `GET / POST /api/auth/users`（仅管理员）：列出 / 创建账号 |
| 口令存储 | pbkdf2_sha256 加盐哈希（随机盐），不依赖 passlib / bcrypt |
| 初始管理员 | 后端启动时幂等播种，凭据取自 `.env` 的 `ADMIN_USERNAME` / `ADMIN_PASSWORD` |
| 有效期 | 由 `JWT_EXPIRE_MINUTES` 控制；过期后前端收到 401 自动登出回登录页 |

三角色权限矩阵（与后端依赖注入一一对应，49 个 REST 接口 + 2 个 WS 通道全覆盖）：

| 能力 | viewer | operator | admin |
| --- | :---: | :---: | :---: |
| 全部读接口（总览 / 设备 / 诊断 / 知识 / 日志 / 报告 / 聊天 / 记忆 / 工单） | ✓ | ✓ | ✓ |
| 触发诊断、SSE 推理、立即扫描、通知审批、新建工单、写记忆、跑技能 | — | ✓ | ✓ |
| 推送报告、删除记忆、账号管理、自主循环开关、手动 tick | — | — | ✓ |

前端行为：未登录渲染登录页（主界面完全不挂载，WS 与轮询自然停止）；写按钮按角色禁用并提示所需角色；顶栏展示当前用户与角色徽标，可一键退出。

新增角色账号（管理员）：

```bash
curl -X POST http://127.0.0.1:8000/api/auth/users \
  -H "Authorization: Bearer <admin-token>" -H "Content-Type: application/json" \
  -d '{"username":"operator1","password":"<至少6位>","role":"operator"}'
```

---

## 8. 常用配置速查

所有变量写在项目根目录的 `.env`（从 `.env.example` 复制）。**修改后需重启后端。**

| 变量 | 默认值 | 作用 |
| --- | --- | --- |
| `DATABASE_URL` | `sqlite:///./data/zhiwei.db` | 数据库地址（仅支持 SQLite） |
| `DATA_DIR` | `data/raw` | 数据目录 |
| `LOG_LEVEL` | `INFO` | 日志级别 |
| `MAX_PARALLEL_DEVICES` | `15` | 并行分析设备数上限 |
| `OLLAMA_BASE_URL` | `http://localhost:11434` | 本地 Ollama 地址 |
| `OLLAMA_MONITOR_MODEL` | `qwen2.5:7b` | monitor 节点的本地模型 |
| `OLLAMA_KNOWLEDGE_MODEL` | `qwen2.5:7b` | knowledge / chat 节点的本地模型 |
| `OLLAMA_REFLECTION_MODEL` | `qwen2.5:7b` | reflection 节点的本地模型 |
| `OLLAMA_EMBEDDING_MODEL` | `bge-m3` | 嵌入模型（1024 维） |
| `DEEPSEEK_API_KEY` | （空） | **留空也能跑**：自动降级为本地 / 规则模式 |
| `DEEPSEEK_BASE_URL` | `https://api.deepseek.com/v1` | DeepSeek 地址 |
| `DEEPSEEK_MODEL` | `deepseek-chat` | 云端模型名 |
| **`AUTH_SECRET`** | （空则每次启动随机） | JWT 签名密钥；显式设置可保证重启后已签发令牌继续有效 |
| **`JWT_EXPIRE_MINUTES`** | `720` | 令牌有效期（分钟） |
| **`ADMIN_USERNAME`** | `admin` | 初始管理员用户名（首次启动幂等播种） |
| **`ADMIN_PASSWORD`** | （必填建议） | 初始管理员密码（首次启动播种，建议修改） |
| **`AUTONOMOUS_LOOP_ENABLED`** | `0` | `1` = 每 30 秒自动扫描全厂、自动诊断与派单（会消耗 token） |
| **`REALTIME_ENABLED`** | `0` | `1` = 按仿真时钟持续追加新读数 |
| `REALTIME_STEP_MINUTES` | `30` | 每次推进的仿真时间（分钟） |
| `REALTIME_TICK_SECONDS` | `5.0` | 真实推进间隔（秒） |
| `OFFLINE_LAG_MINUTES` | `120` | 离线判定阈值：读数落后全厂最新时间超过它才算离线 |
| `REPORT_WEBHOOK_URL` | （空） | 报告推送的群机器人地址（留空则页面内填写） |
| `REPORT_WEBHOOK_TYPE` | `feishu` | 推送渠道：`feishu` / `wecom` / `dingtalk` |
| `CORS_ORIGINS` | `http://localhost:5173,http://127.0.0.1:5173` | 允许的前端来源 |
| `ENABLE_LLM_ENRICHMENT` | `0` | `1` = 开启诊断节点的 LLM 候选校验（默认全确定性） |

**运行时开关**（不用改 `.env`、不用重启；除登录外均需令牌，角色见第 7.4 节）：

```bash
# 1) 登录换取 JWT（用户名密码见 .env 的 ADMIN_USERNAME / ADMIN_PASSWORD）
TOKEN=$(curl -s -X POST http://127.0.0.1:8000/api/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username":"admin","password":"<你的密码>"}' \
  | python -c "import sys,json;print(json.load(sys.stdin)['access_token'])")

# 2) 带令牌调用管理级接口
# 开启 / 关闭自主循环（admin）
curl -X POST -H "Authorization: Bearer $TOKEN" "http://127.0.0.1:8000/api/agent/loop/toggle"
# 强制立即扫描一次（operator 及以上）
curl -X POST -H "Authorization: Bearer $TOKEN" "http://127.0.0.1:8000/api/agent/scan"
# 手动推进一拍实时数据（admin）
curl -X POST -H "Authorization: Bearer $TOKEN" "http://127.0.0.1:8000/api/data/realtime/tick"
```

> 更细的「想调什么 → 改哪个文件 → 改什么」清单（覆盖仿真参数、故障注入、异常阈值、隔离规则、RUL 系数、成本常量、分块参数、检索权重、工况节律、记忆去重等 **40+ 项**）见 `项目详细报告.md` 附录 C。

---

## 9. 系统边界与客观局限

以下是系统当前的实际边界，每条给出一句「因此……」，帮助正确解读它的输出。

| # | 事实 | 因此 |
| --- | --- | --- |
| 1 | **数据由物理仿真器生成，不是真实产线数据** | 67202 条读数全部由 Paris / Arrhenius / Taylor 三个模型 + 确定性噪声产生，故障由预设场景注入；展示的是「在已知真值下算法链路能否正确工作」的验证结果，**不能**用来推断真实产线准确率 |
| 2 | **未接真实设备 / MES / 工单系统** | 没有 OPC UA / MQTT / Modbus 采集层，工单与通知只写在本地表里，不会真正下发到外部系统 |
| 3 | **认证为单机演示级** | JWT 三角色覆盖全部 49 个 REST 接口与 2 个 WS 通道，口令加盐哈希；但无多租户、无登录审计、无密码策略与锁定机制。定位仍是**单机部署的内部演示与验证平台**，不能直接暴露到公网 |
| 4 | **LLM 默认关闭，关闭时如何运行** | `ENABLE_LLM_ENRICHMENT=0` + DeepSeek Key 留空 + Ollama 未启动时，全链路仍可完整运行：诊断 / 预测 / 决策 / 报告 / 自检全是本地确定性算法，聊天走模板兜底（`llm_used=false`） |
| 5 | **单机 SQLite，非分布式** | 存储是单文件库 + 本地 pickle / Chroma 目录，自治循环与实时引擎都是进程内协程；不支持多实例水平扩展 |
| 6 | **历史库不含工况节律** | 工况调制只在 `RealtimeEngine`（默认关）生效，`data/zhiwei.db` 里的 67202 条读数是「恒定负荷下的退化曲线」，看不到夜班降载、换刀窗口、周三保养等现象 |
| 7 | **`health` 缺失 ≠ 设备离线** | 健康分缺失时按中性基线 `0.8` 处理（视为在运行）；「离线」只由读数新鲜度决定（落后全厂最新 > 120 分钟）。判断数据是否新鲜应看 `latest_reading.timestamp` |
| 8 | **RAG 知识源仅 5 类文档 + 3 张表** | 索引共 136 个 chunk；`recall@5 = 1.0`（15/15）是对 **15 条预置查询**的评测结果，知识库覆盖面受这 5 份人工文档的边界限制，不代表开放域问答能力 |
| 9 | **自主循环与实时引擎默认关** | 默认启动态是「静态数据 + 手动触发」；「数据在往前走」与「无人值守自动派单」需要显式开启环境变量或调用运行时开关 |
| 10 | **实时 tick 追加的读数不带健康分** | `RealtimeEngine` 追加读数的 `health_score` 为空（仿真器输出不含该字段），健康分按中性 `0.8` 处理；实时引擎默认关闭，手动 tick 为演示路径，正式演示前建议以 `verify_diagnosis_accuracy.py` 复核 |

### 演进方向

| 方向 | 含义 |
| --- | --- |
| 接真实数据源 | 在 `src/data/` 新增采集适配层（OPC UA / MQTT / Modbus），把 `RealtimeEngine.tick()` 的数据来源从仿真器换成现场网关，`SensorReading` 契约不变 |
| 认证增强 | 在现有 JWT 三角色基础上加 `tenant_id`、登录审计与密码策略，`reviewer` 从令牌解析而非表单输入 |
| 时序库与分布式 | 把 `sensor_readings` 迁到 TimescaleDB / InfluxDB，`AutonomousLoop` 拆成独立 worker |
| 工况重放与在线学习 | 用真实工况日志重放让历史库带上节律；用现场标注数据校准隔离规则的 12 个阈值与包络常数 |
| 知识图谱动态化 | 让因果图从 FMECA 表与现场工单自动生成 / 更新边权，而不是硬编码 12 条边 |
| 模型自校准 | 用维修回填结果反向评估 RUL 与诊断准确率，形成闭环 |
| 能力外露与 MCP 接入 | 8 个技能已通过 `to_langchain_tool()` 暴露为工具，可进一步包装成 MCP Server 供外部 Agent 调用 |

---

## 10. 文档导航

| 文档 | 内容 |
| --- | --- |
| **`项目详细报告.md`** | 全量技术详解：逐模块、逐算法、带常数与代码片段（21 章 + 3 个附录），含**调优入口速查表** |
| `LICENSE` | MIT 许可 |
| `docs/ARCHITECTURE.md` | 架构说明 |
| `docs/AGENTS.md` | 智能体设计说明 |
| `docs/项目技术教学详解.md` | 教学向解读 |
| `docs/LangGraph智能体学习解析.md` | LangGraph 概念讲解 |
| `docs/PROJECT_MASTER.md` | 项目主文档 |
| `docs/zhiwei-submit/` | 单文件提交版 Demo（`index.html` + `api.py`） |

**常用命令**

```bash
# 全量重建数据库（会删除并重新生成 data/zhiwei.db，首次 clone 才需要）
.venv\Scripts\python.exe -m src.database.regenerate

# 跑测试
.venv\Scripts\python.exe -m pytest tests/ -q

# 演示前预检（只读）
.venv\Scripts\python.exe scripts/verify_diagnosis_accuracy.py

# 构建 / 评估知识索引
.venv\Scripts\python.exe scripts/rag_tools.py build
.venv\Scripts\python.exe scripts/rag_tools.py eval

# 启动后端（必须用 127.0.0.1）
.venv\Scripts\uvicorn src.api.main:app --host 127.0.0.1 --port 8000

# 启动前端
cd web && npm run dev
```
