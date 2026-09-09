"""
==========================================
 数据处理智能体 — data_pipeline.py
==========================================
【角色定位】
Data Pipeline = 系统的"检验科" / "数据预处理车间"

它的工作：
1. 从CSV文件加载原始数据
2. 提取每台设备的时间序列
3. 计算22维特征向量
4. 把数据整理成下游智能体能用的格式

【LangGraph 中的角色】
像流水线上的"第一个工位"——原材料进来，半成品出去。
"""

import pandas as pd
from ..state import AgentState
from ..tools.data_loader import load_machine_logs, get_device_data
from ..tools.feature_engineering import compute_all_devices_features
from langchain_core.messages import HumanMessage


def data_pipeline_agent(state: AgentState) -> dict:
    """
    数据管道智能体：加载 → 提取 → 特征工程。

    【流程】
    1. 加载CSV文件
    2. 对每台设备提取时间序列
    3. 计算22维特征向量
    4. 返回结构化的数据字典

    【输出】
    state.raw_data:     {设备ID: {time_series: {...}, ...}}
    state.feature_vectors: {设备ID: {特征名: 值, ...}}
    """
    device_ids = state.get("device_ids", [])

    # Step 1: 加载数据
    print("[DataPipeline] 加载数据...")
    df = load_machine_logs()

    # Step 2: 提取每台设备的数据
    print(f"[DataPipeline] 提取 {len(device_ids)} 台设备数据...")
    device_data = {}
    for did in device_ids:
        data = get_device_data(df, did)
        if "error" not in data:
            device_data[did] = data
            print(f"  [设备 {did}] {data['log_count']} 条记录")
        else:
            print(f"  [设备 {did}] 无数据，跳过")

    if not device_data:
        return {"error": "所有设备均无数据"}

    # Step 3: 计算22维特征
    print("[DataPipeline] 计算22维特征...")
    features = compute_all_devices_features(device_data)

    # Step 4: 输出统计
    feature_count = len(features.get(device_ids[0], {})) if device_ids else 0
    print(f"[DataPipeline] 完成！{len(device_data)} 台设备，每台 {feature_count} 维特征")

    return {
        "raw_data": device_data,
        "feature_vectors": features,
        "current_device": device_ids[0] if device_ids else None,
        "messages": [HumanMessage(
            content=f"📊 DataPipeline: 处理完成 {len(device_data)} 台设备数据，"
                    f"特征维度 {feature_count}"
        )],
    }
