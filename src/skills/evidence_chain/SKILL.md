# Skill：evidence_chain（E1–E7 证据链构建）

> 版本 2.0.0 · 分类 `analysis` · 确定性 · 副作用 `read`

## 用途

把诊断过程中分散的事实，组装成 **7 条可逐条追溯的证据**，让"为什么判这个故障"可以逐项核对。

| ID | 来源 | 回答的问题 |
| --- | --- | --- |
| E1 | `sensor_readings` | 当前哪些维度异常？主要信号是什么？ |
| E2 | `rolling_baseline` | 相对设备自身健康基线的偏离有多大？ |
| E3 | `trend_analysis` | 是渐进退化还是单点突变？ |
| E4 | `case_library` | 历史上有没有相似案例，根因是什么？ |
| E5 | `fmeca_rules` | 匹配到哪条结构化规则？信号与 SOP 是什么？ |
| E6 | `fault_graph` | 因果链上它是怎么走到这一步的？ |
| E7 | `maintenance_records` | 是不是重复性故障？上次怎么处理的？ |

## 置信度规则（显式，不拍脑袋）

| 证据 | 有数据时 | 缺数据时 | 依据 |
| --- | --- | --- | --- |
| E1 | `min(0.98, 0.55 + anomaly_score)` | — | 异常分越高越确信 |
| E2 | 0.78 | 0.40 | 有没有历史窗口做基线 |
| E3 | 0.75 | 0.35 | 时序点是否 ≥ 6 |
| E4 | 0.72 | 0.35 | 有没有命中相似案例 |
| E5 | 0.82 | 0.30 | 有没有匹配到 FMECA |
| E6 | 0.80 | 0.30 | 因果图有没有形成路径 |
| E7 | 0.60 | 0.35 | 有没有历史维护记录 |

**聚合置信度** = 加权均值，权重 `E2=1.3, E3=1.2, E1=E6=1.0, E4=0.9, E5=0.8, E7=0.6`
（数据驱动的两条权重最高，检索/规则类次之）。

## 入参（EvidenceChainInput）

`anomaly{anomaly_score, anomaly_dims}` / `history[]` / `rule{fault_code, fault_name}` /
`cases[{id, root_cause}]` / `causal_paths[{nodes[], probability}]` / `maintenance_context`

## 出参（EvidenceChainOutput）

`items[7]`（每条含 `id / source / content / confidence / supporting_data`）、`count`、
`aggregate_confidence`。

## 调用示例

```python
from src.skills import SkillRegistry

SkillRegistry.run("evidence_chain", {
    "anomaly": {"anomaly_score": 0.62, "anomaly_dims": ["op_temperature"]},
    "rule": {"fault_code": 8, "fault_name": "散热系统故障"},
    "cases": [{"id": "CASE-004", "root_cause": "散热风扇卡死"}],
})
```

## 确定性声明

纯字符串组装与算术，同样输入必然同样输出；不调用模型、不读数据库（数据由调用方注入）。
