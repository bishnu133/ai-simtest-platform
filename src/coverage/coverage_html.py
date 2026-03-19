"""
Coverage HTML Section — generates the coverage section for the HTML report.

Injects a "Test Coverage" section into the existing report.html, placed
between Recommendations and Conversation Transcripts. Uses the same dark
theme and styling conventions as html_report.py.

Usage from CLI:
    from src.coverage.coverage_html import inject_coverage_into_report
    inject_coverage_into_report("reports/report.html", coverage_report)
"""

from __future__ import annotations

import html as html_mod
from pathlib import Path

from src.coverage.models import CoverageReport


def generate_coverage_html(report: CoverageReport) -> str:
    """Generate the HTML section for coverage metrics."""

    grade = report.grade.value
    score = report.overall_coverage
    grade_color = {
        "A": "#4ade80", "B": "#86efac", "C": "#facc15",
        "D": "#f87171", "F": "#ef4444",
    }.get(grade, "#9ca3af")

    # ── Dimension rows ──────────────────────────────────────────
    dim_rows = ""
    dim_labels = {
        "persona_type": ("Persona Types", "Did 70/20/10 distribution hold?"),
        "topic": ("Topics", "Were defined topics exercised?"),
        "scenario": ("Scenarios", "Category & difficulty breadth"),
        "judge": ("Judges", "All 4 judges active?"),
    }
    for dim_key, (label, desc) in dim_labels.items():
        dim_score = report.dimension_scores.get(dim_key, 0)
        weight = report.dimension_weights.get(dim_key, 0)
        if weight == 0:
            continue
        bar_pct = int(dim_score * 100)
        bar_color = "#4ade80" if dim_score >= 0.8 else "#facc15" if dim_score >= 0.6 else "#f87171"
        dim_rows += f"""
        <tr>
          <td style="padding:8px 12px;font-weight:600;">{html_mod.escape(label)}</td>
          <td style="padding:8px 12px;color:#9ca3af;font-size:0.85em;">{html_mod.escape(desc)}</td>
          <td style="padding:8px 12px;width:200px;">
            <div style="background:#1e293b;border-radius:4px;height:20px;overflow:hidden;">
              <div style="background:{bar_color};height:100%;width:{bar_pct}%;border-radius:4px;"></div>
            </div>
          </td>
          <td style="padding:8px 12px;text-align:right;font-weight:600;color:{bar_color};">{dim_score:.0%}</td>
          <td style="padding:8px 12px;text-align:right;color:#9ca3af;">{weight:.0%}</td>
        </tr>"""

    # ── Persona distribution table ──────────────────────────────
    persona_detail = ""
    if report.persona_type.type_counts:
        pt_rows = ""
        for ptype in sorted(report.persona_type.type_counts):
            count = report.persona_type.type_counts[ptype]
            actual = report.persona_type.type_percentages.get(ptype, 0)
            expected = report.persona_type.expected_percentages.get(ptype, 0)
            dev = report.persona_type.deviation.get(ptype, 0)
            dc = "#4ade80" if abs(dev) < 0.15 else "#facc15" if abs(dev) < 0.25 else "#f87171"
            pt_rows += f"""
            <tr>
              <td style="padding:4px 10px;">{html_mod.escape(ptype)}</td>
              <td style="padding:4px 10px;text-align:center;">{count}</td>
              <td style="padding:4px 10px;text-align:center;">{actual:.0%}</td>
              <td style="padding:4px 10px;text-align:center;">{expected:.0%}</td>
              <td style="padding:4px 10px;text-align:center;color:{dc};">{dev:+.0%}</td>
            </tr>"""
        persona_detail = f"""
        <div style="margin-top:1.2rem;">
          <h3 style="font-size:1em;color:#c084fc;margin-bottom:0.5rem;">Persona Distribution</h3>
          <table style="width:100%;border-collapse:collapse;font-size:0.9em;">
            <thead><tr style="border-bottom:1px solid #334155;">
              <th style="padding:4px 10px;text-align:left;">Type</th>
              <th style="padding:4px 10px;text-align:center;">Count</th>
              <th style="padding:4px 10px;text-align:center;">Actual</th>
              <th style="padding:4px 10px;text-align:center;">Expected</th>
              <th style="padding:4px 10px;text-align:center;">Deviation</th>
            </tr></thead>
            <tbody>{pt_rows}</tbody>
          </table>
        </div>"""

    # ── Gaps ────────────────────────────────────────────────────
    gaps_html = ""
    if report.gaps:
        items = "".join(
            f'<li style="padding:3px 0;color:#d1d5db;">{html_mod.escape(g)}</li>'
            for g in report.gaps[:15]
        )
        more = f'<li style="color:#9ca3af;">...and {len(report.gaps) - 15} more</li>' if len(report.gaps) > 15 else ""
        gaps_html = f"""
        <div style="margin-top:1.2rem;">
          <h3 style="font-size:1em;color:#f87171;margin-bottom:0.4rem;">Coverage Gaps ({len(report.gaps)})</h3>
          <ul style="margin:0;padding-left:1.5rem;">{items}{more}</ul>
        </div>"""

    # ── Recommendations ─────────────────────────────────────────
    recs_html = ""
    if report.recommendations:
        items = "".join(
            f'<li style="padding:3px 0;color:#d1d5db;">{html_mod.escape(r)}</li>'
            for r in report.recommendations[:5]
        )
        recs_html = f"""
        <div style="margin-top:1.2rem;">
          <h3 style="font-size:1em;color:#60a5fa;margin-bottom:0.4rem;">How to Improve Coverage</h3>
          <ul style="margin:0;padding-left:1.5rem;">{items}</ul>
        </div>"""

    return f"""
<!-- Test Coverage (P2 #11) -->
<section class="section">
  <h2>📊 Test Coverage</h2>
  <p class="section-hint">How thorough was this test run? Coverage measures persona diversity, topic breadth, scenario categories, and judge completeness.</p>

  <div style="display:flex;align-items:center;gap:1.5rem;margin-bottom:1.5rem;">
    <div style="background:linear-gradient(135deg,#0f172a,#1e293b);border:2px solid {grade_color};border-radius:12px;padding:1.5rem 2rem;text-align:center;min-width:120px;">
      <div style="font-size:2.5rem;font-weight:800;color:{grade_color};">{grade}</div>
      <div style="font-size:0.85rem;color:#9ca3af;">Grade</div>
    </div>
    <div style="flex:1;">
      <div style="font-size:1.2rem;font-weight:600;color:#e2e8f0;margin-bottom:0.5rem;">
        Overall Coverage: <span style="color:{grade_color};">{score:.0%}</span>
      </div>
      <div style="background:#1e293b;border-radius:6px;height:24px;overflow:hidden;">
        <div style="background:{grade_color};height:100%;width:{int(score*100)}%;border-radius:6px;"></div>
      </div>
    </div>
  </div>

  <table style="width:100%;border-collapse:collapse;margin-bottom:0.5rem;">
    <thead>
      <tr style="border-bottom:1px solid #334155;">
        <th style="padding:8px 12px;text-align:left;">Dimension</th>
        <th style="padding:8px 12px;text-align:left;">Measures</th>
        <th style="padding:8px 12px;text-align:left;">Score</th>
        <th style="padding:8px 12px;text-align:right;">Value</th>
        <th style="padding:8px 12px;text-align:right;">Weight</th>
      </tr>
    </thead>
    <tbody>{dim_rows}</tbody>
  </table>
  {persona_detail}
  {gaps_html}
  {recs_html}
</section>
"""


def inject_coverage_into_report(
    report_path: str | Path,
    coverage_report: CoverageReport,
) -> bool:
    """
    Inject coverage section into an existing HTML report.

    Inserts before the Conversation Transcripts section.
    Returns True on success, False on failure.
    """
    report_path = Path(report_path)
    if not report_path.exists():
        return False

    try:
        content = report_path.read_text(encoding="utf-8")
        section = generate_coverage_html(coverage_report)

        # Primary marker: <!-- Conversations --> comment
        marker = "<!-- Conversations -->"
        if marker in content:
            content = content.replace(marker, section + "\n" + marker)
            report_path.write_text(content, encoding="utf-8")
            return True

        # Fallback: find <section> before Conversation Transcripts <h2>
        h2_marker = "Conversation Transcripts</h2>"
        if h2_marker in content:
            idx = content.rfind("<section", 0, content.index(h2_marker))
            if idx >= 0:
                content = content[:idx] + section + "\n" + content[idx:]
                report_path.write_text(content, encoding="utf-8")
                return True

        # Last resort: before </body>
        if "</body>" in content:
            content = content.replace("</body>", section + "\n</body>")
            report_path.write_text(content, encoding="utf-8")
            return True

        return False
    except Exception:
        return False