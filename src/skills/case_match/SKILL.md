# case_match — 历史案例匹配

## 用途

在 `case_library` 表中检索与故障描述相似的历史案例，返回根因、处置方案与 RUL。
是 E4 证据的来源，也是维护报告「相似案例」章节的数据源。

## 确定性

**完全确定性**（`deterministic: true`）。词法检索，无随机数、无大模型。

## 输入

| 字段 | 类型 | 说明 |
|---|---|---|
| `query` | `str` | 故障描述 / 症状文本，1-1000 字 |
| `fault_code` | `int \| None` | 已知故障码（0-9）时用于精确过滤 |
| `top_k` | `int` | 返回条数，1-20，默认 3 |
| `database_url` | `str \| None` | 可选，覆盖默认 SQLite 连接串 |

## 输出

`items` 为案例列表，每条含 `id` / `machine_id` / `fault_code` / `description` /
`root_cause` / `resolution` / `relevance`（词法命中数）。

## 与 RAG 的分工

| 场景 | 用哪个 |
|---|---|
| 按故障码精确召回结构化案例 | `case_match`（本 skill） |
| 「主轴异响发热」这类语义描述 | `query_knowledge`（`HybridRetriever`，稠密+稀疏+RRF） |

维护报告与聊天 Agent 默认走 `query_knowledge`，本 skill 保留给需要
`case_library` 表结构的调用方（如旧诊断链路）。

## 调用示例

```python
from src.skills import SkillRegistry

SkillRegistry.run("case_match", {"query": "电流异常 电压抖动", "fault_code": 7, "top_k": 3})
```

HTTP：`POST /api/skills/case_match/run`
