"""Prediction specialist node."""

from __future__ import annotations

from datetime import datetime

from src.database.connection import Database
from src.skills.rul_weibull import predict_rul
from .state import DiagnosticState


async def prediction_node(state: DiagnosticState, database: Database) -> dict[str, object]:
    """Calculate a constrained RUL interval from the stored degradation window."""

    result = predict_rul(state.get("history", []), float(state.get("health_score", 1.0)), float(state.get("anomaly_score", 0.0)))
    return {"rul": result.as_dict(), "events": [{"agent": "prediction", "status": "completed", "message": f"RUL P10/P50/P90 = {result.p10}/{result.p50}/{result.p90}h", "timestamp": datetime.now().astimezone().isoformat()}]}
