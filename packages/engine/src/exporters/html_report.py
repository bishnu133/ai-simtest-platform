"""
HTML Report Exporter - Generates beautiful, self-contained visual reports.
No external dependencies - pure HTML/CSS/JS with inline Chart.js from CDN.
"""

from __future__ import annotations

import html
import json
from datetime import datetime, timezone
from pathlib import Path

from src.core.logging import get_logger
from src.models import (
    JudgedConversation,
    JudgmentLabel,
    Persona,
    SimulationReport,
)

logger = get_logger(__name__)


class HTMLReportExporter:
    """
    Generates a self-contained HTML report with visual charts and conversation viewer.
    """

    def export(
        self,
        report: SimulationReport,
        personas: list[Persona],
        output_path: str | Path,
    ) -> Path:
        path = Path(output_path)
        html_content = self._render(report, personas)
        path.write_text(html_content, encoding="utf-8")
        logger.info("exported_html_report", path=str(path))
        return path

    def _render(self, report: SimulationReport, personas: list[Persona]) -> str:
        s = report.summary
        persona_map = {p.id: p for p in personas}

        # Prepare data
        judge_data = report.score_by_judge
        persona_type_data = report.score_by_persona_type

        # Per-persona scores
        persona_scores = []
        for jc in report.judged_conversations:
            p = persona_map.get(jc.conversation.persona_id)
            persona_scores.append({
                "name": p.name if p else "Unknown",
                "type": p.persona_type.value if p else "unknown",
                "score": round(jc.overall_score, 3),
                "pass_rate": round(jc.pass_rate, 3),
                "turns": len(jc.judged_turns),
                "failures": len(jc.failure_modes),
            })

        # Label counts
        label_counts = {"PASS": 0, "WARNING": 0, "FAIL": 0}
        for jc in report.judged_conversations:
            for jt in jc.judged_turns:
                label_counts[jt.overall_label.value] = label_counts.get(jt.overall_label.value, 0) + 1

        # Per-judge per-persona scores for radar
        judge_persona_data = {}
        for jc in report.judged_conversations:
            p = persona_map.get(jc.conversation.persona_id)
            pname = p.name if p else "Unknown"
            judge_scores_for_persona = {}
            for jt in jc.judged_turns:
                for j in jt.judgments:
                    judge_scores_for_persona.setdefault(j.judge_name, []).append(j.score)
            judge_persona_data[pname] = {
                k: round(sum(v) / len(v), 3) for k, v in judge_scores_for_persona.items()
            }

        # Conversations HTML
        convos_html = self._render_conversations(report.judged_conversations, persona_map)
        patterns_html = self._render_failure_patterns(report.failure_patterns)
        recs_html = self._render_recommendations(report.recommendations)
        persona_cards_html = self._render_persona_cards(persona_scores)

        return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>AI SimTest Report — {_esc(s.simulation_name)}</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.1/dist/chart.umd.min.js"></script>
<style>
{self._get_css()}
</style>
</head>
<body>

<header>
  <div class="header-top">
    <h1>AI SimTest Report</h1>
    <span class="header-badge">v0.1.0</span>
  </div>
  <p class="header-sub">{_esc(s.simulation_name)} · {s.timestamp.strftime('%B %d, %Y at %H:%M UTC') if hasattr(s.timestamp, 'strftime') else str(s.timestamp)}</p>
</header>

<!-- Stats Row -->
<div class="stats-row">
  <div class="stat-card">
    <div class="stat-value">{s.total_personas}</div>
    <div class="stat-label">Personas</div>
  </div>
  <div class="stat-card">
    <div class="stat-value">{s.total_conversations}</div>
    <div class="stat-label">Conversations</div>
  </div>
  <div class="stat-card">
    <div class="stat-value">{s.total_turns}</div>
    <div class="stat-label">Turns Judged</div>
  </div>
  <div class="stat-card">
    <div class="stat-value {_score_class(s.pass_rate)}">{s.pass_rate:.0%}</div>
    <div class="stat-label">Pass Rate</div>
  </div>
  <div class="stat-card">
    <div class="stat-value {_score_class(s.average_score)}">{s.average_score:.2f}</div>
    <div class="stat-label">Avg Score</div>
  </div>
  <div class="stat-card">
    <div class="stat-value fail">{s.critical_failures}</div>
    <div class="stat-label">Critical Failures</div>
  </div>
  <div class="stat-card">
    <div class="stat-value">{s.execution_time_seconds:.1f}s</div>
    <div class="stat-label">Execution Time</div>
  </div>
</div>

<!-- Charts Row -->
<div class="charts-row">
  <div class="chart-card">
    <h3>Judge Scores</h3>
    <canvas id="judgeChart" height="220"></canvas>
  </div>
  <div class="chart-card">
    <h3>Turn Labels</h3>
    <canvas id="labelChart" height="220"></canvas>
  </div>
  <div class="chart-card">
    <h3>Score by Persona Type</h3>
    <canvas id="personaTypeChart" height="220"></canvas>
  </div>
</div>

<!-- Persona Cards -->
<section class="section">
  <h2>👥 Persona Results</h2>
  <div class="persona-grid">
    {persona_cards_html}
  </div>
</section>

<!-- Failure Patterns -->
<section class="section">
  <h2>❌ Failure Patterns</h2>
  {patterns_html}
</section>

<!-- Recommendations -->
<section class="section">
  <h2>✅ Recommendations</h2>
  {recs_html}
</section>

<!-- Conversations -->
<section class="section">
  <h2>💬 Conversation Transcripts</h2>
  <p class="section-hint">Click a conversation to expand the full transcript with judge annotations.</p>
  {convos_html}
</section>

<footer>
  Generated by <strong>AI SimTest</strong> v0.1.0 · Open-source AI Simulation Testing Platform
</footer>

<script>
{self._get_chart_js(judge_data, label_counts, persona_type_data)}

// Toggle conversations
document.querySelectorAll('.convo-header').forEach(el => {{
  el.addEventListener('click', () => {{
    const body = el.nextElementSibling;
    body.classList.toggle('open');
    el.classList.toggle('expanded');
  }});
}});
</script>

</body>
</html>"""

    def _render_persona_cards(self, persona_scores: list[dict]) -> str:
        cards = []
        for ps in sorted(persona_scores, key=lambda x: x["score"]):
            score_cls = _score_class(ps["score"])
            type_badge = {"standard": "type-standard", "edge_case": "type-edge", "adversarial": "type-adversarial"}.get(ps["type"], "")
            cards.append(f"""
    <div class="persona-card">
      <div class="persona-header">
        <span class="persona-name">{_esc(ps['name'])}</span>
        <span class="persona-type {type_badge}">{_esc(ps['type'])}</span>
      </div>
      <div class="persona-stats">
        <div><span class="ps-val {score_cls}">{ps['score']:.2f}</span><span class="ps-lbl">Score</span></div>
        <div><span class="ps-val">{ps['turns']}</span><span class="ps-lbl">Turns</span></div>
        <div><span class="ps-val {_score_class(ps['pass_rate'])}">{ps['pass_rate']:.0%}</span><span class="ps-lbl">Pass Rate</span></div>
        <div><span class="ps-val fail">{ps['failures']}</span><span class="ps-lbl">Issues</span></div>
      </div>
    </div>""")
        return "\n".join(cards)

    def _render_conversations(self, judged_convos: list[JudgedConversation], persona_map: dict) -> str:
        blocks = []
        for jc in judged_convos:
            p = persona_map.get(jc.conversation.persona_id)
            pname = p.name if p else "Unknown"
            ptype = p.persona_type.value if p else "unknown"
            score_cls = _score_class(jc.overall_score)

            # Build turns
            turns_html = []
            judged_turn_map = {jt.turn.id: jt for jt in jc.judged_turns}

            for turn in jc.conversation.turns:
                speaker = turn.speaker
                msg = _esc(turn.message)
                latency = f' <span class="turn-latency">{turn.latency_ms:.0f}ms</span>' if turn.latency_ms else ""

                # Check if this is a judged bot turn
                jt = judged_turn_map.get(turn.id)
                judge_badge = ""
                if jt:
                    lbl_cls = jt.overall_label.value.lower()
                    judge_scores = " · ".join(
                        f"{j.judge_name}: {j.score:.0%}" for j in jt.judgments
                    )
                    issues_html = ""
                    if jt.issues:
                        issues_list = "".join(f"<li>{_esc(i[:120])}</li>" for i in jt.issues[:3])
                        issues_html = f'<ul class="turn-issues">{issues_list}</ul>'
                    judge_badge = f"""
                    <div class="turn-judgment {lbl_cls}">
                      <span class="judgment-label">{jt.overall_label.value}</span>
                      <span class="judgment-score">{jt.overall_score:.2f}</span>
                      <span class="judgment-details">{judge_scores}</span>
                      {issues_html}
                    </div>"""

                turns_html.append(f"""
              <div class="turn turn-{speaker}">
                <div class="turn-speaker">{speaker.upper()}{latency}</div>
                <div class="turn-msg">{msg}</div>
                {judge_badge}
              </div>""")

            turns_block = "\n".join(turns_html)

            blocks.append(f"""
  <div class="convo-block">
    <div class="convo-header">
      <span class="convo-persona">{_esc(pname)}</span>
      <span class="persona-type type-{ptype}">{_esc(ptype)}</span>
      <span class="convo-score {score_cls}">{jc.overall_score:.2f}</span>
      <span class="convo-turns">{len(jc.conversation.turns)} turns</span>
      <span class="convo-expand">▶</span>
    </div>
    <div class="convo-body">
      {turns_block}
    </div>
  </div>""")
        return "\n".join(blocks)

    def _render_failure_patterns(self, patterns) -> str:
        if not patterns:
            return '<p class="no-data">No failure patterns detected.</p>'
        rows = []
        for fp in patterns:
            sev_cls = fp.severity.value.lower()
            rows.append(f"""
      <tr>
        <td><span class="severity-dot {sev_cls}"></span>{_esc(fp.pattern_name)}</td>
        <td class="center">{fp.frequency}</td>
        <td><span class="severity-badge {sev_cls}">{fp.severity.value}</span></td>
      </tr>""")
        return f"""
    <table class="pattern-table">
      <thead><tr><th>Pattern</th><th>Freq</th><th>Severity</th></tr></thead>
      <tbody>{"".join(rows)}</tbody>
    </table>"""

    def _render_recommendations(self, recs: list[str]) -> str:
        if not recs:
            return '<p class="no-data">No recommendations — all metrics look healthy!</p>'
        items = []
        for r in recs:
            cls = "rec-critical" if "CRITICAL" in r else "rec-medium" if "MEDIUM" in r or "HIGH" in r else "rec-low"
            items.append(f'<div class="rec-item {cls}">{_esc(r)}</div>')
        return "\n".join(items)

    def _get_chart_js(self, judge_data: dict, label_counts: dict, persona_type_data: dict) -> str:
        judge_labels = json.dumps(list(judge_data.keys()))
        judge_values = json.dumps([round(v * 100, 1) for v in judge_data.values()])
        judge_colors = json.dumps([_score_color(v) for v in judge_data.values()])

        return f"""
// Judge Scores Bar Chart
new Chart(document.getElementById('judgeChart'), {{
  type: 'bar',
  data: {{
    labels: {judge_labels},
    datasets: [{{ label: 'Score %', data: {judge_values}, backgroundColor: {judge_colors}, borderRadius: 6, barThickness: 40 }}]
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

// Label Distribution Donut
new Chart(document.getElementById('labelChart'), {{
  type: 'doughnut',
  data: {{
    labels: ['Pass', 'Warning', 'Fail'],
    datasets: [{{
      data: [{label_counts.get('PASS', 0)}, {label_counts.get('WARNING', 0)}, {label_counts.get('FAIL', 0)}],
      backgroundColor: ['#6ee7b7', '#fbbf24', '#f87171'],
      borderWidth: 0,
      spacing: 2,
    }}]
  }},
  options: {{
    responsive: true,
    cutout: '60%',
    plugins: {{
      legend: {{ position: 'bottom', labels: {{ color: '#8890a8', padding: 12 }} }}
    }}
  }}
}});

// Persona Type Bar Chart
new Chart(document.getElementById('personaTypeChart'), {{
  type: 'bar',
  data: {{
    labels: {json.dumps(list(persona_type_data.keys()))},
    datasets: [{{
      label: 'Avg Score',
      data: {json.dumps([round(v * 100, 1) for v in persona_type_data.values()])},
      backgroundColor: {json.dumps([_score_color(v) for v in persona_type_data.values()])},
      borderRadius: 6,
      barThickness: 40,
    }}]
  }},
  options: {{
    responsive: true,
    plugins: {{ legend: {{ display: false }} }},
    scales: {{
      y: {{ min: 0, max: 100, grid: {{ color: '#1c2030' }}, ticks: {{ color: '#8890a8' }} }},
      x: {{ grid: {{ display: false }}, ticks: {{ color: '#e2e4ec' }} }}
    }}
  }}
}});
"""

    def _get_css(self) -> str:
        return """
:root {
  --bg: #0c0e14; --surface: #151822; --surface2: #1c2030; --border: #2a2f42;
  --text: #e2e4ec; --dim: #8890a8; --pass: #6ee7b7; --warn: #fbbf24; --fail: #f87171;
  --accent: #38bdf8;
}
* { margin: 0; padding: 0; box-sizing: border-box; }
body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; background: var(--bg); color: var(--text); line-height: 1.6; }
header { padding: 2rem 2rem 1rem; border-bottom: 1px solid var(--border); }
.header-top { display: flex; align-items: center; gap: 0.75rem; }
h1 { font-size: 1.75rem; font-weight: 700; }
.header-badge { background: var(--surface2); border: 1px solid var(--border); padding: 0.15rem 0.5rem; border-radius: 4px; font-size: 0.75rem; color: var(--dim); }
.header-sub { color: var(--dim); font-size: 0.9rem; margin-top: 0.25rem; }
h2 { font-size: 1.3rem; font-weight: 700; margin-bottom: 1rem; }
h3 { font-size: 1rem; font-weight: 600; margin-bottom: 0.75rem; color: var(--dim); }

/* Stats */
.stats-row { display: grid; grid-template-columns: repeat(auto-fit, minmax(130px, 1fr)); gap: 0.75rem; padding: 1.5rem 2rem; }
.stat-card { background: var(--surface); border: 1px solid var(--border); border-radius: 10px; padding: 1rem; text-align: center; }
.stat-value { font-size: 1.75rem; font-weight: 700; font-variant-numeric: tabular-nums; }
.stat-label { font-size: 0.75rem; color: var(--dim); text-transform: uppercase; letter-spacing: 0.04em; margin-top: 0.15rem; }
.pass { color: var(--pass); } .warn { color: var(--warn); } .fail { color: var(--fail); }

/* Charts */
.charts-row { display: grid; grid-template-columns: repeat(auto-fit, minmax(300px, 1fr)); gap: 1rem; padding: 0 2rem 1rem; }
.chart-card { background: var(--surface); border: 1px solid var(--border); border-radius: 10px; padding: 1.25rem; }

/* Sections */
.section { padding: 1.5rem 2rem; border-top: 1px solid var(--border); }
.section-hint { color: var(--dim); font-size: 0.85rem; margin-bottom: 1rem; }

/* Persona Cards */
.persona-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(280px, 1fr)); gap: 0.75rem; }
.persona-card { background: var(--surface); border: 1px solid var(--border); border-radius: 10px; padding: 1rem; }
.persona-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 0.75rem; }
.persona-name { font-weight: 600; font-size: 0.9rem; }
.persona-type { font-size: 0.7rem; padding: 0.1rem 0.5rem; border-radius: 10px; text-transform: uppercase; letter-spacing: 0.03em; }
.type-standard { background: rgba(56,189,248,0.15); color: var(--accent); }
.type-edge, .type-edge_case { background: rgba(251,191,36,0.15); color: var(--warn); }
.type-adversarial { background: rgba(248,113,113,0.15); color: var(--fail); }
.persona-stats { display: grid; grid-template-columns: repeat(4, 1fr); gap: 0.5rem; text-align: center; }
.ps-val { display: block; font-size: 1.1rem; font-weight: 700; font-variant-numeric: tabular-nums; }
.ps-lbl { display: block; font-size: 0.65rem; color: var(--dim); text-transform: uppercase; }

/* Failure Patterns Table */
.pattern-table { width: 100%; border-collapse: collapse; }
.pattern-table th { text-align: left; font-size: 0.75rem; color: var(--dim); text-transform: uppercase; padding: 0.5rem 0.75rem; border-bottom: 1px solid var(--border); }
.pattern-table td { padding: 0.6rem 0.75rem; border-bottom: 1px solid rgba(42,47,66,0.5); font-size: 0.88rem; }
.center { text-align: center; }
.severity-dot { display: inline-block; width: 8px; height: 8px; border-radius: 50%; margin-right: 0.5rem; }
.severity-dot.high, .severity-dot.critical { background: var(--fail); }
.severity-dot.medium { background: var(--warn); }
.severity-dot.low { background: var(--pass); }
.severity-badge { font-size: 0.7rem; padding: 0.1rem 0.45rem; border-radius: 4px; text-transform: uppercase; font-weight: 600; }
.severity-badge.high, .severity-badge.critical { background: rgba(248,113,113,0.15); color: var(--fail); }
.severity-badge.medium { background: rgba(251,191,36,0.15); color: var(--warn); }
.severity-badge.low { background: rgba(110,231,183,0.15); color: var(--pass); }

/* Recommendations */
.rec-item { padding: 0.6rem 1rem; border-radius: 8px; margin-bottom: 0.5rem; font-size: 0.9rem; border-left: 3px solid; }
.rec-critical { background: rgba(248,113,113,0.08); border-color: var(--fail); }
.rec-medium { background: rgba(251,191,36,0.08); border-color: var(--warn); }
.rec-low { background: rgba(110,231,183,0.08); border-color: var(--pass); }

/* Conversations */
.convo-block { background: var(--surface); border: 1px solid var(--border); border-radius: 10px; margin-bottom: 0.5rem; overflow: hidden; }
.convo-header { display: flex; align-items: center; gap: 0.75rem; padding: 0.75rem 1rem; cursor: pointer; user-select: none; transition: background 0.15s; }
.convo-header:hover { background: var(--surface2); }
.convo-header.expanded { border-bottom: 1px solid var(--border); }
.convo-header.expanded .convo-expand { transform: rotate(90deg); }
.convo-persona { font-weight: 600; font-size: 0.9rem; }
.convo-score { font-weight: 700; font-variant-numeric: tabular-nums; margin-left: auto; }
.convo-turns { font-size: 0.8rem; color: var(--dim); }
.convo-expand { color: var(--dim); font-size: 0.8rem; transition: transform 0.2s; }
.convo-body { display: none; padding: 0.75rem 1rem; }
.convo-body.open { display: block; }

/* Turns */
.turn { margin-bottom: 0.75rem; }
.turn-speaker { font-size: 0.7rem; font-weight: 700; text-transform: uppercase; color: var(--dim); letter-spacing: 0.04em; margin-bottom: 0.2rem; }
.turn-latency { font-weight: 400; color: var(--border); margin-left: 0.4rem; }
.turn-user .turn-msg { background: var(--surface2); border-radius: 10px 10px 10px 2px; padding: 0.6rem 0.85rem; font-size: 0.88rem; display: inline-block; max-width: 85%; }
.turn-bot .turn-msg { background: rgba(56,189,248,0.08); border: 1px solid rgba(56,189,248,0.15); border-radius: 10px 10px 2px 10px; padding: 0.6rem 0.85rem; font-size: 0.88rem; display: inline-block; max-width: 85%; }

/* Judge annotations on turns */
.turn-judgment { margin-top: 0.35rem; padding: 0.35rem 0.65rem; border-radius: 6px; font-size: 0.78rem; display: inline-flex; flex-wrap: wrap; align-items: center; gap: 0.5rem; }
.turn-judgment.pass { background: rgba(110,231,183,0.1); border: 1px solid rgba(110,231,183,0.2); }
.turn-judgment.warning { background: rgba(251,191,36,0.1); border: 1px solid rgba(251,191,36,0.2); }
.turn-judgment.fail { background: rgba(248,113,113,0.1); border: 1px solid rgba(248,113,113,0.2); }
.judgment-label { font-weight: 700; text-transform: uppercase; font-size: 0.7rem; }
.turn-judgment.pass .judgment-label { color: var(--pass); }
.turn-judgment.warning .judgment-label { color: var(--warn); }
.turn-judgment.fail .judgment-label { color: var(--fail); }
.judgment-score { font-weight: 600; font-variant-numeric: tabular-nums; }
.judgment-details { color: var(--dim); font-size: 0.75rem; }
.turn-issues { margin-top: 0.3rem; padding-left: 1rem; font-size: 0.78rem; color: var(--dim); width: 100%; }
.turn-issues li { margin-bottom: 0.15rem; }

.no-data { color: var(--dim); font-style: italic; }
footer { text-align: center; padding: 2rem; color: var(--dim); font-size: 0.8rem; border-top: 1px solid var(--border); }

@media (max-width: 768px) {
  .stats-row { grid-template-columns: repeat(3, 1fr); }
  .charts-row { grid-template-columns: 1fr; }
  header, .section { padding-left: 1rem; padding-right: 1rem; }
}
"""


# ---- Helpers ----

def _esc(text: str) -> str:
    """HTML-escape text."""
    return html.escape(str(text))


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
