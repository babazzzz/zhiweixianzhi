"""Input/output contracts for the maintenance cost-benefit skill."""

from __future__ import annotations

from pydantic import BaseModel, Field


class MachineEconomics(BaseModel):
    """Machine-level economics needed to price a downtime event."""

    daily_output: float = Field(default=0.0, ge=0, description="日产量（件/天）")
    unit_cost: float = Field(default=0.0, ge=0, description="单位生产成本（元/件）")


class FmecaEffort(BaseModel):
    """FMECA-derived repair effort, optional when no rule matched."""

    estimated_hours: float = Field(default=2.0, gt=0, le=200, description="预计维修工时")
    required_parts: list[str] = Field(default_factory=list, description="所需备件清单")


class CostBenefitInput(BaseModel):
    """Machine economics + RUL interval + optional FMECA effort."""

    machine: MachineEconomics
    rul_p50_hours: float = Field(default=4.0, gt=0, description="RUL 中位数（小时），用于折算 72h 故障概率")
    rule: FmecaEffort | None = Field(default=None, description="匹配到的 FMECA 规则；为空时按通用工时估算")


class DecisionOption(BaseModel):
    """One candidate maintenance strategy with its full cost breakdown."""

    name: str
    downtime_hours: float
    direct_cost: float
    risk_cost: float
    total_cost: float


class CostBenefitOutput(BaseModel):
    """Recommended strategy, three priced options and a what-if projection."""

    recommendation: str
    timing: str
    action: str
    downtime_hours: float
    direct_cost: float
    risk_cost: float
    net_benefit: float
    options: list[DecisionOption] = Field(default_factory=list)
    what_if: dict[str, float] = Field(default_factory=dict)
    parts: list[str] = Field(default_factory=list)
    estimated_hours: float = 0.0
