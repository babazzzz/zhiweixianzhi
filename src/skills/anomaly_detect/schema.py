"""Input/output contracts for the anomaly detection skill."""

from __future__ import annotations

from pydantic import BaseModel, Field


class AnomalyDetectInput(BaseModel):
    """One latest reading plus the machine's recent history."""

    latest: dict[str, float] = Field(description="最新一条读数，键为传感器维度名，值为数值")
    history: list[dict[str, float]] = Field(
        default_factory=list,
        description="历史读数序列（按时间升序）。前 40% 用作健康基线，用于检测渐进退化",
    )


class DimensionDetail(BaseModel):
    """Per-sensor evidence explaining why a dimension scored the way it did."""

    value: float
    baseline: float
    z_score: float
    score: float
    out_of_range: bool = False


class AnomalyDetectOutput(BaseModel):
    """Fused anomaly verdict with a per-dimension explanation."""

    health_score: float = Field(description="健康度，0-1，越大越健康")
    anomaly_score: float = Field(description="异常分，0-1，越大越异常")
    is_anomaly: bool
    anomaly_dims: list[str] = Field(default_factory=list, description="被判定为异常的传感器维度")
    severity: str = Field(description="normal | warning | critical")
    anomaly_type: str = Field(description="stable | gradual | sudden")
    baseline: dict[str, float] = Field(default_factory=dict, description="各维度的鲁棒基线中位数")
    details: dict[str, DimensionDetail] = Field(default_factory=dict)
