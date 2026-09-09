# PROJECT_MASTER.md — 智维先知企业级Agent系统完整规格书

> **这是唯一需要阅读的文件。** 包含项目全部领域知识、架构设计、算法逻辑、文件结构和约束。
> 已有代码：`src/config/model_routing.py`（模型路由）和 `src/llm/client.py`（统一LLM客户端）已实现，直接使用。

---

## 1. 项目概述

CNC设备预测性维护Agent系统。从竞赛级Demo进化为企业级多Agent系统。
- **技术栈**: Python 3.13 + LangGraph + FastAPI + React/TypeScript/Tailwind + SQLite + ChromaDB
- **LLM**: 本地Ollama(qwen2.5:7b + bge-m3) + 云端DeepSeek API 混合路由
- **核心能力**: 7×24持续监控 → 异常检测 → ReAct推理诊断 → RUL预测 → 维护决策 → 自检 → 报告

---

## 2. 领域知识（原4个txt文件完整摘要）

### 2.1 设备参数与阈值（来源：设备说明书.txt）

设备型号：CNC_001 至 CNC_100，工业数控加工中心，出厂2020-2023年。

| 传感器 | 变量名 | 单位 | 正常范围 | 临界下限 | 临界上限 | 说明 |
|--------|--------|------|----------|---------|---------|------|
| 工作电压 | op_voltage | V | 220-230 | 210 | 240 | 供电稳定性 |
| 工作电流 | op_amperage | A | 22-28 | 18 | 35 | 负载与机械状态 |
| 工作温度 | op_temperature | C | 70-90 | 60 | 105 | 散热与摩擦状态 |
| 转子转速 | rotor_speed | RPM | 250-350 | 220 | 380 | 传动系统状态 |

新增传感器维度（仿真器生成，原数据无）：
vibration_rms(mm/s), motor_power(kW), cutting_force(N), feed_force(N),
spindle_load(%), coolant_temp(C), coolant_pressure(MPa), oil_pressure(MPa),
noise_level(dB), tool_wear(mm), surface_roughness(Ra), position_error(mm),
power_factor, harmonic_distortion(%), bearing_temp(C), motor_vibration(mm/s),
ambient_temp(C), humidity(%)

### 2.2 故障类型编码与SOP（来源：维护手册.txt）

| 码 | 故障名 | 英文 | 系统 | 部件 | 信号特征 | 物理机理 | SOP步骤 | 备件 | 工时(h) | 工程师 |
|----|--------|------|------|------|---------|---------|---------|------|---------|--------|
| 0 | 正常运行 | normal | - | - | 无异常 | 设备稳定 | - | - | - | - |
| 1 | 轴承磨损 | bearing_wear | 主轴系统 | 主轴轴承 | 温↑+转速↓+电流波动↑ | 滚道疲劳剥落/润滑不足→摩擦→发热 | 受控停机→内窥镜检查→拆主轴→换轴承→校准→跑合30min | SKF 7014CDGA/P4A×2, Kluber NBU15 200g | 4.5 | 高级机械 |
| 2 | 电机过载 | motor_overload | 驱动传动 | 主轴电机 | 电流>35A+温度>100C | 传动过紧/刀具磨损→超扭矩→电流超限 | 急停→等15min放电→盘车→绝缘测试→换电机→自整定 | 伺服电机2kW | 8 | 电气+机械 |
| 3 | 温度过高 | over_temperature | 冷却润滑 | 散热风扇/热交换器 | 温>100C+电流正常 | 风扇故障/滤网堵→散热不足→温度独立升高 | 空转冷却→查冷却系统→清散热器→查润滑油→重启监控 | 散热风扇24V, PT100传感器, 切削液20L | 2 | 初级 |
| 4 | 电压不稳 | voltage_instability | 电气电源 | 外部供电/稳压器 | 电压波动>210-240V | 电网波动/稳压器故障→输入超范围 | 查电源线→测端子电压→换电源模块→查稳压器→监控5min | 电源模块220V/24V/10A | 1.5 | 电气 |
| 5 | 电流异常 | current_abnormality | 驱动传动 | 刀具/传动皮带 | 电流<18A或>35A | 刀具断裂/皮带打滑/切削参数不当 | 查刀具→查皮带→查切削参数→转动主轴→校电流互感器 | 传动皮带SPZ-1000, 电流互感器 | 1 | 工艺+机械 |
| 6 | 转子故障 | rotor_fault | 主轴系统 | 转子/编码器 | 转速偏差>10%+异响 | 编码器故障/轴承偏心→转速反馈不稳 | 查编码器线→清洁码盘→查轴承→换编码器→查变频器 | 编码器2500PPR | 3 | - |
| 7 | 电源模块故障 | power_module_failure | 电气电源 | 电源模块 | 无法启动/电压=0 | 内部稳压电路老化→输出不稳 | 查断路器→测24V输出→换电源模块→查下游短路 | 电源模块 | 1 | - |
| 8 | 散热系统故障 | cooling_system_failure | 冷却润滑 | 风扇/滤网 | 温持续升高+电流正常 | 风扇卡死/滤网堵→热量积聚 | 查风扇→清滤网→查热交换器→换风扇电机 | 风扇电机 | 0.5 | - |
| 9 | 其他/复合 | composite | 多系统 | 多部件 | 多故障同时 | 按优先级查：电源→轴承→冷却→编码器 | 综合诊断流程 | - | - | - |

### 2.3 历史案例库（来源：案例知识库.txt）

| 案例 | 设备 | 故障码 | 现象摘要 | 根因 | 维修 | RUL(h) |
|------|------|--------|---------|------|------|--------|
| 001 | CNC_002 | 7→5→0循环 | 电压222→229→220V抖动，电流21→21A降 | 电源模块老化+轴承微磨 | 换电源模块+润滑轴承 | 6 |
| 002 | CNC_029 | 2短暂 | 电流27A接近上限，温度84C | 瞬时切削负载过大，非故障 | 降进给率10% | - |
| 003 | CNC_016 | 1间歇 | 温78→68C降，电流波动，电压225→207V | 轴承早期磨损+间隙增大 | 检查轴承+注脂，备件 | 300 |
| 004 | CNC_096 | 8 | 温持续升高至90C，电流正常 | 散热风扇卡死 | 换风扇 | - |
| 005 | CNC_030 | 3,7,0切换 | 电流20→13A降，电压221→225→188V | 电源线缆接触不良 | 紧固端子+换空开 | - |
| 006 | CNC_011 | 6,7 | 编码器反馈不稳→钻孔偏移→边界扫描失败 | 转子故障影响加工精度 | 检查编码器+Z轴驱动 | - |
| 007 | CNC_039 | 3,7,0切换 | 同案例005 | 电源进线接触不良 | 紧固端子+换空开 | - |

风险演化路径示例：
Power_Supply_Instability → Motor_Current_Spike → Motor_Overheating → Motor_Overload_Fault → Potential_Shutdown

### 2.4 设备成本与产量（来源：设备成本与产量.txt）

100台设备，每台含：生产日期(YYYYMM)、上次维护时间、下次维护时间、最近维修时间、日产量(55-3000件)、单位生产成本(1-114元)。
格式示例：`设备编号：CNC_001 设备类型：CNC Machine 生产日期：202104 上次维护时间：2025/3/5 下次维护时间：2025/6/3 最近维修时间：2025/4/30 日产量：1431 单位生产成本：3`

成本计算：停机损失 = 日产量 × 单位生产成本 × 停机天数
维护成本 = 备件费用 + 工时费用 + 辅料费用

---

## 3. 系统架构

### 3.1 六层架构

```
Frontend (React/TS/Tailwind)
    ↓ REST + WebSocket
API Layer (FastAPI)
    ↓
Agent Layer (LangGraph Harness)
  Supervisor → 6 Specialist Agents → Loops(ReAct/Reflection/Planning/Memory)
    ↓
Tool Layer (MCP Protocol + Skills)
  6 MCP Servers + 7 Skills
    ↓
Knowledge Layer (FMECA + ChromaDB + Fault Graph + 4 Memory Types)
    ↓
Data Layer (SQLite + CNC Simulator + ChromaDB)
```

### 3.2 六个Agent

| Agent | 职责 | 模型 | 循环 | 输入 | 输出 |
|-------|------|------|------|------|------|
| Monitor | 7×24监控，异常评分 | zhiwei-monitor(7b) | Memory Loop | 18维传感器数据 | health_score, is_anomaly, anomaly_dims, severity |
| Diagnostic | 根因分析，ReAct推理 | deepseek-chat | ReAct Loop(≤3轮) | 异常告警+历史数据 | fault_mode, evidence_chain[E1-E7], confidence, severity |
| Prediction | RUL预测，威布尔 | deepseek-chat | 无 | 诊断结果+退化曲线 | rul_p10/p50/p90, degradation_rate, monitoring_freq |
| Decision | 维护决策，What-if | deepseek-chat | Planning Loop | 诊断+预测+成本 | recommendation, timing, action, cost分析, net_benefit |
| Knowledge | 知识检索+案例匹配 | zhiwei-knowledge(7b) | 无 | 查询文本 | 检索结果摘要 |
| Report | 报告生成+案例存储 | deepseek-chat | 无 | 全部诊断结果 | 结构化诊断报告 |
| Reflection | 5项自检 | zhiwei-reflection(7b) | Reflection Loop | 诊断结果 | checks[], all_passed |

### 3.3 诊断流程（事件驱动循环）

```
数据流到达 → Monitor评分
  ├─ health_score > 0.7 → 继续监控
  └─ 异常 → Diagnostic(ReAct循环)
       ├─ Thought → Action(查趋势/查FMECA/查案例) → Observation
       ├─ 置信度>70%? → 继续 / → Prediction → Decision → Report
       └─ Report → Reflection自检
            ├─ all_passed → 输出+存案例
            └─ 未通过 → 回到Diagnostic(≤3次)
```

### 3.4 知识库三层

**层1: FMECA结构化规则库** — SQL表，故障模式→原因→后果→检测方法→维修SOP
**层2: 向量知识库** — ChromaDB，存设备手册/案例/标准文档，bge-m3做1024维嵌入
**层3: 故障传播图** — 有向图(networkx)，~30节点×~50边，前向推演+后向溯源

---

## 4. 完整文件结构与实现规格

### 4.1 目录结构

```
src/
├── config/
│   ├── __init__.py          # 已有: 导出get_agent_config等
│   └── model_routing.py     # 已有: 7个Agent路由配置+System Prompts
├── llm/
│   ├── __init__.py          # 已有
│   └── client.py            # 已有: UnifiedLLMClient(Ollama+DeepSeek)
├── database/
│   ├── __init__.py
│   ├── schemas.py           # SQLAlchemy ORM models
│   ├── connection.py        # 数据库连接管理
│   └── init_db.py           # 建表+初始数据
├── simulator/
│   ├── __init__.py
│   ├── physics_models.py    # 物理退化模型(Paris/Arrhenius/Taylor)
│   ├── fault_injector.py    # 故障注入器
│   ├── cnc_simulator.py     # 主仿真器
│   └── data_loader.py       # 仿真数据写入数据库
├── knowledge/
│   ├── __init__.py
│   ├── fmeca.py             # FMECA结构化规则库
│   ├── fault_graph.py       # 故障传播有向图
│   └── vector_store.py      # ChromaDB向量存储
├── mcp_servers/
│   ├── __init__.py
│   ├── timeseries_server.py # 时序数据查询MCP
│   ├── knowledge_server.py  # 知识检索MCP
│   ├── analytics_server.py  # 统计分析MCP
│   ├── physics_server.py    # 物理模型MCP
│   ├── relational_server.py # 关系数据MCP
│   └── notify_server.py     # 通知推送MCP
├── skills/
│   ├── __init__.py
│   ├── anomaly_detect.py    # 异常检测Skill
│   ├── evidence_chain.py    # 证据链构建Skill
│   ├── rul_weibull.py       # RUL威布尔预测Skill
│   ├── fault_graph_skill.py # 故障图推理Skill
│   ├── case_match.py        # 案例匹配Skill
│   ├── cost_benefit.py       # 成本效益分析Skill
│   └── report_gen.py        # 报告生成Skill
├── agents/
│   ├── __init__.py
│   ├── state.py             # LangGraph State定义
│   ├── monitor_agent.py     # Monitor Agent
│   ├── diagnostic_agent.py  # Diagnostic Agent(ReAct)
│   ├── prediction_agent.py  # Prediction Agent
│   ├── decision_agent.py    # Decision Agent
│   ├── knowledge_agent.py   # Knowledge Agent
│   ├── report_agent.py      # Report Agent
│   └── reflection_agent.py  # Reflection Agent
├── graph/
│   ├── __init__.py
│   ├── main_graph.py        # LangGraph主图(Supervisor编排)
│   └── edges.py             # 条件边路由逻辑
├── api/
│   ├── __init__.py
│   ├── main.py              # FastAPI app
│   ├── routes/
│   │   ├── machines.py      # 设备CRUD
│   │   ├── diagnostics.py   # 诊断触发+查询
│   │   ├── realtime.py      # WebSocket实时推送
│   │   └── knowledge.py     # 知识库查询
│   └── dependencies.py      # 依赖注入
└── utils/
    ├── __init__.py
    ├── json_parser.py       # LLM输出JSON解析
    └── validators.py        # 数据验证
```

### 4.2 数据库 Schema (src/database/schemas.py)

```python
# 使用 SQLAlchemy 2.0 ORM + SQLite

class MachineMetadata:
    # 设备元数据
    id: str  # CNC_001
    model: str  # CNC Machine
    manufacture_date: str  # 202104
    install_date: datetime
    last_maintenance: datetime
    next_maintenance: datetime
    last_repair: datetime
    daily_output: int  # 日产量
    unit_cost: float  # 单位生产成本
    status: str  # running/warning/critical/maintenance/offline

class SensorReading:
    # 时序传感器读数（核心表，仿真器写入）
    id: int (PK, autoincrement)
    machine_id: str  # FK -> MachineMetadata.id
    timestamp: datetime  # indexed
    op_voltage: float
    op_amperage: float
    op_temperature: float
    rotor_speed: float
    vibration_rms: float
    motor_power: float
    cutting_force: float
    feed_force: float
    spindle_load: float
    coolant_temp: float
    coolant_pressure: float
    oil_pressure: float
    noise_level: float
    tool_wear: float
    surface_roughness: float
    position_error: float
    power_factor: float
    harmonic_distortion: float
    bearing_temp: float
    motor_vibration: float
    ambient_temp: float
    humidity: float
    fault_code: int  # 0-9, 0=正常
    health_score: float  # 0.0-1.0

class FaultRecord:
    # 故障事件记录
    id: int (PK)
    machine_id: str (FK)
    fault_code: int  # 1-9
    fault_name: str
    detected_at: datetime
    resolved_at: datetime | None
    evidence_chain: str  # JSON array
    confidence: float
    severity: str  # warning/critical/emergency
    rul_p10: int
    rul_p50: int
    rul_p90: int
    root_cause: str
    resolution: str | None

class MaintenanceRecord:
    # 维护工单
    id: int (PK)
    machine_id: str (FK)
    fault_record_id: int | None (FK)
    maintenance_type: str  # corrective/preventive/emergency
    start_time: datetime
    end_time: datetime | None
    downtime_hours: float
    parts_cost: float
    labor_cost: float
    parts_replaced: str  # JSON array
    technician: str
    notes: str

class FMECARule:
    # FMECA结构化规则
    id: int (PK)
    fault_code: int  # 1-9
    fault_name: str  # 轴承磨损
    system: str  # 主轴系统
    component: str  # 主轴轴承
    evidence_signals: str  # JSON: ["温度↑+转速↓+电流波动↑"]
    physical_mechanism: str
    detection_method: str
    sop_steps: str  # JSON array of steps
    required_parts: str  # JSON array
    estimated_hours: float
    engineer_level: str
    severity: str  # warning/critical/emergency
    occurrence_rate: float  # 3-5%
    detectability: float  # 0.0-1.0

class CaseLibrary:
    # 历史案例库
    id: int (PK)
    machine_id: str
    fault_code: int
    description: str
    param_changes: str  # JSON
    root_cause: str
    resolution: str
    rul_hours: int | None
    risk_evolution_path: str  # "A → B → C → D"
    created_at: datetime
```

### 4.3 CNC仿真器 (src/simulator/)

**physics_models.py** — 三个物理退化模型：
```python
# Paris疲劳裂纹扩展: da/dN = C * (ΔK)^m
# C=1e-12, m=3.0 (钢材典型值)
# 轴承退化: 温度日增量 = base_rate * (1 + wear_factor)

# Arrhenius热加速: rate = A * exp(-Ea / (k*T))
# Ea=0.7eV, k=8.617e-5 eV/K
# 电机绕组老化加速

# Taylor刀具寿命: T = C * (v/f)^n
# C=200, n=0.25, v=切削速度, f=进给量
# 刀具磨损预测

def bearing_degradation(day: int, load_factor: float = 1.0) -> dict:
    """返回轴承退化状态: wear_amount, temp_increase, vibration_increase"""

def motor_aging(day: int, avg_temp: float) -> dict:
    """返回电机老化: insulation_resistance, current_drift"""

def tool_wear(cutting_speed: float, feed_rate: float, material_hardness: float) -> dict:
    """返回刀具磨损量"""
```

**fault_injector.py** — 故障注入器：
```python
# 故障注入策略:
# - 总故障率3-5%（非原72.3%）
# - 渐变式故障（非突变式）
# - 故障前兆期: 故障发生前3-7天参数开始偏移
# - 每种故障有特定参数变化模式

FAULT_PATTERNS = {
    1: {  # 轴承磨损
        "temp_increase_per_day": 0.8,  # °C/day
        "vibration_increase_per_day": 0.05,  # mm/s/day
        "current_drift_per_day": 0.1,  # A/day
        "speed_decrease_per_day": 0.3,  # RPM/day
        "precursor_days": 7,
    },
    2: {  # 电机过载
        "current_spike": 35.0,  # A
        "temp_rapid_rise": 5.0,  # °C/hour
        "precursor_days": 2,
    },
    # ... 故障3-9的退化模式
}
```

**cnc_simulator.py** — 主仿真器：
```python
# 生成规格: 50台设备 × 180天 × 1分钟采样间隔 × 18维传感器
# 每台设备约 180*24*60 = 259,200 条记录
# 总记录约 50 * 259,200 = 12,960,000 条
# 为控制数据库大小，采样间隔设为5分钟，总记录约260万条

# 每台设备随机分配:
# - 0-2种故障（故障率3-5%）
# - 故障发生时间随机分布在第30-150天
# - 故障严重程度随机
# - 设备基线参数在正常范围内随机

class CNCSimulator:
    def generate_dataset(self, n_machines=50, days=180, interval_minutes=5) -> None
    def get_reading(self, machine_id, timestamp) -> dict
    def get_window(self, machine_id, start, end) -> list[dict]
```

### 4.4 知识库 (src/knowledge/)

**fmeca.py** — 初始化时从上面的2.2故障类型表加载10条FMECA规则到数据库。

**fault_graph.py** — 故障传播有向图：
```python
# 使用 networkx.DiGraph
# 节点类型: symptom / fault / component / consequence
# 约30节点，50边
# 示例边: temperature_rise -> bearing_wear -> spindle_failure -> machine_downtime

# 节点示例:
NODES = {
    "temperature_rise": {"type": "symptom", "threshold": ">95C"},
    "current_increase": {"type": "symptom", "threshold": ">35A"},
    "vibration_increase": {"type": "symptom", "threshold": ">4.5mm/s"},
    "speed_drop": {"type": "symptom", "threshold": "<220RPM"},
    "bearing_wear": {"type": "fault", "code": 1},
    # ...
    "machine_downtime": {"type": "consequence"},
    "quality_degradation": {"type": "consequence"},
}

# 功能:
def forward_trace(start_node) -> list[str]  # 前向推演: 不修会怎样
def backward_trace(symptom) -> list[str]   # 后向溯源: 为什么坏
def get_path(start, end) -> list[str]      # 获取演化路径
```

**vector_store.py** — ChromaDB向量存储：
```python
# 嵌入模型: bge-m3 (1024维, 本地Ollama)
# 集合:
# - "maintenance_manual": 维护手册内容分块
# - "case_library": 历史案例
# - "equipment_specs": 设备规格
# - "sop_documents": SOP文档

# 使用 UnifiedLLMClient.embed() 做嵌入
# 文档分块策略: 按故障类型分块，每块200-500字

class VectorStore:
    def __init__(self, persist_dir="./data/chromadb")
    def add_documents(self, collection: str, docs: list[dict]) -> None
    def search(self, collection: str, query: str, top_k: int = 5) -> list[dict]
    def search_similar_cases(self, fault_description: str, top_k: int = 3) -> list[dict]
```

### 4.5 MCP服务器 (src/mcp_servers/)

每个MCP服务器暴露一组工具，通过标准协议供Agent调用。
**注意**: 不需要实现完整的MCP协议服务器，用Python函数+LangGraph ToolNode即可。

```python
# timeseries_server.py
def query_timeseries(machine_id: str, start: str, end: str) -> list[dict]
def get_latest_reading(machine_id: str) -> dict
def get_statistics(machine_id: str, window_hours: int) -> dict
def get_trend(machine_id: str, metric: str, hours: int) -> dict

# knowledge_server.py
def search_fmeca(fault_code: int) -> dict
def search_cases(query: str, top_k: int = 3) -> list[dict]
def get_sop(fault_code: int) -> dict
def search_manual(query: str) -> list[dict]

# analytics_server.py
def calculate_health_score(machine_id: str) -> float
def detect_anomaly(machine_id: str, window: int = 60) -> dict
def calculate_degradation_rate(machine_id: str, metric: str) -> float
def fleet_comparison(machine_id: str) -> dict

# physics_server.py
def predict_bearing_life(current_wear: float, load: float) -> dict
def predict_motor_life(avg_temp: float, hours: int) -> dict
def predict_tool_life(speed: float, feed: float, hardness: float) -> dict

# relational_server.py
def get_machine_info(machine_id: str) -> dict
def get_maintenance_history(machine_id: str) -> list[dict]
def get_fault_history(machine_id: str) -> list[dict]
def calculate_downtime_cost(machine_id: str, hours: float) -> dict

# notify_server.py
def send_alert(machine_id: str, severity: str, message: str) -> bool
def create_maintenance_order(machine_id: str, fault_id: int) -> int
```

### 4.6 Skills (src/skills/)

Skills封装完整业务流程，Agent调用Skill，Skill调用MCP工具。

```python
# anomaly_detect.py — 异常检测Skill
# 输入: machine_id, sensor_data
# 输出: health_score, is_anomaly, anomaly_dims, severity
# 逻辑: 18维阈值检查 + 滑动窗口统计 + 马氏距离

# evidence_chain.py — 证据链构建Skill
# 输入: machine_id, anomaly_result
# 输出: evidence_chain[E1-E7], confidence
# E1: 当前参数偏离基线
# E2: 24小时趋势分析
# E3: 7天退化速率
# E4: 跨设备对比
# E5: FMECA规则匹配
# E6: 历史案例匹配
# E7: 故障传播图推演

# rul_weibull.py — RUL预测Skill（迁移自原节点②）
# 核心算法: 威布尔分布 RUL = eta * (-ln(health_index))^(1/beta)
# beta = 1.0 + deg_rate_norm * 2.5 * maturity_factor * noise_factor
# eta = 720 * (1 - risk_score/150) / deg_rate_norm
# 输出: rul_p10, rul_p50, rul_p90, degradation_rate, monitoring_freq
# 约束: P10 < P50 < P90, 退化率上限0.005, 温度限180°C

# case_match.py — 案例匹配Skill
# 使用bge-m3向量嵌入+ChromaDB检索相似历史案例

# cost_benefit.py — 成本效益分析Skill
# 停机损失 = 日产量 × 单位成本 × 停机天数
# 维护成本 = 备件费 + 工时费 + 辅料费
# 风险成本 = 故障概率 × (停机损失 + 维护成本)
# net_benefit = 避免损失 - 维护成本

# report_gen.py — 报告生成Skill
# 生成结构化诊断报告: 设备概况→异常发现→诊断分析→RUL→维护建议→风险评估
```

### 4.7 Agent实现 (src/agents/)

**state.py** — LangGraph State：
```python
from typing import TypedDict, Annotated
from langgraph.graph import add_messages

class DiagnosticState(TypedDict):
    # 输入
    machine_id: str
    trigger_timestamp: str
    sensor_data: dict
    # Monitor输出
    health_score: float
    is_anomaly: bool
    anomaly_dims: list[str]
    severity: str  # normal/warning/critical
    # Diagnostic输出
    fault_mode: str
    fault_code: int
    evidence_chain: list[str]  # E1-E7
    confidence: float
    root_cause: str
    react_iterations: int
    # Prediction输出
    rul_p10: int
    rul_p50: int
    rul_p90: int
    degradation_rate: float
    monitoring_freq: str
    # Decision输出
    recommendation: str
    timing: str  # immediate/degraded/scheduled
    action: str  # replace/repair/monitor
    downtime_hours: float
    direct_cost: float
    risk_cost: float
    net_benefit: float
    # Report输出
    report: str
    # Reflection输出
    reflection_passed: bool
    reflection_checks: list[dict]
    # 控制
    retry_count: int
    max_retries: int  # 3
```

**diagnostic_agent.py** — ReAct循环：
```python
# 1. Thought: 分析异常模式
# 2. Action: 调用工具(查趋势/查FMECA/查案例)
# 3. Observation: 工具返回
# 4. 重复直到confidence>0.7或达到max_iterations=3
# 5. 构建E1-E7证据链
# 使用UnifiedLLMClient.chat(get_agent_config("diagnostic"), messages)
```

### 4.8 LangGraph主图 (src/graph/main_graph.py)

```python
from langgraph.graph import StateGraph, END

# 节点: monitor -> diagnostic -> prediction -> decision -> report -> reflection
# 条件边:
#   monitor: is_anomaly=False -> END, True -> diagnostic
#   diagnostic: confidence>0.7 -> prediction, <0.7 && retries<3 -> diagnostic(retry), >=3 -> prediction
#   reflection: all_passed -> END, not_passed && retries<3 -> diagnostic(retry), >=3 -> END

graph = StateGraph(DiagnosticState)
graph.add_node("monitor", monitor_node)
graph.add_node("diagnostic", diagnostic_node)
graph.add_node("prediction", prediction_node)
graph.add_node("decision", decision_node)
graph.add_node("report", report_node)
graph.add_node("reflection", reflection_node)

graph.set_entry_point("monitor")
graph.add_conditional_edges("monitor", route_monitor)        # -> diagnostic | END
graph.add_conditional_edges("diagnostic", route_diagnostic)   # -> prediction | diagnostic(retry)
graph.add_edge("prediction", "decision")
graph.add_edge("decision", "report")
graph.add_edge("report", "reflection")
graph.add_conditional_edges("reflection", route_reflection)   # -> END | diagnostic(retry)
```

### 4.9 API (src/api/main.py)

```python
# FastAPI endpoints:
# GET  /api/machines                  # 设备列表
# GET  /api/machines/{id}             # 设备详情
# GET  /api/machines/{id}/realtime    # 最新读数
# GET  /api/machines/{id}/history     # 历史数据(分页)
# POST /api/diagnostics/trigger       # 触发诊断
# GET  /api/diagnostics/{id}          # 诊断结果
# GET  /api/diagnostics                # 诊断列表
# GET  /api/machines/{id}/health      # 健康评分
# GET  /api/knowledge/fmeca/{code}    # FMECA查询
# POST /api/knowledge/search           # 知识检索
# WS   /ws/realtime                    # WebSocket实时推送
```

### 4.10 前端 (web/ 或 frontend/)

已有Vite+React+Tailwind项目。需实现的页面：
1. **设备总览页** — 50台设备卡片，显示健康评分、状态、最新告警
2. **设备详情页** — 实时传感器图表(Recharts)、历史趋势、故障记录
3. **诊断报告页** — 诊断结果展示、证据链可视化、RUL图表
4. **知识库页** — FMECA浏览、案例搜索
5. **告警面板** — 实时告警列表(WebSocket)

配色：中性色(白/灰/黑)+状态色(绿=正常/黄=警告/红=危险)，shadcn/ui风格。

---

## 5. 已有算法逻辑（迁移自原6个节点）

### 5.1 趋势演化预测（原节点①）
```python
# 退化速率计算:
# raw_rate = temp_slope * 0.6 + curr_slope * 0.4
# temp_slope = max((short_temp - mid_temp)/mid_temp, (short_temp - long_temp)/long_temp * 0.7)
# degradation_rate = max(0.0005, raw_rate * (0.7 + 0.3 * anomaly_confidence))
# 温度波动>8 → ×1.4, >5 → ×1.15
# 上限: 0.005（物理约束）
# 多步预测: pred_temp = avg_temp * exp(degradation_rate * step)
# 温度硬限: 180°C
```

### 5.2 RUL估计（原节点②）
```python
# 威布尔分布:
# health_index = max(0.05, 1.0 - risk_score/100)
# beta = 1.0 + deg_rate_norm * 2.5 * maturity_factor * noise_factor
#   deg_rate_norm = min(1.0, max(0.1, degradation_rate/0.015))
#   maturity_factor = 0.9 + (equip_maturity/100) * 0.4
#   noise_factor = max(0.75, 1.0 - noise_index * 0.3)
# eta = 720 * (1 - risk_score/150) / max(0.3, deg_rate_norm * maturity_factor)
# RUL = eta * (-ln(health_index))^(1/beta)
# 加速因子: temp_stress * curr_stress
# P10 = RUL * 1.3, P50 = RUL, P90 = RUL * 0.7 (置信区间)
# 约束: P10 > P50 > P90, RUL >= 4h
```

### 5.3 跨设备统计（原节点③）
```python
# 舰队基线: 所有同型设备的参数均值+标准差
# 故障质心: 每种故障的参数中心(μ, σ)
# 特征权重: 各维度对故障判别的贡献度
# 马氏距离: D = sqrt((x-μ)' Σ^-1 (x-μ))
```

### 5.4 贝叶斯异常概率（原节点④）
```python
# 先验: critical=0.85, high=0.60, medium=0.30, low=0.08
# 似然: anomaly + rul + degradation + temp
# Log-Odds融合: log(p/(1-p)) + Σ log(L_i/(1-L_i)) * 0.5
# 输出概率: 1/(1+exp(-log_odds)) * 100
```

### 5.5 证据链引擎（原节点⑥）
```python
# 7条证据(E1-E7):
# E1: 当前参数偏离基线 — 哪些维度异常
# E2: 24小时趋势分析 — 上升/下降/稳定
# E3: 7天退化速率 — 退化加速度
# E4: 跨设备对比 — 同型设备对比百分位
# E5: FMECA规则匹配 — 匹配故障模式
# E6: 历史案例匹配 — 相似案例
# E7: 故障传播图推演 — 可能的演化路径
# 置信度: 每条证据贡献0.05-0.15，基线0.6
```

---

## 6. 硬性约束（不可违反）

1. **禁止random造假**: 仿真器必须基于物理模型(Paris/Arrhenius/Taylor)，退化是渐变的不是突变的
2. **故障率3-5%**: 50台设备中只有1-3台会在180天内出现故障
3. **温度硬限180°C**: 任何预测温度不得超过180°C
4. **退化率上限0.005/min**: 物理约束
5. **RUL一致性**: P10 > P50 > P90（P10是乐观估计=更长的剩余寿命）
6. **置信度门槛**: Diagnostic输出confidence必须>0.7才进入Prediction
7. **ReAct最多3轮**: 超过3轮直接进入Prediction
8. **Reflection不过最多重试3次**: 超过直接输出当前结果
9. **所有LLM输出必须解析JSON**: Qwen2.5可能输出```json包裹，需要处理
10. **Ollama不支持原生Function Calling**: 用结构化Prompt引导JSON输出
11. **SQLite作为数据库**: 不需要TimescaleDB/InfluxDB，用SQLite+索引即可
12. **代码必须有完整type hint和docstring**
13. **禁止硬编码设备ID/参数**: 从数据库读取

---

## 7. 已有代码（直接使用，不要重写）

### src/config/model_routing.py
- `AGENT_ROUTING` 字典: 7个Agent的模型配置(provider/model/temperature/system_prompt)
- `get_agent_config(agent_name)` 函数: 获取Agent配置
- `EMBEDDING_MODEL = "bge-m3"`

### src/llm/client.py
- `UnifiedLLMClient` 类: 统一LLM调用接口
- `client.chat(config, messages, tools)` → `{"content", "tool_calls", "usage", "latency_ms"}`
- `client.embed(text)` → `list[float]` (1024维)
- `client.embed_batch(texts)` → `list[list[float]]`
- `client.health_check()` → `{"ollama": bool, "deepseek": bool}`

### 使用方式
```python
from src.config.model_routing import get_agent_config
from src.llm.client import UnifiedLLMClient

client = UnifiedLLMClient()
config = get_agent_config("diagnostic")
result = await client.chat(config, messages=[{"role": "user", "content": "..."}])
```

### 环境变量 (.env)
```
OLLAMA_BASE_URL=http://127.0.0.1:11434
OLLAMA_MONITOR_MODEL=zhiwei-monitor
OLLAMA_KNOWLEDGE_MODEL=zhiwei-knowledge
OLLAMA_REFLECTION_MODEL=zhiwei-reflection
OLLAMA_EMBEDDING_MODEL=bge-m3
DEEPSEEK_API_KEY=（需要用户填写）
DEEPSEEK_BASE_URL=https://api.deepseek.com/v1
DEEPSEEK_MODEL=deepseek-chat
DATABASE_URL=sqlite:///./data/zhiwei.db
```

---

## 8. 实现优先级

**必须实现（P0）:**
1. `src/database/schemas.py` + `connection.py` + `init_db.py`
2. `src/simulator/` 全部4个文件
3. `src/knowledge/fmeca.py` + `fault_graph.py` + `vector_store.py`
4. `src/skills/anomaly_detect.py` + `evidence_chain.py` + `rul_weibull.py`
5. `src/agents/state.py` + `monitor_agent.py` + `diagnostic_agent.py` + `reflection_agent.py`
6. `src/graph/main_graph.py` + `edges.py`
7. `src/api/main.py` + 基础路由

**应该实现（P1）:**
8. `src/agents/prediction_agent.py` + `decision_agent.py` + `report_agent.py` + `knowledge_agent.py`
9. `src/skills/case_match.py` + `cost_benefit.py` + `report_gen.py` + `fault_graph_skill.py`
10. `src/mcp_servers/` 全部6个文件

**可以后补（P2）:**
11. 前端React组件
12. WebSocket实时推送
13. 高级可视化

---

## 9. 依赖清单

```
# requirements.txt
langgraph>=0.2.0
langchain>=0.3.0
fastapi>=0.115.0
uvicorn>=0.30.0
httpx>=0.27.0
sqlalchemy>=2.0.0
chromadb>=0.5.0
networkx>=3.3
python-dotenv>=1.0.0
pydantic>=2.9.0
numpy>=1.26.0
scipy>=1.13.0
```
