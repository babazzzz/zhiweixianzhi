"""
==========================================
 ReAct分析智能体 — analysis.py
==========================================
★ 这是系统中最核心的智能体 ★

【什么是 ReAct？】
ReAct = Reason + Act（推理 + 行动）

传统的 AI 是"输入→输出"的简单模式：
    问 → 答

ReAct 是"思考→行动→观察→再思考"的循环：
    问 → 思考(Thought) → 选工具(Action) → 看结果(Observation) → 再思考...

好比医生诊断：
    普通AI：看体温单 → 直接说"你发烧了"
    ReAct：  看体温单 → "体温偏高，但需要结合其他指标"
              → 检查血常规 → "白细胞偏高"
              → 结合体温和白细胞 → "可能是感染"

【LangChain 的 ReAct 实现】
使用 langgraph.prebuilt.create_react_agent 创建 ReAct Agent，
你只需要：
1. 定义工具（tools）—— 智能体能调用的函数
2. 写系统提示词（prompt）—— 告诉智能体怎么用工具
3. 提供 LLM
剩下的事（Thought→Action→Observation 循环）自动处理！

【工具（Tools）是什么？】
工具就是"智能体能调用的 Python 函数"，用 @tool 装饰器标记。
每个工具需要写清楚：
- 函数名：智能体通过函数名选择调用哪个工具
- 文档字符串：智能体通过文档理解工具的作用
- 参数和返回值：定义了工具的输入输出格式
"""

from typing import Any
from langchain_core.tools import tool
from langchain_core.messages import HumanMessage, SystemMessage

# langgraph.prebuilt.create_react_agent — ReAct Agent 创建
from langgraph.prebuilt import create_react_agent

from ..state import AgentState
from ..llm import get_agent_llm
from ..tools.algorithms import (
    zscore_anomaly_detection,
    cusum_detection,
    weibull_rul_estimation,
    isolation_forest_detect,
    comprehensive_analysis,
)


# ==========================================
# 工具定义（Tools）
# ==========================================

@tool
def analyze_temperature(data: list[float]) -> dict:
    """分析温度数据，检测温度异常和趋势。参数: data为温度时间序列。返回异常计数、Z值、CUSUM触发等。"""
    zscore = zscore_anomaly_detection(data)
    cusum = cusum_detection(data)
    return {"anomaly_count": zscore["anomaly_count"], "max_zscore": zscore["max_zscore"],
            "cusum_triggered": cusum["triggered"]}

@tool
def analyze_current(data: list[float]) -> dict:
    """分析电流数据，检测电流异常和负载变化。参数: data为电流时间序列。返回异常计数和Z值。"""
    zscore = zscore_anomaly_detection(data)
    return {"anomaly_count": zscore["anomaly_count"], "max_zscore": zscore["max_zscore"]}

@tool
def estimate_rul(temperature_data: list[float], operating_hours: float = 100) -> dict:
    """基于威布尔分布估计设备剩余寿命(RUL)。参数: temperature_data为温度序列，operating_hours为已运行小时数。返回rul_hours、故障概率等。"""
    return weibull_rul_estimation(temperature_data, operating_hours)

@tool
def comprehensive_device_analysis(time_series: dict) -> dict:
    """对设备进行全面综合分析。参数: time_series需包含temperature, current, voltage, speed等字段。返回风险等级、融合评分等。"""
    return comprehensive_analysis(time_series)


TOOLS = [analyze_temperature, analyze_current, estimate_rul, comprehensive_device_analysis]

REACT_SYSTEM_PROMPT = """你是一位CNC设备预测性维护专家。使用工具分析设备数据。
流程：先analyze_temperature和analyze_current → estimate_rul → comprehensive_device_analysis。
每一步先思考(Thought)再行动(Action)，根据观察(Observation)决定下一步。"""


def analysis_agent(state: AgentState) -> dict:
    """ReAct分析智能体"""
    device_id = state.get("current_device", "unknown")
    print(f"\n[Analysis Agent] 开始分析设备 {device_id}...")

    raw_data = state.get("raw_data", {})
    device_data = raw_data.get(device_id, {})
    time_series = device_data.get("time_series", {})
    features = state.get("feature_vectors", {}).get(device_id, {})

    llm = get_agent_llm()
    agent = create_react_agent(model=llm, tools=TOOLS, prompt=REACT_SYSTEM_PROMPT)

    try:
        result = agent.invoke({
            "messages": [("user",
                f"请全面分析设备 {device_id} 的运行状态。"
                f"温度数据({len(time_series.get('temperature', []))}个样本)：{time_series.get('temperature', [])[:10]}"
                f"电流数据({len(time_series.get('current', []))}个样本)：{time_series.get('current', [])[:10]}"
            )]
        })
        conclusion = result["messages"][-1].content if result.get("messages") else "分析完成"
    except Exception as e:
        print(f"[Analysis Agent] ReAct失败: {e}，使用降级算法")
        conclusion = f"降级算法模式（ReAct不可用）"

    # 算法分析（无论ReAct是否成功都运行）
    algo_result = comprehensive_analysis(time_series)

    analysis_result = {
        "device_id": device_id,
        "conclusion": conclusion,
        "risk_level": algo_result.get("risk_level", "unknown"),
        "fusion_score": algo_result.get("fusion_score", 0),
        "details": algo_result,
    }

    return {
        "analysis_result": analysis_result,
        "messages": [HumanMessage(content=f"🔬 Analysis: 设备 {device_id} 分析完成，风险等级 {algo_result.get('risk_level', 'N/A')}")],
    }
