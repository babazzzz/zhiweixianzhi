"""定向重算指定设备的历史读数（不触碰其它设备，也不触碰其它业务表）。

为什么需要它
------------
`src/database/regenerate.py` 的第一步就是 `db_path.unlink()`——它会删掉整个
`data/zhiwei.db`，连同诊断、维护报告、工单、通知、聊天会话一起清空。
当仿真器补建了新的故障征兆（例如故障码 2/3/6/7），我们只想让受影响的那几台
设备重新生成读数，其余 94 台和所有业务表必须原样保留，所以不能走 regenerate。

用法::

    # 预览将要删除的行数，不动数据库
    .venv/Scripts/python.exe scripts/recompute_fault_readings.py --dry-run

    # 正式重算默认 6 台注入故障设备（写库前会自动备份整库）
    .venv/Scripts/python.exe scripts/recompute_fault_readings.py

    # 只重算指定设备
    .venv/Scripts/python.exe scripts/recompute_fault_readings.py CNC_007 CNC_025
"""

from __future__ import annotations

import argparse
import shutil
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.config import PROJECT_ROOT  # noqa: E402
from src.database.connection import Database  # noqa: E402
from src.database.schemas import SensorReading  # noqa: E402
from src.simulator.cnc_simulator import CNCSimulator  # noqa: E402
from src.skills.anomaly_detect import detect_anomaly  # noqa: E402

# 必须与 regenerate.py 的生成参数完全一致，否则时间戳对不上，
# INSERT OR IGNORE 会把新读数直接忽略、旧读数残留。
START = datetime(2025, 7, 1)
DAYS = 14
INTERVAL_MINUTES = 30
TOTAL_STEPS = DAYS * 24 * 60 // INTERVAL_MINUTES  # 672

DEFAULT_MACHINES = ("CNC_007", "CNC_018", "CNC_025", "CNC_036", "CNC_052", "CNC_078")


def backup_database(db_path: Path) -> Path:
    """用 SQLite 在线备份 API 复制整库，保证副本一致（避免直接 copy 遇到 WAL）。"""

    import sqlite3

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    target = db_path.parent / f"{db_path.name}.bak_{stamp}"
    source = sqlite3.connect(str(db_path))
    try:
        sink = sqlite3.connect(str(target))
        try:
            source.backup(sink)
        finally:
            sink.close()
    finally:
        source.close()
    return target


def recompute(machine_ids: list[str], dry_run: bool = False) -> None:
    """按 regenerate.py 的口径重算目标设备读数。"""

    db_path = PROJECT_ROOT / "data" / "zhiwei.db"
    database = Database()

    if dry_run:
        print(f"[dry-run] 目标数据库：{db_path}")
        for machine_id in machine_ids:
            existing = database.count_readings_for_machine(machine_id)
            print(f"- {machine_id}：现有 {existing} 条 → 将删除后重插 {TOTAL_STEPS} 条")
        print("[dry-run] 未做任何写入。")
        return

    backup_path = backup_database(db_path)
    print(f"已备份整库 → {backup_path}")

    simulator = CNCSimulator(start=START)
    for machine_id in machine_ids:
        if database.get_machine(machine_id) is None:
            print(f"- {machine_id}：设备不存在，跳过")
            continue

        removed = database.delete_readings_for_machine(machine_id)
        history: list[dict[str, float]] = []
        batch: list[SensorReading] = []

        for step in range(TOTAL_STEPS):
            timestamp = START + timedelta(minutes=step * INTERVAL_MINUTES)
            raw = simulator.get_reading(machine_id, timestamp)
            metrics = {
                key: float(value)
                for key, value in raw.items()
                if key not in ("machine_id", "timestamp", "fault_code") and isinstance(value, (int, float))
            }
            fault_code = int(raw.get("fault_code", 0))

            # 健康分口径与 regenerate.py:78-99 保持一致：
            # 故障机用真实异常检测分数，健康机用 0.95→0.75 的演示曲线，首条为 1.0。
            if history:
                if fault_code == 0:
                    health_score = max(0.75, 0.95 - 0.20 * (step / TOTAL_STEPS))
                else:
                    health_score = detect_anomaly(metrics, history).health_score
            else:
                health_score = 1.0

            batch.append(SensorReading(machine_id, timestamp, metrics, fault_code, health_score))
            history.append(metrics)

            if len(batch) >= 500:
                database.insert_readings(batch)
                batch.clear()

        if batch:
            database.insert_readings(batch)

        print(f"- {machine_id}：删除 {removed} 条 → 重插 {TOTAL_STEPS} 条")


def main() -> None:
    parser = argparse.ArgumentParser(description="定向重算指定设备的历史读数")
    parser.add_argument("machines", nargs="*", default=list(DEFAULT_MACHINES), help="设备 ID 列表")
    parser.add_argument("--dry-run", action="store_true", help="只预览影响面，不写库")
    args = parser.parse_args()
    recompute(args.machines or list(DEFAULT_MACHINES), dry_run=args.dry_run)


if __name__ == "__main__":
    main()
