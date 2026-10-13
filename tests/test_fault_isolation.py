"""诊断正确性回归：故障隔离、同侪基线、严重度口径。

这些断言只喂纯 dict / 合成数据，不碰生产数据库与仿真器。目的是把三条容易静默回归的
约定固化下来：
1. "同侪偏差 → 故障码"的映射（历史上 3 台故障机全部被兜底规则误判成"轴承磨损"）；
2. 异常检测必须用同侪基线（用本机历史基线会把"全厂一起老化"的设备 100% 判成异常）；
3. 严重度必须走 `src/severity.py` 的单一口径（仪表盘与诊断结论共用）。
"""

from __future__ import annotations

from src.agents.diagnostic_agent import _select_fault_code
from src.severity import severity_from_reading
from src.skills.anomaly_detect import detect_anomaly, peer_baseline_from_machines

# 数据来自 data/zhiwei.db 实测：仿真器补建征兆后，6 台设备末位读数相对
# 94 台健康机中位数的偏差（%）。
_INJECTED_CASES: dict[int, dict[str, float]] = {
    7: {"op_voltage": -3.75, "harmonic_distortion": 57.2, "power_factor": -2.55},
    1: {"vibration_rms": 87.3, "position_error": 100.4, "op_temperature": 14.7},
    3: {"op_temperature": 13.3},
    8: {"coolant_temp": 29.5, "coolant_pressure": -10.4},
    2: {"op_amperage": 39.7, "spindle_load": 34.6},
    6: {"rotor_speed": -7.58, "harmonic_distortion": 35.7},
}


def test_injected_faults_map_to_expected_codes() -> None:
    """故障码 7/1/3/8/2/6 都能被各自独有的征兆命中。"""

    for expected, deviations in _INJECTED_CASES.items():
        latest = {"op_amperage": 36.3} if expected == 2 else {}
        assert _select_fault_code(set(), latest, deviations) == expected


def test_natural_bearing_wear_does_not_masquerade_as_rotor_fault() -> None:
    """自然磨损把转速压到 -4% 以下时，只要谐波没同步恶化就不能判转子故障。

    94 台健康机里有 14 台转速偏差低于 -4%（最低 -4.61%），单看转速会大面积误报。
    """

    deviations = {"rotor_speed": -4.61, "harmonic_distortion": -0.9}
    assert _select_fault_code(set(), {}, deviations) == 0


def test_temperature_fault_is_not_stolen_by_bearing_rule() -> None:
    """bearing_temp 由温度派生，不能作为轴承磨损判据，否则温度过高会被抢答。"""

    deviations = {"op_temperature": 13.3, "bearing_temp": 13.0}
    assert _select_fault_code(set(), {}, deviations) == 3


def test_healthy_machine_with_normal_deviations_returns_zero() -> None:
    """各维度都在自然波动范围内时不报故障。"""

    deviations = {"op_temperature": 4.3, "vibration_rms": 7.4, "rotor_speed": -3.0, "harmonic_distortion": 1.35}
    assert _select_fault_code(set(), {}, deviations) == 0


def test_peer_baseline_ignores_fleet_wide_drift() -> None:
    """同侪基线：全厂一起老化的设备不该被判异常。

    仿真器给 100 台都叠加了 14 天单调退化，用"本机历史前 40%"作基线会把每一台都判成
    异常（实测 94/94），进而被诊断兜底规则误判成"轴承磨损"。同侪比较才能消掉这个共同趋势。
    """

    machines = [{"latest_reading": {"health_score": 0.9, "metrics": {"op_temperature": 88.0 + i * 0.1}}} for i in range(20)]
    baseline = peer_baseline_from_machines(machines)
    # 本机读数相对两周前整体上移了 9.5℃，但相对同侪只差 0.6%
    history = [{"op_temperature": 80.0 + i * 0.05} for i in range(100)]

    assert detect_anomaly({"op_temperature": 89.5}, history, baseline).is_anomaly is False
    # 同样的读数，用旧的本机历史基线就会被判异常——这正是被修掉的那个 bug
    assert detect_anomaly({"op_temperature": 89.5}, history).is_anomaly is True


def test_peer_baseline_still_flags_a_real_outlier() -> None:
    """同侪基线不能把真异常也一起放过。"""

    machines = [{"latest_reading": {"health_score": 0.9, "metrics": {"coolant_temp": 28.0 + i * 0.01}}} for i in range(20)]
    baseline = peer_baseline_from_machines(machines)
    history = [{"coolant_temp": 28.0} for _ in range(100)]

    result = detect_anomaly({"coolant_temp": 36.0}, history, baseline)  # 比同侪高 28.6%
    assert "coolant_temp" in result.anomaly_dims
    assert result.is_anomaly is True


def test_severity_follows_the_single_shared_rule() -> None:
    """严重度必须走 src/severity.py 的同一口径（仪表盘与诊断结论共用）。"""

    assert severity_from_reading(0.24, 2)[1] == "critical"   # 故障机：低健康分
    assert severity_from_reading(0.90, 7)[1] == "warning"    # 有故障但健康分不低
    assert severity_from_reading(0.75, 0)[1] == "normal"
    assert severity_from_reading(None, 0)[1] == "normal"     # 健康分缺失 ≠ 离线
