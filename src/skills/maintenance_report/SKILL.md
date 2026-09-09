# maintenance_report 维护报告生成

- **Skill ID**: `maintenance_report`
- **分类**: operations ｜ **确定性**: 是 ｜ **需要 LLM**: 否
- **版本**: 1.0.0

## 触发场景

设备被诊断为高风险，且管理员在通知页**批准**该报警后，由 API 层自动调用本 skill 生成维护报告并落库（`maintenance_reports` 表）。也可通过 `/api/skills/maintenance_report/run` 手动触发。

## 输入

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| machine_id | string | ✔ | 设备编号，如 `B2012` |
| diagnostic_id | string? | ✘ | 关联诊断；缺省取该设备最近一次诊断 |
| notification_id | string? | ✘ | 触发审批的通知 ID，写入报告头 |
| order_id | string? | ✘ | 关联维修工单 ID |
| approved_by | string? | ✘ | 批准人 |
| include_history | bool | ✘ | 是否附维修历史与相似案例（默认 true） |

## 输出

`report_id`（RPT-xxx，已落库）、`title`、`summary`（一句话摘要）、`markdown`（完整正文）、`sections`（章节锚点）、`data_sources`（引用的数据表，保证可追溯）。

## 报告结构

一、设备概况 → 二、诊断结论 → 三、根因机理与处置规程（FMECA）→ 四、历史维修记录 → 五、相似历史案例 → 六、维护建议

## 数据来源

全部来自 SQLite 实况：`machines` / `diagnostics` / `fmeca_rules` / `maintenance_records` / `case_library`。不调用大模型，相同输入必得相同输出，便于竞赛答辩演示与审计。
