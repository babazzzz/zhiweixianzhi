"""Input/output contracts for the diagnostic report rendering skill."""

from __future__ import annotations

from pydantic import BaseModel, Field


class ReportEvidence(BaseModel):
    """Evidence item as rendered into the report."""

    id: str = ""
    source: str = ""
    content: str = ""
    confidence: float = 0.0


class ReportDecision(BaseModel):
    """Decision facts as rendered into the report."""

    recommendation: str = "—"
    timing: str = "—"
    downtime_hours: float | str = "—"
    direct_cost: float | str = "—"
    net_benefit: float | str = "—"
    parts: list[str] = Field(default_factory=list)


class DiagnosticReportInput(BaseModel):
    """Diagnostic state needed to render one report."""

    machine_id: str = Field(default="unknown", description="设备 ID")
    fault_mode: str = Field(default="未检测到明确故障", description="故障模式")
    root_cause: str = Field(default="未形成可证实根因", description="根因描述")
    severity: str = Field(default="normal", description="normal | warning | critical | emergency")
    confidence: float = Field(default=0.0, ge=0, le=1, description="诊断置信度")
    rul: dict[str, float | str] = Field(default_factory=dict, description="RUL 区间，键为 rul_p10/p50/p90")
    evidence: list[ReportEvidence] = Field(default_factory=list, description="E1–E7 证据链")
    decision: ReportDecision | None = Field(default=None, description="维护决策；无异常时可省略")


class DiagnosticReportOutput(BaseModel):
    """Rendered Markdown report plus its metadata."""

    title: str
    markdown: str
    generated_at: str
    #: 报告字数，用于前端预估阅读时长与日志审计
    char_count: int = 0
