# Skill：fault_graph_trace（故障因果图溯源）

> 版本 2.0.0 · 分类 `analysis` · 确定性 · 副作用 `read`

## 用途

在 FMECA 因果传播图（14 节点 / 12 条边）上做**有向遍历**，回答两类问题：

| 方向 | 回答的问题 | 典型场景 |
| --- | --- | --- |
| `forward` | 这个症状/故障**会导致什么后果**？ | 风险外推、影响面评估 |
| `backward` | 这个症状**可能的根因链**是什么？ | 根因定位（E6 证据） |

## 图结构

- 症状层：`voltage_instability` / `current_spike` / `temperature_rise` / `vibration_increase` / `speed_drop`
- 故障层：`power_module_failure` / `bearing_wear` / `motor_overload` / `cooling_failure` / `rotor_fault`
- 后果层：`motor_overheating` / `spindle_failure` / `quality_degradation` / `machine_downtime`

**路径概率** = 边上概率连乘；**滞后时间**保留区间字符串（如 `24-168h`），不取中点——
因为边的滞后本身就是一个范围，强行取中点是在假装精确。

## 入参（FaultGraphTraceInput）

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `node` | string | 起始节点 id；也接受中文故障名（内部自动映射） |
| `direction` | `forward` \| `backward` | 默认 `forward` |
| `max_depth` | 1–6 | 最大传播深度，默认 3 |

## 出参（FaultGraphTraceOutput）

`node`（归一化后的 id）/ `direction` / `paths[]`（`nodes` + `probability` + `lag_hours`，
按概率降序，最多 10 条）/ `count` / `known_nodes`（**仅当**起始节点不在图中时返回，
避免调用方瞎猜节点名）。

## 调用示例

```python
from src.skills import SkillRegistry

SkillRegistry.run("fault_graph_trace", {"node": "spindle_failure", "direction": "backward"})
# → bearing_wear → spindle_failure（概率 0.78）
```

## 确定性声明

图结构在构造时固定，遍历结果只依赖入参，不调用模型、不读数据库。
