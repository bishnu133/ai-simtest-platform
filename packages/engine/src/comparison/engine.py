"""
Comparison Engine - Compare two simulation reports side-by-side.
Shows what improved, what regressed, and generates a diff report.
"""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel, Field

from src.core.logging import get_logger

logger = get_logger(__name__)


# ============================================================
# Comparison Models
# ============================================================

class MetricDelta(BaseModel):
    """Change in a single metric between two runs."""
    metric: str
    before: float
    after: float
    delta: float  # after - before
    pct_change: float  # percentage change
    status: str  # "improved", "regressed", "unchanged"


class JudgeDelta(BaseModel):
    """Change in a judge's score between two runs."""
    judge_name: str
    before_score: float
    after_score: float
    delta: float
    status: str


class PersonaTypeDelta(BaseModel):
    """Change in persona type performance."""
    persona_type: str
    before_score: float
    after_score: float
    delta: float
    status: str


class ComparisonReport(BaseModel):
    """Full comparison between two simulation runs."""
    before_name: str
    after_name: str
    before_id: str = ""
    after_id: str = ""

    # Overall metrics
    summary_deltas: list[MetricDelta] = Field(default_factory=list)

    # Per-judge changes
    judge_deltas: list[JudgeDelta] = Field(default_factory=list)

    # Per-persona-type changes
    persona_type_deltas: list[PersonaTypeDelta] = Field(default_factory=list)

    # New and resolved failure patterns
    new_failures: list[str] = Field(default_factory=list)
    resolved_failures: list[str] = Field(default_factory=list)
    persistent_failures: list[str] = Field(default_factory=list)

    # New and resolved recommendations
    new_recommendations: list[str] = Field(default_factory=list)
    resolved_recommendations: list[str] = Field(default_factory=list)

    # Overall verdict
    overall_verdict: str = ""  # "improved", "regressed", "mixed", "unchanged"
    verdict_reason: str = ""


# ============================================================
# Comparison Engine
# ============================================================

class ComparisonEngine:
    """
    Compare two simulation summary.json reports and produce a diff.
    """

    def compare_files(
        self,
        before_path: str | Path,
        after_path: str | Path,
    ) -> ComparisonReport:
        """Compare two summary.json files."""
        before = self._load_summary(before_path)
        after = self._load_summary(after_path)
        return self.compare(before, after)

    def compare(self, before: dict, after: dict) -> ComparisonReport:
        """Compare two summary dicts (from summary.json exports)."""
        b_summary = before.get("summary", {})
        a_summary = after.get("summary", {})

        report = ComparisonReport(
            before_name=b_summary.get("simulation_name", "Before"),
            after_name=a_summary.get("simulation_name", "After"),
            before_id=b_summary.get("simulation_id", ""),
            after_id=a_summary.get("simulation_id", ""),
        )

        # Compare summary metrics
        metrics = [
            ("pass_rate", "Pass Rate"),
            ("average_score", "Average Score"),
            ("critical_failures", "Critical Failures"),
            ("warnings", "Warnings"),
            ("total_turns", "Total Turns"),
        ]
        for key, label in metrics:
            b_val = b_summary.get(key, 0)
            a_val = a_summary.get(key, 0)
            delta = a_val - b_val

            # For failures/warnings, lower is better
            invert = key in ("critical_failures", "warnings")

            if abs(delta) < 0.001:
                status = "unchanged"
            elif (delta < 0 and invert) or (delta > 0 and not invert):
                status = "improved"
            else:
                status = "regressed"

            pct = (delta / b_val * 100) if b_val != 0 else (100.0 if delta > 0 else 0.0)

            report.summary_deltas.append(MetricDelta(
                metric=label,
                before=b_val,
                after=a_val,
                delta=round(delta, 4),
                pct_change=round(pct, 1),
                status=status,
            ))

        # Compare judge scores
        b_judges = before.get("score_by_judge", {})
        a_judges = after.get("score_by_judge", {})
        all_judges = set(list(b_judges.keys()) + list(a_judges.keys()))

        for judge in sorted(all_judges):
            b_score = b_judges.get(judge, 0.0)
            a_score = a_judges.get(judge, 0.0)
            delta = a_score - b_score

            status = "unchanged"
            if delta > 0.02:
                status = "improved"
            elif delta < -0.02:
                status = "regressed"

            report.judge_deltas.append(JudgeDelta(
                judge_name=judge,
                before_score=round(b_score, 3),
                after_score=round(a_score, 3),
                delta=round(delta, 3),
                status=status,
            ))

        # Compare persona type scores
        b_types = before.get("score_by_persona_type", {})
        a_types = after.get("score_by_persona_type", {})
        all_types = set(list(b_types.keys()) + list(a_types.keys()))

        for ptype in sorted(all_types):
            b_score = b_types.get(ptype, 0.0)
            a_score = a_types.get(ptype, 0.0)
            delta = a_score - b_score

            status = "unchanged"
            if delta > 0.02:
                status = "improved"
            elif delta < -0.02:
                status = "regressed"

            report.persona_type_deltas.append(PersonaTypeDelta(
                persona_type=ptype,
                before_score=round(b_score, 3),
                after_score=round(a_score, 3),
                delta=round(delta, 3),
                status=status,
            ))

        # Compare failure patterns
        b_patterns = {
            fp.get("pattern_name", fp.get("description", ""))
            for fp in before.get("failure_patterns", [])
        }
        a_patterns = {
            fp.get("pattern_name", fp.get("description", ""))
            for fp in after.get("failure_patterns", [])
        }

        report.new_failures = sorted(a_patterns - b_patterns)
        report.resolved_failures = sorted(b_patterns - a_patterns)
        report.persistent_failures = sorted(b_patterns & a_patterns)

        # Compare recommendations
        b_recs = set(before.get("recommendations", []))
        a_recs = set(after.get("recommendations", []))
        report.new_recommendations = sorted(a_recs - b_recs)
        report.resolved_recommendations = sorted(b_recs - a_recs)

        # Overall verdict
        report.overall_verdict, report.verdict_reason = self._determine_verdict(report)

        return report

    def format_console(self, report: ComparisonReport) -> str:
        """Format comparison report as a rich console-friendly string."""
        lines = []
        lines.append(f"╔══════════════════════════════════════════════════════════╗")
        lines.append(f"║  COMPARISON: {report.before_name} → {report.after_name}")
        lines.append(f"╚══════════════════════════════════════════════════════════╝")
        lines.append("")

        # Overall verdict
        verdict_icon = {
            "improved": "✅ IMPROVED",
            "regressed": "❌ REGRESSED",
            "mixed": "⚠️  MIXED",
            "unchanged": "➖ UNCHANGED",
        }.get(report.overall_verdict, "❓ UNKNOWN")
        lines.append(f"  Overall: {verdict_icon}")
        lines.append(f"  Reason: {report.verdict_reason}")
        lines.append("")

        # Summary metrics
        lines.append("  METRIC CHANGES:")
        for d in report.summary_deltas:
            icon = "🟢" if d.status == "improved" else "🔴" if d.status == "regressed" else "⚪"
            sign = "+" if d.delta > 0 else ""
            if isinstance(d.before, float) and d.before <= 1.0:
                lines.append(
                    f"    {icon} {d.metric:22s}  {d.before:.1%} → {d.after:.1%}  ({sign}{d.delta:.1%})"
                )
            else:
                lines.append(
                    f"    {icon} {d.metric:22s}  {d.before} → {d.after}  ({sign}{d.delta})"
                )

        # Judge scores
        if report.judge_deltas:
            lines.append("")
            lines.append("  JUDGE SCORE CHANGES:")
            for jd in report.judge_deltas:
                icon = "🟢" if jd.status == "improved" else "🔴" if jd.status == "regressed" else "⚪"
                sign = "+" if jd.delta > 0 else ""
                lines.append(
                    f"    {icon} {jd.judge_name:22s}  {jd.before_score:.1%} → {jd.after_score:.1%}  ({sign}{jd.delta:.1%})"
                )

        # Failure patterns
        if report.resolved_failures:
            lines.append("")
            lines.append("  ✅ RESOLVED FAILURES:")
            for f in report.resolved_failures:
                lines.append(f"    - {f[:80]}")

        if report.new_failures:
            lines.append("")
            lines.append("  ❌ NEW FAILURES:")
            for f in report.new_failures:
                lines.append(f"    - {f[:80]}")

        if report.persistent_failures:
            lines.append("")
            lines.append("  ⚠️  PERSISTENT FAILURES:")
            for f in report.persistent_failures:
                lines.append(f"    - {f[:80]}")

        lines.append("")
        return "\n".join(lines)

    def save_comparison(
        self, report: ComparisonReport, output_path: str | Path
    ) -> Path:
        """Save comparison report as JSON."""
        path = Path(output_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w") as f:
            json.dump(report.model_dump(), f, indent=2, default=str)
        logger.info("comparison_saved", path=str(path))
        return path

    def _load_summary(self, path: str | Path) -> dict:
        """Load a summary.json file."""
        with open(Path(path)) as f:
            return json.load(f)

    def _determine_verdict(
        self, report: ComparisonReport
    ) -> tuple[str, str]:
        """Determine overall verdict from comparison data."""
        improved_count = sum(
            1 for d in report.summary_deltas if d.status == "improved"
        )
        regressed_count = sum(
            1 for d in report.summary_deltas if d.status == "regressed"
        )

        # Check key metrics specifically
        pass_rate_delta = next(
            (d for d in report.summary_deltas if d.metric == "Pass Rate"), None
        )
        score_delta = next(
            (d for d in report.summary_deltas if d.metric == "Average Score"), None
        )

        # Strong signals
        if pass_rate_delta and pass_rate_delta.delta > 0.05:
            return "improved", f"Pass rate improved by {pass_rate_delta.delta:.1%}"
        if pass_rate_delta and pass_rate_delta.delta < -0.05:
            return "regressed", f"Pass rate dropped by {abs(pass_rate_delta.delta):.1%}"

        if len(report.resolved_failures) > len(report.new_failures):
            return "improved", f"{len(report.resolved_failures)} failures resolved, {len(report.new_failures)} new"

        if len(report.new_failures) > len(report.resolved_failures):
            return "regressed", f"{len(report.new_failures)} new failures introduced"

        if improved_count > regressed_count:
            return "improved", f"{improved_count} metrics improved vs {regressed_count} regressed"
        elif regressed_count > improved_count:
            return "regressed", f"{regressed_count} metrics regressed vs {improved_count} improved"
        elif improved_count == 0 and regressed_count == 0:
            return "unchanged", "No significant changes detected"
        else:
            return "mixed", f"{improved_count} improved, {regressed_count} regressed"
