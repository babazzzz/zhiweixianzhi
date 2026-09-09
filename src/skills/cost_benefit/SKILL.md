# Skill：cost_benefit（维护成本收益分析）

> 版本 2.0.0 · 分类 `operations` · 确定性（纯计算，无 LLM）· 副作用 `read`

## 用途

基于设备的**产量/成本基数**与**剩余寿命 RUL**，对三种维护策略统一定价：

| 策略 | 停机 | 直接成本 | 风险成本 |
| --- | --- | --- | --- |
| 立即维修 | 全额工时 | 备件 + 工时 | 0 |
| 计划窗口维修 | 60% 停机损失 | 备件 + 工时 | 72h 故障概率 × 故障损失 |
| 减载运行 20% | 0 | 25% 直接成本 | 75% 风险概率 × 故障损失 + 20% 产值损失 |

推荐项 = 三个候选中**总成本最低**者。

## 成本口径（可审计，不隐藏假设）

- 工时单价 `LABOR_RATE_PER_HOUR = 360 元/小时`
- 单件备件折算 `PART_UNIT_COST = 1500 元`
- 停机损失 = `日产量 × 单位成本 ÷ 24 × 维修工时`
- 故障损失 = `两天产值 + 直接成本 × 1.8`（含次生损伤与交期违约）
- 风险概率 = `min(0.95, 72 / RUL_P50)`

## 入参（CostBenefitInput）

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `machine.daily_output` | float ≥ 0 | 日产量（件/天） |
| `machine.unit_cost` | float ≥ 0 | 单位生产成本（元/件） |
| `rul_p50_hours` | float > 0 | RUL 中位数（小时），< 4 时按 4 兜底 |
| `rule.estimated_hours` | float | 预计维修工时；缺省 2.0 |
| `rule.required_parts` | string[] | 备件清单；缺省空数组 |

## 出参（CostBenefitOutput）

`recommendation` / `timing` / `action` / `downtime_hours` / `direct_cost` / `risk_cost` /
`net_benefit` / `options[]`（三个候选的完整拆分）/ `what_if` / `parts` / `estimated_hours`。

## 调用示例

```python
from src.skills import SkillRegistry

SkillRegistry.run("cost_benefit", {
    "machine": {"daily_output": 1200, "unit_cost": 8.0},
    "rul_p50_hours": 60.0,
    "rule": {"estimated_hours": 4.5, "required_parts": ["SKF 7014CDGA/P4A×2"]},
})
```

## 确定性声明

同样输入必然得到同样输出；结果只依赖入参与两个常量，不读取数据库、不调用模型。
