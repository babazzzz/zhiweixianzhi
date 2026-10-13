"""ReAct-style diagnostic node backed by deterministic FMECA and causal evidence."""

from __future__ import annotations

import os
from datetime import datetime

from src.database.connection import Database
from src.knowledge.fault_graph import FaultGraph
from src.skills.case_match import match_cases
from src.skills.evidence_chain import build_evidence_chain
from src.severity import severity_from_reading
from .state import DiagnosticState


async def diagnostic_node(state: DiagnosticState, database: Database, graph: FaultGraph) -> dict[str, object]:
    """Infer a failure mode, collect E1–E7 evidence, and optionally ask the configured LLM to refine JSON."""

    latest = state.get("sensor_data", {})
    anomaly_dims = set(state.get("anomaly_dims", []))
    deviations = _fleet_deviations(database, latest)
    fault_code = _select_fault_code(anomaly_dims, latest, deviations)
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
    # 严重度必须与设备列表/总览走同一口径（见 src/severity.py）。这里刻意用**已落库的
    # health_score**（列表页展示的就是它），而不是本次实时健康分，避免同一台设备在
    # "仪表盘"和"诊断结论"里出现两个不同的等级。
    stored_health = (database.latest_reading(str(state.get("machine_id", ""))) or {}).get("health_score")
    _, severity = severity_from_reading(stored_health, fault_code)
    return {"fault_code": fault_code, "fault_mode": fault_name, "root_cause": root_cause, "evidence_chain": evidence, "confidence": round(confidence, 4), "severity": severity, "react_iterations": min(3, int(state.get("react_iterations", 0)) + 1), "events": [event]}


def _fleet_deviations(database: Database, latest: dict[str, float]) -> dict[str, float]:
    """计算本机读数相对"同型号健康设备中位数"的偏差百分比。

    为什么需要它：只看"哪些维度异常"无法区分故障类型——轴承磨损与散热故障都会
    推高温度。把每台设备放到同侪里比较，**专属征兆**才会浮出来（例如冷却水温只有
    散热故障才会偏离同侪）。健康设备占比 94%，中位数天然抗污染。
    """

    samples: dict[str, list[float]] = {}
    try:
        for machine in database.list_machines():
            reading = machine.get("latest_reading") or {}
            health = reading.get("health_score")
            if health is not None and float(health) < 0.6:
                continue  # 剔除已劣化设备，避免基线被故障读数拉偏
            for key, value in (reading.get("metrics") or {}).items():
                if key == "fault_code":
                    continue  # 标签位不参与偏差计算，避免"抄答案"
                try:
                    samples.setdefault(key, []).append(float(value))
                except (TypeError, ValueError):
                    continue
    except Exception:
        return {}
    deviations: dict[str, float] = {}
    for key, value in latest.items():
        values = samples.get(key)
        if not values or key == "fault_code":
            continue
        median = sorted(values)[len(values) // 2]
        if abs(median) < 1e-9:
            continue
        try:
            deviations[key] = (float(value) - median) / abs(median) * 100
        except (TypeError, ValueError):
            continue
    return deviations


def _select_fault_code(anomaly_dims: set[str], latest: dict[str, float],
                       deviations: dict[str, float] | None = None) -> int:
    """按"与同型号健康设备的偏差"做故障隔离，而非"命中任一异常维度即判轴承磨损"。

    优先级按征兆的**独特性**排列——冷却水温/压力只受散热系统影响，振动+位置精度
    只受轴承影响。先判独特征兆，再回退到通用征兆，避免各故障互相抢答。
    """

    deviation = deviations or {}

    def above(metric: str, threshold: float) -> bool:
        return deviation.get(metric, 0.0) >= threshold

    def below(metric: str, threshold: float) -> bool:
        return deviation.get(metric, 0.0) <= -threshold

    # ① 散热系统故障：冷却水温升高 / 冷却压力下降，是它区别于轴承磨损的独有征兆
    if above("coolant_temp", 8.0) or below("coolant_pressure", 5.0):
        return 8
    # ② 电源模块故障：供电电压低于同侪，或功率因数与谐波同时恶化
    if below("op_voltage", 1.5) or (below("power_factor", 2.0) and above("harmonic_distortion", 10.0)):
        return 7
    # ③ 轴承磨损：振动 / 位置精度由 bearing 直接驱动，是轴承的专属机械征兆。
    #    这里刻意不再看 bearing_temp——它等于 temp + 4.5*bearing，由温度派生，
    #    故障码 3（温度过高）抬高温度后会连带抬起 bearing_temp，
    #    导致"温度过高"被抢判成"轴承磨损"（CNC_025 正是这样被误判的）。
    if above("vibration_rms", 15.0) or above("position_error", 30.0):
        return 1
    # ④ 电机过载：电流 / 主轴负载显著高于同侪
    if above("op_amperage", 10.0) or above("spindle_load", 10.0) or float(latest.get("op_amperage", 0.0)) > 35.0:
        return 2
    # ⑤ 温度过高：温度升高但散热通道正常（冷却维度不偏离）
    if above("op_temperature", 8.0):
        return 3
    # ⑥ 转子故障：掉速必须与谐波畸变同时出现。
    #    只看掉速无法区分"转子故障"与"自然轴承老化"——94 台健康机里就有 14 台
    #    因轴承自然磨损把转速压到 -4% 以下，单靠 rotor_speed 一条会被大量误报。
    if below("rotor_speed", 4.0) and above("harmonic_distortion", 10.0):
        return 6
    # ⑦ 通用征兆兜底：异常维度呈现"振动 + 轴承温度/转速/位置精度"组合 → 轴承磨损
    if {"vibration_rms", "motor_vibration"} & anomaly_dims and {"bearing_temp", "rotor_speed", "position_error", "oil_pressure"} & anomaly_dims:
        return 1
    if "op_voltage" in anomaly_dims:
        return 4
    if "op_amperage" in anomaly_dims:
        return 5
    return 0


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
