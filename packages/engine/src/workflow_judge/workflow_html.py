"""
Inject Workflow Judge v3 results into the existing HTML report.

V3 additions:
- Score breakdown ("Score Path") in expandable detail rows
- Applicability status and reason
- NEEDS_REVIEW / SKIPPED badges
- Evaluation mode visual distinction
- Turn references in step evidence
- Failure category tags
- Efficiency score display
"""
from __future__ import annotations

import html
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .models import WorkflowDefinition, WorkflowResult


def _status_badge(status: str) -> str:
    """V3: Render status badge based on WorkflowStatus."""
    badges = {
        "passed": '<span style="color:#6ee7b7;font-weight:bold;">PASS</span>',
        "failed": '<span style="color:#f87171;font-weight:bold;">FAIL</span>',
        "skipped_not_applicable": '<span style="color:#94a3b8;font-weight:bold;">SKIPPED</span>',
        "needs_review": '<span style="color:#fbbf24;font-weight:bold;">REVIEW</span>',
    }
    return badges.get(status, badges["failed"])


def _build_workflow_html(
    workflow_def: "WorkflowDefinition",
    results: list["WorkflowResult"],
) -> str:
    """Build an HTML section for workflow judge results."""
    # V3: Filter out skipped results for scoring, but show them in table
    scored_results = [r for r in results if r.status != "skipped_not_applicable"]
    total = len(results)
    total_scored = len(scored_results)
    passed = sum(1 for r in scored_results if r.passed)
    failed = total_scored - passed
    skipped = total - total_scored
    needs_review = sum(1 for r in results if r.status == "needs_review")
    avg_score = sum(r.score for r in scored_results) / total_scored if total_scored else 0
    total_critical = sum(r.critical_failures_count for r in scored_results)

    if total_scored == 0:
        overall_badge = '<span style="color:#94a3b8;font-weight:bold;">ALL SKIPPED</span>'
    elif passed == total_scored:
        overall_badge = '<span style="color:#6ee7b7;font-weight:bold;">ALL PASSED</span>'
    elif passed > 0:
        overall_badge = '<span style="color:#fbbf24;font-weight:bold;">PARTIAL</span>'
    else:
        overall_badge = '<span style="color:#f87171;font-weight:bold;">ALL FAILED</span>'

    # Summary cards (V3: added Skipped and Needs Review)
    rows_html = f"""
    <div style="display:flex;gap:16px;margin-bottom:16px;flex-wrap:wrap;">
      <div style="background:var(--surface2, #1c2030);padding:12px 20px;border-radius:8px;min-width:110px;text-align:center;">
        <div style="font-size:1.5rem;font-weight:bold;color:#38bdf8;">{avg_score:.0%}</div>
        <div style="font-size:0.7rem;color:var(--dim, #8890a8);text-transform:uppercase;">Avg Score</div>
      </div>
      <div style="background:var(--surface2, #1c2030);padding:12px 20px;border-radius:8px;min-width:110px;text-align:center;">
        <div style="font-size:1.5rem;font-weight:bold;color:#6ee7b7;">{passed}</div>
        <div style="font-size:0.7rem;color:var(--dim, #8890a8);text-transform:uppercase;">Passed</div>
      </div>
      <div style="background:var(--surface2, #1c2030);padding:12px 20px;border-radius:8px;min-width:110px;text-align:center;">
        <div style="font-size:1.5rem;font-weight:bold;color:#f87171;">{failed}</div>
        <div style="font-size:0.7rem;color:var(--dim, #8890a8);text-transform:uppercase;">Failed</div>
      </div>
      <div style="background:var(--surface2, #1c2030);padding:12px 20px;border-radius:8px;min-width:110px;text-align:center;">
        <div style="font-size:1.5rem;font-weight:bold;color:#94a3b8;">{skipped}</div>
        <div style="font-size:0.7rem;color:var(--dim, #8890a8);text-transform:uppercase;">Skipped</div>
      </div>
      <div style="background:var(--surface2, #1c2030);padding:12px 20px;border-radius:8px;min-width:110px;text-align:center;">
        <div style="font-size:1.5rem;font-weight:bold;color:#fbbf24;">{needs_review}</div>
        <div style="font-size:0.7rem;color:var(--dim, #8890a8);text-transform:uppercase;">Needs Review</div>
      </div>
      <div style="background:var(--surface2, #1c2030);padding:12px 20px;border-radius:8px;min-width:110px;text-align:center;">
        <div style="font-size:1.5rem;font-weight:bold;color:{'#f87171' if total_critical > 0 else '#6ee7b7'};">{total_critical}</div>
        <div style="font-size:0.7rem;color:var(--dim, #8890a8);text-transform:uppercase;">Critical Violations</div>
      </div>
    </div>
    """

    # Per-conversation table
    table_rows = ""
    for idx, r in enumerate(results):
        persona = html.escape(r.persona_name or r.conversation_id or "Unknown")
        score_color = "#94a3b8" if r.status == "skipped_not_applicable" else (
            "#6ee7b7" if r.score >= 0.7 else "#fbbf24" if r.score >= 0.5 else "#f87171")
        status_html = _status_badge(r.status)

        mode_colors = {"llm": "#38bdf8", "fallback_keyword": "#fbbf24", "fallback_text": "#f87171", "skipped": "#94a3b8"}
        mode_color = mode_colors.get(r.evaluation_mode, "#8890a8")
        mode_badge = f'<span style="background:var(--surface2, #1c2030);padding:2px 8px;border-radius:4px;font-size:0.7rem;color:{mode_color};">{html.escape(r.evaluation_mode)}</span>'

        short_violation = ""
        if r.violations:
            short_violation = html.escape(r.violations[0])
            if len(r.violations) > 1:
                short_violation += f' <span style="color:var(--dim, #8890a8);">(+{len(r.violations)-1} more)</span>'

        # V3: Failure category tags
        cat_tags = ""
        if r.failure_categories:
            cat_tags = " ".join(
                f'<span style="background:#3b1c1c;color:#f87171;padding:1px 6px;border-radius:3px;font-size:0.65rem;margin-left:2px;">{html.escape(c)}</span>'
                for c in r.failure_categories[:3])

        safe_wf = workflow_def.name.replace(" ", "_").replace("'", "")
        detail_id = f"wf_detail_{safe_wf}_{idx}"

        score_display = "—" if r.status == "skipped_not_applicable" else f"{r.score:.2f}"

        table_rows += f"""
        <tr style="border-bottom:1px solid var(--border, #2a2f42);cursor:pointer;" onclick="var d=document.getElementById('{detail_id}');d.style.display=d.style.display==='none'?'table-row':'none';" title="Click to expand details">
          <td style="padding:0.5rem 0.75rem;">{persona}</td>
          <td style="padding:0.5rem 0.75rem;text-align:center;color:{score_color};font-weight:700;">{score_display}</td>
          <td style="padding:0.5rem 0.75rem;text-align:center;">{r.step_score:.2f}</td>
          <td style="padding:0.5rem 0.75rem;text-align:center;">{r.rule_score:.2f}</td>
          <td style="padding:0.5rem 0.75rem;text-align:center;">{r.condition_score:.2f}</td>
          <td style="padding:0.5rem 0.75rem;text-align:center;">{status_html}</td>
          <td style="padding:0.5rem 0.75rem;">{mode_badge}</td>
          <td style="padding:0.5rem 0.75rem;font-size:0.78rem;word-wrap:break-word;overflow-wrap:break-word;">{short_violation} {cat_tags}</td>
        </tr>"""

        detail_content = _build_detail_row(r, workflow_def)
        table_rows += f"""
        <tr id="{detail_id}" style="display:none;background:var(--surface2, #1c2030);">
          <td colspan="8" style="padding:0.75rem 1rem;">{detail_content}</td>
        </tr>"""

    # Step completion bars
    step_html = ""
    if scored_results:
        step_names = list(dict.fromkeys(
            sr.step_name for r in scored_results for sr in r.step_results
        ))
        for sname in step_names:
            completed = sum(1 for r in scored_results for sr in r.step_results if sr.step_name == sname and sr.status.value == "completed")
            partial = sum(1 for r in scored_results for sr in r.step_results if sr.step_name == sname and sr.status.value == "partial")
            missed = sum(1 for r in scored_results for sr in r.step_results if sr.step_name == sname and sr.status.value == "missed")
            full_pct = int((completed / total_scored) * 100) if total_scored else 0
            partial_pct = int((partial / total_scored) * 50) if total_scored else 0
            step_html += f"""
            <div style="margin-bottom:8px;">
              <div style="display:flex;justify-content:space-between;font-size:0.85rem;margin-bottom:2px;">
                <span>{html.escape(sname)}</span>
                <span style="color:var(--dim, #8890a8);font-size:0.75rem;">
                  <span style="color:#6ee7b7;">{completed}</span> done,
                  <span style="color:#fbbf24;">{partial}</span> partial,
                  <span style="color:#f87171;">{missed}</span> missed
                </span>
              </div>
              <div style="background:var(--surface2, #1c2030);border-radius:4px;height:8px;overflow:hidden;display:flex;">
                <div style="background:#38bdf8;height:100%;width:{full_pct}%;"></div>
                <div style="background:#fbbf24;height:100%;width:{partial_pct}%;opacity:0.6;"></div>
              </div>
            </div>"""

    # Hard rule results
    rule_html = ""
    if scored_results:
        rule_names = list(dict.fromkeys(rr.rule_name for r in scored_results for rr in r.rule_results))
        for rname in rule_names:
            rule_passed = sum(1 for r in scored_results for rr in r.rule_results if rr.rule_name == rname and rr.passed)
            sev = next((rr.severity for r in scored_results for rr in r.rule_results if rr.rule_name == rname), "")
            sev_css = {"critical": "severity-badge critical", "high": "severity-badge high", "medium": "severity-badge medium"}.get(sev, "severity-badge low")
            all_pass = rule_passed == total_scored
            icon_color = "#6ee7b7" if all_pass else "#f87171"
            rule_html += f"""
            <div style="display:flex;justify-content:space-between;align-items:center;padding:6px 0;border-bottom:1px solid var(--surface2, #1c2030);font-size:0.85rem;">
              <span>{html.escape(rname)} <span class="{sev_css}" style="font-size:0.65rem;margin-left:4px;">{html.escape(sev.upper())}</span></span>
              <span style="color:{icon_color};font-weight:600;">{rule_passed}/{total_scored}</span>
            </div>"""

    # Soft condition results
    cond_html = ""
    if scored_results and any(r.condition_results for r in scored_results):
        cond_names = list(dict.fromkeys(
            cr.description for r in scored_results for cr in r.condition_results
        ))
        for cname in cond_names:
            cond_met = sum(1 for r in scored_results for cr in r.condition_results if cr.description == cname and cr.met)
            all_met = cond_met == total_scored
            icon_color = "#6ee7b7" if all_met else "#fbbf24"
            cond_html += f"""
            <div style="display:flex;justify-content:space-between;padding:4px 0;border-bottom:1px solid var(--surface2, #1c2030);font-size:0.85rem;">
              <span>{html.escape(cname)}</span>
              <span style="color:{icon_color};font-weight:600;">{cond_met}/{total_scored}</span>
            </div>"""

    grid_cols = "1fr 1fr" if not cond_html else "1fr 1fr 1fr"
    detail_grid = f"""
  <div style="display:grid;grid-template-columns:{grid_cols};gap:1.5rem;margin-top:1rem;">
    <div>
      <h3>Step Completion</h3>
      {step_html if step_html else '<p style="color:var(--dim, #8890a8);">No step data</p>'}
    </div>
    <div>
      <h3>Hard Rule Results</h3>
      {rule_html if rule_html else '<p style="color:var(--dim, #8890a8);">No hard rules</p>'}
    </div>
    {"<div><h3>Success Conditions</h3>" + cond_html + "</div>" if cond_html else ""}
  </div>"""

    section = f"""
<section class="section">
  <h2>&#x1f527; Workflow: {html.escape(workflow_def.name)}</h2>
  <p class="section-hint">
    Domain: {html.escape(workflow_def.domain)} &middot; Steps: {workflow_def.total_steps} &middot; Rules: {len(workflow_def.hard_rules)}
    &middot; Order: {html.escape(workflow_def.order_mode)} &middot; {overall_badge}
  </p>

  {rows_html}

  <h3>Per-Conversation Results</h3>
  <p style="font-size:0.75rem;color:var(--dim, #8890a8);margin-bottom:8px;">Click any row to expand step-by-step details and score breakdown</p>
  <table class="pattern-table" style="table-layout:fixed;width:100%;">
    <colgroup>
      <col style="width:18%;">
      <col style="width:7%;">
      <col style="width:7%;">
      <col style="width:7%;">
      <col style="width:7%;">
      <col style="width:7%;">
      <col style="width:10%;">
      <col style="width:37%;">
    </colgroup>
    <thead>
      <tr>
        <th>Persona</th><th class="center">Score</th><th class="center">Steps</th>
        <th class="center">Rules</th><th class="center">Conditions</th>
        <th class="center">Status</th><th>Mode</th><th>Violations / Categories</th>
      </tr>
    </thead>
    <tbody>{table_rows}
    </tbody>
  </table>

  {detail_grid}
</section>
"""
    return section


def _build_detail_row(r: "WorkflowResult", workflow_def: "WorkflowDefinition") -> str:
    """Build expanded detail content for a single conversation (V3: with score breakdown and turn refs)."""
    parts = []

    # V3: Applicability info (if skipped or low confidence)
    if r.status == "skipped_not_applicable" or r.applicability_confidence < 1.0:
        app_color = "#94a3b8" if r.status == "skipped_not_applicable" else "#fbbf24"
        parts.append(f"""
        <div style="margin-bottom:10px;padding:8px;background:var(--surface2, #1c2030);border-radius:6px;border-left:3px solid {app_color};">
          <strong style="font-size:0.82rem;">Applicability</strong>
          <div style="font-size:0.78rem;color:var(--dim, #8890a8);margin-top:4px;">
            Confidence: {r.applicability_confidence:.0%} &middot; {html.escape(r.applicability_reason)}
          </div>
        </div>""")

    if r.status == "skipped_not_applicable":
        return "\n".join(parts) if parts else '<span style="color:var(--dim, #8890a8);font-size:0.8rem;">Workflow not applicable — evaluation skipped</span>'

    # V3: Score breakdown (Score Path)
    if r.score_breakdown:
        sb = r.score_breakdown
        order_info = ""
        if sb.order_penalty_applied > 0:
            order_info = f' <span style="color:#fbbf24;">→ order penalty: -{sb.order_penalty_applied:.0%} → {sb.post_order_score:.3f}</span>'
        critical_info = ""
        if sb.critical_cap_applied:
            critical_info = f' <span style="color:#f87171;">→ critical cap → {sb.critical_cap_score:.3f}</span>'

        parts.append(f"""
        <div style="margin-bottom:10px;padding:8px;background:var(--surface2, #1c2030);border-radius:6px;border-left:3px solid #38bdf8;">
          <strong style="font-size:0.82rem;">Score Path</strong>
          <div style="font-size:0.78rem;color:var(--dim, #8890a8);margin-top:4px;line-height:1.6;">
            Steps: {sb.raw_step_score:.3f} &times; {sb.normalized_weights[0]:.2f} +
            Rules: {sb.raw_rule_score:.3f} &times; {sb.normalized_weights[1]:.2f} +
            Conditions: {sb.raw_condition_score:.3f} &times; {sb.normalized_weights[2]:.2f}
            = <strong style="color:#e2e8f0;">{sb.weighted_score:.3f}</strong>
            {order_info}{critical_info}
            → <strong style="color:{'#6ee7b7' if sb.threshold_met else '#f87171'};">{sb.final_score:.3f}</strong>
            (threshold: {sb.pass_threshold})
          </div>
        </div>""")

    # Step details (V3: with turn references)
    if r.step_results:
        step_rows = ""
        for sr in r.step_results:
            status_color = {"completed": "#6ee7b7", "partial": "#fbbf24", "missed": "#f87171"}.get(sr.status.value, "#8890a8")
            evidence = html.escape(sr.evidence[:300]) if sr.evidence else "—"
            # V3: turn reference
            turn_ref = ""
            if sr.evidence_detail and sr.evidence_detail.first_detected_turn >= 0:
                turn_ref = f'<span style="color:#38bdf8;font-size:0.7rem;margin-left:4px;">[turn {sr.evidence_detail.first_detected_turn}]</span>'
            matched_by = ""
            if sr.evidence_detail and sr.evidence_detail.matched_by != "unmatched":
                matched_by = f'<span style="background:var(--surface2, #1c2030);padding:1px 5px;border-radius:3px;font-size:0.65rem;margin-left:4px;">{html.escape(sr.evidence_detail.matched_by)}</span>'
            step_rows += f"""<tr>
              <td style="padding:3px 6px;font-size:0.78rem;">{html.escape(sr.step_name)}{turn_ref}{matched_by}</td>
              <td style="padding:3px 6px;font-size:0.78rem;color:{status_color};font-weight:600;">{sr.status.value.upper()}</td>
              <td style="padding:3px 6px;font-size:0.75rem;color:var(--dim, #8890a8);word-wrap:break-word;max-width:500px;">{evidence}</td>
            </tr>"""
        parts.append(f"""
        <div style="margin-bottom:10px;">
          <strong style="font-size:0.82rem;">Steps</strong>
          <table style="width:100%;margin-top:4px;border-collapse:collapse;">
            <thead><tr style="border-bottom:1px solid var(--border, #2a2f42);">
              <th style="text-align:left;padding:3px 6px;font-size:0.72rem;">Step</th>
              <th style="text-align:left;padding:3px 6px;font-size:0.72rem;">Status</th>
              <th style="text-align:left;padding:3px 6px;font-size:0.72rem;">Evidence</th>
            </tr></thead>
            <tbody>{step_rows}</tbody>
          </table>
        </div>""")

    # Rule details
    if r.rule_results:
        rule_rows = ""
        for rr in r.rule_results:
            pass_color = "#6ee7b7" if rr.passed else "#f87171"
            pass_label = "PASS" if rr.passed else "FAIL"
            evidence = html.escape(rr.evidence[:300]) if rr.evidence else "—"
            rule_rows += f"""<tr>
              <td style="padding:3px 6px;font-size:0.78rem;">{html.escape(rr.rule_name)}</td>
              <td style="padding:3px 6px;font-size:0.78rem;color:{pass_color};font-weight:600;">{pass_label}</td>
              <td style="padding:3px 6px;font-size:0.75rem;color:var(--dim, #8890a8);word-wrap:break-word;max-width:500px;">{evidence}</td>
            </tr>"""
        parts.append(f"""
        <div style="margin-bottom:10px;">
          <strong style="font-size:0.82rem;">Hard Rules</strong>
          <table style="width:100%;margin-top:4px;border-collapse:collapse;">
            <thead><tr style="border-bottom:1px solid var(--border, #2a2f42);">
              <th style="text-align:left;padding:3px 6px;font-size:0.72rem;">Rule</th>
              <th style="text-align:left;padding:3px 6px;font-size:0.72rem;">Result</th>
              <th style="text-align:left;padding:3px 6px;font-size:0.72rem;">Evidence</th>
            </tr></thead>
            <tbody>{rule_rows}</tbody>
          </table>
        </div>""")

    # Violations
    if r.violations:
        violation_items = "".join(
            f'<li style="font-size:0.78rem;color:#f87171;margin-bottom:3px;word-wrap:break-word;">{html.escape(v)}</li>'
            for v in r.violations
        )
        parts.append(f"""
        <div>
          <strong style="font-size:0.82rem;">All Violations ({len(r.violations)})</strong>
          <ul style="margin:4px 0 0 16px;padding:0;">{violation_items}</ul>
        </div>""")

    # V3: Efficiency score
    if r.efficiency_score > 0:
        eff_color = "#6ee7b7" if r.efficiency_score > 0.3 else "#fbbf24" if r.efficiency_score > 0.15 else "#f87171"
        parts.append(f"""
        <div style="margin-top:6px;font-size:0.75rem;color:var(--dim, #8890a8);">
          Efficiency: <span style="color:{eff_color};font-weight:600;">{r.efficiency_score:.2f}</span>
          (completed required steps / total turns)
        </div>""")

    if not parts:
        return '<span style="color:var(--dim, #8890a8);font-size:0.8rem;">No detail data available</span>'

    return "\n".join(parts)


def inject_workflow_into_report(
    html_path: str | Path,
    workflow_def: "WorkflowDefinition",
    results: list["WorkflowResult"],
) -> bool:
    html_path = Path(html_path)
    if not html_path.exists():
        return False
    content = html_path.read_text(encoding="utf-8")
    wf_marker = f"Workflow: {html.escape(workflow_def.name)}"
    if wf_marker in content:
        return False
    section = _build_workflow_html(workflow_def, results)
    convo_marker = "Conversation Transcripts"
    if convo_marker in content:
        idx = content.index(convo_marker)
        search_area = content[:idx]
        section_start = search_area.rfind('<section')
        if section_start >= 0:
            content = content[:section_start] + section + "\n" + content[section_start:]
        else:
            content = content.replace("</body>", section + "\n</body>")
    elif "</body>" in content:
        content = content.replace("</body>", section + "\n</body>")
    else:
        content += section
    html_path.write_text(content, encoding="utf-8")
    return True


def inject_multi_workflow_into_report(
    html_path: str | Path,
    workflow_results_list: list[tuple],
) -> int:
    count = 0
    for workflow_def, results in workflow_results_list:
        if inject_workflow_into_report(html_path, workflow_def, results):
            count += 1
    return count
