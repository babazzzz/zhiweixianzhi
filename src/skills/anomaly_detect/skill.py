"""Explainable multi-signal anomaly detection with adaptive baselines."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from statistics import mean, median, stdev
from typing import Mapping, Sequence

from ..base import BaseSkill, SkillExample, SkillMeta
from .schema import AnomalyDetectInput, AnomalyDetectOutput, DimensionDetail

#: metric -> (normal_low, normal_high, critical_low, critical_high)
#: 阈值取自 data/knowledge/设备说明书.txt 第三章的 22 维参数定义。
THRESHOLDS: dict[str, tuple[float, float, float, float]] = {
    "op_voltage": (220.0, 230.0, 210.0, 240.0),
    "op_amperage": (22.0, 28.0, 18.0, 35.0),
    "op_temperature": (70.0, 90.0, 60.0, 105.0),
    "rotor_speed": (250.0, 350.0, 220.0, 380.0),
    "vibration_rms": (0.0, 4.5, 0.0, 7.1),
    "coolant_temp": (18.0, 35.0, 10.0, 55.0),
    "coolant_pressure": (1.2, 2.6, 0.8, 3.2),
    "oil_pressure": (1.6, 2.8, 1.0, 3.5),
    "bearing_temp": (70.0, 95.0, 60.0, 115.0),
    "motor_vibration": (0.0, 3.5, 0.0, 6.0),
    "humidity": (20.0, 80.0, 5.0, 95.0),
}


@dataclass(frozen=True, slots=True)
class AnomalyResult:
    """Dataclass form kept for callers that predate the skill registry."""

    health_score: float
    anomaly_score: float
    is_anomaly: bool
    anomaly_dims: list[str]
    severity: str
    anomaly_type: str
    baseline: dict[str, float]
    details: dict[str, dict[str, float | str | bool]]

    def as_dict(self) -> dict[str, object]:
        """Return a JSON-safe representation."""

        return asdict(self)


def detect_anomaly(latest: Mapping[str, float], history: Sequence[Mapping[str, float]]) -> AnomalyResult:
    """Fuse threshold, robust deviation and trend evidence for available sensors.

    基线取历史序列的**前 40%**（假定为健康期），这样后期的渐进退化会被判为异常，
    而不是被"自适应"地抹平。缺失维度直接跳过，绝不用合成值填充。
    """

    samples = list(history)
    baseline_count = max(10, int(len(samples) * 0.4))
    baseline_samples = samples[:baseline_count]
    baseline: dict[str, float] = {}
    details: dict[str, dict[str, float | str | bool]] = {}
    dims: list[str] = []
    scores: list[float] = []
    for metric, current_value in latest.items():
        try:
            current = float(current_value)
        except (TypeError, ValueError):
            continue
        baseline_series = [float(row[metric]) for row in baseline_samples if metric in row]
        full_series = [float(row[metric]) for row in samples if metric in row]
        center = median(baseline_series) if baseline_series else current
        spread = stdev(baseline_series) if len(baseline_series) > 1 else max(abs(center) * 0.02, 0.01)
        baseline[metric] = round(center, 6)
        z_score = abs(current - center) / max(spread, 1e-6)
        threshold_score = _threshold_score(metric, current)
        trend_score = _trend_score(full_series)
        score = min(1.0, max(threshold_score, min(1.0, z_score / 5.0), trend_score))
        if score >= 0.55:
            dims.append(metric)
        scores.append(score)
        details[metric] = {
            "value": round(current, 6), "baseline": round(center, 6), "z_score": round(z_score, 4),
            "score": round(score, 4), "out_of_range": threshold_score >= 0.55,
        }
    anomaly_score = sum(scores) / len(scores) if scores else 0.0
    if len(dims) >= 3 or anomaly_score >= 0.78:
        severity = "critical"
    elif dims or anomaly_score >= 0.40:
        severity = "warning"
    else:
        severity = "normal"
    trend_count = sum(1 for item in details.values() if item.get("z_score", 0.0) < 2.5 and item.get("score", 0.0) >= 0.35)
    anomaly_type = "gradual" if trend_count >= 1 else ("sudden" if dims else "stable")
    return AnomalyResult(
        round(1.0 - anomaly_score, 4), round(anomaly_score, 4),
        bool(dims or anomaly_score >= 0.40), dims, severity, anomaly_type, baseline, details,
    )


def _threshold_score(metric: str, value: float) -> float:
    """Map engineering thresholds to a continuous anomaly score."""

    bounds = THRESHOLDS.get(metric)
    if bounds is None:
        return 0.0
    _, _, lower, upper = bounds
    if lower <= value <= upper:
        return 0.0
    distance = lower - value if value < lower else value - upper
    margin = max(upper - lower, abs(value) * 0.1, 1e-6)
    return min(1.0, 0.55 + distance / margin)


def _trend_score(series: Sequence[float]) -> float:
    """Measure a smooth relative trend using the first/last thirds of real samples."""

    if len(series) < 6:
        return 0.0
    third = max(2, len(series) // 3)
    start, end = mean(series[:third]), mean(series[-third:])
    relative = abs(end - start) / max(abs(start), 1e-6)
    return min(0.8, relative * 4.0)


class AnomalyDetectSkill(BaseSkill):
    """Detect anomalies by fusing thresholds, robust deviation and trend evidence."""

    meta = SkillMeta(
        id="anomaly_detect",
        name="多信号异常检测",
        version="2.0.0",
        description=(
            "对一台设备的最新读数做异常检测。融合三类证据：工程阈值超限、相对鲁棒基线的 z-score 偏离、"
            "历史窗口的渐变趋势。基线和各维度的偏离明细，严重度分为 normal/warning/critical。"
        ),
        category="analysis",
        deterministic=True,
        requires_llm=False,
        tags=("anomaly", "health", "sensor", "E1", "E2"),
        examples=(
            SkillExample(
                description="温度从 72℃ 爬升到 88℃、电流稳定 —— 典型散热失效",
                payload={
                    "latest": {"op_temperature": 88.0, "op_amperage": 25.0, "op_voltage": 226.0},
                    "history": [
                        {"op_temperature": 72.0, "op_amperage": 25.0, "op_voltage": 226.0},
                        {"op_temperature": 76.0, "op_amperage": 25.1, "op_voltage": 225.8},
                        {"op_temperature": 81.0, "op_amperage": 24.9, "op_voltage": 226.2},
                        {"op_temperature": 85.0, "op_amperage": 25.0, "op_voltage": 226.0},
                    ],
                },
            ),
        ),
    )
    input_schema = AnomalyDetectInput
    output_schema = AnomalyDetectOutput

    def run(self, payload: AnomalyDetectInput) -> AnomalyDetectOutput:
        """Run detection and convert the dataclass result into the pydantic contract."""

        result = detect_anomaly(payload.latest, payload.history)
        return AnomalyDetectOutput(
            health_score=result.health_score,
            anomaly_score=result.anomaly_score,
            is_anomaly=result.is_anomaly,
            anomaly_dims=result.anomaly_dims,
            severity=result.severity,
            anomaly_type=result.anomaly_type,
            baseline=result.baseline,
            details={key: DimensionDetail(**value) for key, value in result.details.items()},
        )
