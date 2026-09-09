"""Public deterministic CNC simulator used for algorithm validation and demos."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Iterator

from .fault_injector import scenario_for_machine
from .physics_models import DegradationState, bearing_degradation, motor_aging, tool_wear
from .sensor_models import sensor_reading


class CNCSimulator:
    """Generate smooth, reproducible telemetry from Paris/Arrhenius/Taylor models."""

    def __init__(self, start: datetime | None = None) -> None:
        self.start = start or datetime(2025, 1, 1)

    def get_reading(self, machine_id: str, timestamp: datetime) -> dict[str, float | str]:
        """Return one physically modelled reading for the requested machine/time."""

        elapsed_minutes = max(0.0, (timestamp - self.start).total_seconds() / 60.0)
        day = elapsed_minutes / 1440.0
        number = int(machine_id.removeprefix("CNC_"))
        baseline = {"op_temperature": 80.0 + (number % 7) * 1.1, "op_amperage": 24.0 + (number % 5) * 0.6, "op_voltage": 225.0 + (number % 4) * 0.7, "rotor_speed": 300.0 + (number % 6) * 5.0, "vibration_rms": 1.8 + (number % 3) * 0.08, "coolant_temp": 28.0, "coolant_pressure": 1.8, "oil_pressure": 2.2}
        bearing = bearing_degradation(day, 0.95 + (number % 4) * 0.04)["wear_amount"]
        motor = 1.0 - motor_aging(day, baseline["op_temperature"])["insulation_resistance"]
        tool = tool_wear(120.0, 0.12, 180.0, day * 8.0)["wear_amount"]
        payload = sensor_reading(int(elapsed_minutes), day, DegradationState(bearing, motor, tool), scenario_for_machine(machine_id), baseline)
        payload["machine_id"] = machine_id
        payload["timestamp"] = timestamp.isoformat()
        return payload

    def iter_dataset(self, n_machines: int = 50, days: int = 180, interval_minutes: int = 5) -> Iterator[dict[str, float | str]]:
        """Stream a dataset without materializing millions of rows in memory."""

        if n_machines < 1 or days < 1 or interval_minutes < 1:
            raise ValueError("n_machines, days and interval_minutes must be positive")
        total_steps = days * 24 * 60 // interval_minutes
        for machine_number in range(1, n_machines + 1):
            machine_id = f"CNC_{machine_number:03d}"
            for step in range(total_steps):
                yield self.get_reading(machine_id, self.start + timedelta(minutes=step * interval_minutes))

    def get_window(self, machine_id: str, start: datetime, end: datetime, interval_minutes: int = 5) -> list[dict[str, float | str]]:
        """Return a bounded physical window for charts or training."""

        if end < start:
            raise ValueError("end must be greater than or equal to start")
        current = start
        rows: list[dict[str, float | str]] = []
        while current <= end:
            rows.append(self.get_reading(machine_id, current))
            current += timedelta(minutes=interval_minutes)
        return rows
