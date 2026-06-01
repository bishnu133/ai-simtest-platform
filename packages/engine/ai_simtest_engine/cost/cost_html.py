"""
Cost HTML Section — Injects cost summary into the HTML report.

Renders:
  1. Stat cards: Total estimated cost, tokens, calls, cost/conversation
  2. Confidence indicator badge (high/partial/low) with notes
  3. Per-component bar chart (Chart.js)
  4. Per-component table (primary truth, not just chart)
  5. Subcomponent detail rows (Review #11: expandable nested view)
  6. Per-model table
  7. Budget status banner (if --budget-limit was set)
  8. Pricing observability banner (Review #10: unknown models surfaced)

All values explicitly labeled as ESTIMATED.
Uses the same dark theme and injection pattern as coverage_html.py.
"""
from __future__ import annotations

import html as html_mod
import json
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ai_simtest_engine.cost.models import CostReport

# ── Marker for idempotency ────────────────────────────────────────────────────
_COST_MARKER = "<!-- CostSummaryPanel -->"


def generate_cost_html(report: "CostReport") -> str:
    """Generate the HTML section for cost metrics."""

    total_cost = report.total_estimated_cost_usd
    total_tokens = report.total_tokens
    total_calls = report.total_calls
    confidence = report.usage_coverage_confidence
    cost_per_conv = report.cost_per_completed_conversation
    cost_per_turn = report.cost_per_turn

    # Get dicts from frozen tuples
    per_component = report.get_per_component()
    per_model = report.get_per_model()

    # ── Confidence badge ──────────────────────────────────────
    conf_colors = {
        "high": ("#6ee7b7", "High confidence"),
        "partial": ("#fbbf24", "Partial — some calls missing usage"),
        "low": ("#f87171", "Low — most calls missing usage"),
        "none": ("#9ca3af", "No data"),
    }
    conf_color, conf_label = conf_colors.get(confidence, conf_colors["none"])

    # Review #7: Add confidence notes if present
    conf_notes_html = ""
    if report.confidence_notes:
        conf_notes_html = (
            f'<div style="font-size:0.75rem;color:var(--dim,#8890a8);margin-top:4px;">'
            f'{html_mod.escape(report.confidence_notes)}</div>'
        )

    confidence_badge = (
        f'<span style="background:var(--surface2,#1c2030);padding:3px 10px;border-radius:6px;'
        f'font-size:0.75rem;color:{conf_color};border:1px solid {conf_color}40;">'
        f'{html_mod.escape(conf_label)}</span>'
    )

    # ── Budget banner ─────────────────────────────────────────
    budget_html = ""
    if report.budget_limit is not None:
        if report.budget_exceeded:
            budget_html = f"""
    <div style="padding:0.75rem 1rem;background:rgba(248,113,113,0.08);border:1px solid rgba(248,113,113,0.3);
                border-radius:8px;margin-bottom:1rem;font-size:0.85rem;">
      <strong style="color:#f87171;">Budget exceeded</strong> — Estimated cost
      ${report.budget_exceeded_at_cost:.4f} crossed limit ${report.budget_limit:.2f}
      (mode: {html_mod.escape(report.budget_mode)})
    </div>"""
        else:
            pct = (total_cost / report.budget_limit * 100) if report.budget_limit > 0 else 0
            budget_html = f"""
    <div style="padding:0.75rem 1rem;background:rgba(110,231,183,0.08);border:1px solid rgba(110,231,183,0.3);
                border-radius:8px;margin-bottom:1rem;font-size:0.85rem;">
      <strong style="color:#6ee7b7;">Within budget</strong> — ${total_cost:.4f} of ${report.budget_limit:.2f}
      ({pct:.0f}% used, mode: {html_mod.escape(report.budget_mode)})
    </div>"""

    # ── Review #10: Pricing observability banner ──────────────
    pricing_html = ""
    if report.pricing_lookup_failures > 0:
        models_list = list(report.unknown_priced_models) if report.unknown_priced_models else ["unknown"]
        # Truncate long model lists to avoid banner overflow
        max_display = 5
        if len(models_list) > max_display:
            displayed = ", ".join(models_list[:max_display])
            unknown_display = f"{displayed} (+{len(models_list) - max_display} more)"
        else:
            unknown_display = ", ".join(models_list)
        pricing_html = f"""
    <div style="padding:0.75rem 1rem;background:rgba(251,191,36,0.08);border:1px solid rgba(251,191,36,0.3);
                border-radius:8px;margin-bottom:1rem;font-size:0.85rem;">
      <strong style="color:#fbbf24;">Pricing uncertainty</strong> —
      {report.pricing_lookup_failures} pricing lookup(s) failed for: {html_mod.escape(unknown_display)}.
      Cost defaulted to $0 for {report.zero_cost_due_to_unknown_pricing} call(s).
      Actual provider billing may differ.
    </div>"""

    # ── Stat cards ────────────────────────────────────────────
    stats_html = f"""
    <div style="display:flex;gap:12px;margin-bottom:1.2rem;flex-wrap:wrap;">
      <div style="background:var(--surface2,#1c2030);padding:12px 20px;border-radius:8px;min-width:130px;text-align:center;">
        <div style="font-size:1.4rem;font-weight:bold;color:#38bdf8;">${total_cost:.4f}</div>
        <div style="font-size:0.65rem;color:var(--dim,#8890a8);text-transform:uppercase;">Estimated total</div>
      </div>
      <div style="background:var(--surface2,#1c2030);padding:12px 20px;border-radius:8px;min-width:110px;text-align:center;">
        <div style="font-size:1.4rem;font-weight:bold;color:#e2e8f0;">{total_tokens:,}</div>
        <div style="font-size:0.65rem;color:var(--dim,#8890a8);text-transform:uppercase;">Total tokens</div>
      </div>
      <div style="background:var(--surface2,#1c2030);padding:12px 20px;border-radius:8px;min-width:110px;text-align:center;">
        <div style="font-size:1.4rem;font-weight:bold;color:#e2e8f0;">{total_calls}</div>
        <div style="font-size:0.65rem;color:var(--dim,#8890a8);text-transform:uppercase;">LLM calls</div>
      </div>
      <div style="background:var(--surface2,#1c2030);padding:12px 20px;border-radius:8px;min-width:130px;text-align:center;">
        <div style="font-size:1.4rem;font-weight:bold;color:#c084fc;">${cost_per_conv:.4f}</div>
        <div style="font-size:0.65rem;color:var(--dim,#8890a8);text-transform:uppercase;">Est. cost/conversation</div>
      </div>
      <div style="background:var(--surface2,#1c2030);padding:12px 20px;border-radius:8px;min-width:110px;text-align:center;">
        <div style="font-size:1.4rem;font-weight:bold;color:#c084fc;">${cost_per_turn:.4f}</div>
        <div style="font-size:0.65rem;color:var(--dim,#8890a8);text-transform:uppercase;">Est. cost/turn</div>
      </div>
    </div>"""

    # ── Usage coverage ────────────────────────────────────────
    usage_html = ""
    if report.calls_without_usage > 0 or report.failed_calls_with_usage > 0 or report.failed_calls_without_usage > 0:
        usage_html = f"""
    <div style="font-size:0.8rem;color:var(--dim,#8890a8);margin-bottom:1rem;">
      Calls with usage: {report.calls_with_usage} &middot;
      Without usage: <span style="color:#fbbf24;">{report.calls_without_usage}</span> &middot;
      Failed (with usage): {report.failed_calls_with_usage} &middot;
      Failed (no usage): {report.failed_calls_without_usage} &middot;
      Retried: {report.retried_calls}
    </div>"""

    # ── Per-component table with subcomponent rows (Review #11) ───
    comp_rows = ""
    sorted_comps = sorted(
        per_component.items(),
        key=lambda x: x[1].estimated_cost_usd,
        reverse=True,
    )
    for comp_name, cc in sorted_comps:
        pct = (cc.estimated_cost_usd / total_cost * 100) if total_cost > 0 else 0
        bar_width = int(min(pct, 100))
        sub_dict = cc.get_subcomponents_dict()
        has_subs = len(sub_dict) > 0
        cursor = 'cursor:pointer;' if has_subs else ''
        expand_icon = '<span class="cost-expand-icon" style="font-size:0.7rem;margin-right:4px;">▶</span>' if has_subs else '<span style="margin-right:4px;width:12px;display:inline-block;"></span>'

        comp_rows += f"""
        <tr style="border-bottom:1px solid var(--border,#2a2f42);{cursor}" class="cost-comp-row">
          <td style="padding:6px 10px;font-weight:600;font-size:0.85rem;">{expand_icon}{html_mod.escape(comp_name)}</td>
          <td style="padding:6px 10px;text-align:right;font-size:0.85rem;">{cc.total_calls}</td>
          <td style="padding:6px 10px;text-align:right;font-size:0.85rem;">{cc.prompt_tokens:,}</td>
          <td style="padding:6px 10px;text-align:right;font-size:0.85rem;">{cc.completion_tokens:,}</td>
          <td style="padding:6px 10px;text-align:right;font-size:0.85rem;">{cc.total_tokens:,}</td>
          <td style="padding:6px 10px;text-align:right;font-weight:600;color:#38bdf8;font-size:0.85rem;">${cc.estimated_cost_usd:.4f}</td>
          <td style="padding:6px 10px;width:120px;">
            <div style="background:var(--surface2,#1c2030);border-radius:3px;height:14px;overflow:hidden;">
              <div style="background:#38bdf8;height:100%;width:{bar_width}%;border-radius:3px;"></div>
            </div>
          </td>
        </tr>"""

        # Review #11: Subcomponent detail rows
        if has_subs:
            for sc_name, sc in sorted(sub_dict.items(), key=lambda x: x[1].estimated_cost_usd, reverse=True):
                sc_pct = (sc.estimated_cost_usd / total_cost * 100) if total_cost > 0 else 0
                sc_bar = int(min(sc_pct, 100))
                comp_rows += f"""
        <tr class="cost-subcomp-row" style="border-bottom:1px solid var(--border,#2a2f42);display:none;">
          <td style="padding:4px 10px 4px 30px;font-size:0.8rem;color:var(--dim,#8890a8);">↳ {html_mod.escape(sc_name)}</td>
          <td style="padding:4px 10px;text-align:right;font-size:0.8rem;color:var(--dim,#8890a8);">{sc.total_calls}</td>
          <td style="padding:4px 10px;text-align:right;font-size:0.8rem;color:var(--dim,#8890a8);">{sc.prompt_tokens:,}</td>
          <td style="padding:4px 10px;text-align:right;font-size:0.8rem;color:var(--dim,#8890a8);">{sc.completion_tokens:,}</td>
          <td style="padding:4px 10px;text-align:right;font-size:0.8rem;color:var(--dim,#8890a8);">{sc.total_tokens:,}</td>
          <td style="padding:4px 10px;text-align:right;font-size:0.8rem;color:#38bdf8;">${sc.estimated_cost_usd:.4f}</td>
          <td style="padding:4px 10px;width:120px;">
            <div style="background:var(--surface2,#1c2030);border-radius:3px;height:10px;overflow:hidden;">
              <div style="background:#38bdf880;height:100%;width:{sc_bar}%;border-radius:3px;"></div>
            </div>
          </td>
        </tr>"""

    # Subcomponent toggle script — scoped to cost panel container
    subcomp_script = """
    <script>
    (function() {
      var panel = document.getElementById('costComponentPanel');
      if (!panel) return;
      panel.querySelectorAll('.cost-comp-row').forEach(function(row) {
        row.addEventListener('click', function() {
          var next = this.nextElementSibling;
          while (next && next.classList.contains('cost-subcomp-row')) {
            next.style.display = next.style.display === 'none' ? '' : 'none';
            next = next.nextElementSibling;
          }
          var icon = this.querySelector('.cost-expand-icon');
          if (icon && icon.textContent.trim()) {
            icon.textContent = icon.textContent === '▶' ? '▼' : '▶';
          }
        });
      });
    })();
    </script>"""

    comp_table = f"""
    <h3 style="font-size:0.95rem;margin:1rem 0 0.5rem;">Per-component breakdown</h3>
    <div id="costComponentPanel">
    <table style="width:100%;border-collapse:collapse;margin-bottom:1rem;">
      <thead><tr style="border-bottom:1px solid var(--border,#2a2f42);">
        <th style="padding:6px 10px;text-align:left;font-size:0.72rem;color:var(--dim,#8890a8);text-transform:uppercase;">Component</th>
        <th style="padding:6px 10px;text-align:right;font-size:0.72rem;color:var(--dim,#8890a8);text-transform:uppercase;">Calls</th>
        <th style="padding:6px 10px;text-align:right;font-size:0.72rem;color:var(--dim,#8890a8);text-transform:uppercase;">Prompt</th>
        <th style="padding:6px 10px;text-align:right;font-size:0.72rem;color:var(--dim,#8890a8);text-transform:uppercase;">Completion</th>
        <th style="padding:6px 10px;text-align:right;font-size:0.72rem;color:var(--dim,#8890a8);text-transform:uppercase;">Total</th>
        <th style="padding:6px 10px;text-align:right;font-size:0.72rem;color:var(--dim,#8890a8);text-transform:uppercase;">Est. cost</th>
        <th style="padding:6px 10px;text-align:left;font-size:0.72rem;color:var(--dim,#8890a8);text-transform:uppercase;">Share</th>
      </tr></thead>
      <tbody>{comp_rows}</tbody>
    </table>
    </div>
    {subcomp_script}"""

    # ── Per-model table ───────────────────────────────────────
    model_rows = ""
    sorted_models = sorted(
        per_model.items(),
        key=lambda x: x[1].estimated_cost_usd,
        reverse=True,
    )
    for model_name, mc in sorted_models:
        model_rows += f"""
        <tr style="border-bottom:1px solid var(--border,#2a2f42);">
          <td style="padding:6px 10px;font-size:0.85rem;max-width:200px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;"
              title="{html_mod.escape(model_name)}">{html_mod.escape(model_name[:40])}</td>
          <td style="padding:6px 10px;font-size:0.8rem;color:var(--dim,#8890a8);">{html_mod.escape(mc.provider)}</td>
          <td style="padding:6px 10px;text-align:right;font-size:0.85rem;">{mc.total_calls}</td>
          <td style="padding:6px 10px;text-align:right;font-size:0.85rem;">{mc.total_tokens:,}</td>
          <td style="padding:6px 10px;text-align:right;font-weight:600;color:#38bdf8;font-size:0.85rem;">${mc.estimated_cost_usd:.4f}</td>
        </tr>"""

    model_table = f"""
    <h3 style="font-size:0.95rem;margin:1rem 0 0.5rem;">Per-model breakdown</h3>
    <table style="width:100%;border-collapse:collapse;">
      <thead><tr style="border-bottom:1px solid var(--border,#2a2f42);">
        <th style="padding:6px 10px;text-align:left;font-size:0.72rem;color:var(--dim,#8890a8);text-transform:uppercase;">Model</th>
        <th style="padding:6px 10px;text-align:left;font-size:0.72rem;color:var(--dim,#8890a8);text-transform:uppercase;">Provider</th>
        <th style="padding:6px 10px;text-align:right;font-size:0.72rem;color:var(--dim,#8890a8);text-transform:uppercase;">Calls</th>
        <th style="padding:6px 10px;text-align:right;font-size:0.72rem;color:var(--dim,#8890a8);text-transform:uppercase;">Tokens</th>
        <th style="padding:6px 10px;text-align:right;font-size:0.72rem;color:var(--dim,#8890a8);text-transform:uppercase;">Est. cost</th>
      </tr></thead>
      <tbody>{model_rows}</tbody>
    </table>"""

    # ── Chart.js data ─────────────────────────────────────────
    chart_labels = json.dumps([c for c, _ in sorted_comps])
    chart_values = json.dumps([round(cc.estimated_cost_usd * 1000, 2) for _, cc in sorted_comps])
    chart_colors = json.dumps(["#38bdf8", "#c084fc", "#6ee7b7", "#fbbf24", "#f87171",
                                "#94a3b8", "#60a5fa", "#fb923c", "#a78bfa"][:len(sorted_comps)])

    chart_html = f"""
    <div style="max-width:500px;margin:1rem 0;">
      <canvas id="costComponentChart" height="200"></canvas>
    </div>
    <script>
    if (typeof Chart !== 'undefined') {{
      new Chart(document.getElementById('costComponentChart'), {{
        type: 'bar',
        data: {{
          labels: {chart_labels},
          datasets: [{{
            label: 'Est. cost (millicents)',
            data: {chart_values},
            backgroundColor: {chart_colors},
            borderRadius: 6,
            barThickness: 32,
          }}]
        }},
        options: {{
          indexAxis: 'y',
          responsive: true,
          plugins: {{
            legend: {{ display: false }},
            title: {{ display: true, text: 'Estimated cost by component ($×1000)', color: '#8890a8', font: {{ size: 12 }} }}
          }},
          scales: {{
            x: {{ grid: {{ color: '#1c2030' }}, ticks: {{ color: '#8890a8' }} }},
            y: {{ grid: {{ display: false }}, ticks: {{ color: '#e2e4ec', font: {{ size: 12 }} }} }}
          }}
        }}
      }});
    }}
    </script>"""

    # ── Highest cost highlights ────────────────────────────────
    highlights = ""
    if report.highest_cost_component:
        highlights += f"""
    <div style="font-size:0.82rem;color:var(--dim,#8890a8);margin-top:0.5rem;">
      Highest cost component: <strong style="color:#38bdf8;">{html_mod.escape(report.highest_cost_component)}</strong>
      &middot; Highest cost model: <strong style="color:#c084fc;">{html_mod.escape(report.highest_cost_model)}</strong>
    </div>"""

    return f"""
{_COST_MARKER}
<section class="section">
  <h2>💰 Estimated Cost Summary</h2>
  <p class="section-hint">
    LLM token usage and estimated cost for this simulation run.
    Costs are estimated using LiteLLM pricing tables and returned usage metadata — actual provider billing may differ.
    {confidence_badge}
  </p>
  {conf_notes_html}
  {budget_html}
  {pricing_html}
  {stats_html}
  {usage_html}
  {comp_table}
  {chart_html}
  {model_table}
  {highlights}
</section>
"""


def inject_cost_into_report(
    html_path: str | Path,
    cost_report: "CostReport",
) -> bool:
    """
    Inject cost summary section into an existing HTML report.

    Inserts before the Conversation Transcripts section.
    Idempotent — skips if already injected.
    Returns True on success, False on failure.
    """
    html_path = Path(html_path)
    if not html_path.exists():
        return False

    try:
        content = html_path.read_text(encoding="utf-8")
    except Exception:
        return False

    # Idempotency
    if _COST_MARKER in content:
        return False

    section = generate_cost_html(cost_report)

    # Insert before Conversation Transcripts
    h2_marker = "Conversation Transcripts</h2>"
    if h2_marker in content:
        idx = content.rfind("<section", 0, content.index(h2_marker))
        if idx >= 0:
            content = content[:idx] + section + "\n" + content[idx:]
            html_path.write_text(content, encoding="utf-8")
            return True

    # Fallback: before </body>
    if "</body>" in content:
        content = content.replace("</body>", section + "\n</body>")
        html_path.write_text(content, encoding="utf-8")
        return True

    return False
