"""知识索引构建与召回率评估脚本。

用法::

    # 构建/刷新索引（源文档未变时自动跳过嵌入，秒级完成）
    .venv/Scripts/python.exe scripts/rag_tools.py build

    # 评估召回率（内置 12 条带预期命中的测试查询）
    .venv/Scripts/python.exe scripts/rag_tools.py eval
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.database.connection import Database  # noqa: E402
from src.knowledge.index_builder import KnowledgeIndex  # noqa: E402

# 评估集：query + 任一命中关键词即算召回（hit@k）
EVAL_SET: list[tuple[str, tuple[str, ...]]] = [
    ("主轴温度过高怎么处理", ("温度", "散热")),
    ("轴承磨损维护步骤", ("轴承", "更换")),
    ("冷却液压力低的维修方法", ("冷却", "压力")),
    ("编码器信号异常如何检测", ("编码器", "信号")),
    ("润滑不足导致什么后果", ("润滑",)),
    ("主轴异响振动大是什么故障", ("振动", "异响")),
    ("SKF 7014CDGA/P4A 备件型号", ("skf", "7014")),
    ("换一个主轴轴承需要多久", ("4.5", "工时", "小时")),
    ("CNC 设备每天产量是多少", ("产量", "日生产")),
    ("设备保养周期多长", ("保养", "维护时间")),
    ("复合故障怎么排查", ("复合", "优先级")),
    ("电机绝缘老化的机理", ("绝缘", "老化", "电机")),
]


async def build() -> dict:
    index = KnowledgeIndex(Database())
    return await index.ensure(embed=True, force=True)


async def evaluate(top_k: int = 5) -> dict:
    index = KnowledgeIndex(Database())
    await index.ensure(embed=False)
    hits = 0
    details: list[dict[str, object]] = []
    for query, expected in EVAL_SET:
        results = await index.retriever.search(query, top_k=top_k)
        matched = any(any(keyword in r.chunk.text.lower() for keyword in expected) for r in results)
        first_hit = next((rank for rank, r in enumerate(results, start=1) if any(keyword in r.chunk.text.lower() for keyword in expected)), None)
        hits += bool(matched)
        details.append({"query": query, "hit": matched, "first_hit_rank": first_hit, "top1": results[0].chunk.doc_type if results else None})
    return {"top_k": top_k, "recall": round(hits / len(EVAL_SET), 3), "hits": hits, "total": len(EVAL_SET), "details": details}


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "eval"
    if mode == "build":
        print(asyncio.run(build()))
    else:
        report = asyncio.run(evaluate())
        print(f"recall@{report['top_k']}: {report['recall']} ({report['hits']}/{report['total']})")
        for item in report["details"]:
            flag = "✓" if item["hit"] else "✗"
            print(f" {flag} {item['query']}  top1={item['top1']} first_hit={item['first_hit_rank']}")
