"""Knowledge specialist node for FMECA, cases and causal paths."""

from __future__ import annotations

from src.database.connection import Database
from src.knowledge.fault_graph import FaultGraph
from src.skills.case_match import match_cases
from .state import DiagnosticState


async def knowledge_node(state: DiagnosticState, database: Database, graph: FaultGraph) -> dict[str, object]:
    """Provide structured retrieval context without masking missing sources."""

    code = int(state.get("fault_code", 0))
    rule = database.get_fmeca(code)
    cases = match_cases(database, " ".join(state.get("anomaly_dims", [])), code, 5)
    node = {1: "bearing_wear", 2: "motor_overload", 4: "voltage_instability", 6: "rotor_fault", 7: "power_module_failure", 8: "cooling_failure"}.get(code, "")
    paths = graph.backward_trace(node) if node else []
    return {"knowledge": {"fmeca": rule, "cases": cases, "paths": [{"nodes": path.nodes, "probability": path.probability, "lag_hours": path.lag_hours} for path in paths]}}
