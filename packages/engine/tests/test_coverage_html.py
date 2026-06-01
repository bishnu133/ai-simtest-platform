"""
Tests for coverage HTML report injection (P2 #11).
"""

try:
    import pytest
except ImportError:
    pass

import json
import tempfile
from pathlib import Path

from ai_simtest_engine.coverage.models import CoverageReport
from ai_simtest_engine.coverage.coverage_html import generate_coverage_html, inject_coverage_into_report


def _make_report(grade="B", score=0.78) -> CoverageReport:
    """Create a sample coverage report for testing."""
    return CoverageReport.from_dict({
        "overall_coverage": score,
        "grade": grade,
        "total_conversations": 20,
        "total_personas": 20,
        "dimension_scores": {
            "persona_type": 0.85, "topic": 0.90,
            "scenario": 0.60, "judge": 1.0,
        },
        "dimension_weights": {
            "persona_type": 0.25, "topic": 0.25,
            "scenario": 0.25, "judge": 0.25,
        },
        "gaps": ["No adversarial personas tested", "Scenario category 'memory' not tested"],
        "recommendations": ["Add adversarial personas", "Add --stress-memory"],
        "persona_type": {
            "score": 0.85, "type_counts": {"standard": 14, "edge_case": 4, "adversarial": 2},
            "type_percentages": {"standard": 0.70, "edge_case": 0.20, "adversarial": 0.10},
            "expected_percentages": {"standard": 0.70, "edge_case": 0.20, "adversarial": 0.10},
            "deviation": {"standard": 0.0, "edge_case": 0.0, "adversarial": 0.0},
            "missing_types": [],
        },
        "topic": {"score": 0.90, "defined": ["billing"], "covered": ["billing"], "uncovered": [], "mention_counts": {"billing": 5}},
        "scenario": {"score": 0.60, "categories_tested": ["safety"], "missing_categories": ["memory"], "requested_scenarios": ["prompt_injection"], "difficulties_tested": ["hard"], "stress_included": False},
        "judge": {"score": 1.0, "active_judges": ["grounding", "safety", "quality", "relevance"], "missing_judges": [], "suspicious_judges": [], "verdict_counts": {"grounding": 20}},
    })


SAMPLE_HTML = """<!DOCTYPE html>
<html><head><title>Test</title></head><body>
<!-- Recommendations -->
<section class="section">
  <h2>✅ Recommendations</h2>
</section>

<!-- Conversations -->
<section class="section">
  <h2>💬 Conversation Transcripts</h2>
</section>
</body></html>"""


class TestGenerateCoverageHTML:

    def test_contains_grade(self):
        html = generate_coverage_html(_make_report())
        assert "B" in html
        assert "78%" in html

    def test_contains_dimensions(self):
        html = generate_coverage_html(_make_report())
        assert "Persona Types" in html
        assert "Topics" in html
        assert "Judges" in html

    def test_contains_gaps(self):
        html = generate_coverage_html(_make_report())
        assert "adversarial" in html
        assert "Coverage Gaps" in html

    def test_contains_recommendations(self):
        html = generate_coverage_html(_make_report())
        assert "How to Improve Coverage" in html

    def test_contains_persona_distribution(self):
        html = generate_coverage_html(_make_report())
        assert "Persona Distribution" in html
        assert "standard" in html

    def test_empty_report_no_crash(self):
        html = generate_coverage_html(CoverageReport())
        assert "📊 Test Coverage" in html
        assert "F" in html

    def test_grade_F_color(self):
        html = generate_coverage_html(_make_report("F", 0.20))
        assert "#ef4444" in html

    def test_grade_A_color(self):
        html = generate_coverage_html(_make_report("A", 0.95))
        assert "#4ade80" in html

    def test_xss_escaping(self):
        report = _make_report()
        report.gaps = ["<script>alert('xss')</script>"]
        html = generate_coverage_html(report)
        assert "<script>" not in html
        assert "&lt;script&gt;" in html


class TestInjectCoverage:

    def test_inject_at_conversations_marker(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".html", delete=False) as f:
            f.write(SAMPLE_HTML)
            f.flush()
            result = inject_coverage_into_report(f.name, _make_report())
            assert result is True
            content = Path(f.name).read_text()
            assert "📊 Test Coverage" in content
            # Verify order: Coverage before Conversations
            cov_idx = content.index("Test Coverage")
            conv_idx = content.index("Conversation Transcripts")
            assert cov_idx < conv_idx

    def test_inject_preserves_existing(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".html", delete=False) as f:
            f.write(SAMPLE_HTML)
            f.flush()
            inject_coverage_into_report(f.name, _make_report())
            content = Path(f.name).read_text()
            assert "✅ Recommendations" in content
            assert "💬 Conversation Transcripts" in content

    def test_inject_nonexistent_file(self):
        result = inject_coverage_into_report("/nonexistent/path.html", _make_report())
        assert result is False

    def test_inject_fallback_body(self):
        """If no markers found, inject before </body>."""
        html = "<html><body><h1>Simple</h1></body></html>"
        with tempfile.NamedTemporaryFile(mode="w", suffix=".html", delete=False) as f:
            f.write(html)
            f.flush()
            result = inject_coverage_into_report(f.name, _make_report())
            assert result is True
            content = Path(f.name).read_text()
            assert "Test Coverage" in content

    def test_inject_with_real_coverage_data(self):
        """Test with data loaded from JSON (like the CLI does)."""
        data = {
            "overall_coverage": 0.37, "grade": "F",
            "total_conversations": 5, "total_personas": 0,
            "dimension_scores": {"persona_type": 0.0, "topic": 1.0, "scenario": 0.0, "judge": 0.95},
            "dimension_weights": {"persona_type": 0.38, "topic": 0.0, "scenario": 0.23, "judge": 0.38},
            "gaps": ["No standard personas tested"], "recommendations": ["Add personas"],
            "persona_type": {"score": 0.0, "type_counts": {}, "type_percentages": {}, "expected_percentages": {}, "deviation": {}, "missing_types": ["standard"]},
            "topic": {"score": 1.0, "defined": [], "covered": [], "uncovered": [], "mention_counts": {}},
            "scenario": {"score": 0.0, "categories_tested": [], "missing_categories": [], "requested_scenarios": [], "difficulties_tested": [], "stress_included": False},
            "judge": {"score": 0.95, "active_judges": ["grounding"], "missing_judges": [], "suspicious_judges": [], "verdict_counts": {"grounding": 14}},
        }
        report = CoverageReport.from_dict(data)
        with tempfile.NamedTemporaryFile(mode="w", suffix=".html", delete=False) as f:
            f.write(SAMPLE_HTML)
            f.flush()
            result = inject_coverage_into_report(f.name, report)
            assert result is True
