"""Weibull RUL skill package — exported as a single ``SKILL`` instance."""

from .schema import RULOutput, RULInput
from .skill import RULResult, RULWeibullSkill, predict_rul

SKILL = RULWeibullSkill()

__all__ = ["SKILL", "RULOutput", "RULInput", "RULResult", "RULWeibullSkill", "predict_rul"]
