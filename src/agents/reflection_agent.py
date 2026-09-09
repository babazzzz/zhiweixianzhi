"""Deterministic five-check reflection quality gate."""

from __future__ import annotations

from datetime import datetime

from .state import DiagnosticState


async def reflection_node(state: DiagnosticState) -> dict[str, object]:
    """Validate completeness, consistency, evidence closure and physical bounds."""

    score = float(state.get("anomaly_score", 0.0))
    severity = str(state.get("severity", "normal"))
    rul = state.get("rul", {})
    checks = [
        {"id": "data_complete", "label": "数据完整性", "passed": bool(state.get("sensor_data")) and bool(state.get("history"))},
        {"id": "logic_consistent", "label": "逻辑一致性", "passed": not (severity == "normal" and score >= 0.4)},
        {"id": "evidence_closed", "label": "证据链闭合", "passed": [str(item.get("id")) for item in state.get("evidence_chain", [])] == [f"E{index}" for index in range(1, 8)]},
        {"id": "prediction_reasonable", "label": "预测合理性", "passed": int(rul.get("rul_p10", 0)) > int(rul.get("rul_p50", 0)) > int(rul.get("rul_p90", 0)) >= 4},
        {"id": "physics_valid", "label": "物理约束", "passed": float(rul.get("degradation_rate", 0.0)) <= 0.005 and max((float(state.get("sensor_data", {}).get(metric, 0.0)) for metric in ("op_temperature", "bearing_temp")), default=0.0) <= 180.0},
    ]
    passed = all(bool(item["passed"]) for item in checks)
    retry_count = int(state.get("retry_count", 0)) + (0 if passed else 1)
    status = "passed" if passed else "warning_max_retries" if retry_count >= int(state.get("max_retries", 3)) else "retry_required"
    return {"reflection_passed": passed, "reflection_checks": checks, "retry_count": retry_count, "events": [{"agent": "reflection", "status": status, "message": "5项自检完成", "timestamp": datetime.now().astimezone().isoformat()}]}
