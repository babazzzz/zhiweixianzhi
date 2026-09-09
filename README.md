# 智维先知 — LangGraph 多智能体预测性维护系统

> 基于 LangGraph 的 CNC 设备预测性维护系统，从 Coze 竞赛 Demo 迁移为企业级 Agent 架构

---

## 目录

- [1. 项目全貌：两套系统](#1-项目全貌两套系统)
- [2. 快速启动](#2-快速启动)
- [3. 已知问题修复记录](#3-已知问题修复记录)
- [4. 数据层详解：数据从哪来有多少仿真器是什么](#4-数据层详解)
- [5. 文件结构全景图](#5-文件结构全景图)
- [6. 每个文件做什么怎么做](#6-每个文件做什么怎么做)
- [7. 系统A：CLI 教学版详解](#7-系统acli-教学版详解)
- [8. 系统B：企业级 API 版详解](#8-系统b企业级-api-版详解)
- [9. 调优指南](#9-调优指南)
- [10. 文档导航](#10-文档导航)

---

## 1. 项目全貌：两套系统

这个项目包含**两套完全独立的系统**，共用同一个代码仓库但走不同的执行路径：

| 维度 | 系统A — CLI 教学版 | 系统B — 企业级 API 版 |
|------|------|------|
| **入口** | `python src/main.py` | `python -m uvicorn backend.app:app` |
| **图引擎** | `src/graph/build.py` | `src/graph/main_graph.py` |
| **节点数** | 8 个 | 7 个 |
| **流程** | supervisor → data_pipeline → analysis → diagnosis → decision → self_check → rag → report | monitor → diagnostic → knowledge → prediction → decision → report → reflection |
| **数据源** | CSV 文件 `data/raw/MACHINE_LOG_DATA._2025.csv`（2999 条，5 维传感器） | SQLite `data/zhiwei.db`（67200 条，22 维传感器） |
| **状态定义** | `src/state.py` (AgentState) | `src/agents/state.py` (DiagnosticState) |
| **Agent 文件** | `src/agents/supervisor.py`, `analysis.py`, `diagnosis.py`, `decision.py`, `self_check.py`, `rag.py` | `src/agents/monitor_agent.py`, `diagnostic_agent.py`, `knowledge_agent.py`, `prediction_agent.py`, `decision_agent.py`, `report_agent.py`, `reflection_agent.py` |
| **特征工程** | `src/tools/feature_engineering.py`（22 维统计+频域特征） | `src/skills/anomaly_detect.py`（三信号融合） |
| **RUL 算法** | `src/tools/algorithms.py`（威布尔 MLE） | `src/skills/rul_weibull.py`（威布尔+P10/P50/P90） |
| **知识库** | `src/agents/rag.py`（内嵌 17 条案例+故障模式，关键词匹配） | `src/knowledge/`（FMECA 规则库 + NetworkX 因果图 + ChromaDB 向量库） |
| **前端** | 无 | React+Vite+Tailwind (`web/` 目录) |
| **设计目的** | 学习 LangGraph 核心概念 | 企业级生产系统 |

**两套系统互不干扰**：CLI 版从 CSV 读数据，API 版从 SQLite 读数据。修改其中一套不会影响另一套。

---

## 2. 快速启动

### 2.1 环境准备

```bash
# 1. 进入项目目录
cd D:\daima\ziweixianzhi_langgraph

# 2. 创建/激活虚拟环境
python -m venv .venv
.venv\Scripts\activate

# 3. 安装依赖
pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple

# 4. 确保 Ollama 在运行（本地 LLM）
ollama list
# 应看到: qwen2.5:7b, bge-m3, zhiwei-monitor, zhiwei-knowledge, zhiwei-reflection
# 如果没有，执行:
# ollama pull qwen2.5:7b
# ollama pull bge-m3
```

### 2.2 启动系统A（CLI 教学版）

```bash
# 分析指定设备
python src/main.py --devices CNC_007 CNC_012

# 分析默认设备
python src/main.py
```

**输出**：在终端打印完整的分析报告，包括风险等级、证据链 E1-E7、成本分析、自验收结果。

### 2.3 启动系统B（企业级 API 版）

```bash
# 方式1：通过 backend/app.py 兼容入口
python -m uvicorn backend.app:app --host 127.0.0.1 --port 8000

# 方式2：直接指定 src/api/main.py
python -m uvicorn src.api.main:app --host 127.0.0.1 --port 8000

# 方式3：直接运行 backend/app.py（推荐，自动 sys.path 修复）
python backend/app.py
```

**访问地址**（必须用 `127.0.0.1` 或 `localhost`，**不能用 `0.0.0.0`**，否则浏览器会报 ERR_ADDRESS_INVALID）：

| 用途 | URL |
|---|---|
| API 文档（Swagger UI） | http://127.0.0.1:8000/docs |
| 总览仪表板 | http://127.0.0.1:8000/api/overview |
| 设备列表 | http://127.0.0.1:8000/api/machines |
| 触发诊断 | `POST http://127.0.0.1:8000/api/diagnostics/trigger` |
| 拓扑图数据 | http://127.0.0.1:8000/api/topology |
| 健康检查 | http://127.0.0.1:8000/health |

### 2.4 启动前端（配合系统B）

```bash
cd web
npm install
npm run dev
# 前端运行在 http://localhost:5173
```

### 2.5 重建数据库（**首次 clone 后必做**）

> 仓库不包含 `data/zhiwei.db`（约 56MB，已列入 `.gitignore`）。首次 clone 后必须先生成数据库，否则系统没有数据可分析。生成过程约 2-3 分钟。

如果数据库数据损坏或需要重新生成：

```bash
# 重新生成 100 台设备 × 14 天 × 22 维传感器数据（约需 2-3 分钟）
python -m src.database.regenerate

# 或用原始 CSV 初始化（4 维传感器，2999 条）
python -m src.database.init_db
```

### 2.6 重建知识索引与 RAG 评估（可选）

知识索引基于源文档指纹增量重建（源文档未变时自动跳过嵌入，秒级完成）：

```bash
# 构建/刷新知识索引（108 chunks，6 类文档，bge-m3 嵌入）
.venv/Scripts/python.exe scripts/rag_tools.py build

# 评估召回率（内置 12 条带预期命中的测试查询，实测 recall@5 = 1.0）
.venv/Scripts/python.exe scripts/rag_tools.py eval
```

### 2.7 数据库表自动迁移

`Database` 初始化时会自动执行 `initialize_schema()`（全部 `IF NOT EXISTS` 幂等），对话线程/长期记忆/维护报告/数据血缘等新表在首次启动时自动创建，无需手工迁移。

---

## 3. 已知问题修复记录

### 3.1 浏览器访问 `0.0.0.0:8000` 报 ERR_ADDRESS_INVALID（本次修复）

**症状**：后端正常启动（终端显示 `Uvicorn running on http://0.0.0.0:8000` + 多个 200 OK），但浏览器输入 `http://0.0.0.0:8000/` 显示"无法访问此页面" / `ERR_ADDRESS_INVALID`。

**根因**：`0.0.0.0` 是监听所有网络接口的特殊地址，**不能作为浏览器访问地址**。Chromium 系浏览器对 `0.0.0.0` 统一返回 `ERR_ADDRESS_INVALID`。

**修复**：
- `backend/app.py` 已把 `host="0.0.0.0"` 改为 `host="127.0.0.1"`（仅本机访问，最常用）
- 如需局域网内其他机器访问，临时改回 `0.0.0.0`，访问方用 `http://<你的IP>:8000`
- 浏览器必须用 `http://127.0.0.1:8000/...` 或 `http://localhost:8000/...`

**路径前缀**：所有业务端点都在 `/api/` 下，旧 README 漏写了：
- ❌ `/overview` / `/machines` / `/diagnose/CNC_007`（404）
- ✅ `/api/overview` / `/api/machines` / `/api/diagnostics/trigger`

### 3.2 ModuleNotFoundError: No module named 'src'（本次修复）

**错误现象**：
```
ModuleNotFoundError: No module named 'src'
```

**根因**：项目用 `from src.xxx import ...` 绝对导入，但只有 `src/main.py` 有 `sys.path.insert(0, 项目根目录)` 修复。其他入口文件（`backend/app.py`、`src/api/main.py`、`src/database/regenerate.py`）直接运行时，Python 把脚本所在目录加入 `sys.path[0]`，而不是项目根目录，导致找不到 `src` 包。

**修复**：在三个入口文件的 `from src.xxx import` 之前统一添加 `sys.path` 修复：
```python
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # 上溯到项目根
```

**验证结果**：
- `python backend/app.py` → uvicorn 成功启动 ✅
- `python src/main.py --devices CNC_007` → 完整运行到 Analysis Agent ✅
- `python src/database/regenerate.py` → 导入成功 ✅

> **注意**：用 `python -m uvicorn backend.app:app` 或 `python -m src.database.regenerate`（带 `-m`）启动时不会有此问题，因为 `-m` 模式会自动把当前目录加入 sys.path。只有直接 `python 文件路径` 运行时才需要 sys.path 修复。

### 3.3 CSV 设备ID列名不匹配

**错误现象**：
```
AttributeError: 'str' object has no attribute 'nunique'
```

**根因**：CSV 文件的设备ID列名是 `equipment_id`，但 `data_loader.py` 只查找 `device_id` 和 `Machine_ID`。两个都找不到时，`df.get()` 返回字符串（列名）而非列对象，字符串没有 `.nunique()` 方法。

**修复**：在 `src/tools/data_loader.py` 的 `_find_id_column()` 函数中增加 `equipment_id` 到候选列表，并将 line 47 改为安全调用方式：
```python
# 修复前（有 bug）
print(f"设备数: {df.get('device_id', df.get('Machine_ID', df.columns[0])).nunique()}")

# 修复后
id_col = _find_id_column(df)
print(f"设备数: {df[id_col].nunique()}  (ID列: {id_col})")
```

### 3.4 历史修复记录

| 问题 | 修复位置 | 说明 |
|------|----------|------|
| ModuleNotFoundError: No module named 'src' | `backend/app.py`, `src/api/main.py`, `src/database/regenerate.py` | 三个入口文件缺少 sys.path 修复，已统一添加 |
| FAULT_MODELS/RISK_LEVELS 未定义 | `src/config/__init__.py` | 补充了 10 种故障模式 + 4 级风险配置 |
| .env 缺企业变量 | `.env` | 补充 OLLAMA_MONITOR/KNOWLEDGE/REFLECTION_MODEL 等 |
| tsconfig 缺 Vite 类型 | `web/tsconfig.json` | 添加 `"types": ["vite/client"]` |
| 旧数据 4 维/72.3% 故障率 | `src/database/regenerate.py` | 重建为 22 维/6% 故障率/67200 条 |
| severity 全显示 normal | `src/api/main.py` | 改用存储的 health_score + fault_code 判定 |
| detect_anomaly 基线问题 | `src/skills/anomaly_detect.py` | 改用前 40% 数据作健康基线 |

---

## 4. 数据层详解

### 4.1 数据从哪来？

项目有**三个数据源**，服务于不同系统：

#### 数据源 1：原始竞赛 CSV（系统A使用）

```
data/raw/MACHINE_LOG_DATA._2025.csv    — 2999 条设备日志
data/raw/MACHINE_SUMMARY_DATA._2025.csv — 100 台设备元数据
data/raw/PRODUCT_ASSEMBLY_LINE_WITH_MACHINES_2025.csv — 产线配置
data/raw/PRODUCT_ASSEMBLY_LINE_WITH_MACHINES_TESTS_2025.csv — 质量测试
```

**CSV 列结构**（MACHINE_LOG_DATA）：
```
timestamp, equipment_id, failure_type, current, temperature, voltage, speed
2025/5/18 0:00, CNC_001, 0, 27.41, 83.01, 223.04, 300
```

- 5 维传感器：current(电流), temperature(温度), voltage(电压), speed(转速), power(功率=电流×电压)
- 100 台 CNC 设备，每台约 30 条记录
- 时间跨度：约 7 小时（2025/5/18 0:00 ~ 7:00）
- failure_type：0=正常，其他=故障类型

**特点**：这是竞赛原始数据，维度少、时间短，适合学习但不适合做退化建模。

#### 数据源 2：SQLite 仿真数据库（系统B使用）

```
data/zhiwei.db — 51MB，6 张表
```

| 表名 | 行数 | 说明 |
|------|------|------|
| `machines` | 100 | 设备元数据（型号、产线、维护日期、日产量、单位成本） |
| `sensor_readings` | 67200 | 22 维传感器读数 + health_score + fault_code |
| `fmeca_rules` | 9 | FMECA 故障模式规则（故障码/名称/系统/部件/SOP步骤/备件/工时） |
| `case_library` | 4 | 历史维护案例 |
| `diagnostics` | 按需增长 | 诊断结果快照 |
| `maintenance_records` | 按需增长 | 维护工单 |

**特点**：由仿真器生成，维度全、时间长、故障率合理，适合企业级分析。

#### 数据源 3：内嵌知识库（系统A使用）

系统A的 RAG Agent 内嵌了 17 条知识（7 个案例 + 10 种故障模式），直接写在 `src/agents/rag.py` 代码中，不依赖外部数据库。

### 4.2 数据有多少？

| 维度 | CSV 原始数据 | SQLite 仿真数据 |
|------|-------------|----------------|
| 记录总数 | 2,999 条 | 67,200 条 |
| 设备数量 | 100 台 | 100 台 |
| 传感器维度 | 5 维 | 22 维 |
| 时间跨度 | ~7 小时 | 14 天 |
| 采样间隔 | 不固定 | 30 分钟 |
| 故障率 | 72.3%（不真实） | 6%（6 台设备有渐进式故障） |
| 每台设备记录数 | ~30 条 | 672 条 |

### 4.3 仿真器是什么？

仿真器是一组 Python 模块，用**物理退化模型**生成合成的 CNC 设备传感器数据。它不是简单的随机数生成，而是基于真实的物理公式：

#### 仿真器架构

```
src/simulator/
├── cnc_simulator.py    — 主仿真器（协调所有子模型）
├── physics_models.py   — 物理退化模型（3 个核心公式）
├── sensor_models.py    — 传感器噪声模型（高斯+漂移+尖峰）
├── fault_injector.py   — 故障注入器（6 台设备的故障场景）
└── run_simulation.py   — 批量仿真脚本
```

#### 三大物理退化模型

| 模型 | 公式 | 作用 | 文件 |
|------|------|------|------|
| **Paris 疲劳裂纹模型** | da/dN = C(ΔK)^m | 模拟轴承疲劳裂纹扩展，影响振动和温度 | `physics_models.py` |
| **Arrhenius 热加速模型** | rate = A·exp(-Ea/kT) | 模拟温度对电子器件老化的加速作用 | `physics_models.py` |
| **Taylor 刀具寿命模型** | T = C·v^(-1/n)·f^(-1/m) | 模拟切削刀具磨损，影响电流和加工质量 | `physics_models.py` |

#### 22 维传感器清单

```
op_voltage          — 工作电压
op_amperage         — 工作电流
op_temperature      — 工作温度
rotor_speed         — 转子转速
vibration_rms       — 振动均方根值
motor_power         — 电机功率
cutting_force       — 切削力
feed_force          — 进给力
spindle_load        — 主轴负载
coolant_temp         — 冷却液温度
coolant_pressure     — 冷却液压力
oil_pressure        — 油压
noise_level         — 噪声水平
tool_wear           — 刀具磨损量
surface_roughness    — 表面粗糙度
position_error      — 位置误差
power_factor        — 功率因数
harmonic_distortion  — 谐波失真
bearing_temp         — 轴承温度
motor_vibration      — 电机振动
ambient_temp         — 环境温度
humidity            — 湿度
```

#### 故障注入器

`fault_injector.py` 为 6 台设备注入渐进式故障：

| 设备 | 故障类型 | 注入方式 |
|------|----------|----------|
| CNC_007 | 轴承磨损 | 振动逐步增大，轴承温度上升 |
| CNC_018 | 刀具磨损 | 电流增大，切削力变化 |
| CNC_025 | 冷却系统故障 | 冷却液温度上升，压力下降 |
| CNC_036 | 电机过热 | 电机温度急升，电流波动 |
| CNC_052 | 电源模块老化 | 电压波动增大 |
| CNC_078 | 进给系统卡滞 | 进给力增大，转速不稳定 |

#### 仿真器工作流程

```
CNCSimulator.get_reading(machine_id, timestamp)
    │
    ├─→ 1. 查询设备元数据（型号、已运行天数）
    │
    ├─→ 2. 计算基础退化量
    │      ├─ Paris 模型 → 轴承退化 → 影响振动/温度
    │      ├─ Arrhenius → 电路老化 → 影响电压/电流
    │      └─ Taylor → 刀具磨损 → 影响电流/切削力
    │
    ├─→ 3. 故障注入器叠加故障信号
    │      └─ 如果该设备有注入故障，叠加故障特征
    │
    ├─→ 4. 传感器模型添加噪声
    │      ├─ 高斯噪声（正常波动）
    │      ├─ 传感器漂移（缓慢偏移）
    │      └─ 随机尖峰（偶发干扰）
    │
    └─→ 5. 输出 22 维传感器读数 + fault_code
```

---

## 5. 文件结构全景图

```
D:\daima\ziweixianzhi_langgraph\
│
├── src/
│   ├── main.py                        # ★ 系统A入口：python src/main.py
│   ├── state.py                       # ★ 系统A状态定义 (AgentState)
│   │
│   ├── config/
│   │   ├── __init__.py                # 配置中心：路径/22维传感器/故障模型/风险等级
│   │   └── model_routing.py          # LLM 模型路由表（7个Agent各用哪个模型）
│   │
│   ├── llm/
│   │   ├── __init__.py               # LLM 工厂函数 (get_llm/get_agent_llm)
│   │   └── client.py                 # 统一 LLM 客户端（Ollama + DeepSeek 双路由）
│   │
│   ├── tools/                        # ★ 系统A工具层
│   │   ├── data_loader.py            # CSV 数据加载（从原始竞赛CSV读取5维数据）
│   │   ├── feature_engineering.py    # 22维特征工程（统计+频域+交叉耦合）
│   │   ├── algorithms.py             # 6大算法库（威布尔/马氏/贝叶斯/IF/CUSUM/Zscore）
│   │   └── type_converter.py         # numpy→Python原生类型转换
│   │
│   ├── agents/                       # ★ 两套系统的Agent都在这里
│   │   │
│   │   │  === 系统A: CLI教学版 ===
│   │   ├── supervisor.py             # 调度Agent：接收设备列表→创建分析任务
│   │   ├── data_pipeline.py          # 数据管道：加载CSV→提取时序→22维特征
│   │   ├── analysis.py              # ★ ReAct分析：LLM推理+工具调用→异常检测+RUL
│   │   ├── diagnosis.py             # 诊断Agent：E1-E7证据链→故障匹配
│   │   ├── decision.py              # 决策Agent：成本分析→维护工单
│   │   ├── self_check.py            # ★ 自验收：5项检查→pass/fail→条件边路由
│   │   ├── rag.py                   # RAG：关键词检索17条案例知识
│   │   │
│   │   │  === 系统B: 企业级API版 ===
│   │   ├── state.py                 # ★ 系统B状态定义 (DiagnosticState)
│   │   ├── monitor_agent.py          # 监控Agent：读取传感器→异常检测→路由判定
│   │   ├── diagnostic_agent.py       # 诊断Agent：三信号融合→故障诊断→证据链
│   │   ├── knowledge_agent.py        # 知识Agent：FMECA规则+因果图+案例检索
│   │   ├── prediction_agent.py       # 预测Agent：威布尔RUL→P10/P50/P90
│   │   ├── decision_agent.py         # 决策Agent：3方案成本对比→维护建议
│   │   ├── report_agent.py           # 报告Agent：生成结构化诊断报告
│   │   └── reflection_agent.py       # 反思Agent：5项自检→通过/回退重试
│   │
│   ├── graph/                       # ★ 两套图引擎
│   │   ├── build.py                 # 系统A图构建：8节点+条件边+MemorySaver
│   │   ├── main_graph.py            # 系统B图构建：7节点+异步绑定+条件路由
│   │   └── edges.py                 # 系统B路由函数（独立文件便于测试）
│   │
│   ├── skills/                      # ★ 系统B技能层：8个规范化子包 + 注册表
│   │   ├── base.py                  # BaseSkill/SkillMeta/SkillRegistry（自动发现+tool_calls审计）
│   │   ├── anomaly_detect/          # 异常检测：三信号融合+健康评分
│   │   ├── rul_weibull/             # RUL预测：威布尔分布→P10/P50/P90
│   │   ├── evidence_chain/          # E1-E7证据链：7维证据构建+贝叶斯融合
│   │   ├── cost_benefit/            # 成本效益：预防vs纠正vs更换三方案对比
│   │   ├── case_match/              # 案例匹配：历史维护案例检索
│   │   ├── fault_graph_trace/       # 因果图查询：NetworkX故障传播路径
│   │   ├── diagnostic_report/       # 诊断报告生成
│   │   └── maintenance_report/      # 维护报告生成（审批联动，纯确定性拼装）
│   │   （每个子包：schema.py + skill.py + SKILL.md）
│   │
│   ├── memory/                      # ★ 四层记忆架构
│   │   └── manager.py               # MemoryManager门面：会话/情节/设备画像/语义
│   │
│   ├── data/                        # ★ 数据层增强
│   │   ├── workload.py              # 工况调制：班次/换刀/保养窗口/周末单班
│   │   └── realtime.py              # RealtimeEngine：真实时钟追加读数+数据血缘
│   │
│   ├── knowledge/                   # ★ 系统B知识层
│   │   ├── fmeca.py                 # FMECA规则库：9条故障模式规则
│   │   ├── fault_graph.py           # NetworkX因果图：14节点12边故障传播
│   │   ├── chunking.py              # 6类源文档→108 chunks结构化分块
│   │   ├── retrieval.py             # HybridRetriever：BM25+稠密+RRF融合
│   │   ├── index_builder.py         # KnowledgeIndex：指纹增量重建
│   │   └── vector_store.py          # ChromaDB向量库：bge-m3嵌入(1024维)
│   │
│   ├── database/                    # ★ 系统B数据层
│   │   ├── connection.py            # SQLite事务仓库：9+张表CRUD（启动自动建表）
│   │   ├── schemas.py               # 数据模型：MachineMetadata/SensorReading
│   │   ├── init_db.py               # 初始化脚本：从CSV加载+FMECA规则+案例
│   │   └── regenerate.py            # ★ 重建脚本：仿真器生成22维×14天×100台
│   │
│   ├── agents/
│   │   ├── chat_agent.py            # ★ 第三条图：classify_intent→dispatch→compose
│   │   └── ...（系统A/B 各Agent）
│   │
│   ├── simulator/                   # ★ 物理仿真器
│   │   ├── cnc_simulator.py         # 主仿真器：协调子模型→输出22维读数
│   │   ├── physics_models.py         # 物理退化：Paris/Arrhenius/Taylor三大公式
│   │   ├── sensor_models.py         # 传感器噪声：高斯+漂移+尖峰
│   │   ├── fault_injector.py        # 故障注入：6台设备渐进式故障场景
│   │   └── run_simulation.py        # 批量仿真：一键生成全部数据
│   │
│   └── api/
│       └── main.py                  # ★ 系统B的FastAPI后端：50条REST+SSE+WebSocket
│
├── backend/
│   └── app.py                       # 兼容入口：转发到 src.api.main:app
│
├── scripts/
│   └── rag_tools.py                 # 知识索引构建(build) + 召回率评估(eval)
│
├── web/                             # ★ 系统B前端
│   ├── src/
│   │   ├── App.tsx                  # 薄壳：10导航项+WebSocket轮询
│   │   ├── api.ts / types.ts        # API客户端与类型
│   │   ├── components/ui.tsx        # 原子组件+RiskBadge+HealthBar
│   │   ├── pages/                   # fleet/agents/knowledge/chat 四页面
│   │   └── TrendChart.tsx           # 22维趋势图组件
│   ├── package.json
│   └── vite.config.ts
│
├── data/
│   ├── raw/                         # 原始竞赛CSV文件（4个）
│   └── zhiwei.db                    # ★ SQLite仿真数据库（51MB，67200条）
│
├── docs/
│   ├── AGENTS.md                    # 项目宪法（Codex产出）
│   ├── PROJECT_MASTER.md            # 完整规格书（领域知识+架构+算法+约束）
│   ├── 企业级Agent建设规划.md
│   ├── 系统架构设计文档.md
│   ├── 模型选型与部署方案.md
│   └── 问题日志.md
│
├── .env                             # 环境变量配置
├── requirements.txt                 # Python依赖清单
├── README.md                        # ★ 你正在看的这个文件
└── 项目详细报告.md                   # 完整技术报告（14章）
```

---

## 6. 每个文件做什么怎么做

### 6.1 入口文件

#### `src/main.py` — 系统A CLI入口
- **做什么**：解析命令行参数 → 构建状态图 → 执行分析 → 打印报告
- **怎么做**：`argparse` 解析 `--devices` 和 `--thread` → 调用 `build_analysis_graph()` → 调用 `run_analysis()` → 格式化输出
- **运行方式**：`python src/main.py --devices CNC_007 CNC_012`

#### `backend/app.py` — 系统B兼容入口
- **做什么**：转发到 `src.api.main:app`，让 `uvicorn backend.app:app` 能正常工作
- **怎么做**：`from src.api.main import app` + `uvicorn.run()`

### 6.2 配置层

#### `src/config/__init__.py` — 配置中心
- **做什么**：定义所有路径常量、22 维传感器列名、10 种故障模型、4 级风险等级
- **关键变量**：
  - `DATA_FILES` — CSV 文件路径
  - `SENSOR_COLUMNS` — 22 维传感器列名元组
  - `FAULT_MODELS` — 10 种故障（F001-F010），供系统A的 `diagnosis.py` 使用
  - `RISK_LEVELS` — 4 级风险配置（critical/high/medium/low），供系统A的 `decision.py` 使用
  - `DATABASE_URL` — SQLite 路径

#### `src/config/model_routing.py` — LLM 路由表
- **做什么**：定义 7 个 Agent 各自使用哪个 LLM 模型
- **路由策略**：
  - Monitor/Knowledge → 本地 Ollama（zhiwei-monitor, zhiwei-knowledge）
  - Diagnostic/Prediction/Decision/Report → 云端 DeepSeek API
  - Reflection → 本地 Ollama（zhiwei-reflection, temp=0.0）

#### `src/llm/client.py` — 统一 LLM 客户端
- **做什么**：封装 Ollama + DeepSeek 双路由，自动切换
- **怎么做**：检查 DeepSeek API Key 是否可用 → 可用则走云端，不可用则回退本地 Ollama

### 6.3 系统A：工具层

#### `src/tools/data_loader.py` — CSV 数据加载
- **做什么**：从 `MACHINE_LOG_DATA._2025.csv` 加载 5 维传感器数据
- **怎么做**：`pd.read_csv()` → 自动识别设备ID列（`equipment_id`/`device_id`/`Machine_ID`）→ 按设备ID提取时序数据
- **调优入口**：
  - 增加新数据源 → 修改 `DATA_FILES` 配置 + 在 `load_machine_logs()` 中添加加载逻辑
  - 支持新列名 → 在 `_find_id_column()` 的 `candidates` 列表中添加

#### `src/tools/feature_engineering.py` — 22 维特征工程
- **做什么**：从 5 维原始数据提取 22 维特征
- **特征分层**：
  - 第1层（5维）：基础参数均值（temp/current/voltage/speed/power）
  - 第2层（7维）：工程衍生（焦耳热/功率因数/散热效率/负载指数/转速稳定性/温升速率/电流-温度耦合）
  - 第3层（4维）：统计特征（变异系数/偏度/峰度/IQR比）
  - 第4层（3维）：频域特征（主频/频谱熵/高频能量比）— 使用 FFT
  - 第5层（3维）：交叉耦合（温度-转速/电压-电流/功率-负载）
- **调优入口**：
  - 增加特征 → 在 `compute_22d_features()` 中添加新的特征计算
  - 修改特征权重 → 在 `algorithms.py` 的 `bayesian_fusion()` 中调整 `weight` 参数

#### `src/tools/algorithms.py` — 6 大算法库
- **做什么**：提供系统A所有检测算法
- **算法清单**：
  1. `weibull_rul_estimation()` — 威布尔分布 RUL 预测（MLE 拟合 + 条件故障概率）
  2. `mahalanobis_distance()` — 马氏距离故障匹配（MinCovDet + 卡方阈值）
  3. `bayesian_fusion()` — 贝叶斯 Log-Odds 多证据融合
  4. `isolation_forest_detect()` — Isolation Forest 异常检测
  5. `cusum_detection()` — CUSUM 累积和检测（早期退化捕捉）
  6. `zscore_anomaly_detection()` — Z-Score 快速异常检测
- **调优入口**：
  - 威布尔参数 → 修改 `weibull_rul_estimation()` 中的 `operating_hours` 默认值
  - 异常检测灵敏度 → 调整 `zscore_anomaly_detection()` 的 `threshold`（默认 2.5）
  - CUSUM 触发阈值 → 调整 `cusum_detection()` 的 `threshold`（默认 5.0）

### 6.4 系统A：Agent 层

#### `src/agents/supervisor.py` — 调度 Agent
- **做什么**：接收设备列表 → 创建分析任务 → 调用 LLM 确认
- **输出**：`analysis_tasks` 列表 + 确认消息

#### `src/agents/data_pipeline.py` — 数据管道
- **做什么**：调用 `data_loader` 加载 CSV → 提取每台设备时序 → 调用 `feature_engineering` 计算 22 维特征
- **输出**：`raw_data`（时序字典）+ `feature_vectors`（特征字典）

#### `src/agents/analysis.py` — ReAct 分析 Agent ★核心
- **做什么**：使用 LangGraph 的 `create_react_agent` 创建 ReAct 智能体，LLM 自主选择工具分析数据
- **工具**：`analyze_temperature` / `analyze_current` / `estimate_rul` / `comprehensive_device_analysis`
- **降级策略**：ReAct 失败时回退到纯算法 `comprehensive_analysis()`

#### `src/agents/diagnosis.py` — 诊断 Agent
- **做什么**：构建 E1-E7 七段证据链 → 匹配故障类型 → 计算综合风险
- **E1-E7 定义**：
  - E1=温度异常 / E2=电流异常 / E3=电压稳定性 / E4=转速波动
  - E5=综合异常评分 / E6=寿命预测 / E7=趋势分析
- **故障匹配**：基于 `FAULT_MODELS` 的参数匹配规则，按匹配度排序返回 Top-3

#### `src/agents/decision.py` — 决策 Agent
- **做什么**：根据风险等级制定维护建议 → 成本分析 → 生成 SOP 工单
- **成本模型**：预防性维护 vs 故障后维修，计算节省金额和百分比

#### `src/agents/self_check.py` — 自验收 Agent ★核心
- **做什么**：5 项检查 → pass/fail → 通过则继续，不通过则回退到 analysis
- **5 项检查**：结构完整性 / 逻辑一致性 / 数值合理性 / 证据链完整性 / 故障匹配质量
- **循环逻辑**：最多 3 次重试，超过则带警告输出（不阻塞）

#### `src/agents/rag.py` — RAG 知识检索
- **做什么**：从内嵌的 17 条知识中关键词检索相关案例
- **为什么不用向量库**：知识库小（17条），关键词匹配更快更可靠

### 6.5 系统B：Agent 层

系统B的 7 个 Agent 全部异步实现（`async def`），每个 Agent 都有确定性算法兜底——即使 LLM 不可用也能完整运行。

#### `src/agents/monitor_agent.py` — 监控 Agent
- **做什么**：从 SQLite 读取最新传感器数据 → 调用 `anomaly_detect` 检测异常 → 设置 `is_anomaly` 标志
- **路由**：正常 → END；异常 → 进入诊断

#### `src/agents/diagnostic_agent.py` — 诊断 Agent
- **做什么**：三信号融合（Z-Score + CUSUM + 趋势）→ 构建 E1-E7 证据链 → 匹配 FMECA 故障规则
- **兜底**：LLM 失败时用确定性规则匹配

#### `src/agents/knowledge_agent.py` — 知识 Agent
- **做什么**：查询 FMECA 规则库 → 查询 NetworkX 因果图 → 检索历史案例
- **输出**：结构化的故障知识上下文

#### `src/agents/prediction_agent.py` — 预测 Agent
- **做什么**：调用 `rul_weibull` 技能 → 计算 RUL 的 P10/P50/P90 分位数
- **兜底**：LLM 失败时用纯威布尔计算

#### `src/agents/decision_agent.py` — 决策 Agent
- **做什么**：调用 `cost_benefit` 技能 → 3 方案对比（预防/纠正/更换）→ 生成维护建议
- **兜底**：LLM 失败时用确定性成本计算

#### `src/agents/report_agent.py` — 报告 Agent
- **做什么**：汇总所有结果 → 生成结构化 Markdown 诊断报告
- **纯代码生成**：不依赖 LLM，确保格式一致

#### `src/agents/reflection_agent.py` — 反思 Agent
- **做什么**：5 项自检（证据完整性/逻辑一致性/数值合理性/RUL置信度/建议可执行性）
- **路由**：通过 → END；不通过 → 回退到 diagnostic（最多 3 次）

### 6.6 系统B：技能层（Skills 注册表）

技能层已从单文件重构为**规范化子包 + 注册表**架构：

- `src/skills/base.py` — 定义 `BaseSkill`（统一 run 接口 + `SkillMeta` 元数据）与 `SkillRegistry`（自动发现：扫描 skills/ 下所有子包，读取各子包 `skill.py` 并注册；`run()` 执行结果自动写入 `tool_calls` 审计表）
- 每个技能是一个子包：`schema.py`（输入/输出 dataclass）+ `skill.py`（实现）+ `__init__.py` + `SKILL.md`（说明文档）

**8 个技能子包**：

| 子包 | 技能 ID | 做什么 |
|------|---------|--------|
| `anomaly_detect/` | `anomaly_detect` | 三信号融合（Z-Score+CUSUM+趋势）+ health_score |
| `rul_weibull/` | `rul_weibull` | 威布尔 RUL + P10/P50/P90 分位数 |
| `evidence_chain/` | `evidence_chain` | E1-E7 七维证据构建 + 贝叶斯融合 |
| `cost_benefit/` | `cost_benefit` | 预防/纠正/更换三方案成本对比 |
| `case_match/` | `case_match` | 历史维护案例检索 |
| `fault_graph_trace/` | `fault_graph_trace` | NetworkX 故障传播路径查询 |
| `diagnostic_report/` | `diagnostic_report` | 结构化 Markdown 诊断报告 |
| `maintenance_report/` | `maintenance_report` | 维护报告生成（纯确定性拼装：设备档案/FMECA/维修史/案例，落库 `maintenance_reports`，带 `data_sources` 可追溯） |

**注册与调用**：

```python
from src.skills.base import SkillRegistry

SkillRegistry.discover()                      # 幂等，FastAPI lifespan 启动时自动执行
meta = SkillRegistry.meta("maintenance_report")
result = SkillRegistry.run("maintenance_report", machine_id="CNC_036", trigger="approval")
```

技能也可通过 REST 调用：`GET /api/skills` 列出全部、`POST /api/skills/{id}/run` 执行。

### 6.7 系统B：记忆模块（四层记忆架构）

`src/memory/manager.py` — `MemoryManager` 门面，四层记忆共用 SQLite 持久化：

| 层 | 存储 | 说明 |
|----|------|------|
| 会话记忆 | `conversation_threads` + `conversation_messages` | 按线程存储对话；每 12 条自动滚动生成摘要，控制上下文长度 |
| 情节记忆 | 复用 `diagnostics` / `maintenance_records` | 诊断与维护的历史事件 |
| 设备画像 | `long_term_memory`（kind=machine） | `refresh_machine_profile()` 从情节层沉淀（severity 分组 + 维修历史汇总） |
| 语义记忆 | `long_term_memory`（kind=fact/preference/lesson） | 人工或 Agent 写入的长期事实，按 kind+subject+content 去重，pinned 优先 |

核心 API：`open_thread / append_message / recent_history`（会话）、`remember / recall / forget`（长期）、`build_context`（把相关记忆注入 LLM 提示词）、`refresh_machine_profile`（画像沉淀）。

REST 端点：`GET/POST /api/memory`、`DELETE /api/memory/{id}`、`POST /api/memory/profile/{machine_id}/refresh`。

### 6.8 系统B：聊天 Agent（第三条 StateGraph）

`src/agents/chat_agent.py` — 在诊断图之外新增的第三条图，支撑前端聊天窗口：

```
classify_intent（关键词优先分类） → dispatch（确定性工具执行） → compose（LLM 组织语言 / 模板兜底）
```

- **5 类意图**：`machine_info`（设备信息）/ `machine_sop`（维护 SOP）/ `machine_records`（检测与维护记录）/ `report`（维护报告）/ `general`（知识库问答）
- 设备 ID 提取：正则 `CNC[_\- ]?(\d{1,3})`
- 意图路由走 `src/config/model_routing.py` 的 `"chat"` 配置，LLM 用 `UnifiedLLMClient`（DeepSeek 可用时云端，否则 Ollama + 模板兜底）
- REST 端点：`POST /api/chat`、`GET /api/chat/threads`、`GET /api/chat/threads/{id}`

### 6.9 系统B：混合检索 RAG（BM25 + 稠密 + RRF）

`src/knowledge/` 三模块：

| 模块 | 做什么 |
|------|--------|
| `chunking.py` | 6 类源文档（manual/spec/case/cost/fmeca/machine）→ 结构化分块，共 108 chunks，保留 section_path 层级 |
| `retrieval.py` | `HybridRetriever`：BM25 关键词检索 + bge-m3 稠密向量检索（ChromaDB，1024 维）→ RRF（倒数排名融合）合并排序 |
| `index_builder.py` | `KnowledgeIndex`：源文档指纹增量重建（未变则跳过嵌入）+ `ensure()` 幂等初始化 |

调优入口：分块粒度改 `chunking.py`；检索权重/RRF 参数改 `retrieval.py`；重建策略改 `index_builder.py`。

REST 端点：`POST /api/knowledge/search`（已接混合检索，返回 chunks 结果）。

### 6.10 系统B：数据层工况调制与实时增强

- `src/data/workload.py` — **工况调制**，让仿真数据更贴合真实生产节律：
  - 白班满负荷 / 夜班降载 15% / 班次切换爬坡
  - 换刀窗口（每 8 小时，负载瞬时波动）
  - 周三保养窗口（停机，电流 ×0.05、温度回落）
  - 周末单班
  - `modulate_metrics()`：停机/降载时对电流、振动、温度做差异化线性调制（温度仅体现 40% 负荷差，符合热惯性）
- `src/data/realtime.py` — **RealtimeEngine**：按 `REALTIME_STEP_MINUTES` 真实时钟追加调制读数，每次写入记录 `data_provenance`（数据血缘：来源/时间/参数），可追溯每条数据的生成方式
- 默认 `REALTIME_ENABLED=0` 关闭；开启后通过 `POST /api/data/realtime/tick` 驱动
- REST 端点：`GET /api/data/provenance`、`GET /api/data/realtime/status`

### 6.11 维护报告审批联动（高风险 → 批准 → 自动生成）

`src/database/connection.py` 的 `review_notification()` 内置审批联动：当**高风险/紧急**通知被管理员批准后，自动调用 `SkillRegistry.run("maintenance_report", ...)` 生成维护报告并落库 `maintenance_reports`，报告 ID 随审批结果返回前端。

联调实测：审批 CNC_036 紧急通知 → 自动生成报告 `RPT-DEA7315984`。

REST 端点：`GET /api/reports`、`GET /api/reports/{id}`。

### 6.12 系统B：API 层

#### `src/api/main.py` — FastAPI 后端
- **做什么**：50 条 REST 路由 + SSE 流式推送 + WebSocket 实时通信
- **端点分组**（`/api/` 前缀）：

| 分组 | 代表端点 | 说明 |
|------|---------|------|
| 总览/设备 | `GET /api/overview`、`/api/machines`、`/api/machines/{id}/history` | 仪表板、设备列表、历史数据 |
| 诊断 | `POST /api/diagnostics/trigger`、`/api/diagnostics`、`POST /api/diagnostics/stream` | 触发诊断、历史、SSE 流 |
| 拓扑 | `GET /api/topology` | 设备拓扑图数据 |
| 知识库 | `GET /api/knowledge/fmeca`、`POST /api/knowledge/search` | FMECA 规则、混合检索 |
| 运维 | `GET/POST /api/operations/orders` | 维护工单 |
| 通知 | `POST /api/notifications/{id}/review` | 审批联动维护报告生成 |
| Skills | `GET /api/skills`、`POST /api/skills/{id}/run` | 注册表列表与执行 |
| 聊天 | `POST /api/chat`、`GET /api/chat/threads/{id}` | 聊天 Agent、会话历史 |
| 记忆 | `GET/POST /api/memory`、`POST /api/memory/profile/{id}/refresh` | 长期记忆 CRUD、画像沉淀 |
| 报告 | `GET /api/reports`、`GET /api/reports/{id}` | 维护报告查询 |
| 数据层 | `GET /api/data/provenance`、`/api/data/realtime/status`、`POST /api/data/realtime/tick` | 血缘、实时引擎 |
| 运行日志 | `GET /api/logs` | tool_calls 审计与运行记录 |

完整清单见 Swagger UI：http://127.0.0.1:8000/docs

### 6.13 系统B：前端

前端已从单文件 `App.tsx` 重构为**组件化**结构：

```
web/src/
├── App.tsx              # 薄壳：导航 + WebSocket 轮询（10 个导航项，含待审徽标）
├── api.ts / types.ts    # API 客户端与类型定义
├── components/ui.tsx    # 通用原子组件 + RiskBadge（风险一眼可见）+ HealthBar（健康条）
└── pages/
    ├── fleet.tsx        # 总览：风险优先矩阵，故障/高风险设备置顶
    ├── agents.tsx       # 运行日志：Agent 执行记录与 tool_calls 审计
    ├── knowledge.tsx    # 知识库：混合检索结果与分块详情
    └── chat.tsx         # 智能问答：聊天窗口（设备信息/SOP/记录）
```

- **尺寸锁定**：`.app-shell{height:100vh;overflow:hidden}` + 内容区固定 1280px，解决窗口缩放导致页面忽大忽小的问题
- 通知审批界面显示报告联动结果；运行日志页可查看每次调用了什么、执行情况

---

## 7. 系统A：CLI 教学版详解

### 7.1 状态图结构

```
START
  ↓
[Supervisor]           ← 接收设备列表，创建分析任务
  ↓
[Data Pipeline]         ← 加载CSV → 5维时序 → 22维特征
  ↓
[Analysis] ←──────┐    ← ReAct推理 + 算法检测 + RUL预测
  ↓               │
[Diagnosis]       │    ← E1-E7证据链 + 故障匹配
  ↓               │
[Decision]        │    ← 成本分析 + 维护工单
  ↓               │
[Self-Check] ─────┘    ← 5项检查 → pass/fail (最多3次回退)
  ↓ (pass)
[RAG]                  ← 关键词检索案例知识
  ↓
[Report]               ← 生成Markdown报告
  ↓
END
```

### 7.2 条件边路由

`route_after_check()` 函数根据 `verification_status` 决定下一步：
- `"pass"` → 走到 `rag` 节点
- `"fail"` → 回退到 `analysis` 节点
- `"max_retry"` → 也走到 `rag` 节点（带警告输出，不阻塞）

### 7.3 数据流

```
用户输入: CNC_007
    ↓
data_loader.load_machine_logs() → CSV → DataFrame (2999行, 7列)
    ↓
data_loader.get_device_data(df, "CNC_007") → 时序数据 (temperature, current, voltage, speed)
    ↓
feature_engineering.compute_22d_features() → 22维特征向量
    ↓
analysis_agent → ReAct LLM + comprehensive_analysis() → 风险评分 + RUL
    ↓
diagnosis_agent → build_evidence_chain() → E1-E7 → match_fault_type() → 故障Top3
    ↓
decision_agent → _calculate_costs() → 成本对比 → _generate_work_order() → 工单
    ↓
self_check_agent → 5项检查 → pass/fail
    ↓
rag_agent → keyword_search() → 案例知识
    ↓
report_generator → Markdown报告
```

---

## 8. 系统B：企业级 API 版详解

### 8.1 状态图结构

```
START
  ↓
[Monitor]              ← 读取最新传感器数据 → 异常检测
  ↓ (is_anomaly=True)        ↓ (is_anomaly=False)
[Diagnostic]           END (正常，不进入诊断)
  ↓
[Knowledge]            ← FMECA规则 + 因果图 + 案例检索
  ↓
[Prediction]           ← 威布尔RUL → P10/P50/P90
  ↓
[Decision]             ← 三方案成本对比 → 维护建议
  ↓
[Report]               ← 结构化诊断报告
  ↓
[Reflection] ──pass──→ END
  │                    ↑
  └──fail──→ [Diagnostic] (最多3次回退)
```

### 8.2 与系统A的核心区别

| 区别点 | 系统A | 系统B |
|--------|-------|-------|
| Agent 实现 | 同步函数 | 异步函数 (async/await) |
| 数据源 | CSV 文件 | SQLite 数据库 |
| 传感器维度 | 5 维 | 22 维 |
| 知识库 | 代码内嵌 17 条 | FMECA表 + NetworkX图 + ChromaDB |
| LLM 依赖 | ReAct 必须有 LLM | 每个Agent有确定性兜底 |
| 前端 | 无 | React 完整前端 |
| 成本分析 | 2 方案（预防vs纠正） | 3 方案（预防vs纠正vs更换） |
| RUL 输出 | 单点预测 | P10/P50/P90 分位数 |
| 自验收 | 5 项检查 | 5 项检查（不同维度） |

---

## 9. 调优指南

### 9.1 调优入口速查表

| 想调什么 | 改哪个文件 | 改什么 |
|----------|-----------|--------|
| 系统A数据源 | `src/config/__init__.py` | `DATA_FILES["machine_logs"]` 路径 |
| 系统A特征维度 | `src/tools/feature_engineering.py` | `compute_22d_features()` 添加新特征 |
| 系统A异常检测灵敏度 | `src/tools/algorithms.py` | `zscore_anomaly_detection(threshold=2.5)` |
| 系统A CUSUM 触发阈值 | `src/tools/algorithms.py` | `cusum_detection(threshold=5.0)` |
| 系统A故障匹配规则 | `src/config/__init__.py` | `FAULT_MODELS` 列表 |
| 系统A风险阈值 | `src/agents/diagnosis.py` | `risk_score` 的 0.8/0.6/0.4 阈值 |
| 系统A成本参数 | `src/agents/decision.py` | `COST_PARAMS` 字典 |
| 系统A自验收重试次数 | `src/config/__init__.py` | `SYSTEM_CONFIG["max_self_check_retries"]` |
| 系统A知识库 | `src/agents/rag.py` | `_get_knowledge_base()` 返回的列表 |
| 系统B异常检测基线 | `src/skills/anomaly_detect.py` | `detect_anomaly()` 中前 40% 数据作基线 |
| 系统B health_score 阈值 | `src/api/main.py` | severity 判定阈值 (0.45/0.55) |
| 系统B仿真器参数 | `src/simulator/cnc_simulator.py` | `CNCSimulator` 构造函数 |
| 系统B故障注入 | `src/simulator/fault_injector.py` | `_SCENARIOS` 字典 |
| 系统B FMECA 规则 | `src/database/init_db.py` | `FMECA_RULES` 元组 |
| 系统B LLM 路由 | `src/config/model_routing.py` | `AGENT_ROUTING` 字典（含 `"chat"` 聊天路由） |
| 系统B 混合检索权重 | `src/knowledge/retrieval.py` | BM25/稠密各自 top_k 与 RRF 融合参数 |
| 系统B 知识分块粒度 | `src/knowledge/chunking.py` | 各类文档的分块大小与切分规则 |
| 系统B 工况调制 | `src/data/workload.py` | 班次时间/降载幅度/换刀周期/保养窗口 |
| 系统B 实时引擎 | `.env` + `src/data/realtime.py` | `REALTIME_ENABLED=1` 开启、`REALTIME_STEP_MINUTES` 步长 |
| 仿真天数/间隔/设备数 | `src/database/regenerate.py` | `regenerate(days, interval_minutes, n_machines)` |

### 9.2 常见调优场景

#### 场景1：想让系统A检测更灵敏

```python
# src/tools/algorithms.py
def zscore_anomaly_detection(data, threshold=1.5):  # 从2.5降到1.5
    ...
def cusum_detection(data, threshold=3.0):  # 从5.0降到3.0
    ...
```

#### 场景2：想修改系统B的故障注入

```python
# src/simulator/fault_injector.py
_SCENARIOS = {
    "CNC_007": {"fault_code": 1, "start_day": 3, "severity": 0.8},
    # 添加新设备:
    "CNC_099": {"fault_code": 5, "start_day": 7, "severity": 0.6},
}
```

然后重新生成数据库：
```bash
python -m src.database.regenerate
```

#### 场景3：想让系统B用云端 LLM

```bash
# .env 文件中添加
DEEPSEEK_API_KEY=your_api_key_here
```

系统会自动将 Diagnostic/Prediction/Decision/Report 路由到 DeepSeek API。

#### 场景4：想增加新的故障类型

1. 在 `src/config/__init__.py` 的 `FAULT_MODELS` 中添加新故障定义
2. 在 `src/database/init_db.py` 的 `FMECA_RULES` 中添加对应的 FMECA 规则
3. 在 `src/agents/rag.py` 的 `_get_knowledge_base()` 中添加案例知识
4. 在 `src/simulator/fault_injector.py` 中添加故障注入逻辑
5. 重新生成数据库：`python -m src.database.regenerate`

#### 场景5：想调整自验收严格程度

```python
# src/agents/self_check.py (系统A)
# 或 src/agents/reflection_agent.py (系统B)

# 放宽：只检查关键项（1-4），跳过第5项
critical_passed = all(c[1] for c in checks[:4])
# 改为只检查前3项
critical_passed = all(c[1] for c in checks[:3])
```

### 9.3 性能优化方向

1. **数据库索引**：`sensor_readings` 表已有 `(machine_id, timestamp DESC)` 索引，查询应走索引
2. **批量插入**：`regenerate.py` 每 500 条批量插入，可调大批量提升生成速度
3. **前端按需加载**：22 维趋势图默认显示 3 个指标，用户可按需切换
4. **LLM 超时**：`client.py` 默认 60 秒超时，网络差时可调大
5. **异步并发**：系统B全部 async，可配置 `MAX_PARALLEL_DEVICES` 并行分析多台设备

### 9.4 调试技巧

```bash
# 1. 验证数据库是否正常
python -c "from src.database.connection import Database; db=Database(); print(db.count('machines'), db.count('sensor_readings'))"

# 2. 验证 Ollama 是否在线
ollama list

# 3. 验证 API 是否正常
curl http://localhost:8000/api/overview

# 4. 触发单台设备诊断
curl -X POST http://127.0.0.1:8000/api/diagnostics/trigger -H "Content-Type: application/json" -d '{"machine_id":"CNC_007"}'

# 5. 查看拓扑图
curl http://localhost:8000/api/topology | python -m json.tool | head -50

# 6. 系统A单独测试
python src/main.py --devices CNC_007
```

---

## 附录：环境变量说明 (.env)

| 变量名 | 默认值 | 说明 |
|--------|--------|------|
| `DATABASE_URL` | `sqlite:///./data/zhiwei.db` | SQLite 数据库路径 |
| `OLLAMA_BASE_URL` | `http://127.0.0.1:11434` | Ollama 服务地址 |
| `OLLAMA_MONITOR_MODEL` | `zhiwei-monitor` | 监控Agent使用的本地模型 |
| `OLLAMA_KNOWLEDGE_MODEL` | `zhiwei-knowledge` | 知识Agent使用的本地模型 |
| `OLLAMA_REFLECTION_MODEL` | `zhiwei-reflection` | 反思Agent使用的本地模型 |
| `OLLAMA_EMBEDDING_MODEL` | `bge-m3` | 嵌入模型（ChromaDB用） |
| `OLLAMA_MODEL` | `qwen2.5:7b` | 通用本地模型（系统A使用） |
| `DEEPSEEK_API_KEY` | (空) | DeepSeek API密钥（填入后启用云端推理） |
| `DEEPSEEK_BASE_URL` | `https://api.deepseek.com` | DeepSeek API地址 |
| `DEEPSEEK_MODEL` | `deepseek-chat` | DeepSeek 模型名 |
| `MAX_PARALLEL_DEVICES` | `15` | 最大并行分析设备数 |
| `LOG_LEVEL` | `INFO` | 日志级别 |

---

_本 README 由 WorkBuddy 生成，基于对项目全部源码的逐文件分析。_

---

## 10. 文档导航

| 文档 | 内容 | 何时读 |
|---|---|---|
| **`项目详细报告.md`** | 逐模块"做了什么 / 怎么做 / 关键文件 / 验证"，含第 15 章 8 项改造记录、第 16 章验收后补齐 | 快速了解全貌 / 汇报 |
| **`docs/项目技术教学详解.md`** | 老师讲解式长讲义：LangGraph 图编排 / RAG / Skill / Tool / ReAct / 三种 Loop / 模型调用与调优 / 上下文工程 / 四层记忆 / MCP 与 Harness，含自查练习 | **想真正学会在 LangGraph 下搭智能体时读它** |
| **`docs/ARCHITECTURE.md`** | 新架构分层图 + 三条 LangGraph 图 + 50 端点 + 数据层断点续写 | 快速定位架构与文件 |
| **`docs/`(其余)** | AGENTS.md / PROJECT_MASTER.md / 问题日志 / LangGraph 学习解析 / 归档 | 历史与协作约定 |

运行回归测试：

```bash
.venv/Scripts/python.exe -m pytest tests/ -q        # 15 passed ~1s（纯函数+确定性路径，不触网）
```

> `docs/archive/` 仅做历史保留（含 4 份去重后的知识源快照、3 份改造前规划文档），运行期不读取。

