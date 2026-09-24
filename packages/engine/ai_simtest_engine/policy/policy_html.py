"""
Policy Compliance HTML Report v2.1 — Enterprise-grade compliance visualization.

Features:
- Overall compliance verdict with gate mode badge
- Provenance/audit section rendered in HTML
- Control family breakdown chart (Chart.js)
- Rule results table: failed-first sort, severity badges, rule_status, evidence counts
- Collapsible evidence groups via <details>/<summary>
- Severity and control-family filter buttons (JS)
- Remediation guidance for each failed rule
- Responsive dark theme matching existing report style

Injected into the existing report.html before </body>.
"""

from __future__ import annotations

import html
import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from .models import ComplianceScorecard, PolicySeverity


def generate_compliance_html(scorecard: ComplianceScorecard) -> str:
    """Generate self-contained HTML section for the compliance scorecard."""

    # ── Status & summary ──
    if scorecard.overall_compliant:
        verdict_class = "compliance-pass"
        verdict_icon = "✅"
        verdict_text = "COMPLIANT"
    else:
        verdict_class = "compliance-fail"
        verdict_icon = "❌"
        verdict_text = "NON-COMPLIANT"

    gate_mode = scorecard.gate_mode.replace("_", " ").title()
    sev_counts = scorecard.violation_count_by_severity

    # ── Control family chart data ──
    cf_data = scorecard.control_family_results
    cf_labels = json.dumps(list(cf_data.keys()))
    cf_passed = json.dumps([cf_data[k]["passed"] for k in cf_data])
    cf_failed = json.dumps([cf_data[k]["failed"] for k in cf_data])
    cf_skipped = json.dumps([cf_data[k].get("skipped", 0) for k in cf_data])

    # ── Provenance ──
    prov = scorecard.provenance or {}
    prov_rows = ""
    for k, v in prov.items():
        prov_rows += f'<tr><td class="prov-key">{_esc(k)}</td><td class="prov-val">{_esc(str(v))}</td></tr>'

    # ── Collect unique severities and families for filter buttons ──
    all_severities = sorted(set(r.severity.value for r in scorecard.results))
    all_families = sorted(set(r.control_family or "ungrouped" for r in scorecard.results))

    sev_filter_btns = ''.join(
        f'<button class="filter-btn sev-btn" data-filter="sev-{s}" onclick="toggleFilter(this, \'sev-{s}\')">{s}</button>'
        for s in all_severities
    )
    family_filter_btns = ''.join(
        f'<button class="filter-btn fam-btn" data-filter="fam-{s}" onclick="toggleFilter(this, \'fam-{s}\')">{s}</button>'
        for s in all_families
    )

    # ── Rule rows (sorted: failed first, then by severity) ──
    sev_order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    sorted_results = sorted(
        scorecard.results,
        key=lambda r: (0 if not r.passed and not r.not_evaluated else 1, sev_order.get(r.severity.value, 9))
    )

    rule_rows = ""
    for r in sorted_results:
        status_icon = "✓" if r.passed else ("⊘" if r.not_evaluated else "✗")
        status_class = "pass" if r.passed else ("skip" if r.not_evaluated else "fail")
        sev_class = f"sev-{r.severity.value}"
        family_class = f"fam-{r.control_family or 'ungrouped'}"
        rs = getattr(r, 'rule_status', status_class)

        scope_badge = ""
        if r.scope_applied:
            scope_badge = f'<span class="scope-badge" title="{_esc(r.scope_applied)}">🎯</span>'

        family_badge = ""
        if r.control_family:
            family_badge = f'<span class="family-badge">{_esc(r.control_family)}</span>'

        status_badge = f'<span class="rs-badge rs-{rs}">{rs}</span>' if rs not in ("passed", "failed") else ""

        evidence_block = ""
        if not r.passed and r.evidence:
            evidence_items = ""
            for e in r.evidence[:5]:
                turn_info = f" turn {e.turn_index}" if e.turn_index is not None else ""
                snippet = _esc(e.bot_response_snippet[:150]) if e.bot_response_snippet else ""
                evidence_items += f'''
                    <div class="evidence-item">
                        <span class="ev-conv">{_esc(e.conversation_id)}{turn_info}</span>
                        <span class="ev-persona">{_esc(e.persona_name)}</span>
                        <span class="ev-score">{e.score:.2f}</span>
                        <span class="ev-issue">{_esc(e.issue)}</span>
                        {f'<div class="ev-snippet">{snippet}</div>' if snippet else ''}
                    </div>'''
            evidence_block = f'''
                <details class="evidence-details">
                    <summary>{len(r.evidence)} evidence item(s)</summary>
                    <div class="evidence-container">{evidence_items}</div>
                </details>'''

        remediation_block = ""
        if not r.passed and not r.not_evaluated and r.remediation:
            remediation_block = f'<div class="remediation">💡 {_esc(r.remediation)}</div>'

        eval_info = ""
        if r.evaluated_count > 0:
            eval_info = f'{r.evaluated_count} eval'
            if r.failed_count > 0:
                eval_info += f', {r.failed_count} fail'

        rule_rows += f'''
            <tr class="rule-row {status_class} {sev_class} {family_class}">
                <td class="rule-name">{_esc(r.rule_name)} {family_badge} {scope_badge} {status_badge}</td>
                <td class="rule-judge">{_esc(r.judge)}</td>
                <td class="rule-condition">{_esc(r.condition.value)}</td>
                <td class="rule-status"><span class="status-{status_class}">{status_icon}</span></td>
                <td class="rule-actual">{r.actual_value:.3f}</td>
                <td class="rule-threshold">{r.threshold:.3f}</td>
                <td class="rule-severity"><span class="{sev_class}">{r.severity.value}</span>
                    <span class="eval-info">{eval_info}</span></td>
            </tr>'''
        if evidence_block or remediation_block:
            rule_rows += f'''
            <tr class="detail-row {status_class} {sev_class} {family_class}">
                <td colspan="7">{evidence_block}{remediation_block}</td>
            </tr>'''

    # ── Severity breakdown ──
    sev_badges = ""
    for sev, count in sev_counts.items():
        sev_badges += f'<span class="sev-count sev-{sev}">{count} {sev}</span> '

    return f'''
    <style>
    .compliance-section {{
        margin: 30px 0; padding: 24px; background: #1a1e2e;
        border-radius: 12px; border: 1px solid #2a2e3e;
        font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; color: #e0e0e0;
    }}
    .compliance-header {{ display: flex; justify-content: space-between; align-items: center; margin-bottom: 20px; flex-wrap: wrap; gap: 12px; }}
    .compliance-title {{ font-size: 1.4em; font-weight: 700; color: #fff; }}
    .compliance-verdict {{ padding: 8px 20px; border-radius: 8px; font-weight: 700; font-size: 1.1em; }}
    .compliance-pass {{ background: #1a3a2a; color: #4ade80; border: 1px solid #2d5a3d; }}
    .compliance-fail {{ background: #3a1a1a; color: #f87171; border: 1px solid #5a2d2d; }}
    .compliance-meta {{ display: flex; gap: 24px; margin-bottom: 20px; flex-wrap: wrap; }}
    .meta-card {{ background: #22263a; padding: 12px 18px; border-radius: 8px; text-align: center; min-width: 110px; }}
    .meta-value {{ font-size: 1.5em; font-weight: 700; color: #fff; }}
    .meta-label {{ font-size: 0.78em; color: #888; margin-top: 4px; }}
    .gate-badge {{ display: inline-block; padding: 3px 10px; background: #2a2e4e; border-radius: 12px; font-size: 0.75em; color: #93c5fd; font-weight: 600; }}

    /* Provenance */
    .provenance-section {{ margin: 16px 0; }}
    .provenance-section summary {{ cursor: pointer; color: #93c5fd; font-weight: 600; font-size: 0.9em; }}
    .prov-table {{ width: 100%; font-size: 0.8em; margin-top: 8px; }}
    .prov-table td {{ padding: 3px 10px; border-bottom: 1px solid #2a2e3e; }}
    .prov-key {{ color: #888; width: 200px; }}
    .prov-val {{ color: #ccc; font-family: monospace; }}

    /* Filters */
    .filter-bar {{ margin: 14px 0 10px 0; display: flex; flex-wrap: wrap; gap: 6px; align-items: center; }}
    .filter-bar .label {{ font-size: 0.8em; color: #888; margin-right: 4px; }}
    .filter-btn {{ padding: 3px 10px; border-radius: 12px; border: 1px solid #3a3e5e; background: transparent;
        color: #aaa; font-size: 0.75em; cursor: pointer; transition: all 0.15s; }}
    .filter-btn:hover {{ border-color: #6366f1; color: #c7d2fe; }}
    .filter-btn.active {{ background: #3730a3; color: #e0e7ff; border-color: #6366f1; }}

    /* Rules table */
    .rules-table {{ width: 100%; border-collapse: collapse; margin-top: 10px; font-size: 0.88em; }}
    .rules-table th {{ background: #22263a; padding: 9px 10px; text-align: left; font-weight: 600; color: #ccc; border-bottom: 2px solid #3a3e5e; }}
    .rules-table td {{ padding: 7px 10px; border-bottom: 1px solid #2a2e3e; }}
    .rule-row.fail {{ background: rgba(248,113,113,0.05); }}
    .rule-row.skip {{ opacity: 0.55; }}
    tr.rule-row.hidden, tr.detail-row.hidden {{ display: none; }}
    .status-pass {{ color: #4ade80; font-weight: 700; }}
    .status-fail {{ color: #f87171; font-weight: 700; }}
    .status-skip {{ color: #888; font-weight: 700; }}
    .sev-critical {{ color: #f87171; font-weight: 700; }}
    .sev-high {{ color: #fb923c; font-weight: 600; }}
    .sev-medium {{ color: #facc15; }}
    .sev-low {{ color: #4ade80; }}
    .sev-count {{ display: inline-block; padding: 2px 8px; border-radius: 10px; font-size: 0.8em; margin: 0 2px; }}
    .sev-count.sev-critical {{ background: #3a1a1a; }} .sev-count.sev-high {{ background: #3a2a1a; }} .sev-count.sev-medium {{ background: #3a3a1a; }}
    .scope-badge {{ font-size: 0.7em; cursor: help; }} .family-badge {{ display: inline-block; padding: 1px 6px; background: #2a3a3a; border-radius: 8px; font-size: 0.7em; color: #67e8f9; }}
    .eval-info {{ font-size: 0.7em; color: #888; }}
    .rs-badge {{ display: inline-block; padding: 1px 6px; background: #2a2a4a; border-radius: 8px; font-size: 0.65em; color: #a5b4fc; margin-left: 4px; }}

    /* Evidence & remediation */
    .detail-row td {{ padding: 2px 10px 10px 24px; }}
    .evidence-details {{ margin-bottom: 6px; }}
    .evidence-details summary {{ cursor: pointer; color: #c084fc; font-size: 0.82em; font-weight: 500; }}
    .evidence-container {{ display: flex; flex-direction: column; gap: 5px; margin-top: 6px; }}
    .evidence-item {{ padding: 5px 10px; background: #22263a; border-radius: 6px; font-size: 0.78em; display: flex; flex-wrap: wrap; gap: 6px; align-items: center; }}
    .ev-conv {{ color: #93c5fd; font-weight: 600; }} .ev-persona {{ color: #a78bfa; }} .ev-score {{ color: #f87171; font-weight: 600; }} .ev-issue {{ color: #fbbf24; }}
    .ev-snippet {{ width: 100%; color: #888; font-size: 0.84em; font-style: italic; margin-top: 2px; padding: 3px 8px; background: #1a1e2e; border-radius: 4px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }}
    .remediation {{ padding: 7px 12px; background: #1a2a3a; border-radius: 6px; border-left: 3px solid #3b82f6; font-size: 0.82em; color: #93c5fd; margin-top: 4px; }}
    </style>

    <div class="compliance-section" id="compliance-report">
        <div class="compliance-header">
            <span class="compliance-title">📋 Policy Compliance: {_esc(scorecard.policy_set_name)}</span>
            <span class="compliance-verdict {verdict_class}">{verdict_icon} {verdict_text}</span>
        </div>

        <div class="compliance-meta">
            <div class="meta-card"><div class="meta-value">{scorecard.compliance_score:.0f}%</div><div class="meta-label">Compliance Score</div></div>
            <div class="meta-card"><div class="meta-value">{scorecard.passed_rules}/{scorecard.total_rules}</div><div class="meta-label">Rules Passed</div></div>
            <div class="meta-card"><div class="meta-value" style="color: {'#f87171' if scorecard.has_critical_violations else '#4ade80'}">{len(scorecard.critical_violations)}</div><div class="meta-label">Critical Violations</div></div>
            <div class="meta-card"><div class="meta-value">{scorecard.total_evidence_items}</div><div class="meta-label">Evidence Items</div></div>
            <div class="meta-card"><div class="meta-label" style="margin-top:0">Gate Mode</div><div class="gate-badge">{gate_mode}</div></div>
        </div>

        {'<div style="margin: 10px 0">' + sev_badges + '</div>' if sev_badges else ''}

        <details class="provenance-section">
            <summary>🔒 Audit Provenance</summary>
            <table class="prov-table">{prov_rows}</table>
        </details>

        {'<canvas id="complianceCFChart" width="580" height="' + str(max(180, len(cf_data) * 32 + 50)) + '"></canvas>' if cf_data else ''}

        <div class="filter-bar">
            <span class="label">Filter by severity:</span> {sev_filter_btns}
            <span class="label" style="margin-left:12px">Family:</span> {family_filter_btns}
        </div>

        <table class="rules-table" id="complianceRulesTable">
            <thead><tr>
                <th>Rule</th><th>Judge</th><th>Condition</th><th>Status</th><th>Actual</th><th>Threshold</th><th>Severity</th>
            </tr></thead>
            <tbody>{rule_rows}</tbody>
        </table>
    </div>

    <script>
    (function() {{
        // Control family chart
        var cfCtx = document.getElementById('complianceCFChart');
        if (cfCtx && typeof Chart !== 'undefined') {{
            new Chart(cfCtx.getContext('2d'), {{
                type: 'bar',
                data: {{
                    labels: {cf_labels},
                    datasets: [
                        {{ label: 'Passed', data: {cf_passed}, backgroundColor: '#4ade80' }},
                        {{ label: 'Failed', data: {cf_failed}, backgroundColor: '#f87171' }},
                        {{ label: 'Skipped', data: {cf_skipped}, backgroundColor: '#6b7280' }},
                    ]
                }},
                options: {{
                    indexAxis: 'y', responsive: false,
                    plugins: {{ title: {{ display: true, text: 'Compliance by Control Family', color: '#ccc', font: {{ size: 13 }} }}, legend: {{ labels: {{ color: '#ccc' }} }} }},
                    scales: {{ x: {{ stacked: true, ticks: {{ color: '#888', stepSize: 1 }}, grid: {{ color: '#2a2e3e' }} }}, y: {{ stacked: true, ticks: {{ color: '#ccc' }}, grid: {{ display: false }} }} }},
                }}
            }});
        }}

        // Filter logic
        var activeFilters = new Set();
        window.toggleFilter = function(btn, cls) {{
            btn.classList.toggle('active');
            if (activeFilters.has(cls)) activeFilters.delete(cls); else activeFilters.add(cls);
            applyFilters();
        }};
        function applyFilters() {{
            var rows = document.querySelectorAll('#complianceRulesTable tbody tr');
            if (activeFilters.size === 0) {{
                rows.forEach(function(r) {{ r.classList.remove('hidden'); }});
                return;
            }}
            rows.forEach(function(r) {{
                var show = false;
                activeFilters.forEach(function(f) {{ if (r.classList.contains(f)) show = true; }});
                if (show) r.classList.remove('hidden'); else r.classList.add('hidden');
            }});
        }}
    }})();
    </script>
    '''


def inject_compliance_into_report(
    scorecard: ComplianceScorecard,
    html_path: str,
) -> bool:
    """Inject compliance section into existing HTML report. Returns True on success."""
    path = Path(html_path)
    if not path.exists():
        return False
    try:
        content = path.read_text(encoding="utf-8")
    except Exception:
        return False

    section_html = generate_compliance_html(scorecard)

    import re
    content = re.sub(
        r'<!-- COMPLIANCE_START -->.*?<!-- COMPLIANCE_END -->',
        '', content, flags=re.DOTALL,
    )

    marker = "<!-- COMPLIANCE_START -->\n" + section_html + "\n<!-- COMPLIANCE_END -->"

    # Injection priority:
    # 1. Before <footer> — keeps compliance above the footer line
    # 2. Before </body> as fallback
    # 3. Append if nothing else matches
    if "<footer>" in content:
        content = content.replace("<footer>", marker + "\n<footer>")
    elif "<footer " in content:
        # Handle <footer class="..."> variant
        idx = content.index("<footer ")
        content = content[:idx] + marker + "\n" + content[idx:]
    elif "</body>" in content:
        content = content.replace("</body>", marker + "\n</body>")
    else:
        content += marker

    try:
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        return True
    except Exception:
        return False


def _esc(text: str) -> str:
    """HTML-escape text."""
    return html.escape(str(text)) if text else ""