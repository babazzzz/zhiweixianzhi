"""Application configuration and backwards-compatible public exports."""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(PROJECT_ROOT / ".env")

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./data/zhiwei.db")
_configured_data_dir = PROJECT_ROOT / os.getenv("DATA_DIR", "data")
DATA_DIR = _configured_data_dir.parent if _configured_data_dir.name.lower() == "raw" else _configured_data_dir
LOG_DIR = PROJECT_ROOT / os.getenv("LOG_DIR", "data/logs")

# ==========================================
# 认证与权限（JWT）
#   * AUTH_SECRET：JWT 签名密钥，必须通过 .env 提供（勿使用默认值上线）；
#   * ADMIN_USERNAME / ADMIN_PASSWORD：首次启动播种的管理员凭据；
#   * 角色三级：admin（全部）/ operator（读 + 运维写）/ viewer（只读）。
# ==========================================
AUTH_SECRET = os.getenv("AUTH_SECRET", "")
JWT_ALGORITHM = "HS256"
JWT_EXPIRE_MINUTES = int(os.getenv("JWT_EXPIRE_MINUTES", "720"))
ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "admin")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "")
ROLES: tuple[str, ...] = ("admin", "operator", "viewer")
SYSTEM_CONFIG: dict[str, int | str | Path] = {
    "max_parallel_devices": int(os.getenv("MAX_PARALLEL_DEVICES", "15")),
    "log_level": os.getenv("LOG_LEVEL", "INFO"),
    "data_dir": DATA_DIR / "raw",
    "max_self_check_retries": 3,
}
DATA_FILES: dict[str, Path] = {
    "machine_logs": DATA_DIR / "raw" / "MACHINE_LOG_DATA._2025.csv",
    "machine_summary": DATA_DIR / "raw" / "MACHINE_SUMMARY_DATA._2025.csv",
}
SENSOR_COLUMNS: tuple[str, ...] = (
    "op_voltage", "op_amperage", "op_temperature", "rotor_speed", "vibration_rms",
    "motor_power", "cutting_force", "feed_force", "spindle_load", "coolant_temp",
    "coolant_pressure", "oil_pressure", "noise_level", "tool_wear", "surface_roughness",
    "position_error", "power_factor", "harmonic_distortion", "bearing_temp",
    "motor_vibration", "ambient_temp", "humidity",
)

from .model_routing import AGENT_ROUTING, EMBEDDING_MODEL, get_agent_config

# ==========================================
# 故障模型定义（旧demo系统 diagnosis.py 使用）
# 与 rag.py 知识库中的 F01-F10 保持一致
# ==========================================
FAULT_MODELS: list[dict] = [
    {
        "id": "F001",
        "name": "主轴轴承磨损",
        "system": "主轴系统",
        "part": "轴承",
        "params": ["temperature", "current", "speed"],
        "severity": "critical",
    },
    {
        "id": "F002",
        "name": "切削刀具磨损",
        "system": "刀具系统",
        "part": "刀片",
        "params": ["current"],
        "severity": "high",
    },
    {
        "id": "F003",
        "name": "冷却系统故障",
        "system": "冷却系统",
        "part": "冷却泵",
        "params": ["temperature"],
        "severity": "high",
    },
    {
        "id": "F004",
        "name": "进给系统卡滞",
        "system": "进给系统",
        "part": "丝杠",
        "params": ["current", "speed"],
        "severity": "high",
    },
    {
        "id": "F005",
        "name": "导轨润滑不足",
        "system": "导轨系统",
        "part": "导轨",
        "params": ["temperature", "current"],
        "severity": "medium",
    },
    {
        "id": "F006",
        "name": "电机过热",
        "system": "驱动系统",
        "part": "伺服电机",
        "params": ["temperature", "current", "voltage"],
        "severity": "critical",
    },
    {
        "id": "F007",
        "name": "电源模块老化",
        "system": "电气系统",
        "part": "电源模块",
        "params": ["voltage", "current"],
        "severity": "medium",
    },
    {
        "id": "F008",
        "name": "传感器漂移",
        "system": "传感系统",
        "part": "温度传感器",
        "params": ["temperature"],
        "severity": "low",
    },
    {
        "id": "F009",
        "name": "液压系统泄漏",
        "system": "液压系统",
        "part": "液压管路",
        "params": ["current", "speed"],
        "severity": "high",
    },
    {
        "id": "F010",
        "name": "传动皮带松弛",
        "system": "传动系统",
        "part": "皮带",
        "params": ["speed", "current"],
        "severity": "medium",
    },
]

# ==========================================
# 风险等级配置（旧demo系统 decision.py 使用）
# ==========================================
RISK_LEVELS: dict[str, dict] = {
    "critical": {
        "label": "危急",
        "action": "立即停机检修",
        "color": "#ef4444",
    },
    "high": {
        "label": "高风险",
        "action": "24小时内安排预防性维护",
        "color": "#f97316",
    },
    "medium": {
        "label": "中风险",
        "action": "72小时内纳入维护计划",
        "color": "#eab308",
    },
    "low": {
        "label": "低风险",
        "action": "下次定期保养时检查",
        "color": "#22c55e",
    },
}

__all__ = [
    "ADMIN_PASSWORD", "ADMIN_USERNAME", "AGENT_ROUTING", "AUTH_SECRET", "DATABASE_URL", "DATA_DIR",
    "DATA_FILES", "EMBEDDING_MODEL", "FAULT_MODELS", "JWT_ALGORITHM", "JWT_EXPIRE_MINUTES",
    "LOG_DIR", "PROJECT_ROOT", "RISK_LEVELS", "ROLES", "SENSOR_COLUMNS",
    "SYSTEM_CONFIG", "get_agent_config",
]
