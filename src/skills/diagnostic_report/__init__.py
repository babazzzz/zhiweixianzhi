"""Diagnostic report rendering skill package."""

from .schema import DiagnosticReportInput, DiagnosticReportOutput, ReportDecision, ReportEvidence
from .skill import SEVERITY_LABELS, DiagnosticReportSkill, generate_report

SKILL = DiagnosticReportSkill()

__all__ = [
    "SKILL", "SEVERITY_LABELS", "DiagnosticReportInput", "DiagnosticReportOutput",
    "DiagnosticReportSkill", "ReportDecision", "ReportEvidence", "generate_report",
]
