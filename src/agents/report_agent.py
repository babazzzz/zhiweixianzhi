"""Report specialist node."""

from __future__ import annotations

from datetime import datetime

from src.skills.diagnostic_report import generate_report
from .state import DiagnosticState


async def report_node(state: DiagnosticState) -> dict[str, object]:
    """Generate a structured, export-ready report from the complete state."""

    report = generate_report({**state, "rul": state.get("rul", {})}, state.get("evidence_chain", []), state.get("decision", {}))
    return {"report": report, "events": [{"agent": "report", "status": "completed", "message": "结构化报告已生成", "timestamp": datetime.now().astimezone().isoformat()}]}
