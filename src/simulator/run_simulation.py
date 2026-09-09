"""CLI entry point for exporting a deterministic simulator stream as CSV."""

from __future__ import annotations

import csv
import sys
from pathlib import Path

from .cnc_simulator import CNCSimulator


def export_dataset(output: Path, n_machines: int = 50, days: int = 180, interval_minutes: int = 5) -> int:
    """Write simulated records to CSV and return the number of rows."""

    output.parent.mkdir(parents=True, exist_ok=True)
    simulator = CNCSimulator()
    iterator = simulator.iter_dataset(n_machines, days, interval_minutes)
    first = next(iterator, None)
    if first is None:
        return 0
    count = 0
    with output.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(first.keys()))
        writer.writeheader()
        writer.writerow(first)
        count = 1
        for row in iterator:
            writer.writerow(row)
            count += 1
    return count


if __name__ == "__main__":
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("data/processed/simulation.csv")
    print(f"Exported {export_dataset(target)} rows to {target}")
