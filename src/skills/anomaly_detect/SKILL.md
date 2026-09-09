# anomaly_detect — 多信号异常检测

## 用途

对一台 CNC 设备的最新读数做异常检测，输出健康度、异常分、异常维度清单与严重度。
是监控 Agent（MonitorAgent）的核心判据，也是 E1/E2/E3 三条证据的来源。

## 确定性

**完全确定性**（`deterministic: true`，`requires_llm: false`）。
同样输入必然得到同样输出，不调用任何大模型，不使用随机数。

## 输入

| 字段 | 类型 | 说明 |
|---|---|---|
| `latest` | `dict[str, float]` | 最新一条读数，键为传感器维度名（如 `op_temperature`） |
| `history` | `list[dict[str, float]]` | 历史读数序列，按时间升序。前 40% 作为健康基线 |

## 输出

| 字段 | 类型 | 说明 |
|---|---|---|
| `health_score` | `float` | 健康度 0-1，越大越健康 |
| `anomaly_score` | `float` | 异常分 0-1，越大越异常 |
| `is_anomaly` | `bool` | 是否判定为异常 |
| `anomaly_dims` | `list[str]` | 被判定为异常的传感器维度 |
| `severity` | `str` | `normal` / `warning` / `critical` |
| `anomaly_type` | `str` | `stable` / `gradual` / `sudden` |
| `baseline` | `dict[str, float]` | 各维度的鲁棒基线（历史前 40% 的中位数） |
| `details` | `dict[str, DimensionDetail]` | 每维度的 value / baseline / z_score / score / out_of_range |

## 判定逻辑

三个通道取最大值作为该维度的异常分：

1. **工程阈值**：超出临界上下限 → `0.55 + 越界距离/量程`（阈值来自设备说明书第三章）
2. **鲁棒偏离**：`z_score / 5`，z 用中位数和标准差计算，抗单点毛刺
3. **渐变趋势**：历史首尾各三分之一的均值相对变化 × 4，上限 0.8

- 维度分 ≥ 0.55 记入 `anomaly_dims`
- ≥3 个异常维度 或 平均异常分 ≥ 0.78 → `critical`
- 有异常维度 或 平均异常分 ≥ 0.40 → `warning`
- 否则 `normal`

## 设计取舍

- 基线**只取前 40%**而非全量：否则长期退化会被自适应抹平，退化设备永远显示健康
- 缺失维度直接跳过，**绝不用合成值填充**（零幻觉原则）
- 检测的是"异常"不是"故障"；故障模式判定交给 FMECA 规则与诊断 Agent

## 调用示例

```python
from src.skills import SkillRegistry

result = SkillRegistry.run(
    "anomaly_detect",
    {"latest": {"op_temperature": 96.2, "op_amperage": 31.5},
     "history": [...]},
)
```

HTTP：`POST /api/skills/anomaly_detect/run`
