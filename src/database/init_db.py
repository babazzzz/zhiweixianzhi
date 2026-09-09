"""Initialize the local database from project-provided source material only."""

from __future__ import annotations

import csv
from datetime import datetime
from pathlib import Path

from src.config import DATA_FILES, PROJECT_ROOT
from src.database.connection import Database
from src.database.schemas import MachineMetadata, SensorReading

FMECA_RULES: tuple[dict[str, object], ...] = (
    {"fault_code": 1, "fault_name": "轴承磨损", "system_name": "主轴系统", "component": "主轴轴承", "evidence_signals_json": ["温度升高", "振动升高", "转速下降", "电流波动"], "physical_mechanism": "滚道疲劳或润滑不足导致摩擦与热量累积", "detection_method": "振动趋势、温度趋势与转速偏差联合检测", "sop_steps_json": ["受控停机", "内窥镜检查", "拆主轴", "更换轴承", "校准并跑合30分钟"], "required_parts_json": ["SKF 7014CDGA/P4A×2", "Kluber NBU15 200g"], "estimated_hours": 4.5, "engineer_level": "高级机械", "severity": "critical"},
    {"fault_code": 2, "fault_name": "电机过载", "system_name": "驱动传动", "component": "主轴电机", "evidence_signals_json": ["电流超过35A", "温度超过100℃"], "physical_mechanism": "过扭矩使绕组负荷和温升超过设计值", "detection_method": "电流-温度耦合趋势检测", "sop_steps_json": ["急停", "等待放电", "盘车", "绝缘测试", "更换或自整定"], "required_parts_json": ["伺服电机2kW"], "estimated_hours": 8.0, "engineer_level": "电气+机械", "severity": "emergency"},
    {"fault_code": 3, "fault_name": "温度过高", "system_name": "冷却润滑", "component": "散热风扇/热交换器", "evidence_signals_json": ["温度超过100℃", "电流保持正常"], "physical_mechanism": "散热不足使热量在主轴与电机周围积聚", "detection_method": "温度独立上升与冷却参数检查", "sop_steps_json": ["空转冷却", "检查冷却系统", "清洁散热器", "检查润滑油", "重启监控"], "required_parts_json": ["散热风扇24V", "PT100传感器", "切削液20L"], "estimated_hours": 2.0, "engineer_level": "初级", "severity": "warning"},
    {"fault_code": 4, "fault_name": "电压不稳", "system_name": "电气电源", "component": "外部供电/稳压器", "evidence_signals_json": ["电压超出210-240V", "电压波动增大"], "physical_mechanism": "电网波动或稳压器老化造成输入电压偏离", "detection_method": "端子电压和波动标准差检测", "sop_steps_json": ["检查电源线", "测端子电压", "更换电源模块", "检查稳压器", "监控5分钟"], "required_parts_json": ["电源模块220V/24V/10A"], "estimated_hours": 1.5, "engineer_level": "电气", "severity": "warning"},
    {"fault_code": 5, "fault_name": "电流异常", "system_name": "驱动传动", "component": "刀具/传动皮带", "evidence_signals_json": ["电流低于18A或高于35A"], "physical_mechanism": "刀具断裂、皮带打滑或切削参数异常改变负载", "detection_method": "电流阈值和负载相关性检测", "sop_steps_json": ["检查刀具", "检查皮带", "检查切削参数", "检查主轴", "校验电流互感器"], "required_parts_json": ["传动皮带SPZ-1000", "电流互感器"], "estimated_hours": 1.0, "engineer_level": "工艺+机械", "severity": "warning"},
    {"fault_code": 6, "fault_name": "转子故障", "system_name": "主轴系统", "component": "转子/编码器", "evidence_signals_json": ["转速偏差超过10%", "异响"], "physical_mechanism": "编码器反馈失真或轴承偏心导致速度控制不稳定", "detection_method": "转速偏差与振动联合检测", "sop_steps_json": ["检查编码器线", "清洁码盘", "检查轴承", "更换编码器", "检查变频器"], "required_parts_json": ["编码器2500PPR"], "estimated_hours": 3.0, "engineer_level": "高级机械", "severity": "critical"},
    {"fault_code": 7, "fault_name": "电源模块故障", "system_name": "电气电源", "component": "电源模块", "evidence_signals_json": ["无法启动", "电压为0或不稳"], "physical_mechanism": "内部稳压电路老化使输出无法稳定", "detection_method": "24V输出与下游短路检查", "sop_steps_json": ["检查断路器", "测24V输出", "更换电源模块", "检查下游短路"], "required_parts_json": ["电源模块"], "estimated_hours": 1.0, "engineer_level": "电气", "severity": "critical"},
    {"fault_code": 8, "fault_name": "散热系统故障", "system_name": "冷却润滑", "component": "风扇/滤网", "evidence_signals_json": ["温度持续升高", "电流正常"], "physical_mechanism": "风扇卡死或滤网堵塞降低热交换效率", "detection_method": "温度斜率与冷却系统巡检", "sop_steps_json": ["检查风扇", "清洁滤网", "检查热交换器", "更换风扇电机"], "required_parts_json": ["风扇电机"], "estimated_hours": 0.5, "engineer_level": "初级", "severity": "warning"},
    {"fault_code": 9, "fault_name": "复合故障", "system_name": "多系统", "component": "多部件", "evidence_signals_json": ["多个系统指标同时异常"], "physical_mechanism": "多条风险链在同一工况叠加", "detection_method": "优先级诊断：电源→轴承→冷却→编码器", "sop_steps_json": ["执行综合诊断流程", "隔离最高风险链", "组织跨专业维修"], "required_parts_json": [], "estimated_hours": 6.0, "engineer_level": "跨专业", "severity": "emergency"},
)

CASES: tuple[dict[str, object], ...] = (
    {"id": "CASE-001", "machine_id": "CNC_002", "fault_code": 7, "description": "电压222→229→220V抖动，电流下降，电源模块老化伴随轴承微磨。", "param_changes": {"op_voltage": "波动", "op_amperage": "下降"}, "root_cause": "电源模块老化与轴承微磨", "resolution": "更换电源模块并润滑轴承", "rul_hours": 6, "risk_path": "电源不稳→电流异常→过热→停机", "created_at": "2025-03-01T09:00:00+08:00"},
    {"id": "CASE-002", "machine_id": "CNC_029", "fault_code": 2, "description": "电流27A接近上限、温度84℃；瞬时切削负载过大，未形成故障。", "param_changes": {"op_amperage": "接近上限", "op_temperature": "正常"}, "root_cause": "瞬时切削负载过大", "resolution": "降低进给率10%", "rul_hours": None, "risk_path": "切削负载上升→电流升高", "created_at": "2025-03-11T09:00:00+08:00"},
    {"id": "CASE-003", "machine_id": "CNC_016", "fault_code": 1, "description": "温度78→68℃波动，电流波动，电压曾跌至207V；轴承早期磨损。", "param_changes": {"op_temperature": "波动", "op_voltage": "短暂下跌"}, "root_cause": "轴承早期磨损与间隙增大", "resolution": "检查轴承、补充润滑脂并备件待命", "rul_hours": 300, "risk_path": "轴承磨损→振动升高→主轴失效", "created_at": "2025-04-03T09:00:00+08:00"},
    {"id": "CASE-004", "machine_id": "CNC_096", "fault_code": 8, "description": "温度持续升高至90℃，电流正常，散热风扇卡死。", "param_changes": {"op_temperature": "持续上升", "op_amperage": "正常"}, "root_cause": "散热风扇卡死", "resolution": "更换风扇", "rul_hours": None, "risk_path": "风扇卡死→散热失效→温度升高", "created_at": "2025-05-10T09:00:00+08:00"},
)


def bootstrap_database(database: Database | None = None) -> Database:
    """Create schema and load only the CSV/text data already supplied with the project."""

    target = database or Database()
    target.initialize_schema()
    if target.count("machines") == 0:
        _load_machine_metadata(target, DATA_FILES["machine_summary"])
    if target.count("sensor_readings") == 0:
        _load_legacy_readings(target, DATA_FILES["machine_logs"])
    for rule in FMECA_RULES:
        target.upsert_fmeca(rule)
    for case in CASES:
        target.add_case(case)
    return target


def _load_machine_metadata(database: Database, source: Path) -> None:
    """Map the supplied machine summary CSV into the normalized machines table."""

    with source.open("r", encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            machine_id = row["Equipment.Id"].strip()
            sequence = int(machine_id.removeprefix("CNC_"))
            database.upsert_machine(MachineMetadata(
                id=machine_id, model=row["Equipment.Type"].strip(), manufacture_date=row["Month.of.Manufacture"].strip(),
                last_maintenance=row["Last Service Date"].strip() or None, next_maintenance=row["Next Service Date"].strip() or None,
                last_repair=row["Last Repair Date"].strip() or None, daily_output=int(float(row["Units Produced Per day"])),
                unit_cost=float(row["Unit Cost of Production"]), production_line=f"LINE-{((sequence - 1) // 20) + 1:02d}", status="running",
            ))


def _load_legacy_readings(database: Database, source: Path) -> None:
    """Load real legacy four-sensor readings without fabricating missing dimensions."""

    batch: list[SensorReading] = []
    with source.open("r", encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            timestamp = datetime.strptime(row["timestamp"].strip(), "%Y/%m/%d %H:%M")
            metrics = {"op_amperage": float(row["current"]), "op_temperature": float(row["temperature"]), "op_voltage": float(row["voltage"]), "rotor_speed": float(row["speed"])}
            batch.append(SensorReading(machine_id=row["equipment_id"].strip(), timestamp=timestamp, metrics=metrics, fault_code=int(float(row["failure_type"]))))
            if len(batch) >= 1000:
                database.insert_readings(batch)
                batch.clear()
    database.insert_readings(batch)


if __name__ == "__main__":
    bootstrap_database()
    print("Database initialized from supplied project data.")
