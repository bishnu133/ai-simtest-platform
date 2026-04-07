"""
Channel Formatters — P4 #22

Renders NotificationEvent into channel-specific payloads:
  - SlackFormatter: Slack Block Kit JSON (Review #12: template support)
  - TeamsFormatter: Microsoft Teams Adaptive Card JSON
  - EmailFormatter: Subject + HTML body
  - GenericWebhookFormatter: Clean JSON (v1.0 schema)

Each formatter handles severity-based styling, report links (Review #5),
mention injection, and template customization (Review #12).
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from src.notifications.models import (
    ChannelConfig,
    NotificationEvent,
    Severity,
    TemplateConfig,
)


class BaseFormatter(ABC):
    """Abstract base class for channel formatters."""

    @abstractmethod
    def format(self, event: NotificationEvent, channel: ChannelConfig) -> dict[str, Any]:
        """
        Render an event into a channel-specific payload.

        Returns:
            Dict ready to be serialized and sent. Structure depends on channel type.
        """

    def _filter_fields(self, fields: dict[str, Any], template: TemplateConfig) -> dict[str, Any]:
        """Apply include/exclude field filters from template config."""
        if template.include_fields:
            return {k: v for k, v in fields.items() if k in template.include_fields}
        if template.exclude_fields:
            return {k: v for k, v in fields.items() if k not in template.exclude_fields}
        return fields

    def _build_title(self, event: NotificationEvent, template: TemplateConfig) -> str:
        severity_emoji = {"info": "ℹ️", "warning": "⚠️", "critical": "🚨"}.get(
            event.severity.value, "📢"
        )
        prefix = template.title_prefix or "[AI SimTest]"
        return f"{severity_emoji} {prefix} {event.event_type.value}"


class SlackFormatter(BaseFormatter):
    """
    Slack Block Kit formatter.
    Produces: {"blocks": [...], "attachments": [...]}
    """

    SEVERITY_COLORS = {
        Severity.INFO: "#36a64f",
        Severity.WARNING: "#ff9900",
        Severity.CRITICAL: "#cc0000",
    }

    def format(self, event: NotificationEvent, channel: ChannelConfig) -> dict[str, Any]:
        template = channel.template
        title = self._build_title(event, template)
        color = self.SEVERITY_COLORS.get(event.severity, "#36a64f")

        # Build fields from details
        detail_fields = self._filter_fields(
            {"run_id": event.run_id, "severity": event.severity.value,
             **event.details}, template
        )
        slack_fields = [
            {"type": "mrkdwn", "text": f"*{k}:* {v}"}
            for k, v in detail_fields.items()
            if v  # Skip empty values
        ]

        blocks = [
            {"type": "header", "text": {"type": "plain_text", "text": title[:150]}},
            {"type": "section", "text": {"type": "mrkdwn", "text": event.summary}},
        ]

        if slack_fields:
            # Slack allows max 10 fields per section
            blocks.append({"type": "section", "fields": slack_fields[:10]})

        # Report links (Review #5)
        if template.show_report_links and event.links:
            link_parts = [f"<{url}|{label}>" for label, url in event.links.items()]
            blocks.append({
                "type": "context",
                "elements": [{"type": "mrkdwn", "text": " | ".join(link_parts)}]
            })

        # Cost snapshot
        if event.cost_snapshot:
            cost_text = f"💰 Est. cost: ${event.cost_snapshot.get('estimated_total', 0):.4f}"
            blocks.append({
                "type": "context",
                "elements": [{"type": "mrkdwn", "text": cost_text}]
            })

        # Mentions
        mention = ""
        if event.severity == Severity.CRITICAL and template.mention_on_critical:
            mention = template.mention_on_critical
        elif event.severity == Severity.WARNING and template.mention_on_warning:
            mention = template.mention_on_warning

        if mention:
            blocks.append({
                "type": "section",
                "text": {"type": "mrkdwn", "text": f"cc {mention}"}
            })

        # Footer
        footer_text = template.custom_footer or f"{event.source} | {event.environment or 'local'}"
        blocks.append({
            "type": "context",
            "elements": [{"type": "mrkdwn", "text": footer_text}]
        })

        return {
            "blocks": blocks,
            "attachments": [{"color": color, "blocks": []}],
        }


class TeamsFormatter(BaseFormatter):
    """
    Microsoft Teams Adaptive Card formatter.
    Produces Adaptive Card JSON payload.
    """

    SEVERITY_COLORS = {
        Severity.INFO: "good",
        Severity.WARNING: "warning",
        Severity.CRITICAL: "attention",
    }

    def format(self, event: NotificationEvent, channel: ChannelConfig) -> dict[str, Any]:
        template = channel.template
        title = self._build_title(event, template)
        color = self.SEVERITY_COLORS.get(event.severity, "default")

        detail_fields = self._filter_fields(
            {"run_id": event.run_id, "severity": event.severity.value,
             **event.details}, template
        )
        facts = [{"title": k, "value": str(v)} for k, v in detail_fields.items() if v]

        body = [
            {"type": "TextBlock", "text": title, "weight": "bolder", "size": "medium",
             "color": color},
            {"type": "TextBlock", "text": event.summary, "wrap": True},
        ]

        if facts:
            body.append({"type": "FactSet", "facts": facts[:10]})

        # Report links as actions
        actions = []
        if template.show_report_links and event.links:
            for label, url in event.links.items():
                actions.append({
                    "type": "Action.OpenUrl", "title": label, "url": url
                })

        card = {
            "type": "message",
            "attachments": [{
                "contentType": "application/vnd.microsoft.card.adaptive",
                "content": {
                    "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
                    "type": "AdaptiveCard",
                    "version": "1.4",
                    "body": body,
                    "actions": actions,
                }
            }]
        }
        return card


class EmailFormatter(BaseFormatter):
    """
    Email formatter producing subject + HTML body.
    Returns: {"subject": str, "html_body": str, "text_body": str}
    """

    def format(self, event: NotificationEvent, channel: ChannelConfig) -> dict[str, Any]:
        template = channel.template
        prefix = template.title_prefix or "[AI SimTest]"
        subject = f"{prefix} {event.severity.value.upper()}: {event.event_type.value} — {event.summary[:80]}"

        severity_color = {"info": "#27ae60", "warning": "#e67e22", "critical": "#c0392b"}.get(
            event.severity.value, "#333"
        )

        detail_fields = self._filter_fields(
            {"run_id": event.run_id, "environment": event.environment,
             "severity": event.severity.value, **event.details}, template
        )

        detail_rows = "".join(
            f"<tr><td style='padding:4px 12px;font-weight:bold;'>{k}</td>"
            f"<td style='padding:4px 12px;'>{_html_escape(str(v))}</td></tr>"
            for k, v in detail_fields.items() if v
        )

        link_items = ""
        if template.show_report_links and event.links:
            link_items = " | ".join(
                f"<a href='{url}'>{label}</a>"
                for label, url in event.links.items()
            )

        cost_line = ""
        if event.cost_snapshot:
            cost_line = (
                f"<p style='color:#888;'>💰 Estimated cost: "
                f"${event.cost_snapshot.get('estimated_total', 0):.4f}</p>"
            )

        footer = template.custom_footer or f"{event.source} | {event.environment or 'local'}"

        html_body = f"""<div style="font-family:Arial,sans-serif;max-width:600px;">
<div style="background:{severity_color};color:#fff;padding:12px 16px;border-radius:4px 4px 0 0;">
<h2 style="margin:0;font-size:16px;">{_html_escape(event.event_type.value)}</h2>
</div>
<div style="border:1px solid #ddd;border-top:none;padding:16px;">
<p style="font-size:14px;margin:0 0 12px;">{_html_escape(event.summary)}</p>
<table style="border-collapse:collapse;width:100%;font-size:13px;">{detail_rows}</table>
{cost_line}
{f'<p style="margin-top:12px;">{link_items}</p>' if link_items else ''}
</div>
<div style="padding:8px 16px;font-size:11px;color:#999;">{_html_escape(footer)}</div>
</div>"""

        text_body = (
            f"{event.event_type.value}\n"
            f"{event.summary}\n\n"
            + "\n".join(f"{k}: {v}" for k, v in detail_fields.items() if v)
            + f"\n\n{footer}"
        )

        return {"subject": subject, "html_body": html_body, "text_body": text_body}


class GenericWebhookFormatter(BaseFormatter):
    """
    Generic webhook formatter — stable v1.0 JSON schema.
    Review #1: schema_version locked.
    """

    def format(self, event: NotificationEvent, channel: ChannelConfig) -> dict[str, Any]:
        return event.to_dict()


def _html_escape(text: str) -> str:
    """Basic HTML escaping for email content."""
    return (text.replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


# ── Formatter Factory ────────────────────────────────────────────────────────

_FORMATTER_MAP = {
    "slack": SlackFormatter,
    "teams": TeamsFormatter,
    "email": EmailFormatter,
    "generic_webhook": GenericWebhookFormatter,
}


def get_formatter(channel_type: str) -> BaseFormatter:
    """Get the appropriate formatter for a channel type."""
    cls = _FORMATTER_MAP.get(channel_type)
    if cls is None:
        return GenericWebhookFormatter()
    return cls()
