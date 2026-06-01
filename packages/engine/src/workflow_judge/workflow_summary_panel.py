"""
Workflow Summary Panel — Injects workflow aggregate metrics into the
top-level HTML report summary section.

This module solves the gap where the HTML report's stats row and charts
only showed the 4 core judge scores (grounding, safety, quality, relevance)
but omitted workflow evaluation results.  After injection, the report
prominently displays:

  1. Two new stat cards in the stats row (Workflow Pass Rate, Critical Violations)
  2. A Workflow Overview chart (bar chart per workflow with avg score)
  3. A Workflow Compliance status banner below the stats row

Usage from CLI _post_simulation_analysis():

    from src.workflow_judge.workflow_summary_panel import inject_workflow_summary_into_report

    inject_workflow_summary_into_report(
        html_path="reports/report.html",
        workflow_exports=[
            {
                "workflow": "Customer Service Workflow",
                "domain": "customer_service",
                "total_conversations": 7,
                "passed": 5,
                "failed": 2,
                "avg_score": 0.72,
                "critical_failures": 1,
                "results": [...],
            },
            ...
        ],
    )

Design choices:
  - Pure HTML/CSS/JS injection — no external dependencies
  - Uses the existing Chart.js CDN already loaded by html_report.py
  - Matches the dark theme CSS variables (--bg, --surface, --pass, --fail, etc.)
  - Idempotent — skips injection if the summary panel marker already exists
  - Graceful degradation — returns False if HTML structure is unexpected
"""
from __future__ import annotations

import html as html_mod
import json
from pathlib import Path
from typing import Any


# ── Marker to detect duplicate injection ─────────────────────────────────────
_PANEL_MARKER = "<!-- WorkflowSummaryPanel -->"


def inject_workflow_summary_into_report(
    html_path: str | Path,
    workflow_exports: list[dict[str, Any]],
) -> bool:
    """
    Inject workflow aggregate metrics into the top-level HTML report.

    Adds:
      1. Stat cards for Workflow Pass Rate and Critical Violations
      2. A "Workflow Compliance" status banner
      3. A Workflow Overview bar chart (one bar per workflow)

    Args:
        html_path: Path to the existing report.html
        workflow_exports: List of workflow export dicts (as saved to workflow_*.json).
            Each must have: workflow, total_conversations, passed, failed, avg_score,
            critical_failures.

    Returns:
        True if injection succeeded, False otherwise.
    """
    html_path = Path(html_path)
    if not html_path.exists():
        return False

    if not workflow_exports:
        return False

    try:
        content = html_path.read_text(encoding="utf-8")
    except Exception:
        return False

    # Idempotency guard
    if _PANEL_MARKER in content:
        return False

    # ── Compute aggregates ────────────────────────────────────────────────
    total_convos = sum(e.get("total_conversations", 0) for e in workflow_exports)
    total_passed = sum(e.get("passed", 0) for e in workflow_exports)
    total_failed = sum(e.get("failed", 0) for e in workflow_exports)
    total_critical = sum(e.get("critical_failures", 0) for e in workflow_exports)
    num_workflows = len(workflow_exports)

    if total_convos > 0:
        overall_pass_rate = total_passed / total_convos
    else:
        overall_pass_rate = 0.0

    weighted_scores = []
    for e in workflow_exports:
        n = e.get("total_conversations", 0)
        s = e.get("avg_score", 0.0)
        weighted_scores.append(n * s)
    overall_avg_score = sum(weighted_scores) / total_convos if total_convos > 0 else 0.0

    # Status determination
    if total_critical > 0:
        status_label = "CRITICAL VIOLATIONS"
        status_color = "#f87171"
        status_icon = "🚨"
    elif total_failed == 0 and total_convos > 0:
        status_label = "ALL WORKFLOWS PASSED"
        status_color = "#6ee7b7"
        status_icon = "✅"
    elif total_passed > 0:
        status_label = "PARTIAL COMPLIANCE"
        status_color = "#fbbf24"
        status_icon = "⚠️"
    else:
        status_label = "ALL WORKFLOWS FAILED"
        status_color = "#f87171"
        status_icon = "❌"

    pass_rate_class = _score_class(overall_pass_rate)
    avg_score_class = _score_class(overall_avg_score)

    # ── 1. Build stat cards HTML ──────────────────────────────────────────
    stat_cards_html = f"""
  <div class="stat-card">
    <div class="stat-value {pass_rate_class}">{overall_pass_rate:.0%}</div>
    <div class="stat-label">Workflow Pass Rate</div>
  </div>
  <div class="stat-card">
    <div class="stat-value {'fail' if total_critical > 0 else 'pass'}">{total_critical}</div>
    <div class="stat-label">Workflow Violations</div>
  </div>"""

    # ── 2. Build compliance banner HTML ───────────────────────────────────
    wf_names_list = ", ".join(
        html_mod.escape(e.get("workflow", "Unknown")[:40]) for e in workflow_exports
    )
    banner_html = f"""
{_PANEL_MARKER}
<!-- Workflow Compliance Banner -->
<div style="margin:0 2rem 1rem;padding:1rem 1.25rem;background:var(--surface, #151822);border:1px solid var(--border, #2a2f42);border-left:4px solid {status_color};border-radius:10px;display:flex;align-items:center;gap:1rem;flex-wrap:wrap;">
  <div style="font-size:1.5rem;">{status_icon}</div>
  <div style="flex:1;min-width:200px;">
    <div style="font-size:1rem;font-weight:700;color:{status_color};">{status_label}</div>
    <div style="font-size:0.82rem;color:var(--dim, #8890a8);margin-top:2px;">
      {num_workflows} workflow(s) evaluated &middot; {total_passed}/{total_convos} conversations passed &middot; Avg score: <span style="color:{_score_color(overall_avg_score)};font-weight:600;">{overall_avg_score:.2f}</span>
    </div>
    <div style="font-size:0.75rem;color:var(--dim, #8890a8);margin-top:2px;">
      Workflows: {wf_names_list}
    </div>
  </div>
  <div style="display:flex;gap:1rem;flex-wrap:wrap;">
    <div style="text-align:center;">
      <div style="font-size:1.3rem;font-weight:700;color:#6ee7b7;">{total_passed}</div>
      <div style="font-size:0.65rem;color:var(--dim, #8890a8);text-transform:uppercase;">Passed</div>
    </div>
    <div style="text-align:center;">
      <div style="font-size:1.3rem;font-weight:700;color:#f87171;">{total_failed}</div>
      <div style="font-size:0.65rem;color:var(--dim, #8890a8);text-transform:uppercase;">Failed</div>
    </div>
    <div style="text-align:center;">
      <div style="font-size:1.3rem;font-weight:700;color:{'#f87171' if total_critical > 0 else '#6ee7b7'};">{total_critical}</div>
      <div style="font-size:0.65rem;color:var(--dim, #8890a8);text-transform:uppercase;">Critical</div>
    </div>
  </div>
</div>
"""

    # ── 3. Build workflow overview chart ───────────────────────────────────
    chart_labels = json.dumps([e.get("workflow", "Unknown")[:30] for e in workflow_exports])
    chart_scores = json.dumps([round(e.get("avg_score", 0) * 100, 1) for e in workflow_exports])
    chart_colors = json.dumps([_score_color(e.get("avg_score", 0)) for e in workflow_exports])

    chart_card_html = f"""
  <div class="chart-card">
    <h3>Workflow Scores</h3>
    <canvas id="workflowOverviewChart" height="220"></canvas>
  </div>"""

    chart_js = f"""
// Workflow Overview Chart (Summary Panel)
new Chart(document.getElementById('workflowOverviewChart'), {{
  type: 'bar',
  data: {{
    labels: {chart_labels},
    datasets: [{{
      label: 'Score %',
      data: {chart_scores},
      backgroundColor: {chart_colors},
      borderRadius: 6,
      barThickness: 40,
    }}]
  }},
  options: {{
    indexAxis: 'y',
    responsive: true,
    plugins: {{ legend: {{ display: false }} }},
    scales: {{
      x: {{ min: 0, max: 100, grid: {{ color: '#1c2030' }}, ticks: {{ color: '#8890a8' }} }},
      y: {{ grid: {{ display: false }}, ticks: {{ color: '#e2e4ec', font: {{ size: 13 }} }} }}
    }}
  }}
}});
"""

    # ── Injection logic ───────────────────────────────────────────────────

    success = False

    # 1. Inject stat cards into stats-row (before closing </div>)
    stats_row_end = content.find("</div>", content.find("stats-row"))
    if stats_row_end > 0:
        # Find the last stat-card closing </div> inside the stats-row
        stats_row_start = content.find("stats-row")
        # Walk backwards from stats_row_end to find the insertion point
        # We want to insert before the closing </div> of the stats-row container
        last_card_marker = content.rfind("</div>", stats_row_start, stats_row_end)
        if last_card_marker > 0:
            # Actually, let's find the end of stats-row div properly
            # The stats-row is a div with multiple stat-card children
            # We need to insert new cards before the stats-row closing tag
            insert_pos = _find_stats_row_closing(content)
            if insert_pos > 0:
                content = content[:insert_pos] + stat_cards_html + "\n" + content[insert_pos:]
                success = True

    # 2. Inject banner after stats-row / before charts-row
    charts_marker = 'class="charts-row"'
    if charts_marker in content:
        idx = content.index(charts_marker)
        # Find the opening < of this div
        div_start = content.rfind("<div", 0, idx)
        if div_start > 0:
            content = content[:div_start] + banner_html + "\n" + content[div_start:]
            success = True

    # 3. Inject chart card into charts-row
    # Find the closing </div> of charts-row
    charts_row_marker = 'class="charts-row"'
    if charts_row_marker in content:
        cr_start = content.index(charts_row_marker)
        # Count divs to find the matching closing tag
        cr_close = _find_closing_div(content, cr_start)
        if cr_close > 0:
            content = content[:cr_close] + chart_card_html + "\n" + content[cr_close:]
            success = True

    # 4. Inject Chart.js code before closing </script>
    # Find the last </script> before </body>
    body_end = content.rfind("</body>")
    if body_end > 0:
        last_script_end = content.rfind("</script>", 0, body_end)
        if last_script_end > 0:
            content = content[:last_script_end] + "\n" + chart_js + "\n" + content[last_script_end:]
            success = True

    if success:
        try:
            html_path.write_text(content, encoding="utf-8")
            return True
        except Exception:
            return False

    return False


def _find_stats_row_closing(content: str) -> int:
    """Find the position of the closing </div> for the stats-row container."""
    marker = 'class="stats-row"'
    idx = content.find(marker)
    if idx < 0:
        return -1
    return _find_closing_div(content, idx)


def _find_closing_div(content: str, start_after: int) -> int:
    """
    Find the closing </div> tag that matches the <div containing start_after position.

    Walks forward from the opening <div counting nested depth.
    Returns the position of the matching </div> (before the tag).
    """
    # Find the opening <div
    div_start = content.rfind("<div", 0, start_after + 1)
    if div_start < 0:
        div_start = content.find("<div", start_after)
        if div_start < 0:
            return -1

    depth = 0
    pos = div_start
    while pos < len(content):
        next_open = content.find("<div", pos + 1)
        next_close = content.find("</div>", pos + 1)

        if next_close < 0:
            return -1  # Malformed HTML

        if next_open >= 0 and next_open < next_close:
            # Found a nested opening div before next close
            depth += 1
            pos = next_open
        else:
            # Found a closing div
            if depth == 0:
                return next_close
            depth -= 1
            pos = next_close

    return -1


def _score_class(score: float) -> str:
    if score >= 0.8:
        return "pass"
    elif score >= 0.5:
        return "warn"
    return "fail"


def _score_color(score: float) -> str:
    if score >= 0.8:
        return "#6ee7b7"
    elif score >= 0.5:
        return "#fbbf24"
    return "#f87171"
