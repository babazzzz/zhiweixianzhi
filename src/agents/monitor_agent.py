"""Monitor specialist node: query real telemetry and run anomaly detection."""

from __future__ import annotations

from datetime import datetime

from src.database.connection import Database
from src.skills.anomaly_detect import detect_anomaly, peer_baseline_from_machines
from .state import DiagnosticState


async def monitor_node(state: DiagnosticState, database: Database) -> dict[str, object]:
    """Populate health and anomaly fields from the latest stored machine window."""

    machine_id = state["machine_id"]
    readings = database.get_readings(machine_id, limit=240)
    if not readings:
        return {"error": f"{machine_id} has no sensor data", "is_anomaly": False, "events": [{"agent": "monitor", "status": "error", "message": "no telemetry"}]}
    latest = readings[-1]
    history = [dict(item["metrics"]) for item in readings if isinstance(item.get("metrics"), dict)]
    # 用"同厂健康设备"的同侪基线，而不是本机历史基线：仿真器给 100 台都叠加了 14 天单调退化，
    # 拿本机两周前的读数作对比会把每一台都判成异常。
    peer_baseline = peer_baseline_from_machines(database.list_machines())
    result = detect_anomaly(dict(latest["metrics"]), history, peer_baseline)
    return {"trigger_timestamp": str(latest["timestamp"]), "sensor_data": dict(latest["metrics"]), "history": history, **result.as_dict(), "events": [{"agent": "monitor", "status": "completed", "message": f"健康分 {result.health_score:.3f}，{len(result.anomaly_dims)} 个异常维度", "timestamp": datetime.now().astimezone().isoformat()}]}
