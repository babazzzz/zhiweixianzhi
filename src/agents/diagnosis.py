"""
==========================================
 诊断智能体 — diagnosis.py
==========================================
【角色定位】
Diagnosis Agent = "主治医师" / "诊断专家"

它的工作：
1. 接收 Analysis Agent 的分析结果
2. 综合所有异常证据，确定故障类型
3. 构建 E1-E7 七段证据链
4. 输出最终诊断结论

【E1-E7 证据链】
这是原项目设计的诊断框架，每条证据链代表一个维度的分析：

E1 = 温度分析证据（是否过热/温升异常）
E2 = 电流分析证据（是否过载/电流异常）
E3 = 电压分析证据（是否电源不稳）
E4 = 转速分析证据（是否机械异常）
E5 = 综合异常评分（Isolation Forest等多维检测）
E6 = 寿命预测证据（RUL/故障概率）
E7 = 趋势分析证据（退化趋势方向）
"""

from langchain_core.messages import HumanMessage, SystemMessage
from ..state import AgentState
from ..llm import get_agent_llm
from ..config import FAULT_MODELS


# ==========================================
# 证据链构建（E1-E7）
# ==========================================

def build_evidence_chain(time_series: dict, analysis_result: dict) -> dict:
    """
    构建 E1-E7 七段证据链。

    每条证据包含：
    - status:   "normal" / "warning" / "critical"
    - score:    0-1 的异常分数
    - detail:   具体描述
    """
    temp = time_series.get("temperature", [])
    curr = time_series.get("current", [])
    volt = time_series.get("voltage", [])
    speed = time_series.get("speed", [])

    details = analysis_result.get("details", {})

    # E1: 温度异常
    e1_score = min(1.0, sum(1 for t in temp if t > 55) / max(len(temp), 1) * 3) if temp else 0
    e1 = {
        "status": "critical" if e1_score > 0.6 else ("warning" if e1_score > 0.3 else "normal"),
        "score": round(e1_score, 3),
        "detail": f"温度均值={sum(temp)/len(temp):.1f}℃, 最大值={max(temp):.1f}℃" if temp else "无温度数据",
    }

    # E2: 电流异常
    e2_score = min(1.0, sum(1 for c in curr if c > 15) / max(len(curr), 1) * 3) if curr else 0
    e2 = {
        "status": "critical" if e2_score > 0.6 else ("warning" if e2_score > 0.3 else "normal"),
        "score": round(e2_score, 3),
        "detail": f"电流均值={sum(curr)/len(curr):.2f}A" if curr else "无电流数据",
    }

    # E3: 电压稳定性
    e3_score = min(1.0, np_std(volt) / 10) if volt else 0
    e3 = {
        "status": "warning" if e3_score > 0.5 else "normal",
        "score": round(e3_score, 3),
        "detail": f"电压标准差={np_std(volt):.1f}V" if volt else "无电压数据",
    }

    # E4: 转速波动
    e4_score = min(1.0, np_std(speed) / 200) if speed else 0
    e4 = {
        "status": "warning" if e4_score > 0.5 else "normal",
        "score": round(e4_score, 3),
        "detail": f"转速标准差={np_std(speed):.0f}rpm" if speed else "无转速数据",
    }

    # E5: 综合异常评分（来自 Isolation Forest）
    e5_score = details.get("fusion_score", 0)
    e5 = {
        "status": "critical" if e5_score > 0.6 else ("warning" if e5_score > 0.3 else "normal"),
        "score": round(e5_score, 3),
        "detail": f"贝叶斯融合评分={e5_score:.3f}",
    }

    # E6: 寿命预测（来自威布尔 RUL）
    rul_details = {k: v for k, v in details.items() if "rul" in k.lower() or "fault_prob" in k}
    e6_score = details.get("fault_prob_24h", 0) if "fault_prob_24h" in str(details) else e5_score * 0.5
    for ev in details.get("details", []):
        if ev.get("name") == "威布尔RUL":
            e6_score = 1 - ev.get("score", 0.5)
            break
    e6 = {
        "status": "critical" if e6_score > 0.6 else ("warning" if e6_score > 0.3 else "normal"),
        "score": round(e6_score, 3),
        "detail": f"24h故障概率≈{e6_score:.2%}",
    }

    # E7: 趋势分析
    temp_trend = np_polyfit(list(range(len(temp))), temp, 1)[0] if len(temp) > 2 else 0
    e7_score = min(1.0, max(0, temp_trend / 0.5))
    e7 = {
        "status": "critical" if e7_score > 0.6 else ("warning" if e7_score > 0.3 else "normal"),
        "score": round(e7_score, 3),
        "detail": f"温升速率={temp_trend:.3f}℃/次采样" if len(temp) > 2 else "数据不足",
    }

    return {"E1": e1, "E2": e2, "E3": e3, "E4": e4, "E5": e5, "E6": e6, "E7": e7}


def np_std(data):
    """安全计算标准差"""
    import numpy as np
    arr = np.array(data)
    return float(arr.std()) if len(arr) > 1 else 0


def np_polyfit(x, y, deg):
    """安全的多项式拟合"""
    import numpy as np
    if len(x) < deg + 1 or len(y) < deg + 1:
        return [0]
    return np.polyfit(x, y, deg)


# ==========================================
# 故障匹配
# ==========================================

def match_fault_type(evidence: dict, features: dict) -> dict:
    """
    根据证据链和特征匹配最可能的故障类型。

    简单的规则匹配策略（非ML，可解释性强）：
    1. 温度异常+电流平稳 = 散热故障
    2. 电流上升+转速下降 = 机械卡滞
    3. 电压波动+电流波动 = 电源老化
    ...
    """
    scores = {}
    for fault in FAULT_MODELS:
        score = 0.0
        params = fault["params"]

        # 根据证据链评分
        for p in params:
            if p == "temperature":
                score += evidence.get("E1", {}).get("score", 0) * 1.0
            elif p == "current":
                score += evidence.get("E2", {}).get("score", 0) * 1.0
            elif p == "voltage":
                score += evidence.get("E3", {}).get("score", 0) * 0.8
            elif p == "speed":
                score += evidence.get("E4", {}).get("score", 0) * 0.7

        # 严重程度加权
        severity_weight = {"critical": 1.2, "high": 1.0, "medium": 0.7, "low": 0.4}
        score *= severity_weight.get(fault["severity"], 1.0)

        scores[fault["id"]] = {
            "fault_id": fault["id"],
            "fault_name": fault["name"],
            "system": fault["system"],
            "part": fault["part"],
            "severity": fault["severity"],
            "match_score": round(min(1.0, score / 3), 3),
        }

    # 按匹配度排序
    sorted_faults = sorted(scores.values(), key=lambda x: x["match_score"], reverse=True)
    return sorted_faults[:3]  # 返回最可能的3个


def diagnosis_agent(state: AgentState) -> dict:
    """
    诊断智能体：综合分析结果 → 故障诊断 → 证据链。

    【输入】
    state.analysis_result: Analysis Agent 的分析结果
    state.raw_data: 原始数据（用于构建证据链）

    【输出】
    state.diagnosis_result: 诊断结果（故障类型+证据链+跨设备分析）
    """
    analysis_result = state.get("analysis_result", {})
    device_id = analysis_result.get("device_id", state.get("current_device", "unknown"))
    raw_data = state.get("raw_data", {})
    device_data = raw_data.get(device_id, {})
    time_series = device_data.get("time_series", {})
    features = state.get("feature_vectors", {}).get(device_id, {})

    print(f"\n[Diagnosis Agent] 诊断设备 {device_id}...")

    # Step 1: 构建 E1-E7 证据链
    evidence = build_evidence_chain(time_series, analysis_result)

    # Step 2: 匹配故障类型
    matched_faults = match_fault_type(evidence, features)

    # Step 3: 计算综合风险
    risk_score = sum(e["score"] for e in evidence.values()) / max(len(evidence), 1)

    if risk_score >= 0.8:
        risk_level = "critical"
    elif risk_score >= 0.6:
        risk_level = "high"
    elif risk_score >= 0.4:
        risk_level = "medium"
    else:
        risk_level = "low"

    diagnosis_result = {
        "device_id": device_id,
        "risk_level": risk_level,
        "risk_score": round(risk_score, 3),
        "matched_faults": matched_faults,
        "primary_fault": matched_faults[0] if matched_faults else None,
        "evidence_chain": evidence,
        "healthy_count": sum(1 for e in evidence.values() if e["status"] == "normal"),
        "warning_count": sum(1 for e in evidence.values() if e["status"] == "warning"),
        "critical_count": sum(1 for e in evidence.values() if e["status"] == "critical"),
    }

    print(f"[Diagnosis Agent] 诊断完成: 风险={risk_level}, "
          f"主要故障={matched_faults[0]['fault_name'] if matched_faults else '无'}")

    return {
        "diagnosis_result": diagnosis_result,
        "messages": [HumanMessage(
            content=f"🏥 Diagnosis Agent: 设备 {device_id} 诊断完成，"
                    f"风险等级 {risk_level}，"
                    f"主要故障: {matched_faults[0]['fault_name'] if matched_faults else '未检测到明确故障'}"
        )],
    }
