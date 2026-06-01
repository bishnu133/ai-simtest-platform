"""
Coverage Metrics Module — P2 #11

Measures how thorough a simulation test run was across 4 dimensions:
  1. Persona-Type Coverage  — Did the 70/20/10 distribution hold?
  2. Topic Coverage         — What % of defined topics were exercised?
  3. Scenario Coverage      — Which categories/difficulties were tested?
  4. Judge Coverage         — Did all 4 judges produce verdicts?

Place under: src/coverage/

Usage:
    from ai_simtest_engine.coverage import CoverageAnalyzer, CoverageConfig

    analyzer = CoverageAnalyzer()
    report = analyzer.analyze(personas, judged_conversations, config)
    print(report.overall_coverage)  # 0.0 - 1.0
    print(report.grade)             # "A", "B", "C", "D", "F"
"""

from ai_simtest_engine.coverage.models import (
    CoverageConfig,
    CoverageGrade,
    CoverageReport,
    PersonaTypeCoverage,
    TopicCoverage,
    ScenarioCoverage,
    JudgeCoverage,
)
from ai_simtest_engine.coverage.analyzer import CoverageAnalyzer
from ai_simtest_engine.coverage.constants import SCENARIO_METADATA
from ai_simtest_engine.coverage.coverage_html import generate_coverage_html, inject_coverage_into_report
from ai_simtest_engine.coverage.workflow_coverage import (
        WorkflowCoverage,
        WorkflowStepCoverage,
        analyze_workflow_coverage,
        load_workflow_coverage_from_exports,
        inject_workflow_coverage_into_report,
    )

__all__ = [
    "CoverageAnalyzer",
    "CoverageConfig",
    "CoverageGrade",
    "CoverageReport",
    "PersonaTypeCoverage",
    "TopicCoverage",
    "ScenarioCoverage",
    "JudgeCoverage",
    "SCENARIO_METADATA",
    "generate_coverage_html",
    "inject_coverage_into_report",
    "WorkflowCoverage",
    "WorkflowStepCoverage",
    "analyze_workflow_coverage",
    "load_workflow_coverage_from_exports",
    "inject_workflow_coverage_into_report",
]
