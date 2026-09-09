"""
==========================================
 调度智能体 — supervisor.py
==========================================
【角色定位】
Supervisor = 整个系统的"调度中心" / "项目经理"

它的工作：
1. 接收用户请求（"分析这几台设备"）
2. 分解成子任务
3. 分配给下游智能体
4. 监控执行进度
5. 处理异常情况

【LangGraph 中的角色】
Supervisor 是一个"图节点"（node），它被添加到 StateGraph 中，
LangGraph 会在执行到该节点时自动调用。

重要概念：一个 Agent = 一个函数，输入 state，输出 state。
就是这么简单。
"""

from langchain_core.messages import HumanMessage, SystemMessage
from ..state import AgentState
from ..llm import get_agent_llm


# 系统提示词 — 告诉LLM它的角色和工作方式
SYSTEM_PROMPT = """你是一个CNC设备预测性维护系统的调度主管(Supervisor)。

你的职责：
1. 接收用户指定的设备列表
2. 为每台设备创建分析任务
3. 协调数据管道、分析、诊断、决策智能体的工作

请用简洁的语言确认任务并输出分析计划，包括：
- 需要分析的设备数量
- 每台设备的基本信息
- 分析流程概述
"""


def supervisor_agent(state: AgentState) -> dict:
    """
    Supervisor 智能体入口函数。

    【输入】
    state.device_ids: 要分析的设备ID列表

    【输出】
    state.analysis_tasks: 分解后的任务列表
    state.messages: 增加一条确认消息
    """
    device_ids = state.get("device_ids", [])
    if not device_ids:
        return {
            "error": "未指定设备ID",
            "analysis_tasks": [],
        }

    # 从原始数据中提取设备信息（如果有）
    raw_data = state.get("raw_data", {})
    device_info = []
    for did in device_ids:
        info = {"device_id": did}
        if did in raw_data:
            data = raw_data[did]
            info["log_count"] = len(data.get("logs", []))
        device_info.append(info)

    # 创建分析任务
    tasks = []
    for info in device_info:
        tasks.append({
            "device_id": info["device_id"],
            "type": "full_analysis",
            "status": "pending",
            "log_count": info.get("log_count", 0),
        })

    # 调用LLM生成确认消息（让用户知道系统已理解）
    llm = get_agent_llm()
    try:
        msg_count = len(device_ids)
        response = llm.invoke([
            SystemMessage(content=SYSTEM_PROMPT),
            HumanMessage(content=f"需要分析以下{msg_count}台CNC设备：{', '.join(device_ids)}")
        ])
        message = response.content
    except Exception as e:
        message = f"[Supervisor] 已接收 {len(device_ids)} 台设备的分析任务"

    return {
        "analysis_tasks": tasks,
        "current_device": device_ids[0] if device_ids else None,
        "messages": [HumanMessage(content=f"🔧 Supervisor: 已创建 {len(tasks)} 个分析任务")],
    }
