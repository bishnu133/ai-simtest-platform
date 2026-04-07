"""
Notification Data Models — P4 #22 (V1 Review-Hardened)

Review fixes applied:
  #3  — Fix guaranteed-event: side-effect-free rate check, mark after success
  #4  — Fix batched-mode: dedupe before buffering, memory cap
  #5  — Separate compact summary from full delivery log
  #7  — Payload-too-large: 413 is terminal (documented, no auto-truncation in V1)
  #10 — Payload size guardrails (max details/links/summary length)
  #12 — Concurrency: all state scoped per-engine instance (documented)
"""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional


SCHEMA_VERSION = "1.0"
SOURCE_IDENTIFIER = "ai-simtest"


class EventType(str, Enum):
    RUN_STARTED = "run_started"
    RUN_COMPLETED = "run_completed"
    RUN_FAILED = "run_failed"
    GATE_FAILED = "gate_failed"
    BUDGET_EXCEEDED = "budget_exceeded"
    CRITICAL_FAILURE = "critical_failure"
    REGRESSION_DETECTED = "regression_detected"
    POLICY_VIOLATION = "policy_violation"
    JUDGE_FAILED = "judge_failed"
    THRESHOLD_BREACHED = "threshold_breached"
    REPORT_GENERATED = "report_generated"
    BATCH_SUMMARY = "batch_summary"  # Review: distinct type for synthetic batch digests


class Severity(str, Enum):
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


class ChannelType(str, Enum):
    SLACK = "slack"
    TEAMS = "teams"
    EMAIL = "email"
    GENERIC_WEBHOOK = "generic_webhook"


class DeliveryMode(str, Enum):
    IMMEDIATE = "immediate"
    BATCHED = "batched"
    CRITICAL_ONLY = "critical_only"


class FailureClass(str, Enum):
    AUTH_ERROR = "auth_error"
    RATE_LIMITED_REMOTE = "rate_limited_remote"
    TIMEOUT = "timeout"
    DNS_ERROR = "dns_error"
    TLS_ERROR = "tls_error"
    BAD_REQUEST = "bad_request"
    SERVER_ERROR = "server_error"
    SMTP_AUTH_ERROR = "smtp_auth_error"
    SMTP_CONNECTION_ERROR = "smtp_connection_error"
    PAYLOAD_TOO_LARGE = "payload_too_large"  # Review #7: terminal in V1, no auto-truncation
    UNKNOWN = "unknown"


class DeliveryStatus(str, Enum):
    SUCCESS = "success"
    FAILED = "failed"
    SUPPRESSED_RATE_LIMIT = "suppressed_rate_limit"
    SUPPRESSED_DEDUPE = "suppressed_dedupe"
    CIRCUIT_OPEN = "circuit_open"
    DRY_RUN = "dry_run"


DEFAULT_SEVERITY: dict[EventType, Severity] = {
    EventType.RUN_STARTED: Severity.INFO,
    EventType.RUN_COMPLETED: Severity.INFO,
    EventType.RUN_FAILED: Severity.CRITICAL,
    EventType.GATE_FAILED: Severity.CRITICAL,
    EventType.BUDGET_EXCEEDED: Severity.WARNING,
    EventType.CRITICAL_FAILURE: Severity.CRITICAL,
    EventType.REGRESSION_DETECTED: Severity.WARNING,
    EventType.POLICY_VIOLATION: Severity.WARNING,
    EventType.JUDGE_FAILED: Severity.WARNING,
    EventType.THRESHOLD_BREACHED: Severity.WARNING,
    EventType.REPORT_GENERATED: Severity.INFO,
    EventType.BATCH_SUMMARY: Severity.INFO,
}

CRITICAL_BYPASS_EVENTS: frozenset[EventType] = frozenset({
    EventType.CRITICAL_FAILURE, EventType.RUN_FAILED, EventType.GATE_FAILED,
})

GUARANTEED_EVENTS: frozenset[EventType] = frozenset({
    EventType.RUN_STARTED, EventType.RUN_COMPLETED,
})

RETRYABLE_FAILURES: frozenset[FailureClass] = frozenset({
    FailureClass.RATE_LIMITED_REMOTE, FailureClass.TIMEOUT,
    FailureClass.SERVER_ERROR, FailureClass.SMTP_CONNECTION_ERROR,
})

# ── Payload Size Guardrails (Review #10) ─────────────────────────────────────
MAX_SUMMARY_LENGTH = 500
MAX_DETAIL_FIELDS = 20
MAX_LINKS = 10
MAX_DETAIL_VALUE_LENGTH = 1000
MAX_BUFFERED_EVENTS_PER_CHANNEL = 100  # Review #4: memory cap for batched mode


@dataclass
class NotificationEvent:
    """
    Single lifecycle event — the atomic unit of notification.
    """
    event_type: EventType
    summary: str
    run_id: str = ""
    severity: Severity = Severity.INFO
    details: dict[str, Any] = field(default_factory=dict)
    links: dict[str, str] = field(default_factory=dict)
    cost_snapshot: dict[str, Any] = field(default_factory=dict)
    environment: str = ""
    event_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    dedupe_key: str = ""
    occurred_at: float = field(default_factory=time.time)
    schema_version: str = SCHEMA_VERSION
    source: str = SOURCE_IDENTIFIER
    version: str = ""

    def __post_init__(self):
        if not self.dedupe_key:
            discriminator = self.details.get("gate_name", "") or \
                            self.details.get("judge_name", "") or \
                            self.details.get("policy_id", "") or \
                            self.details.get("metric_name", "")
            parts = [self.run_id, self.event_type.value]
            if discriminator:
                parts.append(str(discriminator))
            self.dedupe_key = "__".join(parts)
        if self.severity == Severity.INFO and self.event_type in DEFAULT_SEVERITY:
            self.severity = DEFAULT_SEVERITY[self.event_type]
        # Enforce payload guardrails (Review #10)
        self.summary = self.summary[:MAX_SUMMARY_LENGTH]
        if len(self.details) > MAX_DETAIL_FIELDS:
            keys = list(self.details.keys())[:MAX_DETAIL_FIELDS]
            self.details = {k: self.details[k] for k in keys}
        for k, v in list(self.details.items()):
            if isinstance(v, str) and len(v) > MAX_DETAIL_VALUE_LENGTH:
                self.details[k] = v[:MAX_DETAIL_VALUE_LENGTH] + "..."
        if len(self.links) > MAX_LINKS:
            keys = list(self.links.keys())[:MAX_LINKS]
            self.links = {k: self.links[k] for k in keys}

    def to_dict(self) -> dict[str, Any]:
        from datetime import datetime, timezone
        return {
            "schema_version": self.schema_version,
            "event_type": self.event_type.value,
            "event_id": self.event_id,
            "dedupe_key": self.dedupe_key,
            "occurred_at": datetime.fromtimestamp(self.occurred_at, tz=timezone.utc).isoformat(),
            "run_id": self.run_id,
            "environment": self.environment,
            "severity": self.severity.value,
            "summary": self.summary,
            "details": self.details,
            "links": self.links,
            "cost": self.cost_snapshot,
            "source": self.source,
            "version": self.version,
        }


@dataclass(frozen=True)
class DeliveryResult:
    """Immutable record of a single delivery attempt."""
    event_id: str
    event_type: str
    channel_name: str
    channel_type: str
    attempt: int
    max_attempts: int
    latency_ms: float
    status: str
    http_status: int = 0
    failure_class: str = ""
    suppressed_by_rate_limit: bool = False
    suppressed_by_dedupe: bool = False
    circuit_breaker_state: str = "closed"
    timestamp: str = ""
    payload_checksum: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id, "event_type": self.event_type,
            "channel_name": self.channel_name, "channel_type": self.channel_type,
            "attempt": self.attempt, "max_attempts": self.max_attempts,
            "latency_ms": self.latency_ms, "status": self.status,
            "http_status": self.http_status, "failure_class": self.failure_class,
            "suppressed_by_rate_limit": self.suppressed_by_rate_limit,
            "suppressed_by_dedupe": self.suppressed_by_dedupe,
            "circuit_breaker_state": self.circuit_breaker_state,
            "timestamp": self.timestamp, "payload_checksum": self.payload_checksum,
        }


@dataclass
class CircuitBreakerState:
    consecutive_failures: int = 0
    is_open: bool = False
    opened_at: float = 0.0
    failure_threshold: int = 3

    def record_success(self) -> None:
        self.consecutive_failures = 0

    def record_failure(self) -> bool:
        self.consecutive_failures += 1
        if self.consecutive_failures >= self.failure_threshold and not self.is_open:
            self.is_open = True
            self.opened_at = time.time()
            return True
        return False


@dataclass
class TemplateConfig:
    title_prefix: str = ""
    include_fields: list[str] = field(default_factory=list)
    exclude_fields: list[str] = field(default_factory=list)
    mention_on_critical: str = ""
    mention_on_warning: str = ""
    show_report_links: bool = True
    custom_footer: str = ""

    @classmethod
    def from_dict(cls, data: dict) -> TemplateConfig:
        return cls(**{k: data[k] for k in (
            "title_prefix", "include_fields", "exclude_fields",
            "mention_on_critical", "mention_on_warning",
            "show_report_links", "custom_footer",
        ) if k in data})


@dataclass
class ChannelConfig:
    name: str
    type: ChannelType
    url: str = ""
    events: list[EventType] = field(default_factory=list)
    delivery_mode: DeliveryMode = DeliveryMode.IMMEDIATE
    headers: dict[str, str] = field(default_factory=dict)
    fallback_channel: str = ""
    template: TemplateConfig = field(default_factory=TemplateConfig)
    smtp_host: str = ""
    smtp_port: int = 587
    use_tls: bool = True
    username: str = ""
    password: str = ""
    sender: str = ""
    recipients: list[str] = field(default_factory=list)

    def accepts_event(self, event_type: EventType) -> bool:
        if not self.events:
            return True
        return event_type in self.events

    def validate(self) -> list[str]:
        errors = []
        if not self.name:
            errors.append("Channel name is required")
        if self.type in (ChannelType.SLACK, ChannelType.TEAMS, ChannelType.GENERIC_WEBHOOK):
            if not self.url:
                errors.append(f"Channel '{self.name}': URL is required for {self.type.value}")
        if self.type == ChannelType.EMAIL:
            if not self.smtp_host:
                errors.append(f"Channel '{self.name}': smtp_host is required for email")
            if not self.sender:
                errors.append(f"Channel '{self.name}': sender is required for email")
            if not self.recipients:
                errors.append(f"Channel '{self.name}': recipients list is required for email")
        return errors

    @classmethod
    def from_dict(cls, data: dict) -> ChannelConfig:
        events = []
        for e in data.get("events", []):
            try:
                events.append(EventType(e))
            except ValueError:
                pass
        try:
            delivery_mode = DeliveryMode(data.get("delivery_mode", "immediate"))
        except ValueError:
            delivery_mode = DeliveryMode.IMMEDIATE
        channel_type = ChannelType(data.get("type", "generic_webhook"))
        template = TemplateConfig.from_dict(data.get("template", {}))
        return cls(
            name=data.get("name", ""), type=channel_type, url=data.get("url", ""),
            events=events, delivery_mode=delivery_mode,
            headers=data.get("headers", {}),
            fallback_channel=data.get("fallback_channel", ""),
            template=template,
            smtp_host=data.get("smtp_host", ""), smtp_port=data.get("smtp_port", 587),
            use_tls=data.get("use_tls", True), username=data.get("username", ""),
            password=data.get("password", ""), sender=data.get("sender", ""),
            recipients=data.get("recipients", []),
        )


@dataclass
class RetryConfig:
    max_attempts: int = 3
    backoff_base: float = 1.0
    backoff_multiplier: float = 2.0
    jitter_percent: int = 20

    @classmethod
    def from_dict(cls, data: dict) -> RetryConfig:
        return cls(
            max_attempts=data.get("max_attempts", 3),
            backoff_base=data.get("backoff_base", 1.0),
            backoff_multiplier=data.get("backoff_multiplier", 2.0),
            jitter_percent=data.get("jitter_percent", 20),
        )


@dataclass
class CircuitBreakerConfig:
    failure_threshold: int = 3

    @classmethod
    def from_dict(cls, data: dict) -> CircuitBreakerConfig:
        return cls(failure_threshold=data.get("failure_threshold", 3))


@dataclass
class NotificationConfig:
    enabled: bool = True
    max_per_run: int = 20
    dedupe_window_seconds: int = 300
    environment: str = ""
    retry: RetryConfig = field(default_factory=RetryConfig)
    circuit_breaker: CircuitBreakerConfig = field(default_factory=CircuitBreakerConfig)
    severity_overrides: dict[str, Severity] = field(default_factory=dict)
    channels: list[ChannelConfig] = field(default_factory=list)
    max_per_event_type: int = 5
    max_per_channel: int = 10

    def resolve_severity(self, event_type: EventType) -> Severity:
        if event_type.value in self.severity_overrides:
            return self.severity_overrides[event_type.value]
        return DEFAULT_SEVERITY.get(event_type, Severity.INFO)

    def get_channel(self, name: str) -> Optional[ChannelConfig]:
        for ch in self.channels:
            if ch.name == name:
                return ch
        return None

    def validate(self) -> list[str]:
        errors = []
        if not self.channels:
            errors.append("No notification channels configured")
        seen_names = set()
        for ch in self.channels:
            if ch.name in seen_names:
                errors.append(f"Duplicate channel name: '{ch.name}'")
            seen_names.add(ch.name)
            errors.extend(ch.validate())
            if ch.fallback_channel and ch.fallback_channel == ch.name:
                errors.append(f"Channel '{ch.name}': fallback_channel cannot reference itself")
        for ch in self.channels:
            if ch.fallback_channel and ch.fallback_channel not in seen_names:
                errors.append(f"Channel '{ch.name}': fallback_channel '{ch.fallback_channel}' not found")
        return errors

    @classmethod
    def from_dict(cls, data: dict) -> NotificationConfig:
        notif_data = data.get("notifications", data)
        sev_overrides = {}
        for event_str, sev_str in notif_data.get("severity_overrides", {}).items():
            try:
                sev_overrides[event_str] = Severity(sev_str)
            except ValueError:
                pass
        channels = [ChannelConfig.from_dict(cd) for cd in notif_data.get("channels", [])]
        return cls(
            enabled=notif_data.get("enabled", True),
            max_per_run=notif_data.get("max_per_run", 20),
            dedupe_window_seconds=notif_data.get("dedupe_window_seconds", 300),
            environment=notif_data.get("environment", ""),
            retry=RetryConfig.from_dict(notif_data.get("retry", {})),
            circuit_breaker=CircuitBreakerConfig.from_dict(notif_data.get("circuit_breaker", {})),
            severity_overrides=sev_overrides, channels=channels,
            max_per_event_type=notif_data.get("max_per_event_type", 5),
            max_per_channel=notif_data.get("max_per_channel", 10),
        )


class DedupeTracker:
    """
    Per-channel duplicate event suppression.
    Review #12: All state is per-tracker instance. No shared global state.
    """
    def __init__(self, window_seconds: int = 300):
        self.window_seconds = window_seconds
        self._seen: dict[str, dict[str, tuple[float, Severity]]] = {}

    def is_duplicate(self, channel_name: str, dedupe_key: str,
                     severity: Severity, now: float | None = None) -> bool:
        now = now or time.time()
        channel_seen = self._seen.get(channel_name, {})
        if dedupe_key not in channel_seen:
            return False
        prev_time, prev_severity = channel_seen[dedupe_key]
        if (now - prev_time) > self.window_seconds:
            return False
        sev_order = {Severity.INFO: 0, Severity.WARNING: 1, Severity.CRITICAL: 2}
        if sev_order.get(severity, 0) > sev_order.get(prev_severity, 0):
            return False
        return True

    def mark_seen(self, channel_name: str, dedupe_key: str,
                  severity: Severity, now: float | None = None) -> None:
        now = now or time.time()
        if channel_name not in self._seen:
            self._seen[channel_name] = {}
        self._seen[channel_name][dedupe_key] = (now, severity)

    def cleanup_expired(self, now: float | None = None) -> int:
        now = now or time.time()
        removed = 0
        for ch in list(self._seen.keys()):
            expired = [k for k, (ts, _) in self._seen[ch].items() if (now - ts) > self.window_seconds]
            for k in expired:
                del self._seen[ch][k]
                removed += 1
            if not self._seen[ch]:
                del self._seen[ch]
        return removed


@dataclass
class NotificationSummary:
    """
    Summary metrics for the notification delivery pipeline.
    Review #5: Compact summary + full delivery log kept separate.
    """
    notifications_sent: int = 0
    notifications_failed: int = 0
    notifications_retried: int = 0
    notifications_suppressed_rate_limit: int = 0
    notifications_suppressed_dedupe: int = 0
    avg_latency_ms: float = 0.0
    circuit_breaker_trips: int = 0
    per_channel: dict[str, dict[str, int]] = field(default_factory=dict)
    delivery_log: list[dict[str, Any]] = field(default_factory=list)

    def to_summary_dict(self) -> dict[str, Any]:
        """Compact aggregate summary (for summary.json)."""
        return {
            "notifications_sent": self.notifications_sent,
            "notifications_failed": self.notifications_failed,
            "notifications_retried": self.notifications_retried,
            "notifications_suppressed_rate_limit": self.notifications_suppressed_rate_limit,
            "notifications_suppressed_dedupe": self.notifications_suppressed_dedupe,
            "avg_latency_ms": round(self.avg_latency_ms, 1),
            "circuit_breaker_trips": self.circuit_breaker_trips,
            "per_channel": self.per_channel,
            "delivery_log_entries": len(self.delivery_log),
        }

    def to_full_dict(self) -> dict[str, Any]:
        """Full detailed output including delivery log (for notification_log.json). Review #5."""
        d = self.to_summary_dict()
        d["delivery_log"] = self.delivery_log
        return d

    # Backward compat alias
    def to_dict(self) -> dict[str, Any]:
        return self.to_summary_dict()
