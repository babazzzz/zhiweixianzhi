# Skill：diagnostic_report（诊断报告渲染）

> 版本 2.0.0 · 分类 `report` · 确定性 · 副作用 `read`

## 用途

把**一次诊断**的完整状态渲染成固定结构的 Markdown 报告：

1. 诊断摘要（故障模式 / 根因 / 严重度 / 置信度）
2. RUL 区间（P10 乐观 / P50 / P90 保守）
3. E1–E7 证据链（逐条带置信度）
4. 维护决策（推荐 / 窗口 / 停机 / 成本 / 备件）

## 为什么不用 LLM 生成报告

| 维度 | 纯字符串组装（当前） | LLM 生成 |
| --- | --- | --- |
| 可复现 | 同样输入必然同样输出 | 每次不同 |
| 数值可信 | 数字直接来自状态，不会编造 | 可能幻觉 |
| 耗时 | 毫秒级 | 秒级 |
| 审计 | 报告即状态的投影 | 难以核对 |

需要润色时，由上层**显式**调用 LLM 并在报告里标注"本节由模型润色"，不混在本 skill 里。

## 入参（DiagnosticReportInput）

`machine_id` / `fault_mode` / `root_cause` / `severity` / `confidence` /
`rul{rul_p10, rul_p50, rul_p90}` / `evidence[]` / `decision`

## 出参（DiagnosticReportOutput）

`title` / `markdown` / `generated_at` / `char_count`

## 调用示例

```python
from src.skills import SkillRegistry

report = SkillRegistry.run("diagnostic_report", {
    "machine_id": "CNC_007",
    "fault_mode": "轴承磨损",
    "severity": "critical",
    "confidence": 0.86,
    "rul": {"rul_p10": 96, "rul_p50": 72, "rul_p90": 50},
})
print(report["markdown"])
```

## 与 maintenance_report 的区别

| | diagnostic_report | maintenance_report |
| --- | --- | --- |
| 触发时机 | 诊断流程末尾自动渲染 | 管理员**批准通知后**才生成 |
| 读者 | 工程师排查故障 | 维修班组执行作业 |
| 核心内容 | 故障是什么、为什么 | 怎么修、要什么备件、谁来做 |
