"""Constrained Weibull-style RUL interval estimation."""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from statistics import mean
from typing import Mapping, Sequence

from ..base import BaseSkill, SkillExample, SkillMeta
from .schema import RULOutput, RULInput


@dataclass(frozen=True, slots=True)
class RULResult:
    """Dataclass form kept for callers that predate the skill registry."""

    p10: int
    p50: int
    p90: int
    degradation_rate: float
    monitoring_freq: str
    beta: float
    eta: float
    confidence: float

    def as_dict(self) -> dict[str, object]:
        """Return the API representation used by the existing routes."""

        return {"rul_p10": self.p10, "rul_p50": self.p50, "rul_p90": self.p90, **asdict(self)}


def predict_rul(history: Sequence[Mapping[str, float]], health_score: float, anomaly_score: float) -> RULResult:
    """Estimate remaining useful life from observed trends without a fabricated point value.

    本项目约定 **P10 为偏长（乐观）寿命界**，因此严格满足 P10 > P50 > P90。
    退化速率被约束在 [0.0005, 0.005] /分钟，避免外插出荒谬的寿命。
    """

    temperatures = [float(row["op_temperature"]) for row in history if "op_temperature" in row]
    currents = [float(row["op_amperage"]) for row in history if "op_amperage" in row]
    temp_rate = _relative_slope(temperatures)
    current_rate = _relative_slope(currents)
    degradation = min(0.005, max(0.0005, 0.6 * temp_rate + 0.4 * current_rate))
    risk = min(0.98, max(0.01, 1.0 - health_score * 0.75 + anomaly_score * 0.35))
    normalized = min(1.0, max(0.1, degradation / 0.005))
    beta = 1.0 + normalized * 2.5
    eta = 720.0 * (1.0 - risk / 1.5) / max(0.3, normalized)
    median_rul = max(4.0, eta * (-math.log(max(0.05, 1.0 - risk))) ** (1.0 / beta))
    p10 = max(int(math.ceil(median_rul * 1.30)), int(math.ceil(median_rul)) + 1)
    p50 = max(4, int(round(median_rul)))
    p90 = max(4, int(math.floor(median_rul * 0.70)))
    if p10 <= p50:
        p10 = p50 + 1
    if p90 >= p50:
        p90 = max(4, p50 - 1)
    frequency = "15分钟" if risk >= 0.75 else "1小时" if risk >= 0.45 else "4小时"
    confidence = min(0.96, 0.45 + min(0.4, len(history) / 500.0) + (0.15 if temperatures and currents else 0.0))
    return RULResult(p10, p50, p90, round(degradation, 6), frequency, round(beta, 4), round(eta, 2), round(confidence, 4))


def _relative_slope(values: Sequence[float]) -> float:
    """Calculate a bounded relative slope between early and recent real samples."""

    if len(values) < 6:
        return 0.0005
    width = max(2, len(values) // 3)
    early, recent = mean(values[:width]), mean(values[-width:])
    return max(0.0, min(0.005, (recent - early) / max(abs(early), 1.0) / max(len(values) - width, 1)))


class RULWeibullSkill(BaseSkill):
    """Predict a P10/P50/P90 remaining-useful-life interval from observed degradation."""

    meta = SkillMeta(
        id="rul_weibull",
        name="威布尔剩余寿命预测",
        version="2.0.0",
        description=(
            "基于温度/电流的相对退化速率拟合威布尔分布，输出 P10/P50/P90 三档剩余寿命区间（小时）。"
            "本项目约定 P10 为偏长（乐观）寿命界，因此恒有 P10 > P50 > P90；退化速率有物理边界约束，不外插荒谬值。"
        ),
        category="prediction",
        deterministic=True,
        requires_llm=False,
        tags=("rul", "weibull", "prediction", "E3"),
        examples=(
            SkillExample(
                description="健康度 0.42、异常分 0.58 的退化设备，估算剩余寿命区间",
                payload={
                    "history": [
                        {"op_temperature": 72.0, "op_amperage": 24.0},
                        {"op_temperature": 76.0, "op_amperage": 25.2},
                        {"op_temperature": 81.0, "op_amperage": 26.8},
                        {"op_temperature": 86.0, "op_amperage": 28.4},
                    ],
                    "health_score": 0.42,
                    "anomaly_score": 0.58,
                },
            ),
        ),
    )
    input_schema = RULInput
    output_schema = RULOutput

    def run(self, payload: RULInput) -> RULOutput:
        """Fit the Weibull parameters and return the bounded RUL interval."""

        result = predict_rul(payload.history, payload.health_score, payload.anomaly_score)
        return RULOutput(
            rul_p10=result.p10, rul_p50=result.p50, rul_p90=result.p90,
            degradation_rate=result.degradation_rate, monitoring_freq=result.monitoring_freq,
            beta=result.beta, eta=result.eta, confidence=result.confidence,
        )
