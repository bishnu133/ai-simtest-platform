"""
Webhook Notifications Module — P4 #22 (V1 Review-Hardened)

Enterprise-grade notification system for AI SimTest.
All state is per-engine-instance. No shared globals. Review #12.
"""
from ai_simtest_engine.notifications.models import (
    NotificationEvent, EventType, Severity,
    ChannelConfig, ChannelType, NotificationConfig,
    DeliveryMode, TemplateConfig, RetryConfig, CircuitBreakerConfig,
    DeliveryResult, DeliveryStatus, FailureClass,
    CircuitBreakerState, DedupeTracker, NotificationSummary,
    SCHEMA_VERSION, DEFAULT_SEVERITY, CRITICAL_BYPASS_EVENTS,
    GUARANTEED_EVENTS, RETRYABLE_FAILURES,
    MAX_SUMMARY_LENGTH, MAX_DETAIL_FIELDS, MAX_LINKS,
    MAX_BUFFERED_EVENTS_PER_CHANNEL,
)
from ai_simtest_engine.notifications.engine import NotificationEngine
from ai_simtest_engine.notifications.loader import (
    load_notification_config, lint_notification_config,
    redact_url, redact_token,
)
from ai_simtest_engine.notifications.formatters import (
    BaseFormatter, SlackFormatter, TeamsFormatter,
    EmailFormatter, GenericWebhookFormatter, get_formatter,
)
from ai_simtest_engine.notifications.delivery import (
    classify_failure, compute_backoff, compute_payload_checksum,
    deliver_http, deliver_email,
)
from ai_simtest_engine.notifications.notification_html import (
    generate_notification_html, inject_notification_into_report,
)

__all__ = [
    "NotificationEngine", "NotificationEvent", "EventType", "Severity",
    "NotificationConfig", "ChannelConfig", "ChannelType", "DeliveryMode",
    "TemplateConfig", "RetryConfig", "CircuitBreakerConfig",
    "DeliveryResult", "DeliveryStatus", "FailureClass", "NotificationSummary",
    "CircuitBreakerState", "DedupeTracker",
    "load_notification_config", "lint_notification_config",
    "redact_url", "redact_token",
    "BaseFormatter", "SlackFormatter", "TeamsFormatter",
    "EmailFormatter", "GenericWebhookFormatter", "get_formatter",
    "classify_failure", "compute_backoff", "compute_payload_checksum",
    "deliver_http", "deliver_email",
    "generate_notification_html", "inject_notification_into_report",
    "SCHEMA_VERSION", "DEFAULT_SEVERITY", "CRITICAL_BYPASS_EVENTS",
    "GUARANTEED_EVENTS", "RETRYABLE_FAILURES",
]
