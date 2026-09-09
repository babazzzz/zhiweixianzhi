"""
==========================================
 数据加载模块 — data_loader.py
==========================================
【学习要点】
这个模块负责从CSV文件加载原始设备数据。

一个典型的 CNC 设备日志包含 5 个核心参数：
- temperature:  温度（℃）——反映设备热状态
- current:      电流（A）——反映负载情况
- voltage:      电压（V）——反映电源稳定性
- speed:        转速（rpm）——反映机械状态
- power:        功率（W）= 电流 × 电压

【数据格式说明】
原项目的CSV文件包含近百台CNC设备的日志数据，
每行是一条设备日志记录，包含设备ID、时间戳和5个参数。
CSV 列名: timestamp, equipment_id, failure_type, current, temperature, voltage, speed
"""

import pandas as pd
import numpy as np
from pathlib import Path
from typing import Optional
from ..config import DATA_FILES, SYSTEM_CONFIG


def load_machine_logs(filepath: Optional[Path] = None) -> pd.DataFrame:
    """
    加载 CNC 设备日志数据。

    【参数】
    filepath: CSV文件路径，默认使用配置中的路径

    【返回】
    DataFrame，包含所有设备的日志记录
    """
    path = filepath or DATA_FILES["machine_logs"]
    if not path.exists():
        print(f"[警告] 数据文件不存在: {path}")
        print("[提示] 将使用模拟数据继续")
        return _generate_sample_data()

    df = pd.read_csv(path, encoding="utf-8")
    # 统一列名为英文（原项目可能用中文列名）
    df.columns = [c.strip() for c in df.columns]
    print(f"[数据] 已加载 {len(df)} 条记录，{len(df.columns)} 列")
    id_col = _find_id_column(df)
    print(f"[数据] 设备数: {df[id_col].nunique()}  (ID列: {id_col})")
    return df


def get_device_data(
    df: pd.DataFrame,
    device_id: str,
    time_col: str = "timestamp",
) -> dict:
    """
    提取单台设备的数据。

    【参数】
    df: 原始数据DataFrame
    device_id: 设备ID，如 "CNC_007"
    time_col: 时间戳列名

    【返回】
    字典，包含该设备的所有日志记录（转为列表）
    """
    # 自动识别设备ID列
    id_col = _find_id_column(df)

    device_df = df[df[id_col] == device_id].copy()
    if device_df.empty:
        return {"error": f"设备 {device_id} 无数据", "logs": []}

    # 转换为字典列表
    logs = device_df.to_dict("records")

    # 提取5个核心参数的时间序列
    temp = _safe_extract(device_df, ["temperature", "Temperature", "temp", "TEMP"])
    current = _safe_extract(device_df, ["current", "Current", "CURRENT"])
    voltage = _safe_extract(device_df, ["voltage", "Voltage", "VOLTAGE"])
    speed = _safe_extract(device_df, ["speed", "Speed", "SPEED", "rotation_speed"])

    return {
        "device_id": device_id,
        "log_count": len(logs),
        "logs": logs,
        "time_series": {
            "temperature": temp,
            "current": current,
            "voltage": voltage,
            "speed": speed,
            "power": power if (power := (np.array(current) * np.array(voltage)).tolist()) else [],
        },
    }


def get_all_device_ids(df: pd.DataFrame) -> list[str]:
    """获取所有设备的ID列表"""
    id_col = _find_id_column(df)
    return sorted(df[id_col].unique().tolist())


# ==================== 内部辅助函数 ====================

def _find_id_column(df: pd.DataFrame) -> str:
    """自动识别设备ID列名（兼容不同命名）"""
    candidates = ["device_id", "equipment_id", "Machine_ID", "machine_id", "设备ID", "设备编号"]
    for c in candidates:
        if c in df.columns:
            return c
    return df.columns[0]  # 默认取第一列


def _safe_extract(df: pd.DataFrame, candidates: list[str]) -> list:
    """安全提取列数据（自动匹配可能的列名）"""
    for c in candidates:
        if c in df.columns:
            return df[c].dropna().astype(float).tolist()
    return []


def _generate_sample_data() -> pd.DataFrame:
    """生成模拟数据（当真实数据不存在时使用）"""
    np.random.seed(42)
    n_records = 30  # 每台设备30条记录

    rows = []
    for device_id in [f"CNC_{i:03d}" for i in [7, 12, 23, 45, 67]]:
        base_temp = 45 + np.random.randn() * 5
        base_current = 12 + np.random.randn() * 2
        base_voltage = 380 + np.random.randn() * 10
        base_speed = 3000 + np.random.randn() * 200

        # 模拟退化趋势（随时间温度上升、电流增大）
        for t in range(n_records):
            degradation = t / n_records * 15  # 缓慢退化
            rows.append({
                "device_id": device_id,
                "timestamp": f"2025-06-01 {8 + t//4:02d}:{(t*15)%60:02d}:00",
                "temperature": round(base_temp + degradation * 0.5 + np.random.randn() * 2, 1),
                "current": round(base_current + degradation * 0.3 + np.random.randn() * 0.5, 2),
                "voltage": round(base_voltage + np.random.randn() * 3, 1),
                "speed": round(base_speed - degradation * 5 + np.random.randn() * 30, 0),
            })

    print(f"[模拟] 已生成 {len(rows)} 条模拟数据，5台设备")
    return pd.DataFrame(rows)
