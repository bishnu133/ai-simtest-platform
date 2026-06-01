"""
Notification HTML Report Injection — P4 #22

Injects a notification delivery summary panel into the HTML report.
Review #10: Delivery observability in the final report.

Placement: Before <footer> (with </body> fallback), consistent with
the existing pattern used by coverage_html, cost_html, etc.
"""
from __future__ import annotations

from typing import Any

from ai_simtest_engine.notifications.models import NotificationSummary


def generate_notification_html(summary: NotificationSummary) -> str:
    """Generate HTML section for notification delivery summary."""
    total = summary.notifications_sent + summary.notifications_failed
    if total == 0 and summary.notifications_suppressed_rate_limit == 0:
        return ""

    # Status color
    if summary.notifications_failed > 0:
        status_color = "#c0392b"
        status_label = "Issues"
    elif summary.notifications_suppressed_rate_limit > 0:
        status_label = "Rate limited"
        status_color = "#e67e22"
    else:
        status_color = "#27ae60"
        status_label = "All delivered"

    # Per-channel rows
    channel_rows = ""
    for ch_name, ch_data in summary.per_channel.items():
        channel_rows += f"""
        <tr>
            <td style="padding:6px 12px;">{ch_name}</td>
            <td style="padding:6px 12px;text-align:center;">{ch_data.get('sent', 0)}</td>
            <td style="padding:6px 12px;text-align:center;">{ch_data.get('failed', 0)}</td>
            <td style="padding:6px 12px;text-align:center;">{ch_data.get('suppressed', 0)}</td>
        </tr>"""

    html = f"""
<div id="notificationSummary" style="margin:20px 0;padding:0 20px;">
<h2 style="color:#e8e8e8;border-bottom:1px solid #444;padding-bottom:8px;">
    📡 Notification Delivery Summary
    <span style="float:right;font-size:0.7em;color:{status_color};font-weight:normal;">
        {status_label}
    </span>
</h2>
<div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:12px;margin:12px 0;">
    <div style="background:#2a2a2a;border-radius:8px;padding:12px;text-align:center;">
        <div style="font-size:24px;font-weight:bold;color:#27ae60;">{summary.notifications_sent}</div>
        <div style="font-size:11px;color:#999;">Sent</div>
    </div>
    <div style="background:#2a2a2a;border-radius:8px;padding:12px;text-align:center;">
        <div style="font-size:24px;font-weight:bold;color:{'#c0392b' if summary.notifications_failed > 0 else '#666'};">{summary.notifications_failed}</div>
        <div style="font-size:11px;color:#999;">Failed</div>
    </div>
    <div style="background:#2a2a2a;border-radius:8px;padding:12px;text-align:center;">
        <div style="font-size:24px;font-weight:bold;color:#e67e22;">{summary.notifications_retried}</div>
        <div style="font-size:11px;color:#999;">Retried</div>
    </div>
    <div style="background:#2a2a2a;border-radius:8px;padding:12px;text-align:center;">
        <div style="font-size:24px;font-weight:bold;color:#888;">{summary.notifications_suppressed_rate_limit + summary.notifications_suppressed_dedupe}</div>
        <div style="font-size:11px;color:#999;">Suppressed</div>
    </div>
    <div style="background:#2a2a2a;border-radius:8px;padding:12px;text-align:center;">
        <div style="font-size:24px;font-weight:bold;color:#3498db;">{summary.avg_latency_ms:.0f}ms</div>
        <div style="font-size:11px;color:#999;">Avg latency</div>
    </div>
    <div style="background:#2a2a2a;border-radius:8px;padding:12px;text-align:center;">
        <div style="font-size:24px;font-weight:bold;color:{'#c0392b' if summary.circuit_breaker_trips > 0 else '#666'};">{summary.circuit_breaker_trips}</div>
        <div style="font-size:11px;color:#999;">CB trips</div>
    </div>
</div>
{f'''<table style="width:100%;border-collapse:collapse;margin:12px 0;font-size:13px;">
<thead><tr style="border-bottom:1px solid #444;">
    <th style="padding:6px 12px;text-align:left;color:#aaa;">Channel</th>
    <th style="padding:6px 12px;text-align:center;color:#aaa;">Sent</th>
    <th style="padding:6px 12px;text-align:center;color:#aaa;">Failed</th>
    <th style="padding:6px 12px;text-align:center;color:#aaa;">Suppressed</th>
</tr></thead>
<tbody>{channel_rows}</tbody>
</table>''' if channel_rows else ''}
</div>"""
    return html


def inject_notification_into_report(
    html_path: str,
    summary: NotificationSummary,
) -> bool:
    """
    Inject notification summary into an existing HTML report.

    Placement: Before <footer> (with </body> fallback).
    Returns True if injection succeeded.
    """
    html_section = generate_notification_html(summary)
    if not html_section:
        return False

    try:
        with open(html_path, "r", encoding="utf-8") as f:
            content = f.read()

        # Find injection point
        injection_point = content.find("<footer")
        if injection_point == -1:
            injection_point = content.find("</body>")
        if injection_point == -1:
            return False

        new_content = content[:injection_point] + html_section + content[injection_point:]

        with open(html_path, "w", encoding="utf-8") as f:
            f.write(new_content)

        return True
    except Exception:
        return False
