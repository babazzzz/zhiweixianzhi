"""ReAct-style diagnostic node backed by deterministic FMECA and causal evidence."""

from __future__ import annotations

import os
from datetime import datetime

from src.database.connection import Database
from src.knowledge.fault_graph import FaultGraph
from src.skills.case_match import match_cases
from src.skills.evidence_chain import build_evidence_chain
from .state import DiagnosticState


async def diagnostic_node(state: DiagnosticState, database: Database, graph: FaultGraph) -> dict[str, object]:
    """Infer a failure mode, collect E1–E7 evidence, and optionally ask the configured LLM to refine JSON."""

    latest = state.get("sensor_data", {})
    anomaly_dims = set(state.get("anomaly_dims", []))
    fault_code = _select_fault_code(anomaly_dims, latest)
    rule = database.get_fmeca(fault_code) if fault_code else None
    query = " ".join(sorted(anomaly_dims)) + " " + str(state.get("anomaly_type", ""))
    cases = match_cases(database, query, fault_code, 3)
    evidence = build_evidence_chain(_as_anomaly(state), state.get("history", []), rule, cases, graph)
    confidence = min(0.98, 0.56 + sum(float(item["confidence"]) for item in evidence) / max(len(evidence), 1) * 0.42)
    fault_name = str(rule.get("fault_name", "未检测到明确故障")) if rule else "未检测到明确故障"
    root_cause = str(rule.get("physical_mechanism", "需要更多连续时序数据以确认根因")) if rule else "需要更多连续时序数据以确认根因"
    llm_event = await _optional_llm_refinement(state, fault_code, fault_name)
    event = {"agent": "diagnostic", "status": "completed", "message": f"ReAct第 {min(3, int(state.get('react_iterations', 0)) + 1)} 轮：{fault_name}，置信度 {confidence:.3f}", "timestamp": datetime.now().astimezone().isoformat()}
    if llm_event:
        event["llm"] = llm_event
    return {"fault_code": fault_code, "fault_mode": fault_name, "root_cause": root_cause, "evidence_chain": evidence, "confidence": round(confidence, 4), "react_iterations": min(3, int(state.get("react_iterations", 0)) + 1), "events": [event]}


def _select_fault_code(anomaly_dims: set[str], latest: dict[str, float]) -> int:
    """Apply transparent signal combinations before any optional LLM refinement."""

    if "op_voltage" in anomaly_dims and "op_amperage" in anomaly_dims:
        return 7
    if "vibration_rms" in anomaly_dims and ("bearing_temp" in anomaly_dims or "rotor_speed" in anomaly_dims):
        return 1
    if "coolant_temp" in anomaly_dims or ("op_temperature" in anomaly_dims and "op_amperage" not in anomaly_dims):
        return 8
    if "op_amperage" in anomaly_dims and float(latest.get("op_amperage", 0.0)) > 35.0:
        return 2
    if "rotor_speed" in anomaly_dims:
        return 6
    if "op_voltage" in anomaly_dims:
        return 4
    return 5 if "op_amperage" in anomaly_dims else 0


def _as_anomaly(state: DiagnosticState):
    """Rebuild the skill result from the graph state for evidence construction."""

    from src.skills.anomaly_detect import AnomalyResult
    return AnomalyResult(float(state.get("health_score", 1.0)), float(state.get("anomaly_score", 0.0)), bool(state.get("is_anomaly", False)), list(state.get("anomaly_dims", [])), str(state.get("severity", "normal")), str(state.get("anomaly_type", "stable")), {}, {})


async def _optional_llm_refinement(state: DiagnosticState, fault_code: int, fault_name: str) -> dict[str, object] | None:
    """Use DeepSeek only when explicitly enabled; deterministic rules remain the safety fallback."""

    if os.getenv("ENABLE_LLM_ENRICHMENT", "0").lower() not in {"1", "true", "yes"}:
        return None
    try:
        from src.llm.client import UnifiedLLMClient
        client = UnifiedLLMClient(timeout_seconds=20.0)
        response = await client.chat("diagnostic", [{"role": "user", "content": f"候选故障码{fault_code} {fault_name}，异常维度{state.get('anomaly_dims', [])}，只校验候选是否合理。"}])
        parsed = client.parse_json(response.content)
        return {"provider": response.provider, "model": response.model, "parsed": parsed if parsed is not None else {"raw": response.content[:500]}}
    except Exception as error:
        return {"status": "degraded", "message": str(error)}
