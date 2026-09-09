"""Typed LangGraph state shared by the enterprise diagnostic workflow."""

from __future__ import annotations

import operator
from typing import Annotated, TypedDict

from langgraph.graph.message import add_messages
from langchain_core.messages import BaseMessage


class DiagnosticState(TypedDict, total=False):
    """Serializable state for one machine diagnostic run."""

    machine_id: str
    trigger_timestamp: str
    sensor_data: dict[str, float]
    history: list[dict[str, float]]
    health_score: float
    anomaly_score: float
    is_anomaly: bool
    anomaly_dims: list[str]
    anomaly_type: str
    severity: str
    fault_mode: str
    fault_code: int
    root_cause: str
    evidence_chain: list[dict[str, object]]
    confidence: float
    react_iterations: int
    rul: dict[str, object]
    decision: dict[str, object]
    report: dict[str, object]
    reflection_passed: bool
    reflection_checks: list[dict[str, object]]
    retry_count: int
    max_retries: int
    events: Annotated[list[dict[str, object]], operator.add]
    error: str | None
    messages: Annotated[list[BaseMessage], add_messages]


def initial_state(machine_id: str, sensor_data: dict[str, float] | None = None) -> DiagnosticState:
    """Return a clean state with bounded retry policy."""

    return {"machine_id": machine_id, "trigger_timestamp": "", "sensor_data": sensor_data or {}, "history": [], "health_score": 1.0, "anomaly_score": 0.0, "is_anomaly": False, "anomaly_dims": [], "anomaly_type": "stable", "severity": "normal", "fault_mode": "", "fault_code": 0, "root_cause": "", "evidence_chain": [], "confidence": 0.0, "react_iterations": 0, "rul": {}, "decision": {}, "report": {}, "reflection_passed": False, "reflection_checks": [], "retry_count": 0, "max_retries": 3, "events": [], "error": None, "messages": []}
