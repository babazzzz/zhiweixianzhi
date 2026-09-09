"""Skill layer — every capability of the Agent is one discoverable skill package.

目录里**只允许**出现包目录 + ``base.py`` + ``__init__.py``；
历史遗留的平铺 ``*.py`` 函数文件已全部迁入同名包，避免两套风格并存。

典型用法::

    from src.skills import SkillRegistry

    SkillRegistry.discover()                          # 进程启动时调用一次
    SkillRegistry.describe_all()                      # 给 /api/skills 用
    SkillRegistry.run("rul_weibull", payload)         # 统一执行入口（带审计）
"""

from __future__ import annotations

from .base import BaseSkill, SkillExample, SkillMeta, SkillRegistry
from .anomaly_detect import AnomalyResult, detect_anomaly
from .rul_weibull import RULResult, predict_rul

__all__ = [
    "AnomalyResult", "BaseSkill", "RULResult", "SkillExample", "SkillMeta", "SkillRegistry",
    "detect_anomaly", "predict_rul",
]
