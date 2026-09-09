"""Deterministic, gradual fault scenarios for physics-based validation datasets."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class FaultScenario:
    """A fault scenario attached to one simulated machine."""

    machine_id: str
    fault_code: int
    onset_day: float
    precursor_days: float
    severity: float

    def progression(self, day: float) -> float:
        """Return smooth fault progression between zero and one."""

        start = self.onset_day - self.precursor_days
        return min(1.0, max(0.0, (day - start) / max(self.precursor_days, 1.0)))


_SCENARIOS: dict[str, FaultScenario] = {
    # 14 天仿真内的渐进式故障（6 台 / 100 台 = 6% 故障率）
    "CNC_007": FaultScenario("CNC_007", 7, 3.0, 2.0, 0.80),
    "CNC_018": FaultScenario("CNC_018", 1, 5.0, 3.0, 0.85),
    "CNC_025": FaultScenario("CNC_025", 3, 7.0, 2.0, 0.70),
    "CNC_036": FaultScenario("CNC_036", 8, 4.0, 2.0, 0.75),
    "CNC_052": FaultScenario("CNC_052", 2, 9.0, 2.5, 0.65),
    "CNC_078": FaultScenario("CNC_078", 6, 10.0, 3.0, 0.75),
}


def scenario_for_machine(machine_id: str) -> FaultScenario | None:
    """Return the deterministic fault scenario for a machine, or None if healthy."""

    return _SCENARIOS.get(machine_id)
