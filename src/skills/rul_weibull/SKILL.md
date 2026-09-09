# rul_weibull — 威布尔剩余寿命预测

## 用途

根据设备的温度/电流退化趋势，拟合威布尔分布并输出 P10/P50/P90 三档剩余寿命区间（小时），
供决策 Agent 计算维护时机与成本收益。

## 确定性

**完全确定性**（`deterministic: true`，`requires_llm: false`）。

## 输入

| 字段 | 类型 | 范围 | 说明 |
|---|---|---|---|
| `history` | `list[dict[str, float]]` | — | 历史读数序列，至少 6 点 |
| `health_score` | `float` | 0-1 | 来自 `anomaly_detect` |
| `anomaly_score` | `float` | 0-1 | 来自 `anomaly_detect` |

## 输出

| 字段 | 类型 | 说明 |
|---|---|---|
| `rul_p10` | `int` | P10 乐观寿命（小时），**本项目约定为偏长界** |
| `rul_p50` | `int` | P50 中位寿命（小时） |
| `rul_p90` | `int` | P90 保守寿命（小时） |
| `degradation_rate` | `float` | 每分钟相对退化速率，约束在 [0.0005, 0.005] |
| `monitoring_freq` | `str` | `15分钟` / `1小时` / `4小时` |
| `beta` / `eta` | `float` | 威布尔形状 / 尺度参数 |
| `confidence` | `float` | 0.45-0.96，随样本量与维度完整度上升 |

## 计算逻辑

1. 温度与电流各自算**有界相对斜率**（首尾各三分之一均值之差 / 样本跨度）
2. 退化速率 = `0.6 × 温度斜率 + 0.4 × 电流斜率`，裁剪到 [0.0005, 0.005]
3. 风险度 = `1 - 健康度×0.75 + 异常分×0.35`
4. `beta = 1 + 归一化退化×2.5`，`eta = 720×(1-风险/1.5)/归一化退化`
5. 中位寿命 = `eta × (-ln(1-风险))^(1/beta)`，P10 = ×1.30，P90 = ×0.70
6. 强制修正保证 `P10 > P50 > P90`

## 与旧实现的差异（重要）

旧版曾输出伪造的 P5/P95 分位数，且口径不一致。本项目**统一为 P10/P50/P90**：

- P10 = 偏长（乐观）寿命界 → 用于"最理想还能撑多久"
- P90 = 偏短（保守）寿命界 → 用于"最坏情况什么时候必须停机"

`RULOutput.ordered` 属性可断言该顺序成立，便于测试。

## 调用示例

```python
from src.skills import SkillRegistry

SkillRegistry.run("rul_weibull", {"history": [...], "health_score": 0.42, "anomaly_score": 0.58})
```

HTTP：`POST /api/skills/rul_weibull/run`
