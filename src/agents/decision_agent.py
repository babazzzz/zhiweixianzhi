"""Decision specialist node with cost, downtime and parts calculations."""

from __future__ import annotations

from datetime import datetime

from src.database.connection import Database
from src.skills.cost_benefit import analyze_cost_benefit
from .state import DiagnosticState


async def decision_node(state: DiagnosticState, database: Database) -> dict[str, object]:
    """Select a maintenance option using machine economics and FMECA SOP data."""

    machine = database.get_machine(state["machine_id"]) or {"id": state["machine_id"]}
    rule = database.get_fmeca(int(state.get("fault_code", 0)))
    decision = analyze_cost_benefit(machine, state.get("rul", {}), rule)
    return {"decision": decision, "events": [{"agent": "decision", "status": "completed", "message": f"推荐方案：{decision['recommendation']}", "timestamp": datetime.now().astimezone().isoformat()}]}
