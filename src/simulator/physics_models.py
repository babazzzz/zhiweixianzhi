"""Physics-inspired degradation models with no stochastic data generation."""

from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class DegradationState:
    """Normalized physical state used by sensor models."""

    bearing_wear: float
    motor_aging: float
    tool_wear: float


def paris_crack_growth(cycles: float, initial_crack: float = 0.02, stress_range: float = 0.8, c: float = 1e-5, exponent: float = 3.0) -> float:
    """Approximate Paris-law crack growth and clamp it to a physical range."""

    crack = initial_crack + c * max(cycles, 0.0) * max(stress_range, 0.05) ** exponent
    return min(1.0, max(0.0, crack / 0.25))


def bearing_degradation(day: float, load_factor: float = 1.0) -> dict[str, float]:
    """Return bearing wear, temperature rise and vibration rise from fatigue growth."""

    wear = paris_crack_growth(max(day, 0.0) * 24.0 * 60.0, stress_range=0.7 * load_factor)
    return {"wear_amount": wear, "temp_increase": 18.0 * wear, "vibration_increase": 4.0 * wear}


def arrhenius_acceleration(temperature_c: float, activation_energy_ev: float = 0.7) -> float:
    """Return a bounded relative thermal aging rate using the Arrhenius relation."""

    boltzmann = 8.617e-5
    kelvin = max(273.15, temperature_c + 273.15)
    reference = math.exp(-activation_energy_ev / (boltzmann * 343.15))
    rate = math.exp(-activation_energy_ev / (boltzmann * kelvin)) / reference
    return min(6.0, max(0.05, rate))


def motor_aging(day: float, avg_temp: float) -> dict[str, float]:
    """Return insulation loss and current drift from thermal aging."""

    acceleration = arrhenius_acceleration(avg_temp)
    aging = min(1.0, max(0.0, day / 365.0 * acceleration))
    return {"insulation_resistance": max(0.2, 1.0 - 0.7 * aging), "current_drift": 2.5 * aging}


def taylor_tool_life(cutting_speed: float, feed_rate: float, material_hardness: float) -> float:
    """Estimate tool life in hours using a normalized Taylor wear law."""

    speed = max(20.0, cutting_speed)
    feed = max(0.02, feed_rate)
    hardness_factor = max(0.5, material_hardness / 200.0)
    base_life = 200.0 * (speed / feed) ** -0.25
    return max(1.0, base_life / hardness_factor)


def tool_wear(cutting_speed: float, feed_rate: float, material_hardness: float, operating_hours: float) -> dict[str, float]:
    """Return progressive tool wear from the Taylor life estimate."""

    life = taylor_tool_life(cutting_speed, feed_rate, material_hardness)
    wear = min(1.0, max(0.0, operating_hours / life))
    return {"life_hours": life, "wear_amount": wear, "surface_penalty": 0.8 * wear}
