"""
Notification Engine — P4 #22 (V1 Review-Hardened)

Review fixes applied:
  #2  — Fallback re-enters normal processing with loop prevention
  #3  — Guaranteed events only consumed after successful delivery
  #4  — Batched mode: filter+dedupe before buffer, memory cap
  #8  — Urgent events dispatched as fire-and-forget tasks
  #9  — Graceful finalize with bounded timeout, no crash
  #12 — All state is per-engine instance, no shared globals

Design: All state (dedupe, rate counters, circuit breakers, buffers,
background tasks) is instance-scoped. Multiple engines in the same
process are fully isolated. Review #12.
"""
from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timezone
from typing import Any, Optional

from src.notifications.models import (
    ChannelConfig,
    ChannelType,
    CircuitBreakerState,
    CRITICAL_BYPASS_EVENTS,
    DedupeTracker,
    DeliveryMode,
    DeliveryResult,
    DeliveryStatus,
    EventType,
    GUARANTEED_EVENTS,
    MAX_BUFFERED_EVENTS_PER_CHANNEL,
    NotificationConfig,
    NotificationEvent,
    NotificationSummary,
    Severity,
)
from src.notifications.formatters import get_formatter
from src.notifications.delivery import deliver_http, deliver_email

logger = logging.getLogger(__name__)

# Finalize timeout for background tasks (Review #9)
FINALIZE_TIMEOUT_SECONDS = 30.0


class NotificationEngine:
    """
    Core notification engine. All state is per-instance (Review #12).

    Pipeline: filter → dedupe → rate limit → severity → format → dispatch → log

    Review #3: Guaranteed events only marked as consumed after successful delivery.
    Review #2: Fallback channels re-enter normal processing with visited-set loop prevention.
    Review #4: Batched channels apply dedupe before buffering with memory cap.
    Review #8: Urgent events (CRITICAL_BYPASS) dispatch as fire-and-forget tasks.
    Review #9: finalize() awaits background tasks with bounded timeout.
    """

    def __init__(self, config: NotificationConfig, dry_run: bool = False):
        self.config = config
        self.dry_run = dry_run

        self._dedupe = DedupeTracker(window_seconds=config.dedupe_window_seconds)
        self._circuit_breakers: dict[str, CircuitBreakerState] = {
            ch.name: CircuitBreakerState(failure_threshold=config.circuit_breaker.failure_threshold)
            for ch in config.channels
        }

        # Rate limit counters — all per-instance (Review #12)
        self._global_count: int = 0
        self._per_type_count: dict[str, int] = {}
        self._per_channel_count: dict[str, int] = {}
        # Review #3: Track which guaranteed events have been SUCCESSFULLY delivered per channel
        self._guaranteed_delivered: dict[str, bool] = {}

        self._delivery_log: list[DeliveryResult] = []
        self._circuit_breaker_trips: int = 0
        self._suppressed_rate_limit: int = 0
        self._suppressed_dedupe: int = 0
        self._retried_count: int = 0

        # Review #4: Buffered events for batched channels (with memory cap)
        self._buffered_events: dict[str, list[NotificationEvent]] = {
            ch.name: [] for ch in config.channels
            if ch.delivery_mode == DeliveryMode.BATCHED
        }

        # Review #8: Background tasks for fire-and-forget delivery
        self._background_tasks: list[asyncio.Task] = []

    async def notify(self, event: NotificationEvent) -> None:
        """Main entry point. Process event through pipeline for all channels."""
        if not self.config.enabled:
            return
        if not event.environment and self.config.environment:
            event.environment = self.config.environment
        # Resolve severity override
        event.severity = self.config.resolve_severity(event.event_type)

        for channel in self.config.channels:
            await self._process_channel(channel, event, visited=set())

    def notify_sync(self, event: NotificationEvent) -> None:
        """Synchronous wrapper. Creates event loop if needed."""
        try:
            loop = asyncio.get_running_loop()
            task = loop.create_task(self.notify(event))
            self._background_tasks.append(task)
        except RuntimeError:
            asyncio.run(self.notify(event))

    async def _process_channel(
        self,
        channel: ChannelConfig,
        event: NotificationEvent,
        visited: set[str],
    ) -> None:
        """
        Process a single event for a single channel.

        Review #2: visited set prevents infinite fallback loops.
        All pipeline checks (filter, dedupe, rate limit, circuit breaker)
        apply equally to primary and fallback channels.
        """
        # Loop prevention (Review #2)
        if channel.name in visited:
            return
        visited.add(channel.name)

        # 1. Event type filter
        if not channel.accepts_event(event.event_type):
            return

        # 2. Delivery mode filter
        if channel.delivery_mode == DeliveryMode.CRITICAL_ONLY:
            if event.severity not in (Severity.CRITICAL, Severity.WARNING):
                return

        # 3. Dedupe check — applies BEFORE buffering (Review #4)
        if self._dedupe.is_duplicate(channel.name, event.dedupe_key, event.severity):
            self._suppressed_dedupe += 1
            self._delivery_log.append(DeliveryResult(
                event_id=event.event_id, event_type=event.event_type.value,
                channel_name=channel.name, channel_type=channel.type.value,
                attempt=0, max_attempts=0, latency_ms=0.0,
                status=DeliveryStatus.SUPPRESSED_DEDUPE.value,
                suppressed_by_dedupe=True,
                timestamp=datetime.now(timezone.utc).isoformat(),
            ))
            return

        # 4. Batched mode: buffer non-critical events (Review #4)
        if channel.delivery_mode == DeliveryMode.BATCHED:
            if event.event_type not in CRITICAL_BYPASS_EVENTS:
                buf = self._buffered_events.get(channel.name)
                if buf is not None:
                    if len(buf) < MAX_BUFFERED_EVENTS_PER_CHANNEL:
                        buf.append(event)
                    else:
                        logger.warning("notification_buffer_full channel=%s", channel.name)
                    # Mark as seen for dedupe so duplicates don't accumulate
                    self._dedupe.mark_seen(channel.name, event.dedupe_key, event.severity)
                return

        # 5. Circuit breaker check
        cb = self._circuit_breakers.get(channel.name)
        if cb and cb.is_open:
            self._delivery_log.append(DeliveryResult(
                event_id=event.event_id, event_type=event.event_type.value,
                channel_name=channel.name, channel_type=channel.type.value,
                attempt=0, max_attempts=0, latency_ms=0.0,
                status=DeliveryStatus.CIRCUIT_OPEN.value,
                circuit_breaker_state="open",
                timestamp=datetime.now(timezone.utc).isoformat(),
            ))
            # Review #2: Fallback re-enters normal processing
            if channel.fallback_channel:
                fallback = self.config.get_channel(channel.fallback_channel)
                if fallback:
                    await self._process_channel(fallback, event, visited)
            return

        # 6. Rate limit check (side-effect-free, Review #3)
        if not self._check_rate_limit(channel.name, event.event_type):
            self._suppressed_rate_limit += 1
            self._delivery_log.append(DeliveryResult(
                event_id=event.event_id, event_type=event.event_type.value,
                channel_name=channel.name, channel_type=channel.type.value,
                attempt=0, max_attempts=0, latency_ms=0.0,
                status=DeliveryStatus.SUPPRESSED_RATE_LIMIT.value,
                suppressed_by_rate_limit=True,
                timestamp=datetime.now(timezone.utc).isoformat(),
            ))
            return

        # 7. Dispatch — urgent events fire-and-forget (Review #8)
        if event.event_type in CRITICAL_BYPASS_EVENTS:
            try:
                loop = asyncio.get_running_loop()
                task = loop.create_task(self._dispatch(channel, event))
                self._background_tasks.append(task)
            except RuntimeError:
                await self._dispatch(channel, event)
        else:
            await self._dispatch(channel, event)

    def _check_rate_limit(self, channel_name: str, event_type: EventType) -> bool:
        """
        Rate limit check. Returns True if allowed.
        Review #3: SIDE-EFFECT-FREE. Does NOT consume guaranteed slots.
        Counters are incremented only after successful delivery in _dispatch.
        """
        if event_type in CRITICAL_BYPASS_EVENTS:
            return True
        if event_type in GUARANTEED_EVENTS:
            key = f"{channel_name}__{event_type.value}"
            if not self._guaranteed_delivered.get(key):
                return True  # Allowed but NOT marked yet
        if self._global_count >= self.config.max_per_run:
            return False
        type_count = self._per_type_count.get(event_type.value, 0)
        if type_count >= self.config.max_per_event_type:
            return False
        ch_count = self._per_channel_count.get(channel_name, 0)
        if ch_count >= self.config.max_per_channel:
            return False
        return True

    def _increment_rate_counters(self, channel_name: str, event_type: EventType) -> None:
        """Increment counters AFTER successful delivery only."""
        self._global_count += 1
        self._per_type_count[event_type.value] = self._per_type_count.get(event_type.value, 0) + 1
        self._per_channel_count[channel_name] = self._per_channel_count.get(channel_name, 0) + 1
        # Review #3: Mark guaranteed event as delivered
        if event_type in GUARANTEED_EVENTS:
            key = f"{channel_name}__{event_type.value}"
            self._guaranteed_delivered[key] = True

    async def _dispatch(self, channel: ChannelConfig, event: NotificationEvent) -> None:
        """Format and deliver to a single channel."""
        formatter = get_formatter(channel.type.value)
        payload = formatter.format(event, channel)

        if channel.type == ChannelType.EMAIL:
            results = await deliver_email(
                payload=payload, channel=channel,
                retry_config=self.config.retry,
                event_id=event.event_id, event_type=event.event_type.value,
                dry_run=self.dry_run,
            )
        else:
            results = await deliver_http(
                url=channel.url, payload=payload,
                headers=channel.headers, channel=channel,
                retry_config=self.config.retry,
                event_id=event.event_id, event_type=event.event_type.value,
                dry_run=self.dry_run,
            )

        self._delivery_log.extend(results)
        final_result = results[-1] if results else None

        if final_result:
            if final_result.status in (DeliveryStatus.SUCCESS.value, DeliveryStatus.DRY_RUN.value):
                cb = self._circuit_breakers.get(channel.name)
                if cb:
                    cb.record_success()
                self._dedupe.mark_seen(channel.name, event.dedupe_key, event.severity)
                self._increment_rate_counters(channel.name, event.event_type)
            else:
                cb = self._circuit_breakers.get(channel.name)
                if cb:
                    just_opened = cb.record_failure()
                    if just_opened:
                        self._circuit_breaker_trips += 1
                        logger.warning("circuit_breaker_opened channel=%s", channel.name)

        if len(results) > 1:
            self._retried_count += len(results) - 1

    async def flush_buffered(self) -> None:
        """Flush all buffered events as batch summaries. Review #4."""
        for channel_name, events in self._buffered_events.items():
            if not events:
                continue
            channel = self.config.get_channel(channel_name)
            if not channel:
                continue
            summary_event = self._build_batch_summary(events, channel)
            await self._dispatch(channel, summary_event)
            events.clear()

    def _build_batch_summary(
        self, events: list[NotificationEvent], channel: ChannelConfig,
    ) -> NotificationEvent:
        """Build a BATCH_SUMMARY event from buffered events."""
        max_severity = Severity.INFO
        sev_order = {Severity.INFO: 0, Severity.WARNING: 1, Severity.CRITICAL: 2}
        for e in events:
            if sev_order.get(e.severity, 0) > sev_order.get(max_severity, 0):
                max_severity = e.severity
        type_counts: dict[str, int] = {}
        for e in events:
            type_counts[e.event_type.value] = type_counts.get(e.event_type.value, 0) + 1
        summary_parts = [f"{count}x {etype}" for etype, count in type_counts.items()]
        run_id = events[0].run_id if events else ""
        return NotificationEvent(
            event_type=EventType.BATCH_SUMMARY,
            summary=f"Batch digest ({len(events)} events): {', '.join(summary_parts)}",
            run_id=run_id,
            severity=max_severity,
            details={"batched_event_count": len(events), "event_type_breakdown": type_counts},
            links=events[-1].links if events else {},
            cost_snapshot=events[-1].cost_snapshot if events else {},
            environment=events[0].environment if events else "",
        )

    async def finalize(self) -> NotificationSummary:
        """
        Finalize the engine. Review #9: graceful shutdown.
        1. Flush buffered events
        2. Await background tasks with bounded timeout
        3. Log incomplete tasks if timeout
        4. Never crash
        """
        try:
            await self.flush_buffered()
        except Exception as e:
            logger.warning("notification_flush_error: %s", e)

        # Review #9: await background tasks with timeout
        if self._background_tasks:
            try:
                done, pending = await asyncio.wait(
                    self._background_tasks, timeout=FINALIZE_TIMEOUT_SECONDS,
                )
                if pending:
                    logger.warning(
                        "notification_finalize: %d background task(s) did not complete in %.0fs",
                        len(pending), FINALIZE_TIMEOUT_SECONDS,
                    )
                    for task in pending:
                        task.cancel()
                # Collect exceptions from done tasks (Review #9: no silent failures)
                for task in done:
                    if task.exception():
                        logger.warning("notification_task_error: %s", task.exception())
            except Exception as e:
                logger.warning("notification_finalize_wait_error: %s", e)
            self._background_tasks.clear()

        self._dedupe.cleanup_expired()

        # Compute metrics
        sent = sum(1 for r in self._delivery_log
                   if r.status in (DeliveryStatus.SUCCESS.value, DeliveryStatus.DRY_RUN.value))
        failed = sum(1 for r in self._delivery_log
                     if r.status == DeliveryStatus.FAILED.value)

        per_channel: dict[str, dict[str, int]] = {}
        for r in self._delivery_log:
            if r.channel_name not in per_channel:
                per_channel[r.channel_name] = {"sent": 0, "failed": 0, "retried": 0, "suppressed": 0}
            cd = per_channel[r.channel_name]
            if r.status in (DeliveryStatus.SUCCESS.value, DeliveryStatus.DRY_RUN.value):
                cd["sent"] += 1
            elif r.status == DeliveryStatus.FAILED.value:
                cd["failed"] += 1
            elif r.status in (DeliveryStatus.SUPPRESSED_RATE_LIMIT.value,
                              DeliveryStatus.SUPPRESSED_DEDUPE.value,
                              DeliveryStatus.CIRCUIT_OPEN.value):
                cd["suppressed"] += 1

        latency_values = [r.latency_ms for r in self._delivery_log if r.latency_ms > 0]
        avg_latency = sum(latency_values) / len(latency_values) if latency_values else 0.0

        return NotificationSummary(
            notifications_sent=sent,
            notifications_failed=failed,
            notifications_retried=self._retried_count,
            notifications_suppressed_rate_limit=self._suppressed_rate_limit,
            notifications_suppressed_dedupe=self._suppressed_dedupe,
            avg_latency_ms=avg_latency,
            circuit_breaker_trips=self._circuit_breaker_trips,
            per_channel=per_channel,
            delivery_log=[r.to_dict() for r in self._delivery_log],
        )
