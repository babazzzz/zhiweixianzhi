"""Maintenance cost-benefit skill package."""

from .schema import (
    CostBenefitInput, CostBenefitOutput, DecisionOption, FmecaEffort, MachineEconomics,
)
from .skill import CostBenefitSkill, analyze_cost_benefit

SKILL = CostBenefitSkill()

__all__ = [
    "SKILL", "CostBenefitInput", "CostBenefitOutput", "CostBenefitSkill", "DecisionOption",
    "FmecaEffort", "MachineEconomics", "analyze_cost_benefit",
]
