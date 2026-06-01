"""
P3 #19 — Signature HTML Report Injection

Injects a "Behavioral Signature" section into the existing HTML report.
Follows the same pattern as coverage_html.py and workflow_html.py:
  - Finds </body> in the HTML
  - Inserts a new <section> before it
  - Includes Chart.js radar chart + evidence tables

Does NOT modify any other section of the report.
"""

from __future__ import annotations

import html
from pathlib import Path

from .models import BotSignature, AnomalyFlag


def inject_signature_into_report(
    html_path: str | Path,
    signature: BotSignature,
) -> bool:
    """
    Inject behavioral signature section into existing HTML report.

    Args:
        html_path: Path to the HTML report file.
        signature: The BotSignature to render.

    Returns:
        True if injection succeeded, False otherwise.
    """
    html_path = Path(html_path)
    if not html_path.exists():
        return False

    content = html_path.read_text(encoding="utf-8")

    section_html = _build_section_html(signature)

    # Strategy: insert BEFORE the Conversation Transcripts section,
    # or before footer, or before </body> (in that priority order).
    # This places the signature after Recommendations/Coverage/Workflow
    # but before the lengthy transcripts and footer.
    insertion_markers = [
        '<!-- Conversation Transcripts',         # ideal: before transcripts
        '<section class="section">\n  <h2>💬',   # transcripts section heading
        '<footer>',                               # fallback: before footer
        '</body>',                                # last resort
    ]

    idx = -1
    for marker in insertion_markers:
        idx = content.find(marker)
        if idx >= 0:
            break

    if idx < 0:
        return False

    new_content = content[:idx] + section_html + "\n\n" + content[idx:]
    html_path.write_text(new_content, encoding="utf-8")
    return True


def _build_section_html(sig: BotSignature) -> str:
    """Build the complete HTML section for behavioral signature."""
    parts = []

    parts.append("""
<!-- Behavioral Signature (P3 #19) -->
<section class="section">
  <h2>&#x1f9ec; Behavioral Signature</h2>
  <p class="section-hint">
    How the bot communicates — tone, verbosity, consistency, and behavioral patterns.
    Measures <em>style</em>, not correctness. Reliability: {confidence}.
  </p>
""".format(confidence=html.escape(sig.reliability.confidence.value)))

    # Summary text
    if sig.summary_text:
        parts.append(f'  <p style="color:#e2e4ec;margin-bottom:1rem;font-style:italic;">{html.escape(sig.summary_text)}</p>')

    # Radar chart canvas
    parts.append("""
  <div style="max-width:400px;margin:0 auto 1.5rem;">
    <canvas id="signatureRadar" width="400" height="400"></canvas>
  </div>
""")

    # Metrics summary table
    parts.append(_build_metrics_table(sig))

    # Evidence sections
    parts.append(_build_evidence_section("Tone", sig.tone.evidence))
    parts.append(_build_evidence_section("Verbosity", sig.verbosity.evidence))
    parts.append(_build_evidence_section("Patterns", sig.patterns.evidence))
    parts.append(_build_evidence_section("Consistency", sig.consistency.evidence))

    # Top repeated phrases
    if sig.patterns.derived.top_repeated_phrases:
        parts.append(_build_phrase_table(sig))

    # Anomalies
    if sig.anomalies:
        parts.append(_build_anomaly_section(sig.anomalies))

    # Reliability notes
    if sig.reliability.notes:
        parts.append(_build_reliability_notes(sig))

    # Radar chart script
    parts.append(_build_radar_script(sig))

    parts.append("</section>")

    return "\n".join(parts)


def _build_metrics_table(sig: BotSignature) -> str:
    """Build the key metrics summary table."""
    rows = [
        ("Formality", sig.tone.derived.formality_level.value,
         f"Avg score: {sig.tone.raw.avg_formality_score:.2f}"),
        ("Verbosity", sig.verbosity.derived.verbosity_level.value,
         f"Median: {sig.verbosity.raw.median_words:.0f} words"),
        ("Repetition", f"{sig.patterns.derived.repetition_score:.2f}",
         f"{sig.patterns.raw.unique_opening_phrases} unique openings"),
        ("Consistency", f"{sig.consistency.derived.overall_consistency_score:.2f}",
         f"Across {sig.consistency.raw.conversation_count} conversations"),
        ("Empathy", f"{sig.tone.derived.empathy_frequency:.2f}/response",
         f"{sig.tone.raw.empathy_phrase_count} total phrases"),
        ("Hedging", f"{sig.tone.derived.hedge_frequency:.2f}/response",
         f"{sig.tone.raw.hedge_phrase_count} total phrases"),
        ("Refusal rate", f"{sig.tone.derived.refusal_rate:.0%}",
         f"{sig.tone.raw.refusal_count} refusals, {sig.tone.derived.refusal_redirect_rate:.0%} with redirect"),
        ("Questions", f"{sig.patterns.derived.question_asking_rate:.0%}",
         f"{sig.patterns.raw.question_asking_responses} responses ask questions"),
        ("Anomalies", str(sig.anomaly_count), "flagged responses"),
    ]

    html_rows = ""
    for label, value, detail in rows:
        html_rows += f"""
    <tr>
      <td style="padding:6px 12px;border-bottom:1px solid #1c2030;color:#8890a8;">{html.escape(label)}</td>
      <td style="padding:6px 12px;border-bottom:1px solid #1c2030;color:#e2e4ec;font-weight:500;">{html.escape(value)}</td>
      <td style="padding:6px 12px;border-bottom:1px solid #1c2030;color:#8890a8;font-size:0.85em;">{html.escape(detail)}</td>
    </tr>"""

    return f"""
  <table style="width:100%;border-collapse:collapse;margin-bottom:1.5rem;">
    <thead>
      <tr>
        <th style="padding:8px 12px;text-align:left;border-bottom:2px solid #2a3050;color:#8890a8;font-size:0.85em;">Metric</th>
        <th style="padding:8px 12px;text-align:left;border-bottom:2px solid #2a3050;color:#8890a8;font-size:0.85em;">Value</th>
        <th style="padding:8px 12px;text-align:left;border-bottom:2px solid #2a3050;color:#8890a8;font-size:0.85em;">Detail</th>
      </tr>
    </thead>
    <tbody>{html_rows}
    </tbody>
  </table>"""


def _build_evidence_section(title: str, evidence: list[dict[str, str]]) -> str:
    """Build an evidence section with metric + value + example."""
    if not evidence:
        return ""

    rows = ""
    for ev in evidence:
        example_html = ""
        if ev.get("example"):
            example_html = f'<div style="color:#8890a8;font-size:0.82em;margin-top:2px;font-style:italic;">"{html.escape(ev["example"][:150])}"</div>'
        rows += f"""
      <div style="padding:6px 0;border-bottom:1px solid #1c2030;">
        <span style="color:#60a5fa;font-weight:500;">{html.escape(ev.get("metric", ""))}</span>
        <span style="color:#8890a8;margin-left:8px;">{html.escape(ev.get("value", ""))}</span>
        {example_html}
      </div>"""

    return f"""
  <details style="margin-bottom:1rem;">
    <summary style="cursor:pointer;color:#e2e4ec;font-weight:500;padding:4px 0;">{html.escape(title)} evidence ({len(evidence)} items)</summary>
    <div style="padding:4px 0 4px 12px;">{rows}
    </div>
  </details>"""


def _build_phrase_table(sig: BotSignature) -> str:
    """Build the top repeated phrases table."""
    phrases = sig.patterns.derived.top_repeated_phrases[:8]
    if not phrases:
        return ""

    rows = ""
    for p in phrases:
        cat_badge = f'<span style="background:#2a3050;color:#8890a8;padding:1px 6px;border-radius:4px;font-size:0.78em;">{html.escape(p.category)}</span>'
        rows += f"""
    <tr>
      <td style="padding:5px 10px;border-bottom:1px solid #1c2030;color:#e2e4ec;">"{html.escape(p.phrase)}"</td>
      <td style="padding:5px 10px;border-bottom:1px solid #1c2030;color:#fbbf24;text-align:center;">{p.count}</td>
      <td style="padding:5px 10px;border-bottom:1px solid #1c2030;color:#8890a8;text-align:center;">{p.percentage:.0f}%</td>
      <td style="padding:5px 10px;border-bottom:1px solid #1c2030;">{cat_badge}</td>
    </tr>"""

    return f"""
  <details style="margin-bottom:1rem;">
    <summary style="cursor:pointer;color:#e2e4ec;font-weight:500;padding:4px 0;">Top repeated phrases ({len(phrases)})</summary>
    <table style="width:100%;border-collapse:collapse;margin-top:6px;">
      <thead>
        <tr>
          <th style="padding:6px 10px;text-align:left;border-bottom:2px solid #2a3050;color:#8890a8;font-size:0.85em;">Phrase</th>
          <th style="padding:6px 10px;text-align:center;border-bottom:2px solid #2a3050;color:#8890a8;font-size:0.85em;">Count</th>
          <th style="padding:6px 10px;text-align:center;border-bottom:2px solid #2a3050;color:#8890a8;font-size:0.85em;">% responses</th>
          <th style="padding:6px 10px;text-align:left;border-bottom:2px solid #2a3050;color:#8890a8;font-size:0.85em;">Category</th>
        </tr>
      </thead>
      <tbody>{rows}
      </tbody>
    </table>
  </details>"""


def _build_anomaly_section(anomalies: list[AnomalyFlag]) -> str:
    """Build the anomalies section."""
    rows = ""
    for a in anomalies[:10]:
        method_badge = f'<span style="background:#2a3050;color:#8890a8;padding:1px 6px;border-radius:4px;font-size:0.78em;">{html.escape(a.detection_method)}</span>'
        rows += f"""
      <div style="padding:6px 0;border-bottom:1px solid #1c2030;">
        <span style="color:#f87171;font-weight:500;">{html.escape(a.anomaly_type.value)}</span>
        {method_badge}
        <span style="color:#8890a8;margin-left:8px;">conv:{html.escape(a.conversation_id[:12])} turn:{a.turn_index}</span>
        <div style="color:#8890a8;font-size:0.85em;margin-top:2px;">{html.escape(a.description)}</div>
        <div style="color:#6b7280;font-size:0.8em;font-style:italic;margin-top:1px;">"{html.escape(a.text_snippet[:120])}"</div>
      </div>"""

    return f"""
  <details style="margin-bottom:1rem;">
    <summary style="cursor:pointer;color:#f87171;font-weight:500;padding:4px 0;">Anomalies ({len(anomalies)} flagged)</summary>
    <div style="padding:4px 0 4px 12px;">{rows}
    </div>
  </details>"""


def _build_reliability_notes(sig: BotSignature) -> str:
    """Build reliability notes section."""
    unique_notes = list(dict.fromkeys(sig.reliability.notes))  # deduplicate preserving order
    if not unique_notes:
        return ""

    items = "".join(
        f'<li style="padding:2px 0;color:#8890a8;">{html.escape(n)}</li>'
        for n in unique_notes[:8]
    )
    return f"""
  <details style="margin-bottom:0.5rem;">
    <summary style="cursor:pointer;color:#8890a8;font-size:0.85em;padding:4px 0;">Reliability notes ({len(unique_notes)})</summary>
    <ul style="margin:4px 0;padding-left:1.5rem;">{items}</ul>
  </details>"""


def _build_radar_script(sig: BotSignature) -> str:
    """Build Chart.js radar chart script for the signature dimensions."""
    # Normalize all values to 0-1 scale
    formality = sig.tone.raw.avg_formality_score
    empathy = min(sig.tone.derived.empathy_frequency, 1.0)
    verbosity_norm = min(sig.verbosity.raw.median_words / 150.0, 1.0)  # 150 words = max
    consistency = sig.consistency.derived.overall_consistency_score
    repetition = sig.patterns.derived.repetition_score
    question_rate = sig.patterns.derived.question_asking_rate

    return f"""
<script>
(function() {{
  function renderSignatureRadar() {{
    var ctx = document.getElementById('signatureRadar');
    if (!ctx) return;
    new Chart(ctx.getContext('2d'), {{
      type: 'radar',
      data: {{
        labels: ['Formality', 'Empathy', 'Verbosity', 'Consistency', 'Repetition', 'Question-asking'],
        datasets: [{{
          label: 'Behavioral Signature',
          data: [{formality:.2f}, {empathy:.2f}, {verbosity_norm:.2f}, {consistency:.2f}, {repetition:.2f}, {question_rate:.2f}],
          borderColor: '#60a5fa',
          backgroundColor: 'rgba(96, 165, 250, 0.15)',
          borderWidth: 2,
          pointBackgroundColor: '#60a5fa',
          pointRadius: 4,
        }}]
      }},
      options: {{
        responsive: true,
        plugins: {{ legend: {{ display: false }} }},
        scales: {{
          r: {{
            min: 0,
            max: 1,
            ticks: {{ stepSize: 0.25, color: '#8890a8', backdropColor: 'transparent' }},
            grid: {{ color: '#1c2030' }},
            angleLines: {{ color: '#1c2030' }},
            pointLabels: {{ color: '#e2e4ec', font: {{ size: 12 }} }}
          }}
        }}
      }}
    }});
  }}
  // Ensure Chart.js is available — load from CDN if parent report did not include it
  if (typeof Chart !== 'undefined') {{
    renderSignatureRadar();
  }} else {{
    var s = document.createElement('script');
    s.src = 'https://cdn.jsdelivr.net/npm/chart.js@4.4.1/dist/chart.umd.min.js';
    s.onload = renderSignatureRadar;
    document.head.appendChild(s);
  }}
}})();
</script>"""
