"""Anomaly detection skill package — exported as a single ``SKILL`` instance."""

from .schema import AnomalyDetectInput, AnomalyDetectOutput, DimensionDetail
from .skill import THRESHOLDS, AnomalyDetectSkill, AnomalyResult, detect_anomaly, peer_baseline_from_machines

SKILL = AnomalyDetectSkill()

__all__ = [
    "SKILL",
    "THRESHOLDS",
    "AnomalyDetectInput",
    "AnomalyDetectOutput",
    "AnomalyDetectSkill",
    "AnomalyResult",
    "DimensionDetail",
    "detect_anomaly",
    "peer_baseline_from_machines",
]
