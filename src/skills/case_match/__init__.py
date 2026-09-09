"""Historical case matching skill package."""

from .schema import CaseItem, CaseMatchInput, CaseMatchOutput
from .skill import CaseMatchSkill, match_cases

SKILL = CaseMatchSkill()

__all__ = ["SKILL", "CaseItem", "CaseMatchInput", "CaseMatchOutput", "CaseMatchSkill", "match_cases"]
