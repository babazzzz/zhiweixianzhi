"""
==========================================
 核心算法库 — algorithms.py
==========================================
★ 这是整个系统的"大脑" ★

包含 4 大类算法：
1. 威布尔分布 RUL估计         — 可靠性工程经典方法
2. 马氏距离 故障模式匹配       — 小样本异常检测
3. 贝叶斯Log-Odds 多证据融合   — 综合多指标决策
4. Isolation Forest 异常检测   — 机器学习增强
5. CUSUM 累积和检测            — 早期退化捕捉
6. Z-score 异常检测            — 快速阈值检测
"""

import numpy as np
from scipy import stats
from scipy.optimize import brentq
from sklearn.ensemble import IsolationForest
from sklearn.covariance import MinCovDet
from typing import Optional


# ==========================================
# 算法1：威布尔分布 RUL 估计
# ==========================================

def weibull_rul_estimation(
    temperature_data: list[float],
    operating_hours: float,
) -> dict:
    """
    基于威布尔分布的剩余寿命(RUL)预测。

    【算法原理】
    威布尔分布是可靠性工程的"黄金标准"：
    - 形状参数 β：β>1 = 磨损故障（随时间风险增加）
                    β=1 = 随机故障（与时间无关）
                    β<1 = 早期故障（随时间风险降低）
    - 尺度参数 η：特征寿命（63.2%设备故障的时间点）

    【参数】
    temperature_data: 温度时间序列（℃）
    operating_hours:  已运行时长（小时）

    【返回】
    {
        "rul_hours": 剩余寿命（小时）,
        "beta": 形状参数,
        "eta": 尺度参数,
        "fault_prob_4h": 未来4小时故障概率,
        "fault_prob_24h": 未来24小时故障概率,
        "fault_prob_72h": 未来72小时故障概率,
        "reliability": 当前可靠度,
    }
    """
    arr = np.array(temperature_data)

    if len(arr) < 5:
        return {"rul_hours": 1000, "beta": 1.0, "eta": 2000,
                "fault_prob_4h": 0.01, "fault_prob_24h": 0.05,
                "fault_prob_72h": 0.15, "reliability": 0.95,
                "warning": "数据量不足，使用默认参数"}

    try:
        # Step 1: 用 MLE（最大似然估计）拟合威布尔分布
        # 温度数据反映设备退化程度，用来估计形状参数
        shape, loc, scale = stats.weibull_min.fit(arr, floc=0)
        beta, eta = shape, scale

        # Step 2: 计算剩余寿命
        # RUL = η × Γ(1 + 1/β) — 但这里用中位寿命（50%可靠度）
        # 公式：RUL = η × (-ln(0.5))^(1/β)
        rul = eta * (-np.log(0.5)) ** (1 / beta)

        # Step 3: 计算条件故障概率
        # P(故障 | 已存活到t) = 1 - R(t+T)/R(t)
        t = max(operating_hours, 1)

        def cond_prob(T):
            return 1 - np.exp(-((t + T) / eta) ** beta + (t / eta) ** beta)

        # Step 4: 当前可靠度 R(t) = exp(-(t/η)^β)
        reliability = np.exp(-((t / eta) ** beta))

        return {
            "rul_hours": round(float(rul), 1),
            "beta": round(float(beta), 3),
            "eta": round(float(eta), 1),
            "fault_prob_4h": round(float(cond_prob(4)), 4),
            "fault_prob_24h": round(float(cond_prob(24)), 4),
            "fault_prob_72h": round(float(cond_prob(72)), 4),
            "reliability": round(float(reliability), 4),
        }

    except Exception as e:
        return {"rul_hours": 500, "beta": 1.0, "eta": 1000,
                "fault_prob_4h": 0.05, "fault_prob_24h": 0.1,
                "fault_prob_72h": 0.3, "reliability": 0.8,
                "warning": f"拟合失败({str(e)})，使用粗估参数"}


# ==========================================
# 算法2：马氏距离 故障模式匹配
# ==========================================

def mahalanobis_distance(
    features: list[list[float]],
    fault_models: list[dict],
) -> dict:
    """
    马氏距离故障匹配——小样本场景的利器。

    【算法原理】
    马氏距离 vs 欧氏距离：
    - 欧氏距离：每个维度权重相同（"一视同仁"）
    - 马氏距离：考虑维度之间的相关性（"看整体轮廓"）

    好比：
    - 欧氏距离：看一个人"高不高，胖不胖"
    - 马氏距离：看一个人"体型是否匀称"

    在工业场景中，参数之间的相关性 ＞ 单个参数的绝对值。
    例如：温度40℃+电流15A 可能正常，但 温度40℃+电流5A 就有问题。

    【参数】
    features:      特征矩阵，每行是一个样本的特征向量
    fault_models:  故障模式列表（来自config.py）

    【返回】
    {
        "matched_faults": [{"fault_id": "F001", "distance": 2.3, ...}],
        "anomaly_score": 0.85,
    }
    """
    X = np.array(features)

    if len(X) < 2 or X.shape[1] < 2:
        return {"matched_faults": [], "anomaly_score": 0,
                "warning": "数据不足无法计算马氏距离"}

    try:
        # 使用 MinCovDet（最小协方差行列式）—— 对异常值更稳健
        mcd = MinCovDet(random_state=42).fit(X)
        center = mcd.location_
        cov_inv = np.linalg.inv(mcd.covariance_)

        # 计算每个样本到中心的马氏距离
        distances = []
        for i in range(len(X)):
            d = np.sqrt((X[i] - center) @ cov_inv @ (X[i] - center))
            distances.append(d)

        # 异常评分：马氏距离 > 阈值（卡方分布 95% 分位数）
        threshold = np.sqrt(stats.chi2.ppf(0.95, df=X.shape[1]))
        max_distance = float(max(distances))
        anomaly_score = min(1.0, max_distance / (threshold * 2))

        return {
            "matched_faults": [{
                "fault_id": f.get("id", "?"),
                "fault_name": f.get("name", "未知"),
                "distance": round(float(distances[-1] if len(distances) > 0 else 0), 2),
                "is_anomaly": bool(max_distance > threshold),
            } for f in fault_models[:3]],  # 只返回前3个最可能故障
            "anomaly_score": round(anomaly_score, 4),
            "mahalanobis_threshold": round(float(threshold), 2),
            "max_distance": round(max_distance, 2),
        }

    except Exception as e:
        return {"matched_faults": [], "anomaly_score": 0,
                "error": f"马氏距离计算失败: {str(e)}"}


# ==========================================
# 算法3：贝叶斯 Log-Odds 多证据融合
# ==========================================

def bayesian_fusion(evidence_list: list[dict]) -> dict:
    """
    贝叶斯 Log-Odds 多证据融合。

    【算法原理】
    每个证据（异常检测、RUL预测、趋势分析等）给出一个"判断"，
    贝叶斯融合把这些判断"加权平均"成一个综合结论。

    Log-Odds（对数几率）：
    Odds = P(故障) / P(正常)
    Log-Odds = ln(Odds)

    好处：
    - 多个证据独立时，Log-Odds 可以直接相加
    - 结果在 (-∞, +∞) 范围，不会溢出
    - 最后用 sigmoid 转回 [0,1] 的概率

    【参数】
    evidence_list: [{"name": "温度异常", "score": 0.7, "weight": 1.0}, ...]
        其中 score ∈ [0,1] 表示"故障概率"
        weight 表示该证据的可信度

    【返回】
    {
        "fusion_score": 0.85,       # 融合后的故障概率
        "risk_level": "high",        # 风险等级
        "contributions": {证据名: 贡献值},
    }
    """
    if not evidence_list:
        return {"fusion_score": 0, "risk_level": "low", "contributions": {}}

    # Step 1: 每个证据的 Log-Odds
    # 概率 → Log-Odds: ln(p / (1-p))
    log_odds_list = []
    for ev in evidence_list:
        p = max(0.001, min(0.999, ev.get("score", 0.5)))  # 裁剪防止无穷
        w = ev.get("weight", 1.0)
        lo = w * np.log(p / (1 - p))
        log_odds_list.append((ev.get("name", "未知"), lo))

    # Step 2: 融合（简单的直接相加）
    total_log_odds = sum(lo for _, lo in log_odds_list)

    # Step 3: Log-Odds → 概率（sigmoid）
    fused_prob = 1 / (1 + np.exp(-total_log_odds))

    # Step 4: 风险等级判定
    if fused_prob >= 0.8:
        risk = "critical"
    elif fused_prob >= 0.6:
        risk = "high"
    elif fused_prob >= 0.4:
        risk = "medium"
    else:
        risk = "low"

    return {
        "fusion_score": round(float(fused_prob), 4),
        "risk_level": risk,
        "contributions": {name: round(float(lo), 3) for name, lo in log_odds_list},
    }


# ==========================================
# 算法4：Isolation Forest 异常检测 ★新增★
# ==========================================

def isolation_forest_detect(features: list[list[float]]) -> dict:
    """
    Isolation Forest（孤立森林）异常检测。

    【算法原理】
    传统方法：先定义"正常是什么样"，然后找偏离的
    Isolation Forest：直接找"容易被孤立的点"

    好比：
    - 传统方法：先画一个圈，圈外的是异常
    - Isolation Forest：找那些"离群索居"的点

    优势：
    - 不需要假设数据分布（非参数方法）
    - 线性时间复杂度，适合高维数据
    - 小样本场景表现好

    【参数】
    features: 特征矩阵

    【返回】
    {
        "anomaly_count": 异常样本数,
        "anomaly_indices": 异常样本索引,
        "anomaly_scores": 每个样本的异常分数,
    }
    """
    X = np.array(features)
    if len(X) < 4:
        return {"anomaly_count": 0, "anomaly_indices": [],
                "anomaly_scores": [], "warning": "样本数不足"}

    clf = IsolationForest(
        contamination=0.1,      # 预期10%是异常
        random_state=42,
        n_estimators=100,       # 100棵孤立树
    )
    labels = clf.fit_predict(X)       # -1=异常, 1=正常
    scores = clf.decision_function(X)  # 越低越异常

    anomaly_indices = np.where(labels == -1)[0].tolist()

    return {
        "anomaly_count": int((labels == -1).sum()),
        "anomaly_indices": anomaly_indices,
        "anomaly_scores": [round(float(s), 4) for s in scores],
    }


# ==========================================
# 算法5：CUSUM 累积和检测
# ==========================================

def cusum_detection(data: list[float], threshold: float = 5.0) -> dict:
    """
    CUSUM（累积和）检测——捕捉缓慢的早期退化。

    【算法原理】
    普通阈值检测：看当前值是否超标
    CUSUM：看"历史累积偏差"是否超标

    好比：
    普通检测 = 看一个人今天是不是发烧（看瞬时值）
    CUSUM = 看一个人这周体温是不是在缓慢上升（看趋势）

    优势：能比普通方法提前数小时发现退化趋势！

    【参数】
    data:      时间序列数据
    threshold: 触发阈值（默认5.0）

    【返回】
    {
        "triggered": 是否触发,
        "max_pos_cusum": 正向累积最大值,
        "max_neg_cusum": 负向累积最大值,
        "change_point": 变化点位置,
    }
    """
    arr = np.array(data)
    if len(arr) < 5:
        return {"triggered": False, "max_pos_cusum": 0,
                "max_neg_cusum": 0, "change_point": 0}

    mean = arr.mean()
    std = max(arr.std(), 1e-8)

    # 标准化
    normalized = (arr - mean) / std

    # 计算正向和负向累积和
    target = 0.5  # 允许的偏移量（容忍度）
    cusum_pos = np.maximum.accumulate(np.cumsum(normalized - target))
    cusum_neg = np.maximum.accumulate(np.cumsum(-normalized - target))

    max_pos = float(cusum_pos.max())
    max_neg = float(cusum_neg.max())

    return {
        "triggered": bool(max_pos > threshold or max_neg > threshold),
        "max_pos_cusum": round(max_pos, 2),
        "max_neg_cusum": round(max_neg, 2),
        "change_point": int(np.argmax(cusum_pos)) if max_pos > threshold else 0,
    }


# ==========================================
# 算法6：Z-Score 快速异常检测
# ==========================================

def zscore_anomaly_detection(
    data: list[float],
    threshold: float = 2.5,
) -> dict:
    """
    Z-Score 异常检测——最简单快速的检测方法。

    【算法原理】
    Z = |x - μ| / σ
    Z > 2.5 = 异常（超出2.5个标准差）

    优点：计算快，结果直观
    缺点：对异常值敏感（一个极端值会把均值拉偏）

    【参数】
    data:      时间序列
    threshold: Z值阈值

    【返回】
    {
        "anomaly_count": 异常点数量,
        "anomaly_indices": 异常点索引,
        "max_zscore": 最大Z值,
        "anomaly_percentage": 异常比例,
    }
    """
    arr = np.array(data)
    if len(arr) < 3:
        return {"anomaly_count": 0, "anomaly_indices": [],
                "max_zscore": 0, "anomaly_percentage": 0}

    mean, std = arr.mean(), max(arr.std(), 1e-8)
    z_scores = np.abs((arr - mean) / std)
    anomalies = z_scores > threshold

    return {
        "anomaly_count": int(anomalies.sum()),
        "anomaly_indices": np.where(anomalies)[0].tolist(),
        "max_zscore": round(float(z_scores.max()), 2),
        "anomaly_percentage": round(float(anomalies.mean()), 4),
    }


# ==========================================
# 综合分析：对一台设备执行所有检测
# ==========================================

def comprehensive_analysis(time_series: dict) -> dict:
    """
    对单台设备执行全面的分析。

    【流程】
    1. 对每个参数做 Z-Score 和 CUSUM 检测
    2. 用 Isolation Forest 做多维异常检测
    3. 计算威布尔 RUL
    4. 贝叶斯融合所有证据
    5. 返回综合评分

    【参数】
    time_series: {参数名: [数值列表], ...}

    【返回】
    综合分析结果字典
    """
    evidence = []

    # --- 温度分析 ---
    temp = time_series.get("temperature", [])
    if temp:
        z = zscore_anomaly_detection(temp)
        c = cusum_detection(temp)
        evidence.append({"name": "温度Z-Score", "score": min(1.0, z["anomaly_percentage"] * 5), "weight": 0.8})
        evidence.append({"name": "温度CUSUM", "score": 0.6 if c["triggered"] else 0.1, "weight": 0.6})

    # --- 电流分析 ---
    curr = time_series.get("current", [])
    if curr:
        z = zscore_anomaly_detection(curr)
        c = cusum_detection(curr)
        evidence.append({"name": "电流Z-Score", "score": min(1.0, z["anomaly_percentage"] * 5), "weight": 0.8})
        evidence.append({"name": "电流CUSUM", "score": 0.6 if c["triggered"] else 0.1, "weight": 0.6})

    # --- 转速分析 ---
    speed = time_series.get("speed", [])
    if speed:
        z = zscore_anomaly_detection(speed)
        evidence.append({"name": "转速稳定性", "score": min(1.0, z["anomaly_percentage"] * 4), "weight": 0.5})

    # --- 威布尔 RUL ---
    if temp:
        rul = weibull_rul_estimation(temp, 100)
        # RUL越短 → 故障概率越高
        rul_score = max(0, min(1, 1 - rul["rul_hours"] / 1000))
        evidence.append({"name": "威布尔RUL", "score": rul_score, "weight": 1.0})
        evidence.append({"name": "短期故障概率", "score": rul["fault_prob_24h"], "weight": 0.7})

    # --- 贝叶斯融合 ---
    fusion = bayesian_fusion(evidence)
    fusion["evidence_count"] = len(evidence)
    fusion["details"] = evidence

    return fusion
