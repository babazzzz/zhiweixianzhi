"""Typed records exchanged between storage, agents and API routes."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

MachineStatus = Literal["running", "warning", "critical", "maintenance", "offline"]


@dataclass(frozen=True, slots=True)
class MachineMetadata:
    """Stable metadata of a monitored CNC machine."""

    id: str
    model: str
    manufacture_date: str
    last_maintenance: str | None
    next_maintenance: str | None
    last_repair: str | None
    daily_output: int
    unit_cost: float
    production_line: str
    status: MachineStatus


@dataclass(frozen=True, slots=True)
class SensorReading:
    """A timestamped CNC sensor sample with an extensible metrics payload."""

    machine_id: str
    timestamp: datetime
    metrics: dict[str, float]
    fault_code: int = 0
    health_score: float | None = None


@dataclass(frozen=True, slots=True)
class Evidence:
    """One traceable item in the E1–E7 diagnostic evidence chain."""

    id: str
    source: str
    content: str
    confidence: float
    supporting_data: dict[str, float | str]


@dataclass(frozen=True, slots=True)
class DiagnosticSnapshot:
    """Persisted aggregate result from one complete diagnostic run."""

    id: str
    machine_id: str
    created_at: datetime
    status: str
    severity: str
    confidence: float
    payload: dict[str, object]
