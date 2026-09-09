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
    # 关键章节齐全（对前端锚点 / 竞赛演示很关键）
    for section in ("一、设备概况", "二、诊断结论", "三、根因机理与处置规程", "六、维护建议"):
        assert section in md
    # 头部应能追溯审批/诊断来源
    assert "NOTIF-9" in md and "DIA-TEST-1" in md and "管理员" in md
    # data_sources 标注了数据出处
    assert any("fmeca_rules" in s for s in out["data_sources"])


def test_prev_maintenance_path_without_diagnostic(memory_db):
    """无诊断记录时，应仍生成一份“预防性维护”报告而不报错。"""
    _seed_machine(memory_db)
    skill = MaintenanceReportSkill(database=memory_db)
    out = skill.run({"machine_id": "CNC_036", "approved_by": "管理员", "include_history": False})
    assert out["report_id"].startswith("RPT-")
    assert "维护建议" in out["markdown"]
    assert "预防性" in out["summary"] or "暂无诊断" in out["summary"]
