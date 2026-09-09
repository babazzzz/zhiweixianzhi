"""Input/output contracts for the E1–E7 evidence chain skill."""

from __future__ import annotations

from pydantic import BaseModel, Field


class AnomalyFacts(BaseModel):
    """Anomaly detection result facts that the evidence chain must explain."""

    anomaly_score: float = Field(default=0.0, ge=0, le=1, description="融合异常分")
    anomaly_dims: list[str] = Field(default_factory=list, description="被判定为异常的传感器维度")


class CaseFacts(BaseModel):
    """One historical case used as E4 evidence."""

    id: str = ""
    root_cause: str = ""


class RuleFacts(BaseModel):
    """Matched FMECA rule used as E5 evidence."""

    fault_code: int = 0
    fault_name: str = ""


class CausalPath(BaseModel):
    """One causal path from the fault graph, used as E6 evidence."""

    nodes: list[str] = Field(default_factory=list)
    probability: float = 0.0


class EvidenceChainInput(BaseModel):
    """Everything the evidence chain is allowed to cite — no fabrication beyond this."""

    anomaly: AnomalyFacts
    history: list[dict[str, float]] = Field(default_factory=list, description="历史读数序列（时间升序）")
    rule: RuleFacts | None = Field(default=None, description="匹配到的 FMECA 规则")
    cases: list[CaseFacts] = Field(default_factory=list, description="相似历史案例")
    causal_paths: list[CausalPath] = Field(default_factory=list, description="故障图后向溯源路径")
    maintenance_context: str = Field(
        default="未发现可用维护后对照记录",
        description="E7：该设备的历史维护记录摘要；无记录时保持缺省文案",
    )


class EvidenceItem(BaseModel):
    """One evidence node (E1–E7) with a confidence and supporting numbers."""

    id: str
    source: str
    content: str
    confidence: float = Field(ge=0, le=1)
    supporting_data: dict[str, str | float | bool] = Field(default_factory=dict)


class EvidenceChainOutput(BaseModel):
    """Seven ordered evidence items plus an aggregate confidence."""

    items: list[EvidenceItem] = Field(default_factory=list)
    count: int = 0
    #: 证据链整体可信度 = 各条置信度加权均值（E2/E3 权重更高，因为它们是数据驱动的）
    aggregate_confidence: float = Field(default=0.0, ge=0, le=1)
