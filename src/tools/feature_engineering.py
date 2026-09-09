"""
==========================================
 22维特征工程 — feature_engineering.py
==========================================
【学习要点】
特征工程 = 把"原始数据"变成"有意义的特征"。

原始数据只有5个参数（温度、电流、电压、转速），
但设备故障的"迹象"可能藏在更复杂的关系里。

例如：
- 温度上升+电流平稳 = 散热故障（不是负载问题）
- 电流上升+转速下降 = 机械卡滞
- 电压电流同时波动 = 电源老化

特征工程的目标就是：从简单的原始数据中，
提取出能反映设备真实状态的"高质量特征"。

【特征分类】
5个基础参数 → 12维传统特征 + 10维增强特征 = 22维
"""

import numpy as np
from scipy.fft import fft
from scipy.stats import skew, kurtosis


def compute_22d_features(device_data: dict) -> dict:
    """
    计算单台设备的22维特征向量。

    【参数】
    device_data: get_device_data() 返回的设备数据字典
                 包含 time_series 字段

    【返回】
    特征字典，key=特征名，value=特征值
    """
    ts = device_data.get("time_series", {})
    if not ts:
        return {"error": "无时间序列数据"}

    temp = np.array(ts.get("temperature", []))
    curr = np.array(ts.get("current", []))
    volt = np.array(ts.get("voltage", []))
    speed = np.array(ts.get("speed", []))
    power = curr * volt  # 功率 = 电流 × 电压

    features = {}

    # ==================== 第一层：基础参数（5维） ====================
    # 计算每个参数的均值——反映设备的"稳态"工作点
    features["temp_mean"] = float(temp.mean()) if len(temp) > 0 else 0
    features["current_mean"] = float(curr.mean()) if len(curr) > 0 else 0
    features["voltage_mean"] = float(volt.mean()) if len(volt) > 0 else 0
    features["speed_mean"] = float(speed.mean()) if len(speed) > 0 else 0
    features["power_mean"] = float(power.mean()) if len(power) > 0 else 0

    # ==================== 第二层：工程衍生特征（7维） ====================
    # 这些特征反映了设备的"物理状态"

    # 1. 焦耳热 I²R —— 电流热效应，反映线圈/绕组的发热程度
    features["joule_heat"] = float((curr ** 2).mean()) if len(curr) > 0 else 0

    # 2. 功率因数 —— 有功功率/视在功率，反映电能利用效率
    features["power_factor"] = float(
        power.mean() / (curr.mean() * volt.mean() + 1e-8)
    ) if len(curr) > 0 and len(volt) > 0 else 0

    # 3. 散热效率 —— 温度波动/功率波动，反映散热系统状态
    features["thermal_efficiency"] = float(
        temp.std() / (power.std() + 1e-8)
    ) if len(temp) > 1 and len(power) > 1 else 0

    # 4. 负载指数 —— 电流/电压，反映设备负载强度
    features["load_index"] = float(
        curr.mean() / (volt.mean() + 1e-8)
    ) if len(curr) > 0 and len(volt) > 0 else 0

    # 5. 转速稳定性 —— 转速波动/电流波动
    # 转速不稳+电流稳定 = 机械问题，反之 = 电气问题
    features["speed_stability"] = float(
        speed.std() / (curr.std() + 1e-8)
    ) if len(speed) > 1 and len(curr) > 1 else 0

    # 6. 温升速率 —— 温度随时间的变化率
    # 正值 = 升温，越大 = 散热越差
    features["temp_rise_rate"] = float(
        np.polyfit(range(len(temp)), temp, 1)[0]
    ) if len(temp) > 2 else 0

    # 7. 电流-温度耦合 —— 电流和温度的相关性
    # 强正相关 = 正常（负载大→发热多）
    # 弱相关或负相关 = 异常（温度变化与负载无关）
    features["current_temp_coupling"] = float(
        np.corrcoef(curr, temp)[0, 1]
    ) if len(curr) > 2 and len(temp) > 2 else 0

    # ==================== 第三层：统计特征（4维） ★新增★ ====================
    # 这些特征反映数据的"分布形态"，能发现均值看不出的异常

    # 1. 变异系数（CV）—— 标准差/均值，反映数据离散程度
    # 温度CV变大 = 温度波动加剧 = 可能故障前兆
    features["cv_temperature"] = float(
        temp.std() / (temp.mean() + 1e-8)
    ) if len(temp) > 1 else 0

    # 2. 电流偏度 —— 反映电流分布的对称性
    # 正偏态 = 偶尔有大电流冲击
    features["skewness_current"] = float(skew(curr)) if len(curr) > 2 else 0

    # 3. 电压峰度 —— 反映电压分布的"尖锐程度"
    # 高峰度 = 电压有尖峰脉冲 = 电源问题
    features["kurtosis_voltage"] = float(kurtosis(volt)) if len(volt) > 2 else 0

    # 4. 转速四分位距比 —— IQR/均值，稳健的离散度度量
    # 对异常值不敏感，比标准差更稳定
    q75, q25 = np.percentile(speed, [75, 25]) if len(speed) > 2 else (0, 0)
    features["iqr_ratio_speed"] = float(
        (q75 - q25) / (speed.mean() + 1e-8)
    ) if len(speed) > 2 else 0

    # ==================== 第四层：频域特征（3维） ★新增★ ====================
    # 将时域信号变换到频域，发现周期性故障
    # 例如：轴承磨损会在特定频率上产生能量峰值

    if len(temp) > 4:
        # 对温度信号做傅里叶变换
        freq_spec = np.abs(fft(temp - temp.mean()))
        half = len(freq_spec) // 2
        freq_spec_half = freq_spec[1:half]  # 去掉直流分量和镜像

        # 1. 主频 —— 信号最强的频率分量
        # 正常设备有稳定的主频，故障时主频会偏移
        if len(freq_spec_half) > 0:
            features["dominant_freq"] = float(np.argmax(freq_spec_half) + 1)

            # 2. 频谱熵 —— 信号的能量分布均匀程度
            # 低熵 = 能量集中在少数频率（有规律）
            # 高熵 = 能量分散（混乱/随机）= 可能异常
            p = freq_spec_half[:50] / (freq_spec_half[:50].sum() + 1e-8)
            features["spectral_entropy"] = float(
                -np.sum(p * np.log(p + 1e-8))
            )

            # 3. 高频能量比 —— 高频段能量占总能量的比例
            # 轴承磨损、机械松动等故障会产生高频振动
            features["high_freq_energy_ratio"] = float(
                freq_spec[half:].sum() / (freq_spec.sum() + 1e-8)
            )
    else:
        features["dominant_freq"] = 0
        features["spectral_entropy"] = 0
        features["high_freq_energy_ratio"] = 0

    # ==================== 第五层：交叉耦合特征（3维） ★新增★ ====================
    # 不同参数之间的耦合关系——单个参数看不出的问题，交叉对比才能发现

    # 1. 温度-转速耦合 —— 温度随转速变化的关系
    # 正常：转速↑ → 发热↑ → 温度↑（正相关）
    # 异常：温度与转速无关 = 散热或其他问题
    features["temp_speed_coupling"] = float(
        np.corrcoef(temp, speed)[0, 1]
    ) if len(temp) > 2 and len(speed) > 2 else 0

    # 2. 电压-电流耦合 —— 电源质量与负载的关系
    # 强负相关 = 电源内阻大（老化征兆）
    features["voltage_current_coupling"] = float(
        np.corrcoef(volt, curr)[0, 1]
    ) if len(volt) > 2 and len(curr) > 2 else 0

    # 3. 功率-负载耦合
    features["power_load_coupling"] = float(
        np.corrcoef(power, np.full_like(power, features.get("load_index", 0)))[0, 1]
    ) if len(power) > 2 else 0

    return features


def compute_all_devices_features(device_data_map: dict) -> dict:
    """
    批量计算所有设备的22维特征。

    【参数】
    device_data_map: {device_id: device_data_dict, ...}

    【返回】
    {device_id: feature_dict, ...}
    """
    return {
        did: compute_22d_features(data)
        for did, data in device_data_map.items()
    }
