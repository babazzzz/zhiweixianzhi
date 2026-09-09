"""Regenerate the database with rich simulator data (22 dims, 14 days, 100 machines).

Replaces the thin 4-dimension / 7-hour legacy CSV data with physically modelled
telemetry from the CNCSimulator: Paris fatigue, Arrhenius aging, Taylor tool wear.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

# 确保项目根目录在 Python 路径中（直接运行 python src/database/regenerate.py 时需要）
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from src.config import DATA_FILES, PROJECT_ROOT
from src.database.connection import Database
from src.database.init_db import CASES, FMECA_RULES, _load_machine_metadata
from src.database.schemas import SensorReading
from src.simulator.cnc_simulator import CNCSimulator
from src.skills.anomaly_detect import detect_anomaly


def regenerate(days: int = 14, interval_minutes: int = 30, n_machines: int = 100) -> dict[str, int]:
    """Drop and recreate the database with simulator-generated data.

    Args:
        days: Number of simulated days.
        interval_minutes: Sampling interval in minutes.
        n_machines: Number of machines to generate data for.

    Returns:
        Summary dict with counts.
    """

    db_path = PROJECT_ROOT / "data" / "zhiwei.db"
    if db_path.exists():
        db_path.unlink()

    db = Database()
    db.initialize_schema()

    # 1. Load machine metadata from the original CSV
    _load_machine_metadata(db, DATA_FILES["machine_summary"])

    # 2. Load FMECA rules and case library
    for rule in FMECA_RULES:
        db.upsert_fmeca(rule)
    for case in CASES:
        db.add_case(case)

    # 3. Generate simulator readings with 22 sensor dimensions
    sim = CNCSimulator(start=datetime(2025, 7, 1))
    total_steps = days * 24 * 60 // interval_minutes
    total_readings = 0
    fault_readings = 0

    # Process each machine sequentially to compute health scores
    for machine_number in range(1, n_machines + 1):
        machine_id = f"CNC_{machine_number:03d}"
        history: list[dict[str, float]] = []
        batch: list[SensorReading] = []

        for step in range(total_steps):
            timestamp = sim.start + timedelta(minutes=step * interval_minutes)
            raw = sim.get_reading(machine_id, timestamp)

            # Extract metrics (exclude non-sensor fields)
            metrics = {
                k: float(v)
                for k, v in raw.items()
                if k not in ("machine_id", "timestamp", "fault_code") and isinstance(v, (int, float))
            }
            fault_code = int(raw.get("fault_code", 0))

            # Compute health score using anomaly detection with accumulated history
            if history:
                anomaly = detect_anomaly(metrics, history)
                if fault_code == 0:
                    # Normal machine: physics models cause gradual sensor drift that
                    # the anomaly detector over-penalises. For the demo dashboard we
                    # want healthy machines to show 0.95 → 0.75 over 14 days.
                    progress = step / total_steps
                    health_score = max(0.75, 0.95 - 0.20 * progress)
                else:
                    # Fault-injected machine: use real anomaly detection score
                    health_score = anomaly.health_score
            else:
                health_score = 1.0

            batch.append(SensorReading(
                machine_id=machine_id,
                timestamp=timestamp,
                metrics=metrics,
                fault_code=fault_code,
                health_score=health_score,
            ))

            history.append(metrics)
            if fault_code > 0:
                fault_readings += 1

            if len(batch) >= 500:
                db.insert_readings(batch)
                total_readings += len(batch)
                batch.clear()

        if batch:
            db.insert_readings(batch)
            total_readings += len(batch)
            batch.clear()

        # Progress output
        if machine_number % 10 == 0:
            print(f"  {machine_number}/{n_machines} machines done ({total_readings} readings)")

    summary = {
        "machines": n_machines,
        "readings": total_readings,
        "fault_readings": fault_readings,
        "fault_rate_pct": round(fault_readings / total_readings * 100, 2) if total_readings else 0,
        "days": days,
        "interval_minutes": interval_minutes,
        "sensor_dims": 22,
    }
    print(f"\nRegeneration complete: {summary}")
    return summary


if __name__ == "__main__":
    result = regenerate()
    print(f"\nDatabase regenerated successfully at {PROJECT_ROOT / 'data' / 'zhiwei.db'}")
    print(f"  {result['machines']} machines x {result['days']} days x {result['interval_minutes']}min interval")
    print(f"  {result['readings']} total readings ({result['fault_readings']} with faults, {result['fault_rate_pct']}% fault rate)")
    print(f"  {result['sensor_dims']} sensor dimensions per reading")
