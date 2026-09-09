"""Input/output contracts for the historical case matching skill."""

from __future__ import annotations

from pydantic import BaseModel, Field


class CaseMatchInput(BaseModel):
    """A fault description to match against the curated case library."""

    query: str = Field(min_length=1, max_length=1000, description="故障描述或症状文本")
    fault_code: int | None = Field(default=None, ge=0, le=9, description="已知故障码时用于精确过滤")
    top_k: int = Field(default=3, ge=1, le=20, description="返回条数")
    #: 由调用方注入仓储；留空时 skill 自行构造默认 Database
    database_url: str | None = Field(default=None, description="可选：覆盖默认 SQLite 连接串")


class CaseItem(BaseModel):
    """One matched historical case with its resolution."""

    id: str
    machine_id: str
    fault_code: int
    description: str
    root_cause: str
    resolution: str
    #: 词法命中数，用于解释"为什么匹配到这条"
    relevance: int = 0


class CaseMatchOutput(BaseModel):
    """Ranked historical cases plus the retrieval channel that produced them."""

    query: str
    items: list[CaseItem] = Field(default_factory=list)
    count: int = 0
    source: str = Field(default="case_library", description="检索来源标识，便于前端标注引用")
