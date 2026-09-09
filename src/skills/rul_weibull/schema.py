"""Input/output contracts for the Weibull RUL estimation skill."""

from __future__ import annotations

from pydantic import BaseModel, Field


class RULInput(BaseModel):
    """History, health and anomaly scores needed to bound remaining useful life."""

    history: list[dict[str, float]] = Field(
        default_factory=list,
        description="历史读数序列（按时间升序），至少 6 个点才能算出有效趋势",
    )
    health_score: float = Field(default=0.8, ge=0.0, le=1.0, description="来自 anomaly_detect 的健康度")
    anomaly_score: float = Field(default=0.2, ge=0.0, le=1.0, description="来自 anomaly_detect 的异常分")


class RULOutput(BaseModel):
    """P10/P50/P90 interval with the fitted Weibull parameters behind it."""

    rul_p10: int = Field(description="P10 乐观寿命（小时），本项目约定 P10 为偏长寿命界")
    rul_p50: int = Field(description="P50 中位寿命（小时）")
    rul_p90: int = Field(description="P90 保守寿命（小时）")
    degradation_rate: float = Field(description="每分钟相对退化速率，已做物理边界约束")
    monitoring_freq: str = Field(description="建议监测频率：15分钟 / 1小时 / 4小时")
    beta: float = Field(description="Weibull 形状参数")
    eta: float = Field(description="Weibull 尺度参数（小时）")
    confidence: float = Field(description="该估计的置信度 0-1")

    @property
    def ordered(self) -> bool:
        """Assert the project convention P10 > P50 > P90 holds."""

        return self.rul_p10 > self.rul_p50 > self.rul_p90
