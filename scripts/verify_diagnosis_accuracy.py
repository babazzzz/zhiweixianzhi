"""Full production-path verification: monitor(peer baseline) -> routing -> diagnostic.

只读脚本：不写任何数据。用于确认 6/6 诊断且 0/94 健康机误报。
严格复刻 src/agents/monitor_agent.py 与 src/graph/main_graph.py:74 的行为。
"""
import sys

sys.path.insert(0, ".")

from src.agents.diagnostic_agent import _fleet_deviations, _select_fault_code
from src.database import Database
from src.simulator.fault_injector import _SCENARIOS
from src.skills.anomaly_detect.skill import detect_anomaly, peer_baseline_from_machines


def main() -> None:
    db = Database()
    machines = db.list_machines()
    peer = peer_baseline_from_machines(machines)
    truth = {mid: s.fault_code for mid, s in _SCENARIOS.items()}

    rows = []
    for m in machines:
        mid = m["id"]
        readings = db.get_readings(mid, limit=240)
        latest = readings[-1]
        history = [dict(item["metrics"]) for item in readings if isinstance(item.get("metrics"), dict)]
        stored_health = latest.get("health_score")
        res = detect_anomaly(dict(latest["metrics"]), history, peer)
        routed = bool(res.is_anomaly)  # main_graph.py:74 的路由条件
        metrics = dict(latest["metrics"])
        code = None
        if routed:
            deviations = _fleet_deviations(db, metrics)  # diagnostic_node 第 21 行
            code = _select_fault_code(set(res.anomaly_dims), metrics, deviations)  # 第 22 行
        rows.append((mid, truth.get(mid, 0), stored_health, res.is_anomaly, list(res.anomaly_dims), code))

    print("=== FAULTY MACHINES (ground truth) ===")
    for mid, tcode, h, is_anom, dims, code in sorted(rows):
        if tcode == 0:
            continue
        ok = "OK  " if code == tcode else "FAIL"
        hv = f"{h:.3f}" if h is not None else "None"
        print(f"{ok} {mid:9s} truth={tcode} health={hv} is_anomaly={str(is_anom):5s} dims={dims} -> diag={code}")

    healthy = [r for r in rows if r[1] == 0]
    false_anom = [r for r in healthy if r[3]]
    false_diag = [r for r in healthy if r[5] not in (None, 0)]
    print("\n=== HEALTHY MACHINES ===")
    print(f"healthy total           : {len(healthy)}")
    print(f"healthy flagged anomaly : {len(false_anom)}")
    print(f"healthy given fault code: {len(false_diag)}")
    for r in false_anom[:10]:
        print("   !!", r[0], f"health={r[2]:.3f}" if r[2] is not None else "health=None", r[4], "->", r[5])

    faulty = [r for r in rows if r[1] != 0]
    n_ok = sum(1 for r in faulty if r[5] == r[1])
    print(f"\nDIAGNOSIS: {n_ok}/{len(faulty)} correct | healthy false-positive: {len(false_diag)}/{len(healthy)}")
    print("is_anomaly TRUE among faulty:", sum(1 for r in faulty if r[3]), "/", len(faulty))


if __name__ == "__main__":
    main()
