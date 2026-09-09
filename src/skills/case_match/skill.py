"""Case matching over the curated episodic memory table.

注意：本 skill 在 v2 中是**词法检索**通道。语义相似检索由
``src/rag/retriever.py`` 的 ``HybridRetriever`` 负责（稠密 + 稀疏 + RRF 融合），
聊天 Agent 与维护报告应优先使用 ``query_knowledge``，本 skill 保留给需要
按 case_library 表精确召回「结构化案例」的场景。
"""

from __future__ import annotations

from typing import Any

from src.database.connection import Database

from ..base import BaseSkill, SkillExample, SkillMeta
from .schema import CaseItem, CaseMatchInput, CaseMatchOutput


def match_cases(database: Database, query: str, fault_code: int | None = None, top_k: int = 3) -> list[dict[str, Any]]:
    """Return the most textually similar historical cases."""

    return database.search_cases(query, fault_code, top_k)


class CaseMatchSkill(BaseSkill):
    """Retrieve structurally similar historical maintenance cases."""

    meta = SkillMeta(
        id="case_match",
        name="历史案例匹配",
        version="2.0.0",
        description=(
            "在 case_library 表中检索与故障描述词法相似的历史案例，返回根因与处置方案。"
            "适合按故障码做精确召回；需要语义相似检索时请改用 query_knowledge（走 RAG 混合检索）。"
        ),
        category="knowledge",
        deterministic=True,
        requires_llm=False,
        tags=("case", "retrieval", "E4", "lexical"),
        examples=(
            SkillExample(
                description="检索散热相关的历史处置案例",
                payload={"query": "温度持续升高 电流正常 散热风扇", "top_k": 3},
            ),
        ),
    )
    input_schema = CaseMatchInput
    output_schema = CaseMatchOutput

    def __init__(self, database: Database | None = None) -> None:
        self._database = database

    @property
    def database(self) -> Database:
        """Lazily build the default repository so importing this skill stays cheap."""

        if self._database is None:
            self._database = Database()
        return self._database

    def run(self, payload: CaseMatchInput) -> CaseMatchOutput:
        """Search the case library and normalize rows into the output contract."""

        database = Database(payload.database_url) if payload.database_url else self.database
        rows = match_cases(database, payload.query, payload.fault_code, payload.top_k)
        items = [
            CaseItem(
                id=str(row.get("id", "")),
                machine_id=str(row.get("machine_id", "")),
                fault_code=int(row.get("fault_code", 0) or 0),
                description=str(row.get("description", "")),
                root_cause=str(row.get("root_cause", "")),
                resolution=str(row.get("resolution", "")),
                relevance=int(row.get("relevance", 0) or 0),
            )
            for row in rows
        ]
        return CaseMatchOutput(query=payload.query, items=items, count=len(items))
