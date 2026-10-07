"""Test design: the taxonomy, the explainable plan and the TestDesignerAgent."""

from agentlab.design.adaptive import WaveAnalysis, analyse_wave
from agentlab.design.designer import SUITES, TestDesignerAgent, check_suite, profile_hash, signature
from agentlab.design.models import BudgetEstimate, CoverageEntry, PlannedTest, PlanWarning, TestPlan
from agentlab.design.taxonomy import SECURITY_CATEGORIES, TAXONOMY, security_codes

__all__ = [
    "SECURITY_CATEGORIES",
    "SUITES",
    "TAXONOMY",
    "BudgetEstimate",
    "CoverageEntry",
    "PlanWarning",
    "PlannedTest",
    "TestDesignerAgent",
    "TestPlan",
    "WaveAnalysis",
    "analyse_wave",
    "check_suite",
    "profile_hash",
    "security_codes",
    "signature",
]
