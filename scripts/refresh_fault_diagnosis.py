"""刷新指定设备的诊断结论与维护报告。

用途
----
诊断规则升级后（按"与同型号健康设备的偏差"做故障隔离），历史诊断记录仍是旧结论。
本脚本对指定设备**重新跑一遍诊断图**并落库，然后把这些设备已有的维护报告
删掉重建，保证报告页展示的结论与最新诊断一致。

用法::

    .venv/Scripts/python.exe scripts/refresh_fault_diagnosis.py            # 默认 6 台已注入故障设备
    .venv/Scripts/python.exe scripts/refresh_fault_diagnosis.py CNC_036 CNC_018
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.database.connection import Database  # noqa: E402
from src.graph.main_graph import build_graph  # noqa: E402
from src.knowledge.fault_graph import FaultGraph  # noqa: E402
from src.skills.maintenance_report.skill import MaintenanceReportSkill  # noqa: E402

DEFAULT_MACHINES = ("CNC_007", "CNC_018", "CNC_025", "CNC_036", "CNC_052", "CNC_078")


async def refresh(machine_ids: list[str]) -> None:
    database = Database()
    graph = build_graph(database, FaultGraph())
    skill = MaintenanceReportSkill(database)

    for machine_id in machine_ids:
        if database.get_machine(machine_id) is None:
            print(f"- {machine_id}：设备不存在，跳过")
            continue
        thread_id = str(uuid.uuid4())
        state = await graph.ainvoke(
            {"machine_id": machine_id, "retry_count": 0, "max_retries": 3, "events": [], "messages": []},
            {"configurable": {"thread_id": thread_id}},
        )
        payload = dict(state)
        payload.pop("messages", None)
        diagnostic_id = str(uuid.uuid4())
        database.save_diagnostic(
            diagnostic_id, machine_id, "completed",
            str(state.get("severity", "normal")), float(state.get("confidence", 0.0)), payload,
        )
        print(f"- {machine_id}：新诊断 fault={state.get('fault_mode')}"
              f"（code {state.get('fault_code')}）severity={state.get('severity')}")

        removed = database.delete_reports_for_machine(machine_id)
        if removed:
            result = skill.run({"machine_id": machine_id, "diagnostic_id": diagnostic_id, "include_history": True})
            print(f"    重建报告 {result['report_id']}（清理旧报告 {removed} 份）")


def main() -> None:
    parser = argparse.ArgumentParser(description="刷新故障设备的诊断与维护报告")
    parser.add_argument("machines", nargs="*", default=list(DEFAULT_MACHINES), help="设备 ID 列表")
    args = parser.parse_args()
    asyncio.run(refresh(args.machines or list(DEFAULT_MACHINES)))


if __name__ == "__main__":
    main()
