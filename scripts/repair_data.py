"""数据修复工具：回填 sensor_readings 中缺失的 health_score。

背景
----
仿真数据在生成最后一条读数时，个别设备（CNC_001 / CNC_002）的 health_score
写成了 NULL。API 层若把"字段缺失"当成"设备离线"，总览页就会出现莫名其妙的
离线设备。本脚本按设备把 NULL 健康分用**上一条非空值**前向填充（故障码不变，
物理状态连续，前向填充是最保守的选择），修复后重新计算不产生影响。

用法::

    .venv/Scripts/python.exe scripts/repair_data.py          # 执行修复
    .venv/Scripts/python.exe scripts/repair_data.py --dry-run # 只看会改什么
"""

from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.config import DATA_DIR  # noqa: E402


def resolve_db_path() -> Path:
    """定位 SQLite 文件，优先使用 DATABASE_URL，其次 data/zhiwei.db。"""

    url = os.getenv("DATABASE_URL", "")
    if url.startswith("sqlite:///"):
        return Path(url.replace("sqlite:///", "", 1)).resolve()
    return (DATA_DIR / "zhiwei.db").resolve()


def backfill_health(database_path: Path, dry_run: bool = False) -> dict[str, object]:
    """把每条 NULL health_score 用同设备上一条非空值前向填充。"""

    connection = sqlite3.connect(database_path)
    connection.row_factory = sqlite3.Row
    try:
        nulls = connection.execute(
            "SELECT id, machine_id, timestamp FROM sensor_readings WHERE health_score IS NULL ORDER BY machine_id, timestamp"
        ).fetchall()
        repairs: list[tuple[float, int]] = []
        detail: list[dict[str, object]] = []
        for row in nulls:
            previous = connection.execute(
                "SELECT health_score FROM sensor_readings WHERE machine_id=? AND timestamp<? AND health_score IS NOT NULL ORDER BY timestamp DESC LIMIT 1",
                (row["machine_id"], row["timestamp"]),
            ).fetchone()
            if previous is None:
                continue
            repairs.append((float(previous["health_score"]), int(row["id"])))
            detail.append({"id": row["id"], "machine_id": row["machine_id"], "timestamp": row["timestamp"], "filled": round(float(previous["health_score"]), 6)})
        if not dry_run and repairs:
            connection.executemany("UPDATE sensor_readings SET health_score=? WHERE id=?", repairs)
            connection.commit()
        return {"candidates": len(nulls), "repaired": len(repairs), "dry_run": dry_run, "detail": detail}
    finally:
        connection.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="回填缺失的 health_score")
    parser.add_argument("--dry-run", action="store_true", help="只报告将要修复的行，不写库")
    args = parser.parse_args()

    database_path = resolve_db_path()
    if not database_path.exists():
        raise SystemExit(f"数据库不存在：{database_path}")
    result = backfill_health(database_path, dry_run=args.dry_run)
    print(f"数据库：{database_path}")
    print(f"发现 NULL 健康分 {result['candidates']} 行，可修复 {result['repaired']} 行"
          f"{'（dry-run 未写库）' if args.dry_run else '，已写库'}")
    for item in result["detail"]:
        print(f"  - {item['machine_id']} @ {item['timestamp']} → health_score={item['filled']}")


if __name__ == "__main__":
    main()
