"""维护报告 Skill 测试：验证「高风险获批 → 生成可审计报告并落库」这条关键链路。

用内存 SQLite（memory_db fixture），注入真实实现，但不碰生产 data/zhiwei.db。
"""

from __future__ import annotations

from src.database.schemas import MachineMetadata
from src.skills.maintenance_report.skill import MaintenanceReportSkill


def _seed_machine(db, machine_id: str = "CNC_036") -> None:
    db.upsert_machine(MachineMetadata(
        id=machine_id, model="VMC-850", manufacture_date="2023-01-01",
        last_maintenance="2025-06-01", next_maintenance="2025-07-01", last_repair=None,
        daily_output=120, unit_cost=8.5, production_line="L1", status="warning"))


def _seed_fmeca(db, fault_code: int = 1) -> None:
    db.upsert_fmeca({
        "fault_code": fault_code, "fault_name": "主轴轴承磨损", "system_name": "主轴系统",
        "component": "深沟球轴承",
        "evidence_signals_json": ["op_temperature", "vibration_rms"],
        "physical_mechanism": "润滑失效导致滚动体与滚道金属接触磨损",
        "detection_method": "振动频谱 + 温升趋势",
        "sop_steps_json": ["断电挂牌", "拆卸主轴箱侧盖", "更换轴承并加注润滑脂", "空载试运行复测振动"],
        "required_parts_json": ["深沟球轴承", "润滑脂", "密封圈"],
        "estimated_hours": 3.5, "engineer_level": "中级", "severity": "high",
    })


def test_generates_report_and_persists(memory_db):
    """给定设备 + FMECA + 诊断，应生成含 FMECA 处置章节的报告并落库。"""
    _seed_machine(memory_db)
    _seed_fmeca(memory_db)
    memory_db.save_diagnostic(
        diagnostic_id="DIA-TEST-1", machine_id="CNC_036", status="completed",
        severity="high", confidence=0.92,
        payload={"rule": {"fault_code": 1, "fault_name": "主轴轴承磨损"},
                 "anomaly": {"anomaly_dims": ["op_temperature", "vibration_rms"]}})

    skill = MaintenanceReportSkill(database=memory_db)
    out = skill.run({
        "machine_id": "CNC_036", "diagnostic_id": "DIA-TEST-1",
        "notification_id": "NOTIF-9", "approved_by": "管理员", "include_history": False,
    })

    # 输出契约完整
    assert out["report_id"].startswith("RPT-")
    assert out["machine_id"] == "CNC_036"
    assert out["title"] == "CNC_036 维护报告"
    # 报告落库，可回查
    saved = memory_db.get_report(out["report_id"])
    assert saved is not None and saved["markdown"] == out["markdown"]

    md = out["markdown"]
    # 关键章节齐全（对前端锚点 / 竞赛演示很关键）——章节号随报告增强后调整
    for section in ("一、设备概况", "二、诊断结论", "五、根因机理与处置规程", "十、维护建议与追溯"):
        assert section in md
    # 头部应能追溯审批/诊断来源
    assert "NOTIF-9" in md and "DIA-TEST-1" in md and "管理员" in md
    # data_sources 标注了数据出处
    assert any("fmeca_rules" in s for s in out["data_sources"])


def test_data_analysis_section_uses_fleet_baseline(memory_db):
    """给定 22 维读数与历史窗口，报告应产出「数据分析过程」偏差表。"""
    from datetime import datetime

    from src.database.schemas import SensorReading

    _seed_machine(memory_db)
    _seed_fmeca(memory_db)
    # 再造一台健康设备并写入读数，保证全厂基线有参照样本
    _seed_machine(memory_db, machine_id="CNC_037")
    memory_db.insert_readings([SensorReading(
        machine_id="CNC_037", timestamp=datetime(2025, 7, 14, 23, 30),
        metrics={"op_temperature": 80.0, "vibration_rms": 1.8}, health_score=0.95)])
    readings = [{"op_temperature": 80.0, "vibration_rms": 1.8} for _ in range(10)]
    memory_db.save_diagnostic(
        diagnostic_id="DIA-TEST-2", machine_id="CNC_036", status="completed",
        severity="critical", confidence=0.9,
        payload={
            "rule": {"fault_code": 1, "fault_name": "主轴轴承磨损"},
            "sensor_data": {"op_temperature": 99.0, "vibration_rms": 2.8},
            "history": readings,
            "anomaly_dims": ["op_temperature"],
            "health_score": 0.44,
            "anomaly_score": 0.56,
            "anomaly_type": "gradual",
        })

    out = MaintenanceReportSkill(database=memory_db).run({"machine_id": "CNC_036", "diagnostic_id": "DIA-TEST-2"})
    md = out["markdown"]
    assert "三、数据分析过程" in md
    # 表格列头 + 触发值都应出现在正文里（99 vs 基线 80 → 约 +23.8%）
    assert "全厂同型基线" in md and "op_temperature" in md and "99.0000" in md


def test_prev_maintenance_path_without_diagnostic(memory_db):
    """无诊断记录时，应仍生成一份“预防性维护”报告而不报错。"""
    _seed_machine(memory_db)
    skill = MaintenanceReportSkill(database=memory_db)
    out = skill.run({"machine_id": "CNC_036", "approved_by": "管理员", "include_history": False})
    assert out["report_id"].startswith("RPT-")
    assert "维护建议" in out["markdown"]
    assert "预防性" in out["summary"] or "暂无诊断" in out["summary"]
