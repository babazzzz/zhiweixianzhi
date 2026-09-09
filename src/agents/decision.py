"""
==========================================
 决策智能体 — decision.py
==========================================
【角色定位】
Decision Agent = "治疗方案制定师"

它的工作：
1. 根据诊断结果制定决策建议
2. 成本分析（修 vs 不修 vs 什么时候修）
3. SOP维护工单自动生成
4. 维修排期建议
"""

from langchain_core.messages import HumanMessage
from ..state import AgentState
from ..config import RISK_LEVELS

# 成本参数（单位：元）
COST_PARAMS = {
    "hourly_downtime_loss": 5000,    # 每小时停机损失
    "preventive_maintenance": 8000,  # 预防性维护成本
    "corrective_maintenance": 25000, # 故障后维修成本
    "replacement_part": 12000,       # 更换部件成本
    "inspection_cost": 2000,         # 检查成本
}


def decision_agent(state: AgentState) -> dict:
    """
    决策智能体：诊断结果 → 维护决策 + 成本分析 + 工单生成。

    【输入】
    state.diagnosis_result: 诊断结果（风险等级、故障类型）

    【输出】
    state.decision_result: 决策结果（建议、成本、工单）
    """
    diagnosis = state.get("diagnosis_result", {})
    device_id = diagnosis.get("device_id", state.get("current_device", "unknown"))
    risk_level = diagnosis.get("risk_level", "low")
    primary_fault = diagnosis.get("primary_fault", {})

    print(f"\n[Decision Agent] 为设备 {device_id} 制定决策...")

    # Step 1: 根据风险等级决定行动
    risk_config = RISK_LEVELS.get(risk_level, RISK_LEVELS["low"])
    action = risk_config["action"]

    # Step 2: 成本分析
    costs = _calculate_costs(risk_level, primary_fault.get("severity", "low"))

    # Step 3: 维护排期建议
    schedule = _generate_schedule(risk_level, diagnosis.get("analysis_result", {}))

    # Step 4: 生成SOP工单
    work_order = _generate_work_order(device_id, primary_fault, action)

    decision_result = {
        "device_id": device_id,
        "risk_level": risk_level,
        "action": action,
        "action_priority": {"critical": "立即", "high": "紧急", "medium": "计划内", "low": "例行"}.get(risk_level, "例行"),
        "cost_analysis": costs,
        "schedule": schedule,
        "work_order": work_order,
        "recommendation": (
            f"设备 {device_id} 当前风险等级为【{risk_config['label']}】。"
            f"建议{action}。"
            f"若进行预防性维护，预计成本{costs['preventive_cost']}元；"
            f"若发生故障后再维修，预计成本{costs['corrective_cost']}元。"
            f"预防性维护可节省{costs['savings']}元（约{costs['savings_percentage']}%）。"
        ),
    }

    print(f"[Decision Agent] 决策完成: {action}")

    return {
        "decision_result": decision_result,
        "messages": [HumanMessage(
            content=f"📋 Decision Agent: 设备 {device_id} 决策完成，"
                    f"建议: {action}，"
                    f"预防性维护可节省{costs['savings']}元"
        )],
    }


# ==================== 辅助函数 ====================

def _calculate_costs(risk_level: str, severity: str) -> dict:
    """
    计算维护成本对比。

    【核心逻辑】
    预防性维护成本（可计划）：
    - 检查 + 更换（如果需要）
    = 2000 + 12000 = 14000（如果更换零件）
    = 2000（如果只是检查）

    故障后维修成本（不可计划）：
    - 紧急维修 + 停机损失 + 更换
    = 25000 + 停机时间×5000 + 12000

    节省 = 纠正性 - 预防性
    """
    preventive = 2000  # 检查
    if severity in ("critical", "high"):
        preventive += 12000  # 加上零件更换

    downtime_hours = {"critical": 8, "high": 4, "medium": 2, "low": 0}
    corrective = 25000 + COST_PARAMS["hourly_downtime_loss"] * downtime_hours.get(risk_level, 2)
    if severity in ("critical", "high"):
        corrective += 12000

    savings = corrective - preventive
    savings_pct = round(savings / max(corrective, 1) * 100, 1)

    return {
        "preventive_cost": preventive,
        "corrective_cost": corrective,
        "savings": savings,
        "savings_percentage": savings_pct,
        "downtime_risk_hours": downtime_hours.get(risk_level, 2),
    }


def _generate_schedule(risk_level: str, analysis: dict) -> dict:
    """生成维护排期建议"""
    deadlines = {
        "critical": {"text": "立即停机", "max_hours": 0},
        "high": {"text": "24小时内", "max_hours": 24},
        "medium": {"text": "72小时内（下次保养窗口）", "max_hours": 72},
        "low": {"text": "下次定期保养时", "max_hours": 720},
    }
    return deadlines.get(risk_level, deadlines["low"])


def _generate_work_order(device_id: str, fault: dict, action: str) -> dict:
    """生成标准维护工单"""
    fault_name = fault.get("fault_name", "常规检查")
    system = fault.get("system", "综合系统")
    part = fault.get("part", "通用部件")

    return {
        "work_order_id": f"WO-{device_id}-{hash(fault_name) % 10000:04d}",
        "device_id": device_id,
        "fault_description": fault_name,
        "target_system": system,
        "target_part": part,
        "action_required": action,
        "estimated_duration": "4小时" if fault.get("severity") == "critical" else "2小时",
        "required_skills": ["机械维修", "电气检测"],
        "safety_notes": "断电操作，挂牌上锁",
    }
