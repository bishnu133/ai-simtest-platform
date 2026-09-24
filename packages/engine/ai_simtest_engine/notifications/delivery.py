"""
Async Delivery Client — P4 #22 (V1 Review-Hardened)

Review fixes:
  #6  — Honor Retry-After header on HTTP 429
  #7  — 413 payload_too_large is terminal (no auto-truncation in V1)
  #8  — Fire-and-forget via asyncio.create_task for urgent events
  #11 — Dry-run: no network calls, formatter runs, result marked dry_run
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import random
import time
from datetime import datetime, timezone
from typing import Any

from ai_simtest_engine.notifications.models import (
    ChannelConfig,
    ChannelType,
    DeliveryResult,
    DeliveryStatus,
    FailureClass,
    RetryConfig,
    RETRYABLE_FAILURES,
)

logger = logging.getLogger(__name__)

DELIVERY_TIMEOUT = 10.0
MAX_RETRY_AFTER_SECONDS = 30.0  # Review #6: upper bound for Retry-After


def classify_failure(error: Exception, http_status: int = 0) -> FailureClass:
    error_type = type(error).__name__.lower()
    error_msg = str(error).lower()
    if http_status == 401 or http_status == 403:
        return FailureClass.AUTH_ERROR
    if http_status == 429:
        return FailureClass.RATE_LIMITED_REMOTE
    if http_status == 413:
        return FailureClass.PAYLOAD_TOO_LARGE
    if http_status in (400, 422):
        return FailureClass.BAD_REQUEST
    if http_status in (500, 502, 503, 504):
        return FailureClass.SERVER_ERROR
    if "timeout" in error_type or "timeout" in error_msg:
        return FailureClass.TIMEOUT
    if "dns" in error_type or "dns" in error_msg or "name resolution" in error_msg:
        return FailureClass.DNS_ERROR
    if "ssl" in error_type or "tls" in error_type or "certificate" in error_msg:
        return FailureClass.TLS_ERROR
    if "smtp" in error_type:
        if "auth" in error_msg or "login" in error_msg:
            return FailureClass.SMTP_AUTH_ERROR
        return FailureClass.SMTP_CONNECTION_ERROR
    return FailureClass.UNKNOWN


def compute_backoff(attempt: int, config: RetryConfig) -> float:
    base_delay = config.backoff_base * (config.backoff_multiplier ** attempt)
    jitter_range = base_delay * (config.jitter_percent / 100.0)
    jitter = random.uniform(-jitter_range, jitter_range)
    return max(0.1, base_delay + jitter)


def _parse_retry_after(resp) -> float | None:
    """
    Parse Retry-After header. Review #6.
    Returns seconds to wait, or None if header is absent/invalid.
    Capped at MAX_RETRY_AFTER_SECONDS.
    """
    try:
        header_val = resp.headers.get("Retry-After", "") or resp.headers.get("retry-after", "")
        if not header_val:
            return None
        # Try as integer seconds first
        try:
            seconds = int(header_val)
            return min(max(0.1, float(seconds)), MAX_RETRY_AFTER_SECONDS)
        except ValueError:
            pass
        # Try as HTTP-date (RFC 7231) — fallback to computed backoff
        return None
    except Exception:
        return None


def compute_payload_checksum(payload: dict) -> str:
    try:
        payload_bytes = json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
        return hashlib.sha256(payload_bytes).hexdigest()[:16]
    except Exception:
        return ""


async def deliver_http(
    url: str,
    payload: dict[str, Any],
    headers: dict[str, str],
    channel: ChannelConfig,
    retry_config: RetryConfig,
    event_id: str,
    event_type: str,
    dry_run: bool = False,
) -> list[DeliveryResult]:
    """Deliver payload via HTTP POST with retry. Review #6: honors Retry-After."""
    results: list[DeliveryResult] = []
    checksum = compute_payload_checksum(payload)
    max_attempts = retry_config.max_attempts

    for attempt in range(max_attempts):
        start_time = time.monotonic()
        now_iso = datetime.now(timezone.utc).isoformat()

        if dry_run:
            results.append(DeliveryResult(
                event_id=event_id, event_type=event_type,
                channel_name=channel.name, channel_type=channel.type.value,
                attempt=attempt + 1, max_attempts=max_attempts,
                latency_ms=0.0, status=DeliveryStatus.DRY_RUN.value,
                timestamp=now_iso, payload_checksum=checksum,
            ))
            return results

        http_status = 0
        try:
            import httpx
            async with httpx.AsyncClient(timeout=DELIVERY_TIMEOUT, verify=True) as client:
                merged_headers = {"Content-Type": "application/json", **headers}
                resp = await client.post(url, json=payload, headers=merged_headers)
                http_status = resp.status_code
                elapsed_ms = (time.monotonic() - start_time) * 1000

                if 200 <= http_status < 300:
                    results.append(DeliveryResult(
                        event_id=event_id, event_type=event_type,
                        channel_name=channel.name, channel_type=channel.type.value,
                        attempt=attempt + 1, max_attempts=max_attempts,
                        latency_ms=round(elapsed_ms, 1),
                        status=DeliveryStatus.SUCCESS.value,
                        http_status=http_status, timestamp=now_iso,
                        payload_checksum=checksum,
                    ))
                    return results

                failure = classify_failure(Exception(f"HTTP {http_status}"), http_status)
                results.append(DeliveryResult(
                    event_id=event_id, event_type=event_type,
                    channel_name=channel.name, channel_type=channel.type.value,
                    attempt=attempt + 1, max_attempts=max_attempts,
                    latency_ms=round(elapsed_ms, 1),
                    status=DeliveryStatus.FAILED.value,
                    http_status=http_status,
                    failure_class=failure.value, timestamp=now_iso,
                    payload_checksum=checksum,
                ))

                if failure not in RETRYABLE_FAILURES:
                    return results

                # Review #6: honor Retry-After on 429
                if http_status == 429 and attempt < max_attempts - 1:
                    retry_after = _parse_retry_after(resp)
                    if retry_after is not None:
                        await asyncio.sleep(retry_after)
                        continue

        except Exception as exc:
            elapsed_ms = (time.monotonic() - start_time) * 1000
            failure = classify_failure(exc, http_status)
            results.append(DeliveryResult(
                event_id=event_id, event_type=event_type,
                channel_name=channel.name, channel_type=channel.type.value,
                attempt=attempt + 1, max_attempts=max_attempts,
                latency_ms=round(elapsed_ms, 1),
                status=DeliveryStatus.FAILED.value,
                http_status=http_status, failure_class=failure.value,
                timestamp=now_iso, payload_checksum=checksum,
            ))
            if failure not in RETRYABLE_FAILURES:
                return results

        if attempt < max_attempts - 1:
            delay = compute_backoff(attempt, retry_config)
            await asyncio.sleep(delay)

    return results


async def deliver_email(
    payload: dict[str, Any],
    channel: ChannelConfig,
    retry_config: RetryConfig,
    event_id: str,
    event_type: str,
    dry_run: bool = False,
) -> list[DeliveryResult]:
    """Deliver notification via SMTP email."""
    results: list[DeliveryResult] = []
    max_attempts = retry_config.max_attempts
    checksum = compute_payload_checksum(payload)

    for attempt in range(max_attempts):
        start_time = time.monotonic()
        now_iso = datetime.now(timezone.utc).isoformat()

        if dry_run:
            results.append(DeliveryResult(
                event_id=event_id, event_type=event_type,
                channel_name=channel.name, channel_type=channel.type.value,
                attempt=attempt + 1, max_attempts=max_attempts,
                latency_ms=0.0, status=DeliveryStatus.DRY_RUN.value,
                timestamp=now_iso, payload_checksum=checksum,
            ))
            return results

        try:
            import aiosmtplib
            from email.mime.text import MIMEText
            from email.mime.multipart import MIMEMultipart

            msg = MIMEMultipart("alternative")
            msg["Subject"] = payload.get("subject", "AI SimTest Notification")
            msg["From"] = channel.sender
            msg["To"] = ", ".join(channel.recipients)
            if payload.get("text_body"):
                msg.attach(MIMEText(payload["text_body"], "plain"))
            if payload.get("html_body"):
                msg.attach(MIMEText(payload["html_body"], "html"))

            await aiosmtplib.send(
                msg, hostname=channel.smtp_host, port=channel.smtp_port,
                username=channel.username or None,
                password=channel.password or None,
                use_tls=channel.use_tls, timeout=DELIVERY_TIMEOUT,
            )
            elapsed_ms = (time.monotonic() - start_time) * 1000
            results.append(DeliveryResult(
                event_id=event_id, event_type=event_type,
                channel_name=channel.name, channel_type=channel.type.value,
                attempt=attempt + 1, max_attempts=max_attempts,
                latency_ms=round(elapsed_ms, 1),
                status=DeliveryStatus.SUCCESS.value,
                timestamp=now_iso, payload_checksum=checksum,
            ))
            return results

        except ImportError:
            elapsed_ms = (time.monotonic() - start_time) * 1000
            results.append(DeliveryResult(
                event_id=event_id, event_type=event_type,
                channel_name=channel.name, channel_type=channel.type.value,
                attempt=attempt + 1, max_attempts=max_attempts,
                latency_ms=round(elapsed_ms, 1),
                status=DeliveryStatus.FAILED.value,
                failure_class=FailureClass.UNKNOWN.value,
                timestamp=now_iso, payload_checksum=checksum,
            ))
            logger.warning("aiosmtplib not installed — email delivery unavailable")
            return results

        except Exception as exc:
            elapsed_ms = (time.monotonic() - start_time) * 1000
            failure = classify_failure(exc)
            results.append(DeliveryResult(
                event_id=event_id, event_type=event_type,
                channel_name=channel.name, channel_type=channel.type.value,
                attempt=attempt + 1, max_attempts=max_attempts,
                latency_ms=round(elapsed_ms, 1),
                status=DeliveryStatus.FAILED.value,
                failure_class=failure.value,
                timestamp=now_iso, payload_checksum=checksum,
            ))
            if failure not in RETRYABLE_FAILURES:
                return results

        if attempt < max_attempts - 1:
            delay = compute_backoff(attempt, retry_config)
            await asyncio.sleep(delay)

    return results
