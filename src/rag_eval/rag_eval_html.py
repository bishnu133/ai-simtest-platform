"""
RAG/Tool Evaluation — HTML Report Injection (Phase 8).

Injects a RAG/Tool evaluation section into the existing HTML report.
Placed BEFORE Behavioral Signature section (after Workflow).

Similar pattern to workflow_html.py and coverage_html.py.
"""

from __future__ import annotations

import html
from typing import Any, Dict, Optional

from src.rag_eval.engine import RAGEvalReport


def inject_rag_eval_into_report(report: RAGEvalReport, html_path: str) -> bool:
    """
    Inject RAG/Tool evaluation section into existing HTML report.

    Returns True if injection succeeded, False otherwise.
    """
    try:
        with open(html_path, 'r', encoding='utf-8') as f:
            content = f.read()

        section_html = _build_rag_eval_section(report)
        if not section_html:
            return False

        # Insert before Behavioral Signature section, or before footer, or before </body>
        insertion_points = [
            '<!-- BEHAVIORAL_SIGNATURE_SECTION -->',
            '<section class="behavioral-signature',
            '🧬',  # Behavioral signature emoji marker
            '<footer',
            '</body>',
        ]

        inserted = False
        for marker in insertion_points:
            idx = content.find(marker)
            if idx != -1:
                content = content[:idx] + section_html + '\n' + content[idx:]
                inserted = True
                break

        if not inserted:
            # Fallback: insert before </body>
            content = content.replace('</body>', section_html + '\n</body>')

        with open(html_path, 'w', encoding='utf-8') as f:
            f.write(content)

        return True

    except Exception:
        return False


def _build_rag_eval_section(report: RAGEvalReport) -> str:
    """Build the HTML section for RAG/Tool evaluation results."""
    if report.total_turns_evaluated == 0:
        return ""

    # Overall scores
    rag_pct = f"{report.overall_rag_score * 100:.1f}%"
    tool_pct = f"{report.overall_tool_score * 100:.1f}%"
    overall_pct = f"{report.overall_score * 100:.1f}%"
    overall_color = _score_color(report.overall_score)

    # Gate status
    gate_html = ""
    if report.gate_threshold is not None:
        gate_icon = "✅" if report.gate_passed else "❌"
        gate_color = "#4ade80" if report.gate_passed else "#f87171"
        gate_html = f"""
        <div style="margin-top:12px;padding:10px 16px;border-radius:8px;
                    background:{gate_color}22;border:1px solid {gate_color};">
            <span style="font-size:1.1em">{gate_icon}</span>
            <strong>CI/CD Gate:</strong> Overall score {overall_pct}
            {'≥' if report.gate_passed else '<'} {report.gate_threshold * 100:.0f}% threshold
            — <strong>{'PASSED' if report.gate_passed else 'FAILED'}</strong>
        </div>"""

    # RAG metrics table
    rag_rows = ""
    for name, avg in sorted(report.rag_metric_averages.items()):
        pass_rate = report.rag_metric_pass_rates.get(name, 0)
        color = _score_color(avg)
        rag_rows += f"""
        <tr>
            <td style="padding:6px 12px">{html.escape(name.replace('_', ' ').title())}</td>
            <td style="padding:6px 12px;text-align:center">
                <span style="color:{color};font-weight:600">{avg * 100:.1f}%</span>
            </td>
            <td style="padding:6px 12px;text-align:center">{pass_rate * 100:.0f}%</td>
        </tr>"""

    # Tool metrics table
    tool_rows = ""
    for name, avg in sorted(report.tool_metric_averages.items()):
        pass_rate = report.tool_metric_pass_rates.get(name, 0)
        color = _score_color(avg)
        tool_rows += f"""
        <tr>
            <td style="padding:6px 12px">{html.escape(name.replace('_', ' ').title())}</td>
            <td style="padding:6px 12px;text-align:center">
                <span style="color:{color};font-weight:600">{avg * 100:.1f}%</span>
            </td>
            <td style="padding:6px 12px;text-align:center">{pass_rate * 100:.0f}%</td>
        </tr>"""

    # Issues list
    issues_html = ""
    if report.top_issues:
        issue_items = "\n".join(
            f'<li style="padding:4px 0;color:#fbbf24">{html.escape(issue)}</li>'
            for issue in report.top_issues[:8]
        )
        issues_html = f"""
        <div style="margin-top:16px">
            <h4 style="color:#f59e0b;margin-bottom:8px">⚠ Top Issues</h4>
            <ul style="list-style:none;padding-left:8px">{issue_items}</ul>
        </div>"""

    # Build complete section
    section = f"""
<!-- RAG_TOOL_EVAL_SECTION -->
<div style="margin:32px 0;padding:24px;background:#1a1a2e;border-radius:12px;
            border:1px solid #2d2d44;font-family:system-ui,-apple-system,sans-serif;color:#e2e8f0">
    <h2 style="margin:0 0 16px;color:#818cf8;font-size:1.4em">
        🔬 RAG/Tool Evaluation
        <span style="font-size:0.6em;color:#94a3b8;font-weight:normal;margin-left:12px">
            {report.total_turns_evaluated} turns evaluated · {report.evidence_mode} mode · {report.eval_speed} speed
        </span>
    </h2>

    <!-- Score cards -->
    <div style="display:flex;gap:16px;flex-wrap:wrap;margin-bottom:20px">
        <div style="flex:1;min-width:140px;padding:16px;background:#16213e;border-radius:8px;text-align:center">
            <div style="font-size:0.75em;color:#94a3b8;text-transform:uppercase;letter-spacing:0.05em">Overall</div>
            <div style="font-size:2em;font-weight:700;color:{overall_color}">{overall_pct}</div>
        </div>
        <div style="flex:1;min-width:140px;padding:16px;background:#16213e;border-radius:8px;text-align:center">
            <div style="font-size:0.75em;color:#94a3b8;text-transform:uppercase;letter-spacing:0.05em">RAG Score</div>
            <div style="font-size:2em;font-weight:700;color:{_score_color(report.overall_rag_score)}">{rag_pct}</div>
        </div>
        <div style="flex:1;min-width:140px;padding:16px;background:#16213e;border-radius:8px;text-align:center">
            <div style="font-size:0.75em;color:#94a3b8;text-transform:uppercase;letter-spacing:0.05em">Tool Score</div>
            <div style="font-size:2em;font-weight:700;color:{_score_color(report.overall_tool_score)}">{tool_pct}</div>
        </div>
        <div style="flex:1;min-width:140px;padding:16px;background:#16213e;border-radius:8px;text-align:center">
            <div style="font-size:0.75em;color:#94a3b8;text-transform:uppercase;letter-spacing:0.05em">Issues</div>
            <div style="font-size:2em;font-weight:700;color:{'#f87171' if (report.total_rag_issues + report.total_tool_issues) > 0 else '#4ade80'}">{report.total_rag_issues + report.total_tool_issues}</div>
        </div>
    </div>

    {gate_html}

    <!-- Metric tables side by side -->
    <div style="display:flex;gap:20px;flex-wrap:wrap;margin-top:20px">
        {'<div style="flex:1;min-width:280px">' + _metric_table("RAG Metrics", rag_rows) + '</div>' if rag_rows else ''}
        {'<div style="flex:1;min-width:280px">' + _metric_table("Tool Metrics", tool_rows) + '</div>' if tool_rows else ''}
    </div>

    {issues_html}

    <div style="margin-top:16px;padding-top:12px;border-top:1px solid #2d2d44;
                font-size:0.8em;color:#64748b">
        Execution: {report.execution_time_seconds:.1f}s ·
        Conversations: {report.total_conversations} ·
        Evidence: {report.evidence_mode} ·
        Speed: {report.eval_speed}
    </div>
</div>
<!-- /RAG_TOOL_EVAL_SECTION -->
"""
    return section


def _metric_table(title: str, rows: str) -> str:
    if not rows:
        return ""
    return f"""
    <h4 style="color:#a78bfa;margin:0 0 8px">{title}</h4>
    <table style="width:100%;border-collapse:collapse;font-size:0.9em">
        <thead>
            <tr style="border-bottom:1px solid #2d2d44">
                <th style="padding:6px 12px;text-align:left;color:#94a3b8">Metric</th>
                <th style="padding:6px 12px;text-align:center;color:#94a3b8">Avg Score</th>
                <th style="padding:6px 12px;text-align:center;color:#94a3b8">Pass Rate</th>
            </tr>
        </thead>
        <tbody>{rows}</tbody>
    </table>"""


def _score_color(score: float) -> str:
    if score >= 0.8:
        return "#4ade80"
    elif score >= 0.6:
        return "#fbbf24"
    else:
        return "#f87171"
