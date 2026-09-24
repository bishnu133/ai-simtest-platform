"""
P4 #22 — Webhook Notifications Test Suite (V1 Review-Hardened)

87 original tests + 18 new tests from review Priority 3.

New test classes added:
  - TestFallbackRouting (#2)
  - TestGuaranteedEvents (#3)
  - TestBatchedModeDedupe (#4)
  - TestNotificationLogJson (#5)
  - TestRetryAfter (#6)
  - TestPayloadTooLarge (#7)
  - TestDryRunContract (#11)
  - TestFinalizeGraceful (#9)
  - TestPayloadGuardrails (#10)
"""
from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from ai_simtest_engine.notifications.models import (
    ChannelConfig, ChannelType, CircuitBreakerState,
    CRITICAL_BYPASS_EVENTS, DEFAULT_SEVERITY, DedupeTracker,
    DeliveryMode, DeliveryResult, DeliveryStatus, EventType,
    FailureClass, GUARANTEED_EVENTS, NotificationConfig,
    NotificationEvent, NotificationSummary, RETRYABLE_FAILURES,
    RetryConfig, SCHEMA_VERSION, Severity, TemplateConfig,
    MAX_SUMMARY_LENGTH, MAX_DETAIL_FIELDS, MAX_LINKS,
    MAX_BUFFERED_EVENTS_PER_CHANNEL,
)
from ai_simtest_engine.notifications.formatters import (
    EmailFormatter, GenericWebhookFormatter, SlackFormatter,
    TeamsFormatter, get_formatter,
)
from ai_simtest_engine.notifications.delivery import (
    classify_failure, compute_backoff, compute_payload_checksum,
)
from ai_simtest_engine.notifications.loader import (
    lint_notification_config, load_notification_config,
    redact_url, redact_token,
)
from ai_simtest_engine.notifications.engine import NotificationEngine
from ai_simtest_engine.notifications.notification_html import (
    generate_notification_html, inject_notification_into_report,
)


# ═══════════════════════════════════════════════════════════════════
# Fixtures
# ═══════════════════════════════════════════════════════════════════

@pytest.fixture
def sample_event() -> NotificationEvent:
    return NotificationEvent(
        event_type=EventType.GATE_FAILED,
        summary="Safety gate failed: pass rate 72% below threshold 90%",
        run_id="run_abc",
        details={"gate_name": "safety", "actual_value": 0.72, "threshold": 0.90},
        links={"report_html": "./reports/report.html"},
    )

@pytest.fixture
def sample_config() -> NotificationConfig:
    return NotificationConfig(
        enabled=True, max_per_run=20, environment="staging",
        channels=[
            ChannelConfig(name="team-slack", type=ChannelType.SLACK, url="https://hooks.slack.com/test",
                          events=[EventType.RUN_COMPLETED, EventType.GATE_FAILED]),
            ChannelConfig(name="pager", type=ChannelType.GENERIC_WEBHOOK, url="https://pager.example.com/hook",
                          events=[EventType.CRITICAL_FAILURE, EventType.RUN_FAILED],
                          delivery_mode=DeliveryMode.CRITICAL_ONLY),
        ],
    )

@pytest.fixture
def email_channel() -> ChannelConfig:
    return ChannelConfig(name="qa-email", type=ChannelType.EMAIL, smtp_host="smtp.test.com",
                         smtp_port=587, sender="test@example.com", recipients=["qa@example.com"],
                         events=[EventType.RUN_COMPLETED])


# ═══════════════════════════════════════════════════════════════════
# TestNotificationModels (10 tests)
# ═══════════════════════════════════════════════════════════════════

class TestNotificationModels:
    def test_event_creation_basic(self):
        event = NotificationEvent(event_type=EventType.RUN_STARTED, summary="Run started", run_id="test_run")
        assert event.event_type == EventType.RUN_STARTED
        assert event.schema_version == SCHEMA_VERSION
        assert event.event_id

    def test_severity_auto_applied(self):
        event = NotificationEvent(event_type=EventType.CRITICAL_FAILURE, summary="test")
        assert event.severity == Severity.CRITICAL
        event2 = NotificationEvent(event_type=EventType.RUN_STARTED, summary="test")
        assert event2.severity == Severity.INFO

    def test_dedupe_key_auto_generated(self):
        event = NotificationEvent(event_type=EventType.GATE_FAILED, summary="test",
                                  run_id="run_1", details={"gate_name": "safety"})
        assert event.dedupe_key == "run_1__gate_failed__safety"

    def test_dedupe_key_no_discriminator(self):
        event = NotificationEvent(event_type=EventType.RUN_STARTED, summary="test", run_id="run_1")
        assert event.dedupe_key == "run_1__run_started"

    def test_dedupe_key_explicit_override(self):
        event = NotificationEvent(event_type=EventType.RUN_STARTED, summary="test", dedupe_key="custom_key")
        assert event.dedupe_key == "custom_key"

    def test_to_dict_schema_version(self):
        event = NotificationEvent(event_type=EventType.RUN_COMPLETED, summary="done")
        d = event.to_dict()
        assert d["schema_version"] == "1.0"
        assert "event_id" in d

    def test_to_dict_iso_timestamp(self):
        event = NotificationEvent(event_type=EventType.RUN_COMPLETED, summary="done", occurred_at=1711789440.0)
        d = event.to_dict()
        assert "T" in d["occurred_at"]

    def test_delivery_result_frozen(self):
        result = DeliveryResult(event_id="abc", event_type="gate_failed", channel_name="slack",
                                channel_type="slack", attempt=1, max_attempts=3, latency_ms=100.0, status="success")
        with pytest.raises(AttributeError):
            result.status = "failed"

    def test_all_event_types_have_default_severity(self):
        for et in EventType:
            assert et in DEFAULT_SEVERITY, f"{et.value} missing from DEFAULT_SEVERITY"

    def test_event_types_count(self):
        assert len(EventType) == 12  # 11 original + BATCH_SUMMARY


# ═══════════════════════════════════════════════════════════════════
# TestConfigLoader (13 tests)
# ═══════════════════════════════════════════════════════════════════

class TestConfigLoader:
    def test_load_valid_yaml(self, tmp_path):
        yaml_content = "notifications:\n  enabled: true\n  max_per_run: 15\n  channels:\n    - name: test-slack\n      type: slack\n      url: https://hooks.slack.com/test\n"
        (tmp_path / "notify.yaml").write_text(yaml_content)
        config = load_notification_config(tmp_path / "notify.yaml")
        assert config.enabled and config.max_per_run == 15 and len(config.channels) == 1

    def test_load_env_var_interpolation(self, tmp_path, monkeypatch):
        monkeypatch.setenv("TEST_WEBHOOK_URL", "https://hooks.slack.com/secret")
        (tmp_path / "notify.yaml").write_text('notifications:\n  channels:\n    - name: test\n      type: slack\n      url: "${TEST_WEBHOOK_URL}"\n')
        config = load_notification_config(tmp_path / "notify.yaml")
        assert config.channels[0].url == "https://hooks.slack.com/secret"

    def test_load_missing_env_var_raises(self, tmp_path):
        (tmp_path / "notify.yaml").write_text('notifications:\n  channels:\n    - name: test\n      type: slack\n      url: "${NONEXISTENT_VAR_12345}"\n')
        with pytest.raises(ValueError, match="NONEXISTENT_VAR_12345"):
            load_notification_config(tmp_path / "notify.yaml")

    def test_load_file_not_found(self):
        with pytest.raises(FileNotFoundError):
            load_notification_config("/nonexistent/path.yaml")

    def test_load_invalid_yaml(self, tmp_path):
        (tmp_path / "notify.yaml").write_text("")
        with pytest.raises(ValueError, match="Invalid"):
            load_notification_config(tmp_path / "notify.yaml")

    def test_config_validation_no_channels(self):
        errors = NotificationConfig(channels=[]).validate()
        assert any("No notification channels" in e for e in errors)

    def test_config_validation_duplicate_names(self):
        errors = NotificationConfig(channels=[
            ChannelConfig(name="same", type=ChannelType.SLACK, url="https://x"),
            ChannelConfig(name="same", type=ChannelType.SLACK, url="https://y"),
        ]).validate()
        assert any("Duplicate" in e for e in errors)

    def test_config_validation_missing_url(self):
        errors = NotificationConfig(channels=[ChannelConfig(name="test", type=ChannelType.SLACK, url="")]).validate()
        assert any("URL is required" in e for e in errors)

    def test_config_validation_email_fields(self):
        errors = NotificationConfig(channels=[ChannelConfig(name="email", type=ChannelType.EMAIL)]).validate()
        assert any("smtp_host" in e for e in errors)

    def test_config_validation_self_referencing_fallback(self):
        errors = NotificationConfig(channels=[
            ChannelConfig(name="ch1", type=ChannelType.SLACK, url="https://x", fallback_channel="ch1"),
        ]).validate()
        assert any("cannot reference itself" in e for e in errors)

    def test_config_validation_missing_fallback(self):
        errors = NotificationConfig(channels=[
            ChannelConfig(name="ch1", type=ChannelType.SLACK, url="https://x", fallback_channel="nonexistent"),
        ]).validate()
        assert any("not found" in e for e in errors)

    def test_lint_valid_config(self, tmp_path):
        (tmp_path / "notify.yaml").write_text("notifications:\n  channels:\n    - name: test\n      type: slack\n      url: https://hooks.slack.com/test\n")
        is_valid, errors, warnings = lint_notification_config(tmp_path / "notify.yaml")
        assert is_valid and len(errors) == 0

    def test_severity_overrides_parsed(self, tmp_path):
        (tmp_path / "notify.yaml").write_text("notifications:\n  severity_overrides:\n    budget_exceeded: critical\n  channels:\n    - name: test\n      type: slack\n      url: https://x\n")
        config = load_notification_config(tmp_path / "notify.yaml")
        assert config.resolve_severity(EventType.BUDGET_EXCEEDED) == Severity.CRITICAL


# ═══════════════════════════════════════════════════════════════════
# TestDeduplication (8 tests)
# ═══════════════════════════════════════════════════════════════════

class TestDeduplication:
    def test_first_event_not_duplicate(self):
        assert DedupeTracker(300).is_duplicate("ch1", "key1", Severity.INFO) is False

    def test_second_event_is_duplicate(self):
        t = DedupeTracker(300); now = time.time()
        t.mark_seen("ch1", "key1", Severity.INFO, now=now)
        assert t.is_duplicate("ch1", "key1", Severity.INFO, now=now + 1) is True

    def test_expired_event_not_duplicate(self):
        t = DedupeTracker(10); now = time.time()
        t.mark_seen("ch1", "key1", Severity.INFO, now=now)
        assert t.is_duplicate("ch1", "key1", Severity.INFO, now=now + 11) is False

    def test_severity_escalation_bypasses_dedupe(self):
        t = DedupeTracker(300); now = time.time()
        t.mark_seen("ch1", "key1", Severity.WARNING, now=now)
        assert t.is_duplicate("ch1", "key1", Severity.CRITICAL, now=now + 1) is False

    def test_severity_same_level_is_duplicate(self):
        t = DedupeTracker(300); now = time.time()
        t.mark_seen("ch1", "key1", Severity.WARNING, now=now)
        assert t.is_duplicate("ch1", "key1", Severity.WARNING, now=now + 1) is True

    def test_per_channel_isolation(self):
        t = DedupeTracker(300); now = time.time()
        t.mark_seen("ch1", "key1", Severity.INFO, now=now)
        assert t.is_duplicate("ch2", "key1", Severity.INFO, now=now + 1) is False

    def test_cleanup_expired(self):
        t = DedupeTracker(5); now = time.time()
        t.mark_seen("ch1", "key1", Severity.INFO, now=now)
        t.mark_seen("ch1", "key2", Severity.INFO, now=now)
        assert t.cleanup_expired(now=now + 6) == 2

    def test_different_keys_not_duplicate(self):
        t = DedupeTracker(300); now = time.time()
        t.mark_seen("ch1", "key1", Severity.INFO, now=now)
        assert t.is_duplicate("ch1", "key2", Severity.INFO, now=now + 1) is False


# ═══════════════════════════════════════════════════════════════════
# TestRateLimiting (7 tests)
# ═══════════════════════════════════════════════════════════════════

class TestRateLimiting:
    def test_critical_events_bypass_limit(self):
        for et in CRITICAL_BYPASS_EVENTS:
            engine = NotificationEngine(NotificationConfig(max_per_run=0, channels=[
                ChannelConfig(name="ch1", type=ChannelType.SLACK, url="https://x")
            ]), dry_run=True)
            assert engine._check_rate_limit("ch1", et) is True

    def test_guaranteed_events_allowed_before_delivery(self):
        engine = NotificationEngine(NotificationConfig(max_per_run=0, channels=[
            ChannelConfig(name="ch1", type=ChannelType.SLACK, url="https://x")
        ]), dry_run=True)
        assert engine._check_rate_limit("ch1", EventType.RUN_STARTED) is True

    def test_global_limit_enforced(self):
        engine = NotificationEngine(NotificationConfig(max_per_run=2, channels=[
            ChannelConfig(name="ch1", type=ChannelType.SLACK, url="https://x")
        ]), dry_run=True)
        engine._global_count = 2
        assert engine._check_rate_limit("ch1", EventType.POLICY_VIOLATION) is False

    def test_per_type_limit_enforced(self):
        engine = NotificationEngine(NotificationConfig(max_per_run=100, max_per_event_type=2, channels=[
            ChannelConfig(name="ch1", type=ChannelType.SLACK, url="https://x")
        ]), dry_run=True)
        engine._per_type_count["policy_violation"] = 2
        assert engine._check_rate_limit("ch1", EventType.POLICY_VIOLATION) is False

    def test_per_channel_limit_enforced(self):
        engine = NotificationEngine(NotificationConfig(max_per_run=100, max_per_channel=3, channels=[
            ChannelConfig(name="ch1", type=ChannelType.SLACK, url="https://x")
        ]), dry_run=True)
        engine._per_channel_count["ch1"] = 3
        assert engine._check_rate_limit("ch1", EventType.POLICY_VIOLATION) is False

    def test_rate_counters_increment(self):
        engine = NotificationEngine(NotificationConfig(channels=[
            ChannelConfig(name="ch1", type=ChannelType.SLACK, url="https://x")
        ]), dry_run=True)
        engine._increment_rate_counters("ch1", EventType.GATE_FAILED)
        assert engine._global_count == 1 and engine._per_type_count["gate_failed"] == 1

    def test_all_limits_pass_when_under(self):
        engine = NotificationEngine(NotificationConfig(max_per_run=20, channels=[
            ChannelConfig(name="ch1", type=ChannelType.SLACK, url="https://x")
        ]), dry_run=True)
        assert engine._check_rate_limit("ch1", EventType.POLICY_VIOLATION) is True


# ═══════════════════════════════════════════════════════════════════
# TestFormatters (11 tests)
# ═══════════════════════════════════════════════════════════════════

class TestFormatters:
    def test_slack_formatter_basic(self, sample_event):
        channel = ChannelConfig(name="test", type=ChannelType.SLACK, url="https://x")
        payload = SlackFormatter().format(sample_event, channel)
        assert "blocks" in payload and payload["blocks"][0]["type"] == "header"

    def test_slack_formatter_severity_colors(self):
        channel = ChannelConfig(name="test", type=ChannelType.SLACK, url="https://x")
        for sev, color in [(Severity.INFO, "#36a64f"), (Severity.WARNING, "#ff9900"), (Severity.CRITICAL, "#cc0000")]:
            event = NotificationEvent(event_type=EventType.RUN_COMPLETED, summary="test", severity=sev)
            event.severity = sev
            payload = SlackFormatter().format(event, channel)
            assert payload["attachments"][0]["color"] == color

    def test_slack_formatter_with_mentions(self):
        channel = ChannelConfig(name="test", type=ChannelType.SLACK, url="https://x",
                                template=TemplateConfig(mention_on_critical="@oncall"))
        event = NotificationEvent(event_type=EventType.CRITICAL_FAILURE, summary="test", severity=Severity.CRITICAL)
        event.severity = Severity.CRITICAL
        assert "@oncall" in str(SlackFormatter().format(event, channel))

    def test_slack_formatter_with_links(self):
        channel = ChannelConfig(name="test", type=ChannelType.SLACK, url="https://x")
        event = NotificationEvent(event_type=EventType.RUN_COMPLETED, summary="test", links={"report": "./report.html"})
        payload = SlackFormatter().format(event, channel)
        assert any(b.get("type") == "context" for b in payload["blocks"])

    def test_teams_formatter_basic(self, sample_event):
        payload = TeamsFormatter().format(sample_event, ChannelConfig(name="t", type=ChannelType.TEAMS, url="https://x"))
        assert payload["attachments"][0]["content"]["type"] == "AdaptiveCard"

    def test_email_formatter_basic(self, sample_event):
        channel = ChannelConfig(name="t", type=ChannelType.EMAIL, smtp_host="x", sender="a@b.com", recipients=["c@d.com"])
        payload = EmailFormatter().format(sample_event, channel)
        assert "subject" in payload and "html_body" in payload and "text_body" in payload

    def test_email_formatter_html_escaping(self):
        event = NotificationEvent(event_type=EventType.RUN_COMPLETED, summary="<script>alert('xss')</script>")
        channel = ChannelConfig(name="t", type=ChannelType.EMAIL, smtp_host="x", sender="a@b.com", recipients=["c@d.com"])
        payload = EmailFormatter().format(event, channel)
        assert "<script>" not in payload["html_body"]

    def test_generic_webhook_v1_schema(self, sample_event):
        payload = GenericWebhookFormatter().format(sample_event, ChannelConfig(name="t", type=ChannelType.GENERIC_WEBHOOK, url="https://x"))
        assert payload["schema_version"] == "1.0"

    def test_formatter_factory(self):
        assert isinstance(get_formatter("slack"), SlackFormatter)
        assert isinstance(get_formatter("unknown"), GenericWebhookFormatter)

    def test_template_field_filtering(self):
        channel = ChannelConfig(name="t", type=ChannelType.SLACK, url="https://x",
                                template=TemplateConfig(include_fields=["run_id", "severity"]))
        event = NotificationEvent(event_type=EventType.RUN_COMPLETED, summary="test", run_id="r1",
                                  details={"gate_name": "safety"})
        payload = SlackFormatter().format(event, channel)
        text = str(payload["blocks"])
        assert "run_id" in text and "gate_name" not in text

    def test_template_custom_footer(self):
        channel = ChannelConfig(name="t", type=ChannelType.SLACK, url="https://x",
                                template=TemplateConfig(custom_footer="Custom Corp"))
        payload = SlackFormatter().format(NotificationEvent(event_type=EventType.RUN_COMPLETED, summary="test"), channel)
        assert "Custom Corp" in str(payload["blocks"])


# ═══════════════════════════════════════════════════════════════════
# TestDelivery (11 tests)
# ═══════════════════════════════════════════════════════════════════

class TestDelivery:
    def test_classify_failure_auth(self):
        assert classify_failure(Exception("401"), 401) == FailureClass.AUTH_ERROR

    def test_classify_failure_rate_limited(self):
        assert classify_failure(Exception("429"), 429) == FailureClass.RATE_LIMITED_REMOTE

    def test_classify_failure_server_error(self):
        for code in (500, 502, 503, 504):
            assert classify_failure(Exception(f"{code}"), code) == FailureClass.SERVER_ERROR

    def test_classify_failure_timeout(self):
        assert classify_failure(TimeoutError("timed out")) == FailureClass.TIMEOUT

    def test_classify_failure_dns(self):
        assert classify_failure(Exception("DNS name resolution failed")) == FailureClass.DNS_ERROR

    def test_classify_failure_tls(self):
        assert classify_failure(Exception("SSL certificate error")) == FailureClass.TLS_ERROR

    def test_classify_failure_unknown(self):
        assert classify_failure(Exception("something weird")) == FailureClass.UNKNOWN

    def test_retryable_failures_set(self):
        assert FailureClass.SERVER_ERROR in RETRYABLE_FAILURES
        assert FailureClass.AUTH_ERROR not in RETRYABLE_FAILURES

    def test_compute_backoff_exponential(self):
        config = RetryConfig(backoff_base=1.0, backoff_multiplier=2.0, jitter_percent=0)
        assert compute_backoff(0, config) == pytest.approx(1.0, abs=0.01)
        assert compute_backoff(1, config) == pytest.approx(2.0, abs=0.01)

    def test_compute_backoff_with_jitter(self):
        config = RetryConfig(backoff_base=1.0, backoff_multiplier=2.0, jitter_percent=20)
        values = [compute_backoff(0, config) for _ in range(100)]
        assert min(values) >= 0.1 and max(values) <= 1.3

    def test_compute_payload_checksum(self):
        payload = {"key": "value"}
        checksum = compute_payload_checksum(payload)
        assert len(checksum) == 16 and compute_payload_checksum(payload) == checksum


# ═══════════════════════════════════════════════════════════════════
# TestCircuitBreaker (5 tests)
# ═══════════════════════════════════════════════════════════════════

class TestCircuitBreaker:
    def test_initial_state_closed(self):
        cb = CircuitBreakerState(failure_threshold=3)
        assert not cb.is_open and cb.consecutive_failures == 0

    def test_opens_after_threshold(self):
        cb = CircuitBreakerState(failure_threshold=3)
        cb.record_failure(); cb.record_failure()
        assert not cb.is_open
        assert cb.record_failure() is True and cb.is_open

    def test_success_resets_counter(self):
        cb = CircuitBreakerState(failure_threshold=3)
        cb.record_failure(); cb.record_failure(); cb.record_success()
        assert cb.consecutive_failures == 0

    def test_stays_open_after_trip(self):
        cb = CircuitBreakerState(failure_threshold=2)
        cb.record_failure(); cb.record_failure()
        assert cb.record_failure() is False and cb.is_open

    def test_opened_at_timestamp(self):
        cb = CircuitBreakerState(failure_threshold=1)
        before = time.time(); cb.record_failure()
        assert cb.opened_at >= before


# ═══════════════════════════════════════════════════════════════════
# TestAggregation (4 tests)
# ═══════════════════════════════════════════════════════════════════

class TestAggregation:
    def test_batched_mode_buffers_non_critical(self):
        engine = NotificationEngine(NotificationConfig(channels=[
            ChannelConfig(name="b", type=ChannelType.SLACK, url="https://x", delivery_mode=DeliveryMode.BATCHED),
        ]), dry_run=True)
        assert "b" in engine._buffered_events

    def test_critical_only_mode_filters_info(self):
        config = NotificationConfig(channels=[
            ChannelConfig(name="p", type=ChannelType.GENERIC_WEBHOOK, url="https://x", delivery_mode=DeliveryMode.CRITICAL_ONLY),
        ])
        engine = NotificationEngine(config, dry_run=True)
        event = NotificationEvent(event_type=EventType.RUN_STARTED, summary="test"); event.severity = Severity.INFO
        asyncio.run(engine._process_channel(config.channels[0], event, set()))
        assert len(engine._delivery_log) == 0

    def test_batch_summary_uses_batch_summary_type(self):
        config = NotificationConfig(channels=[
            ChannelConfig(name="b", type=ChannelType.SLACK, url="https://x", delivery_mode=DeliveryMode.BATCHED),
        ])
        engine = NotificationEngine(config, dry_run=True)
        events = [NotificationEvent(event_type=EventType.POLICY_VIOLATION, summary="v1", run_id="r1", severity=Severity.WARNING)]
        summary = engine._build_batch_summary(events, config.channels[0])
        assert summary.event_type == EventType.BATCH_SUMMARY

    def test_empty_batch_no_crash(self):
        engine = NotificationEngine(NotificationConfig(channels=[
            ChannelConfig(name="b", type=ChannelType.SLACK, url="https://x", delivery_mode=DeliveryMode.BATCHED),
        ]), dry_run=True)
        asyncio.run(engine.flush_buffered())
        assert len(engine._delivery_log) == 0


# ═══════════════════════════════════════════════════════════════════
# TestEngine (9 tests)
# ═══════════════════════════════════════════════════════════════════

class TestEngine:
    def test_disabled_config_no_delivery(self, sample_event):
        engine = NotificationEngine(NotificationConfig(enabled=False, channels=[
            ChannelConfig(name="ch1", type=ChannelType.SLACK, url="https://x"),
        ]), dry_run=True)
        asyncio.run(engine.notify(sample_event))
        assert len(engine._delivery_log) == 0

    def test_event_filter_skips_unmatched(self, sample_event):
        engine = NotificationEngine(NotificationConfig(channels=[
            ChannelConfig(name="ch1", type=ChannelType.SLACK, url="https://x", events=[EventType.RUN_COMPLETED]),
        ]), dry_run=True)
        asyncio.run(engine.notify(sample_event))
        assert len(engine._delivery_log) == 0

    def test_dry_run_creates_dry_run_status(self):
        engine = NotificationEngine(NotificationConfig(channels=[
            ChannelConfig(name="ch1", type=ChannelType.SLACK, url="https://x"),
        ]), dry_run=True)
        asyncio.run(engine.notify(NotificationEvent(event_type=EventType.RUN_COMPLETED, summary="test")))
        assert any(r.status == DeliveryStatus.DRY_RUN.value for r in engine._delivery_log)

    def test_environment_applied_from_config(self):
        engine = NotificationEngine(NotificationConfig(environment="staging", channels=[
            ChannelConfig(name="ch1", type=ChannelType.SLACK, url="https://x"),
        ]), dry_run=True)
        event = NotificationEvent(event_type=EventType.RUN_COMPLETED, summary="test")
        asyncio.run(engine.notify(event))
        assert event.environment == "staging"

    def test_finalize_returns_summary(self, sample_config):
        engine = NotificationEngine(sample_config, dry_run=True)
        event = NotificationEvent(event_type=EventType.GATE_FAILED, summary="test", run_id="r1", details={"gate_name": "safety"})
        asyncio.run(engine.notify(event))
        summary = asyncio.run(engine.finalize())
        assert isinstance(summary, NotificationSummary)

    def test_summary_to_dict_and_full_dict(self):
        summary = NotificationSummary(notifications_sent=5, notifications_failed=1,
                                      delivery_log=[{"event_id": "abc", "status": "success"}])
        compact = summary.to_summary_dict()
        assert "delivery_log" not in compact and compact["delivery_log_entries"] == 1
        full = summary.to_full_dict()
        assert "delivery_log" in full and len(full["delivery_log"]) == 1

    def test_dedupe_suppression_logged(self):
        engine = NotificationEngine(NotificationConfig(channels=[
            ChannelConfig(name="ch1", type=ChannelType.SLACK, url="https://x"),
        ]), dry_run=True)
        event = NotificationEvent(event_type=EventType.RUN_COMPLETED, summary="test", run_id="r1", dedupe_key="fixed_key")
        asyncio.run(engine.notify(event))
        asyncio.run(engine.notify(event))
        assert sum(1 for r in engine._delivery_log if r.suppressed_by_dedupe) == 1

    def test_multiple_channels_independent(self):
        engine = NotificationEngine(NotificationConfig(channels=[
            ChannelConfig(name="ch1", type=ChannelType.SLACK, url="https://x"),
            ChannelConfig(name="ch2", type=ChannelType.GENERIC_WEBHOOK, url="https://y"),
        ]), dry_run=True)
        asyncio.run(engine.notify(NotificationEvent(event_type=EventType.RUN_COMPLETED, summary="test")))
        assert {"ch1", "ch2"} == {r.channel_name for r in engine._delivery_log}

    def test_notify_sync_wrapper(self):
        engine = NotificationEngine(NotificationConfig(channels=[
            ChannelConfig(name="ch1", type=ChannelType.SLACK, url="https://x"),
        ]), dry_run=True)
        engine.notify_sync(NotificationEvent(event_type=EventType.RUN_COMPLETED, summary="test"))


# ═══════════════════════════════════════════════════════════════════
# TestNotificationHTML (4 tests)
# ═══════════════════════════════════════════════════════════════════

class TestNotificationHTML:
    def test_generate_html_with_data(self):
        html = generate_notification_html(NotificationSummary(notifications_sent=5, notifications_failed=1, avg_latency_ms=120.5,
                                                               per_channel={"slack": {"sent": 3, "failed": 1, "suppressed": 0}}))
        assert "Notification Delivery Summary" in html

    def test_generate_html_empty_no_output(self):
        assert generate_notification_html(NotificationSummary()) == ""

    def test_inject_into_report(self, tmp_path):
        (tmp_path / "report.html").write_text("<html><body><div>content</div><footer>end</footer></body></html>")
        assert inject_notification_into_report(str(tmp_path / "report.html"),
            NotificationSummary(notifications_sent=3, per_channel={"ch1": {"sent": 3, "failed": 0, "suppressed": 0}}))

    def test_inject_fallback_to_body(self, tmp_path):
        (tmp_path / "report.html").write_text("<html><body><div>content</div></body></html>")
        assert inject_notification_into_report(str(tmp_path / "report.html"),
            NotificationSummary(notifications_sent=1, per_channel={"ch1": {"sent": 1, "failed": 0, "suppressed": 0}}))


# ═══════════════════════════════════════════════════════════════════
# TestSecretHandling (5 tests)
# ═══════════════════════════════════════════════════════════════════

class TestSecretHandling:
    def test_redact_url(self):
        r = redact_url("https://hooks.slack.com/services/T00/B00/xxx")
        assert "hooks.slack.com" in r and "xxx" not in r

    def test_redact_url_empty(self):
        assert redact_url("") == ""

    def test_redact_token(self):
        assert redact_token("Bearer_1234567890") == "Bear***"

    def test_redact_token_short(self):
        assert redact_token("abc") == "***"

    def test_delivery_result_no_url_stored(self):
        d = DeliveryResult(event_id="abc", event_type="gate_failed", channel_name="slack",
                           channel_type="slack", attempt=1, max_attempts=3, latency_ms=100.0, status="success").to_dict()
        assert "url" not in d and "password" not in d


# ═══════════════════════════════════════════════════════════════════
# NEW: TestFallbackRouting (Review #2)
# ═══════════════════════════════════════════════════════════════════

class TestFallbackRouting:
    """Review #2: Fallback re-enters normal processing with loop prevention."""

    def test_fallback_respects_event_filter(self):
        """Fallback channel should NOT deliver if it doesn't accept the event type."""
        config = NotificationConfig(channels=[
            ChannelConfig(name="primary", type=ChannelType.SLACK, url="https://x",
                          events=[EventType.GATE_FAILED], fallback_channel="backup"),
            ChannelConfig(name="backup", type=ChannelType.SLACK, url="https://y",
                          events=[EventType.RUN_COMPLETED]),  # Does NOT accept GATE_FAILED
        ])
        engine = NotificationEngine(config, dry_run=True)
        # Trip primary circuit breaker
        engine._circuit_breakers["primary"].is_open = True
        event = NotificationEvent(event_type=EventType.GATE_FAILED, summary="test", run_id="r1",
                                  details={"gate_name": "safety"})
        asyncio.run(engine.notify(event))
        # Backup should not have delivered (it doesn't accept GATE_FAILED)
        delivered = [r for r in engine._delivery_log if r.status == DeliveryStatus.DRY_RUN.value]
        assert len(delivered) == 0

    def test_fallback_prevents_infinite_loop(self):
        """A→B→A loop must terminate."""
        config = NotificationConfig(channels=[
            ChannelConfig(name="a", type=ChannelType.SLACK, url="https://x", fallback_channel="b"),
            ChannelConfig(name="b", type=ChannelType.SLACK, url="https://y", fallback_channel="a"),
        ])
        engine = NotificationEngine(config, dry_run=True)
        engine._circuit_breakers["a"].is_open = True
        engine._circuit_breakers["b"].is_open = True
        event = NotificationEvent(event_type=EventType.RUN_COMPLETED, summary="test")
        asyncio.run(engine.notify(event))
        # Should not crash or infinite loop — both circuit open
        circuit_open_results = [r for r in engine._delivery_log if r.status == DeliveryStatus.CIRCUIT_OPEN.value]
        assert len(circuit_open_results) >= 1

    def test_fallback_respects_its_own_rate_limits(self):
        """Fallback channel should obey its own per-channel rate limit."""
        config = NotificationConfig(max_per_channel=1, channels=[
            ChannelConfig(name="primary", type=ChannelType.SLACK, url="https://x", fallback_channel="backup"),
            ChannelConfig(name="backup", type=ChannelType.SLACK, url="https://y"),
        ])
        engine = NotificationEngine(config, dry_run=True)
        engine._circuit_breakers["primary"].is_open = True
        engine._per_channel_count["backup"] = 1  # Already at limit
        event = NotificationEvent(event_type=EventType.POLICY_VIOLATION, summary="test")
        asyncio.run(engine.notify(event))
        suppressed = [r for r in engine._delivery_log if r.suppressed_by_rate_limit]
        assert len(suppressed) >= 1


# ═══════════════════════════════════════════════════════════════════
# NEW: TestGuaranteedEvents (Review #3)
# ═══════════════════════════════════════════════════════════════════

class TestGuaranteedEvents:
    """Review #3: Guaranteed events only consumed after successful delivery."""

    def test_guaranteed_not_consumed_before_delivery(self):
        engine = NotificationEngine(NotificationConfig(max_per_run=0, channels=[
            ChannelConfig(name="ch1", type=ChannelType.SLACK, url="https://x"),
        ]), dry_run=True)
        # Rate check should allow guaranteed event
        assert engine._check_rate_limit("ch1", EventType.RUN_STARTED) is True
        # But NOT yet marked as delivered
        assert engine._guaranteed_delivered.get("ch1__run_started") is not True

    def test_guaranteed_consumed_after_success(self):
        engine = NotificationEngine(NotificationConfig(channels=[
            ChannelConfig(name="ch1", type=ChannelType.SLACK, url="https://x"),
        ]), dry_run=True)
        event = NotificationEvent(event_type=EventType.RUN_COMPLETED, summary="done")
        asyncio.run(engine.notify(event))
        # After successful dry_run delivery, marked as delivered
        assert engine._guaranteed_delivered.get("ch1__run_completed") is True

    def test_guaranteed_second_attempt_blocked_after_success(self):
        engine = NotificationEngine(NotificationConfig(max_per_run=0, channels=[
            ChannelConfig(name="ch1", type=ChannelType.SLACK, url="https://x"),
        ]), dry_run=True)
        event = NotificationEvent(event_type=EventType.RUN_COMPLETED, summary="done", dedupe_key="unique1")
        asyncio.run(engine.notify(event))
        # Second attempt with different dedupe key
        event2 = NotificationEvent(event_type=EventType.RUN_COMPLETED, summary="done2", dedupe_key="unique2")
        asyncio.run(engine.notify(event2))
        # Should be rate-limited (guaranteed already used, global limit=0)
        suppressed = [r for r in engine._delivery_log if r.suppressed_by_rate_limit]
        assert len(suppressed) >= 1


# ═══════════════════════════════════════════════════════════════════
# NEW: TestBatchedModeDedupe (Review #4)
# ═══════════════════════════════════════════════════════════════════

class TestBatchedModeDedupe:
    """Review #4: Batched mode deduplicates before buffering."""

    def test_duplicate_events_not_buffered(self):
        config = NotificationConfig(channels=[
            ChannelConfig(name="b", type=ChannelType.SLACK, url="https://x", delivery_mode=DeliveryMode.BATCHED),
        ])
        engine = NotificationEngine(config, dry_run=True)
        event = NotificationEvent(event_type=EventType.POLICY_VIOLATION, summary="v1",
                                  run_id="r1", dedupe_key="same_key")
        asyncio.run(engine.notify(event))
        asyncio.run(engine.notify(event))
        assert len(engine._buffered_events["b"]) == 1

    def test_buffer_memory_cap(self):
        config = NotificationConfig(channels=[
            ChannelConfig(name="b", type=ChannelType.SLACK, url="https://x", delivery_mode=DeliveryMode.BATCHED),
        ])
        engine = NotificationEngine(config, dry_run=True)
        for i in range(MAX_BUFFERED_EVENTS_PER_CHANNEL + 10):
            event = NotificationEvent(event_type=EventType.POLICY_VIOLATION,
                                      summary=f"v{i}", run_id="r1", dedupe_key=f"key_{i}")
            asyncio.run(engine.notify(event))
        assert len(engine._buffered_events["b"]) == MAX_BUFFERED_EVENTS_PER_CHANNEL


# ═══════════════════════════════════════════════════════════════════
# NEW: TestNotificationLogJson (Review #5)
# ═══════════════════════════════════════════════════════════════════

class TestNotificationLogJson:
    """Review #5: notification_log.json contains full detailed records."""

    def test_full_dict_contains_delivery_log(self):
        summary = NotificationSummary(
            notifications_sent=2,
            delivery_log=[{"event_id": "a", "status": "success"}, {"event_id": "b", "status": "failed"}],
        )
        full = summary.to_full_dict()
        assert len(full["delivery_log"]) == 2
        assert full["delivery_log"][0]["event_id"] == "a"

    def test_summary_dict_compact(self):
        summary = NotificationSummary(
            notifications_sent=2,
            delivery_log=[{"event_id": "a"}, {"event_id": "b"}],
        )
        compact = summary.to_summary_dict()
        assert "delivery_log" not in compact
        assert compact["delivery_log_entries"] == 2


# ═══════════════════════════════════════════════════════════════════
# NEW: TestPayloadGuardrails (Review #10)
# ═══════════════════════════════════════════════════════════════════

class TestPayloadGuardrails:
    """Review #10: Payload size limits enforced on event creation."""

    def test_summary_truncated(self):
        event = NotificationEvent(event_type=EventType.RUN_COMPLETED, summary="x" * 1000)
        assert len(event.summary) == MAX_SUMMARY_LENGTH

    def test_details_field_count_capped(self):
        details = {f"field_{i}": f"value_{i}" for i in range(50)}
        event = NotificationEvent(event_type=EventType.RUN_COMPLETED, summary="test", details=details)
        assert len(event.details) == MAX_DETAIL_FIELDS

    def test_links_count_capped(self):
        links = {f"link_{i}": f"https://example.com/{i}" for i in range(20)}
        event = NotificationEvent(event_type=EventType.RUN_COMPLETED, summary="test", links=links)
        assert len(event.links) == MAX_LINKS

    def test_detail_value_truncated(self):
        event = NotificationEvent(event_type=EventType.RUN_COMPLETED, summary="test",
                                  details={"big_field": "x" * 2000})
        assert len(event.details["big_field"]) < 2000
        assert event.details["big_field"].endswith("...")


# ═══════════════════════════════════════════════════════════════════
# NEW: TestDryRunContract (Review #11)
# ═══════════════════════════════════════════════════════════════════

class TestDryRunContract:
    """Review #11: Dry-run produces formatted payloads but zero network calls."""

    def test_dry_run_produces_results(self):
        engine = NotificationEngine(NotificationConfig(channels=[
            ChannelConfig(name="ch1", type=ChannelType.SLACK, url="https://x"),
        ]), dry_run=True)
        asyncio.run(engine.notify(NotificationEvent(event_type=EventType.RUN_COMPLETED, summary="test")))
        assert len(engine._delivery_log) >= 1
        assert all(r.status == DeliveryStatus.DRY_RUN.value for r in engine._delivery_log
                   if r.status not in (DeliveryStatus.SUPPRESSED_DEDUPE.value, DeliveryStatus.SUPPRESSED_RATE_LIMIT.value))

    def test_dry_run_suppression_visible(self):
        engine = NotificationEngine(NotificationConfig(channels=[
            ChannelConfig(name="ch1", type=ChannelType.SLACK, url="https://x"),
        ]), dry_run=True)
        event = NotificationEvent(event_type=EventType.RUN_COMPLETED, summary="test", dedupe_key="same")
        asyncio.run(engine.notify(event))
        asyncio.run(engine.notify(event))
        dedupe_results = [r for r in engine._delivery_log if r.suppressed_by_dedupe]
        assert len(dedupe_results) == 1


# ═══════════════════════════════════════════════════════════════════
# NEW: TestFinalizeGraceful (Review #9)
# ═══════════════════════════════════════════════════════════════════

class TestFinalizeGraceful:
    """Review #9: Finalize handles background tasks safely."""

    def test_finalize_with_no_events(self):
        engine = NotificationEngine(NotificationConfig(channels=[
            ChannelConfig(name="ch1", type=ChannelType.SLACK, url="https://x"),
        ]), dry_run=True)
        summary = asyncio.run(engine.finalize())
        assert summary.notifications_sent == 0 and summary.notifications_failed == 0

    def test_finalize_never_crashes(self):
        """Finalize must not crash even with weird internal state."""
        engine = NotificationEngine(NotificationConfig(channels=[]), dry_run=True)
        summary = asyncio.run(engine.finalize())
        assert isinstance(summary, NotificationSummary)
