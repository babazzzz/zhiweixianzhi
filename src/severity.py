"""严重度评分的**唯一口径**：把 (健康分, 故障码) 折算成 (健康分, 严重度)。

为什么单独成模块
----------------
设备总览、设备列表、单机详情、诊断智能体都要判定严重度。历史上
`/api/machines` 与 `/api/overview` 各写一套，导致同一台设备在两个页面等级不一致。
现在所有地方都必须调用 `severity_from_reading`，不要再复制这段分支逻辑。
"""

from __future__ import annotations

#: health_score 缺失时使用的中性基线。字段缺失 ≠ 设备离线——离线只由读数新鲜度决定。
NEUTRAL_HEALTH = 0.8


def severity_from_reading(health: float | None, fault_code: int) -> tuple[float, str]:
    """把一条读数折算成 (健康分, 严重度)。缺失健康分按中性基线处理。"""

    score = NEUTRAL_HEALTH if health is None else float(health)
    if fault_code > 0:
        severity = "critical" if score < 0.55 else "warning"
    elif score < 0.45:
        severity = "critical"
    elif score < 0.55:
        severity = "warning"
    else:
        severity = "normal"
    return score, severity
