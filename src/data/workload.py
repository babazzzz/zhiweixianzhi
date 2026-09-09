"""真实工况建模：班次 / 换刀 / 维护窗口对设备负荷的确定性调制。

数据层回答（需求 7）的分项之一——"怎么更贴合现实"：

* 原仿真只有退化物理（Paris/Arrhenius/Taylor），负荷恒定，像"24 小时连轴转"。
* 本模块在时间轴上叠加确定性的工况节律：白班满负荷、夜班降载、班次交接
  短暂爬坡、每 8 小时换一次刀（刀具负荷归零再爬升）、每周三上午计划保养。
* 全部由 timestamp 推导，无随机数——同一时刻任何机器重算结果一致，
  保证"重启不产生新数据、数据可复现"的竞赛答辩口径。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True, slots=True)
class WorkloadContext:
    """One timestamp's operating regime and its modulation factors."""

    load_factor: float          # 总负荷系数（1.0 = 铭牌工况）
    in_shift: bool              # 是否在班次内
    tool_change_window: bool    # 是否处于换刀窗口（负载为 0）
    maintenance_window: bool    # 是否处于计划保养窗口
    note: str                   # 人类可读说明，写入数据血缘与前端提示


def workload_for(timestamp: datetime) -> WorkloadContext:
    """Derive the deterministic workload regime for one timestamp."""

    hour = timestamp.hour
    weekday = timestamp.weekday()  # 0=Monday

    # 每周三 08:00-10:00：计划保养窗口，整场停机点检
    if weekday == 2 and 8 <= hour < 10:
        return WorkloadContext(0.0, False, False, True, "计划保养窗口（周三上午）")
    # 每 8 小时的换刀窗口：02/10/18 点整点后 15 分钟
    if hour in {2, 10, 18} and timestamp.minute < 15:
        return WorkloadContext(0.0, True, True, False, "换刀窗口")

    # 两班制：白班 08-20 满负荷，夜班 20-08 降载 15%
    if 8 <= hour < 20:
        load = 1.0
        note = "白班满负荷"
    else:
        load = 0.85
        note = "夜班降载15%"

    # 班次开始后第一个小时为爬坡段（负荷逐步拉满，符合真实产线 ramp-up）
    if hour in {8, 20}:
        ramp = 0.85 + 0.15 * (timestamp.minute / 60.0)
        load = min(load, ramp)
        note = "班次交接爬坡"
    # 周末单班运行，整体负荷再降 10%
    if weekday >= 5:
        load *= 0.9
        note += "（周末单班）"

    return WorkloadContext(round(load, 3), True, False, False, note)


def modulate_metrics(metrics: dict[str, float], workload: WorkloadContext) -> dict[str, float]:
    """Apply the workload factors to a raw sensor reading (physical coupling).

    负荷对传感器的影响不是等比缩放：
    * 切削功率相关量（电流、振动）近似随负荷线性变化；
    * 热量相关量（操作侧温度）变化被热惯性缓冲，只体现 40% 的负荷差；
    * 冷却/润滑回路对负荷不敏感（闭环控制），仅受停机影响。
    """

    if workload.load_factor >= 1.0:
        return metrics
    factor = workload.load_factor
    modulated = dict(metrics)
    if factor <= 0.0:
        # 停机窗口：主轴停转，温度向环境温度回落
        for key in ("op_amperage", "rotor_speed", "vibration_rms"):
            if key in modulated:
                modulated[key] = round(modulated[key] * 0.05, 3)
        if "op_temperature" in modulated:
            modulated["op_temperature"] = round(28.0 + (modulated["op_temperature"] - 28.0) * 0.35, 2)
        return modulated
    if "op_amperage" in modulated:
        modulated["op_amperage"] = round(modulated["op_amperage"] * factor, 3)
    if "rotor_speed" in modulated:
        modulated["rotor_speed"] = round(modulated["rotor_speed"] * (0.9 + 0.1 * factor), 2)
    if "vibration_rms" in modulated:
        modulated["vibration_rms"] = round(modulated["vibration_rms"] * factor, 3)
    if "op_temperature" in modulated:
        heat_factor = 1.0 - (1.0 - factor) * 0.4
        modulated["op_temperature"] = round(modulated["op_temperature"] * heat_factor, 2)
    return modulated
