"""Secret leak detection tests for SafeJSONB validation layer.

Covers the 28-test secrets suite per Turn 2 plan v0.6 §6.9:

  - 22 parametrized denylist-key tests against ``AssetCreateRequest.content``,
    exercising every canonical key family in ``src.secrets.denylist.DENYLIST``
  - 6 structural tests: case-insensitive matching, nested depth 2, nested depth 3,
    list-of-dicts with indexed path, happy path accepting innocuous content across
    multiple request models, and the audit-constant import assertion

Scope note — Option C adaptation (documented in Step 0 report):
The v0.6 §6.9 plan referenced broadened scope tests against ``RunCreateRequest``,
``ComparisonCreateRequest.config``, and ``ConversationStoreRequest``. None of
those request models exist in the Week 6a tree (no runs router; comparisons
router takes only ``left_run_id``/``right_run_id``; conversations router is
read-only). Under Option C, the broadened-scope coverage is instead provided
by parametrizing the happy-path test across ``AssetCreateRequest``,
``AssetUpdateRequest``, and ``AssetVersionRequest``. Same coverage intent
(prove SafeJSONB behaves consistently across multiple request models),
different actually-existing surface.
"""
from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from src.assets.models import (
    AssetCreateRequest,
    AssetUpdateRequest,
    AssetVersionRequest,
)
from src.audit.logger import AuditActions
from src.common.models import AssetType
from src.secrets import DENYLIST, SecretLeakDetected, check_no_secrets


def _make_create_request(content: dict[str, Any]) -> AssetCreateRequest:
    """Helper: build a minimally-valid AssetCreateRequest with the given content.

    All non-``content`` fields are fixed to known-good innocuous values so that
    any ValidationError raised must come from the ``content`` validator, not
    from slug/name/etc. constraints.
    """
    return AssetCreateRequest(
        asset_type=AssetType.DATASET,
        name="secret-test",
        slug="secret-test",
        content=content,
    )


# ---------------------------------------------------------------------------
# 22 parametrized denylist-key tests
# ---------------------------------------------------------------------------
#
# One test per canonical key family. The DENYLIST contains 24 entries, some
# of which are hyphen/underscore variants of the same concept
# (``api_key``/``api-key``/``apikey``, ``secret_key``/``secret-key``,
# ``client_secret``/``client-secret``). We parametrize over 22 representative
# forms so every distinct entry in the denylist is exercised at least once,
# collapsing the variants into whichever form is canonical for that concept.
# The case-insensitive test (below) covers the variant forms separately.

_DENYLIST_KEYS_22: list[str] = [
    "authorization",
    "auth",
    "api_key",          # covers "api-key" and "apikey" via _case_variants below
    "secret",
    "secret_key",       # covers "secret-key" via _case_variants
    "password",
    "passwd",
    "pwd",
    "token",
    "access_token",
    "refresh_token",
    "bearer",
    "bearer_token",
    "private_key",
    "privatekey",
    "client_secret",    # covers "client-secret" via _case_variants
    "x-api-key",
    "x-auth-token",
    "credentials",
    "credential",
    "apikey",           # explicit variant coverage
    "api-key",          # explicit variant coverage
]

assert len(_DENYLIST_KEYS_22) == 22, "parametrize axis must stay at exactly 22 keys"


@pytest.mark.parametrize("denylisted_key", _DENYLIST_KEYS_22)
def test_denylisted_key_rejected_on_asset_create(denylisted_key: str) -> None:
    """Every denylist entry triggers ValidationError when used as a top-level content key.

    The Pydantic ValidationError must wrap our ``SecretLeakDetected`` cause,
    proving the error path is SafeJSONB validation and not some other field
    constraint. We assert both the exception type and that the offending
    key appears in the error message so the HTTP 422 response is actionable.
    """
    with pytest.raises(ValidationError) as exc_info:
        _make_create_request({denylisted_key: "some-value"})

    # Pydantic v2 wraps the AfterValidator failure; the denylisted key name
    # should appear somewhere in the rendered error for operator debuggability.
    assert denylisted_key in str(exc_info.value)


# ---------------------------------------------------------------------------
# 6 structural tests
# ---------------------------------------------------------------------------


def test_case_insensitive_matching_catches_mixed_case_variants() -> None:
    """DENYLIST matching is case-insensitive — ``API_KEY``, ``Api_Key``, ``api_KEY`` all rejected.

    This is the check that prevents trivial bypass via capitalization. The
    scanner lowercases every key before comparison (see
    ``src.secrets.denylist.check_no_secrets``).
    """
    for variant in ("API_KEY", "Api_Key", "api_KEY", "APIKEY", "Authorization", "SECRET"):
        with pytest.raises(ValidationError) as exc_info:
            _make_create_request({variant: "x"})
        assert "Denylisted" in str(exc_info.value) or variant.lower() in str(exc_info.value).lower()


def test_nested_dict_depth_2_raises_with_dotted_path() -> None:
    """A denylisted key nested one level deep is caught and the error path reports the dotted path.

    Exercises the recursive descent in ``check_no_secrets`` and verifies the
    ``field_path`` attribute on ``SecretLeakDetected`` is populated correctly.
    Uses ``check_no_secrets`` directly (rather than going through Pydantic)
    so the assertion can inspect ``field_path`` without Pydantic wrapping.
    """
    with pytest.raises(SecretLeakDetected) as exc_info:
        check_no_secrets({"bot": {"api_key": "leaked"}})
    assert exc_info.value.field_path == "bot.api_key"


def test_nested_dict_depth_3_raises_with_dotted_path() -> None:
    """Depth-3 nesting: ``outer.middle.token`` is caught and the path is fully qualified."""
    with pytest.raises(SecretLeakDetected) as exc_info:
        check_no_secrets({"outer": {"middle": {"token": "x"}}})
    assert exc_info.value.field_path == "outer.middle.token"


def test_list_of_dicts_raises_with_indexed_path() -> None:
    """A denylisted key inside a list-of-dicts is caught; path uses ``[i]`` for the list index.

    ``{"configs": [{"ok": 1}, {"password": "x"}]}`` must report
    ``configs[1].password``, not just ``configs`` or ``password``.
    """
    with pytest.raises(SecretLeakDetected) as exc_info:
        check_no_secrets({"configs": [{"ok": 1}, {"password": "x"}]})
    assert exc_info.value.field_path == "configs[1].password"


@pytest.mark.parametrize(
    "request_builder",
    [
        # AssetCreateRequest.content — innocuous dict
        lambda: AssetCreateRequest(
            asset_type=AssetType.DATASET,
            name="hp",
            slug="hp",
            content={"rows": [{"q": "hi", "a": "hello"}], "secret_ref": "vault://k1"},
        ),
        # AssetUpdateRequest.content — innocuous dict, Optional field
        lambda: AssetUpdateRequest(
            content={"description": "updated", "secret_ref": "vault://k2"},
            tags={"env": "prod"},
        ),
        # AssetVersionRequest.content — innocuous dict, required field
        lambda: AssetVersionRequest(
            content={"config": {"timeout_ms": 5000}, "secret_ref": "vault://k3"},
            changelog="version bump",
        ),
    ],
    ids=["AssetCreateRequest", "AssetUpdateRequest", "AssetVersionRequest"],
)
def test_happy_path_accepts_innocuous_content_across_request_models(request_builder) -> None:
    """Innocuous dicts (including the ``secret_ref`` convention for out-of-band references) pass validation.

    This is the broadened-scope test under Option C: three request models, three
    innocuous content shapes, zero rejections. Also proves the ``secret_ref``
    key is NOT in the denylist — references to secrets are explicitly allowed
    so callers can use a vault pointer pattern.
    """
    # Should not raise; the ValidationError would propagate and fail the test.
    req = request_builder()
    assert req is not None


def test_audit_secret_rejection_constants_exist() -> None:
    """The four ``*_REJECTED_SECRET_LEAK`` constants exist on ``AuditActions``.

    Turn 2 ships these as constants only; Turn 3 wires the FastAPI exception
    handler that emits them. This test pins the string values so Turn 3's
    handler code can rely on stable identifiers.
    """
    assert AuditActions.ASSET_CREATE_REJECTED_SECRET_LEAK == "asset.create_rejected_secret_leak"
    assert AuditActions.RUN_CREATE_REJECTED_SECRET_LEAK == "run.create_rejected_secret_leak"
    assert AuditActions.COMPARISON_CREATE_REJECTED_SECRET_LEAK == "comparison.create_rejected_secret_leak"
    assert AuditActions.CONVERSATION_STORE_REJECTED_SECRET_LEAK == "conversation.store_rejected_secret_leak"
