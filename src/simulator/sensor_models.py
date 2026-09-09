"""Sensor transfer functions from physical state to 22 telemetry dimensions."""

from __future__ import annotations

import math
from typing import Mapping

from .physics_models import DegradationState
from .fault_injector import FaultScenario


def deterministic_noise(step: int, scale: float) -> float:
    """Produce repeatable multi-frequency instrumentation noise, not random samples."""

    return scale * (0.65 * math.sin(step * 0.071) + 0.35 * math.sin(step * 0.013 + 1.7))


def sensor_reading(step: int, day: float, state: DegradationState, scenario: FaultScenario | None, baseline: Mapping[str, float]) -> dict[str, float]:
    """Generate a physically coupled sensor vector for one timestamp."""

    progress = scenario.progression(day) if scenario else 0.0
    fault_code = scenario.fault_code if scenario else 0
    bearing = state.bearing_wear + (progress * scenario.severity if scenario and fault_code == 1 else 0.0)
    cooling = progress * scenario.severity if scenario and fault_code == 8 else 0.0
    motor = state.motor_aging
    temp = baseline["op_temperature"] + 14.0 * bearing + 18.0 * cooling + deterministic_noise(step, 0.35)
    current = baseline["op_amperage"] + 2.2 * bearing + 0.8 * motor + deterministic_noise(step + 3, 0.16)
    voltage = baseline["op_voltage"] + deterministic_noise(step + 5, 0.55) - (1.8 * progress if fault_code == 7 else 0.0)
    speed = baseline["rotor_speed"] - 28.0 * bearing + deterministic_noise(step + 7, 0.8)
    vibration = baseline["vibration_rms"] + 3.2 * bearing + deterministic_noise(step + 9, 0.04)
    coolant_temp = baseline["coolant_temp"] + 11.0 * cooling + deterministic_noise(step + 11, 0.15)
    return {
        "op_voltage": round(voltage, 4), "op_amperage": round(current, 4), "op_temperature": round(min(180.0, temp), 4), "rotor_speed": round(max(0.0, speed), 4),
        "vibration_rms": round(max(0.0, vibration), 4), "motor_power": round(voltage * current * 0.001, 4), "cutting_force": round(1200.0 + current * 15.0 + 300.0 * bearing, 4), "feed_force": round(430.0 + current * 6.0 + 80.0 * bearing, 4),
        "spindle_load": round(min(100.0, current / 35.0 * 100.0), 4), "coolant_temp": round(coolant_temp, 4), "coolant_pressure": round(max(0.0, baseline["coolant_pressure"] - 0.25 * cooling), 4), "oil_pressure": round(max(0.0, baseline["oil_pressure"] - 0.15 * bearing), 4),
        "noise_level": round(62.0 + vibration * 2.5 + deterministic_noise(step + 13, 0.2), 4), "tool_wear": round(min(1.0, state.tool_wear), 4), "surface_roughness": round(0.8 + 0.8 * state.tool_wear + 0.4 * bearing, 4), "position_error": round(0.015 + 0.03 * bearing + deterministic_noise(step + 15, 0.001), 5),
        "power_factor": round(max(0.5, 0.96 - 0.06 * motor - 0.02 * bearing), 4), "harmonic_distortion": round(2.0 + 1.5 * motor + deterministic_noise(step + 17, 0.05), 4), "bearing_temp": round(min(180.0, temp + 4.5 * bearing), 4), "motor_vibration": round(max(0.0, vibration * 0.7 + 0.2 * motor), 4),
        "ambient_temp": round(23.0 + deterministic_noise(step + 19, 1.1), 4), "humidity": round(48.0 + deterministic_noise(step + 23, 3.0), 4), "fault_code": float(fault_code),
    }
