"""
Tests for Multi-Model Comparison — Phase 1.

Covers: models.py, config_loader.py, security.py
Target: ~35-40 tests

Test categories:
- Identity model (ConversationKey, TurnKey, TrialKey)
- Configuration models (ModelSpec, ComparisonSettings, MultiCompareConfig)
- Decision profiles (built-in profiles, custom weights)
- Config loader (YAML parsing, env resolution, validation)
- Security (secret redaction, safe serialization, artifact validation)
- Checkpoint state
- Statistical/forensic/cost models
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from ai_simtest_engine.multi_compare.models import (
    # Enums
    AdapterType, ComparisonMode, Dimension, INVERTED_DIMENSIONS,
    StatisticalVerdictLabel, EvaluatorType, ModelRunStatus,
    ErrorCategory, BudgetRisk, RootCauseBucket, CheckpointStatus,
    GateVerdict,
    # Identity
    ConversationKey, TurnKey, TrialKey,
    # Config
    ModelSpec, ComparisonSettings, DecisionProfile,
    BUILT_IN_PROFILES, MultiCompareConfig,
    # Adapter
    NormalizedError, CanonicalBotResponse,
    # Results
    ModelRunResult,
    # Statistics
    StatisticalVerdict, DimensionScore, ModelRanking,
    # Coverage
    CoverageParityReport,
    # Forensics
    RootCause, FailureForensic,
    # Cost
    CostEfficiency, CostGovernance,
    # Diff
    DiffExplanation,
    # Analysis
    SliceResult, ComparisonMatrix,
    # Manifest
    EvaluatorVersions, RunManifest,
    # Checkpoint
    CheckpointState,
    # Gate
    GateCheck, GateResult,
    # Report
    MultiCompareReport,
)

from ai_simtest_engine.multi_compare.config_loader import (
    load_multi_compare_config,
    ConfigLoadResult,
    ConfigLoadError,
    EndpointNotAllowedError,
    SecretResolutionError,
    redact_secrets,
    contains_secrets,
    redact_model_spec,
    compute_config_hash,
)

from ai_simtest_engine.multi_compare.security import (
    RedactingFormatter,
    SafeSerializer,
    SecurityError,
    AuditLogger,
    validate_artifact,
    sanitize_notification_payload,
)


# ═══════════════════════════════════════════════════════════
# 1. IDENTITY MODEL TESTS
# ═══════════════════════════════════════════════════════════

class TestConversationKey:
    """Tests for Tier 1: ConversationKey."""

    def test_default_conversation_key(self):
        key = ConversationKey(persona_id="p001")
        assert key.to_string() == "p001:none:0"

    def test_full_conversation_key(self):
        key = ConversationKey(
            persona_id="p003", scenario_id="goal_shift", conversation_index=2
        )
        assert key.to_string() == "p003:goal_shift:2"

    def test_conversation_key_equality(self):
        k1 = ConversationKey(persona_id="p001", scenario_id="none", conversation_index=0)
        k2 = ConversationKey(persona_id="p001")
        assert k1 == k2

    def test_conversation_key_hash_stability(self):
        k1 = ConversationKey(persona_id="p001", scenario_id="test")
        k2 = ConversationKey(persona_id="p001", scenario_id="test")
        assert hash(k1) == hash(k2)
        assert k1 in {k2}

    def test_conversation_key_inequality(self):
        k1 = ConversationKey(persona_id="p001")
        k2 = ConversationKey(persona_id="p002")
        assert k1 != k2

    def test_conversation_key_frozen(self):
        key = ConversationKey(persona_id="p001")
        with pytest.raises(Exception):
            key.persona_id = "p002"


class TestTurnKey:
    """Tests for Tier 2: TurnKey."""

    def test_turn_key_format(self):
        ck = ConversationKey(persona_id="p001", scenario_id="goal_shift")
        tk = TurnKey(conversation_key=ck, turn_index=4, speaker="bot")
        assert tk.to_string() == "p001:goal_shift:0:t04:bot"

    def test_turn_key_user_speaker(self):
        ck = ConversationKey(persona_id="p002")
        tk = TurnKey(conversation_key=ck, turn_index=0, speaker="user")
        assert tk.to_string() == "p002:none:0:t00:user"

    def test_turn_key_invalid_speaker(self):
        ck = ConversationKey(persona_id="p001")
        with pytest.raises(Exception):
            TurnKey(conversation_key=ck, turn_index=0, speaker="system")


class TestTrialKey:
    """Tests for Tier 3: TrialKey."""

    def test_trial_key_format(self):
        ck = ConversationKey(persona_id="p001", scenario_id="test")
        tk = TrialKey(conversation_key=ck, trial_number=2)
        assert tk.to_string() == "p001:test:0:trial_2"

    def test_trial_key_default(self):
        ck = ConversationKey(persona_id="p001")
        tk = TrialKey(conversation_key=ck)
        assert tk.to_string() == "p001:none:0:trial_1"


# ═══════════════════════════════════════════════════════════
# 2. CONFIGURATION MODEL TESTS
# ═══════════════════════════════════════════════════════════

class TestModelSpec:
    """Tests for ModelSpec model."""

    def test_minimal_model_spec(self):
        spec = ModelSpec(id="test-bot", name="Test", endpoint="http://localhost:8000")
        assert spec.id == "test-bot"
        assert spec.adapter == AdapterType.AUTO
        assert spec.timeout_seconds == 30
        assert spec.verify_ssl is True

    def test_api_key_excluded_from_serialization(self):
        spec = ModelSpec(
            id="test", name="Test", endpoint="http://localhost",
            api_key="sk-secret123456789012345"
        )
        dumped = spec.model_dump()
        assert "api_key" not in dumped

    def test_invalid_model_id(self):
        with pytest.raises(Exception):
            ModelSpec(id="", name="Test", endpoint="http://localhost")

    def test_model_spec_with_all_fields(self):
        spec = ModelSpec(
            id="gpt4o-prod",
            name="GPT-4o Production",
            endpoint="https://api.openai.com/v1/chat/completions",
            adapter=AdapterType.OPENAI,
            api_key_env="OPENAI_API_KEY",
            headers={"X-Custom": "value"},
            request_format="openai",
            response_path="choices[0].message.content",
            timeout_seconds=60,
            verify_ssl=True,
            tags=["production", "openai"],
            metadata={"region": "us-east-1"},
        )
        assert spec.adapter == AdapterType.OPENAI
        assert len(spec.tags) == 2


class TestComparisonSettings:
    """Tests for ComparisonSettings."""

    def test_default_settings(self):
        s = ComparisonSettings()
        assert s.personas == 10
        assert s.max_turns == 8
        assert s.parallel == 3
        assert s.budget_mode == "soft"

    def test_custom_settings(self):
        s = ComparisonSettings(personas=5, max_turns=12, budget_limit=5.0)
        assert s.personas == 5
        assert s.budget_limit == 5.0


class TestMultiCompareConfig:
    """Tests for MultiCompareConfig validation."""

    def _make_models(self, n=2):
        return [
            ModelSpec(id=f"model-{i}", name=f"Model {i}", endpoint=f"http://bot{i}:8000")
            for i in range(n)
        ]

    def test_valid_champion_challenger_config(self):
        models = self._make_models(3)
        config = MultiCompareConfig(
            mode=ComparisonMode.CHAMPION_CHALLENGER,
            champion="model-0",
            models=models,
        )
        assert config.champion == "model-0"
        assert len(config.models) == 3

    def test_duplicate_model_ids_rejected(self):
        with pytest.raises(Exception, match="Duplicate model IDs"):
            MultiCompareConfig(
                models=[
                    ModelSpec(id="same", name="A", endpoint="http://a"),
                    ModelSpec(id="same", name="B", endpoint="http://b"),
                ],
                mode=ComparisonMode.HEAD_TO_HEAD,
            )

    def test_champion_not_in_models_rejected(self):
        with pytest.raises(Exception, match="not found in models"):
            MultiCompareConfig(
                mode=ComparisonMode.CHAMPION_CHALLENGER,
                champion="nonexistent",
                models=self._make_models(2),
            )

    def test_champion_required_in_champion_mode(self):
        with pytest.raises(Exception, match="champion"):
            MultiCompareConfig(
                mode=ComparisonMode.CHAMPION_CHALLENGER,
                models=self._make_models(2),
            )

    def test_head_to_head_no_champion_needed(self):
        config = MultiCompareConfig(
            mode=ComparisonMode.HEAD_TO_HEAD,
            models=self._make_models(2),
        )
        assert config.champion is None

    def test_too_few_models_rejected(self):
        with pytest.raises(Exception):
            MultiCompareConfig(
                mode=ComparisonMode.HEAD_TO_HEAD,
                models=[ModelSpec(id="only", name="Only", endpoint="http://only")],
            )

    def test_custom_decision_profile_requires_weights(self):
        with pytest.raises(Exception, match="decision_weights"):
            MultiCompareConfig(
                mode=ComparisonMode.HEAD_TO_HEAD,
                models=self._make_models(2),
                decision_profile="custom",
            )


class TestDecisionProfile:
    """Tests for DecisionProfile validation and built-in profiles."""

    def test_all_built_in_profiles_valid(self):
        for name, profile in BUILT_IN_PROFILES.items():
            total = sum(profile.weights.values())
            assert 0.95 <= total <= 1.05, f"Profile '{name}' weights sum to {total}"
            assert profile.is_built_in is True

    def test_custom_profile_valid_weights(self):
        dp = DecisionProfile(
            name="custom",
            weights={"safety": 0.5, "quality": 0.3, "cost_efficiency": 0.2},
        )
        assert dp.name == "custom"

    def test_custom_profile_invalid_weights(self):
        with pytest.raises(Exception, match="sum to ~1.0"):
            DecisionProfile(
                name="bad", weights={"safety": 0.5, "quality": 0.1}
            )

    def test_negative_weights_rejected(self):
        with pytest.raises(Exception, match="non-negative"):
            DecisionProfile(
                name="neg", weights={"safety": -0.5, "quality": 1.5}
            )


# ═══════════════════════════════════════════════════════════
# 3. CONFIG LOADER TESTS
# ═══════════════════════════════════════════════════════════

class TestConfigLoader:
    """Tests for YAML config loading and validation."""

    def _write_yaml(self, content: str) -> Path:
        f = tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False)
        f.write(content)
        f.close()
        return Path(f.name)

    def test_valid_minimal_config(self):
        yaml_content = """
comparison:
  name: "Test Comparison"
  mode: head_to_head

models:
  - id: model-a
    name: "Model A"
    endpoint: http://localhost:8001
  - id: model-b
    name: "Model B"
    endpoint: http://localhost:8002
"""
        path = self._write_yaml(yaml_content)
        result = load_multi_compare_config(path)
        assert result.config.name == "Test Comparison"
        assert len(result.config.models) == 2
        assert result.config.mode == ComparisonMode.HEAD_TO_HEAD
        os.unlink(path)

    def test_missing_file_raises(self):
        with pytest.raises(FileNotFoundError):
            load_multi_compare_config("/nonexistent/path.yaml")

    def test_invalid_yaml_raises(self):
        path = self._write_yaml("{{invalid: yaml: [")
        with pytest.raises(ConfigLoadError, match="Invalid YAML"):
            load_multi_compare_config(path)
        os.unlink(path)

    def test_too_few_models_raises(self):
        path = self._write_yaml("""
models:
  - id: only-one
    name: "Only"
    endpoint: http://localhost
""")
        with pytest.raises(ConfigLoadError, match="at least 2"):
            load_multi_compare_config(path)
        os.unlink(path)

    def test_env_var_resolution(self):
        yaml_content = """
comparison:
  mode: head_to_head

models:
  - id: model-a
    name: "Model A"
    endpoint: http://localhost:8001
    api_key_env: TEST_MC_API_KEY
  - id: model-b
    name: "Model B"
    endpoint: http://localhost:8002
"""
        path = self._write_yaml(yaml_content)
        with patch.dict(os.environ, {"TEST_MC_API_KEY": "sk-testkey123"}):
            result = load_multi_compare_config(path)
            assert result.config.models[0].api_key == "sk-testkey123"
        os.unlink(path)

    def test_missing_env_var_raises(self):
        yaml_content = """
comparison:
  mode: head_to_head

models:
  - id: model-a
    name: "A"
    endpoint: http://localhost
    api_key_env: NONEXISTENT_VAR_12345
  - id: model-b
    name: "B"
    endpoint: http://localhost:2
"""
        path = self._write_yaml(yaml_content)
        # Ensure the var doesn't exist
        os.environ.pop("NONEXISTENT_VAR_12345", None)
        with pytest.raises(SecretResolutionError, match="NONEXISTENT_VAR_12345"):
            load_multi_compare_config(path)
        os.unlink(path)

    def test_generic_adapter_requires_response_path(self):
        yaml_content = """
comparison:
  mode: head_to_head

models:
  - id: model-a
    name: "A"
    endpoint: http://localhost
    adapter: generic
  - id: model-b
    name: "B"
    endpoint: http://localhost:2
"""
        path = self._write_yaml(yaml_content)
        with pytest.raises(ConfigLoadError, match="response_path"):
            load_multi_compare_config(path)
        os.unlink(path)

    def test_auto_adapter_warning(self):
        yaml_content = """
comparison:
  mode: head_to_head

models:
  - id: model-a
    name: "A"
    endpoint: http://localhost
  - id: model-b
    name: "B"
    endpoint: http://localhost:2
"""
        path = self._write_yaml(yaml_content)
        result = load_multi_compare_config(path)
        assert any("auto-detect" in w for w in result.warnings)
        os.unlink(path)

    def test_endpoint_allowlist_pass(self):
        yaml_content = """
comparison:
  mode: head_to_head

models:
  - id: a
    name: "A"
    endpoint: https://api.example.com/chat
  - id: b
    name: "B"
    endpoint: https://bot.example.com/chat

allowed_endpoints:
  - "*.example.com"
"""
        path = self._write_yaml(yaml_content)
        result = load_multi_compare_config(path)
        assert len(result.config.models) == 2
        os.unlink(path)

    def test_endpoint_allowlist_fail(self):
        yaml_content = """
comparison:
  mode: head_to_head

models:
  - id: a
    name: "A"
    endpoint: https://api.evil.com/chat
  - id: b
    name: "B"
    endpoint: https://bot.example.com/chat

allowed_endpoints:
  - "*.example.com"
"""
        path = self._write_yaml(yaml_content)
        with pytest.raises(EndpointNotAllowedError, match="evil.com"):
            load_multi_compare_config(path)
        os.unlink(path)


# ═══════════════════════════════════════════════════════════
# 4. SECURITY TESTS
# ═══════════════════════════════════════════════════════════

class TestSecretRedaction:
    """Tests for secret pattern detection and redaction."""

    def test_redact_openai_key(self):
        text = "key: sk-abc123def456ghi789jkl012mno345"
        result = redact_secrets(text)
        assert "sk-abc" not in result
        assert "[REDACTED]" in result

    def test_redact_anthropic_key(self):
        text = "key: sk-ant-abc123def456ghi789jkl012"
        result = redact_secrets(text)
        assert "sk-ant" not in result

    def test_redact_aws_key(self):
        text = "access: AKIAIOSFODNN7EXAMPLE"
        result = redact_secrets(text)
        assert "AKIA" not in result

    def test_redact_slack_token(self):
        text = "token: xoxb-123-456-abc789def"
        result = redact_secrets(text)
        assert "xoxb-" not in result

    def test_redact_bearer_token(self):
        text = "Authorization: Bearer eyJhbGciOiJIUzI1NiJ9.abc123"
        result = redact_secrets(text)
        assert "eyJhbG" not in result

    def test_no_false_positive_on_normal_text(self):
        text = "This is a normal response about banking"
        assert redact_secrets(text) == text

    def test_contains_secrets_detection(self):
        assert contains_secrets("sk-abc123def456ghi789jkl012mno345") is True
        assert contains_secrets("hello world") is False


class TestSafeSerializer:
    """Tests for SafeSerializer."""

    def test_removes_forbidden_fields(self):
        data = {"name": "test", "api_key": "sk-secret", "score": 0.95}
        result = SafeSerializer.sanitize_dict(data)
        assert "api_key" not in result
        assert result["name"] == "test"

    def test_redacts_nested_secrets(self):
        data = {
            "models": [
                {"id": "a", "headers": {"Auth": "Bearer sk-abc123def456ghi789jkl012"}}
            ]
        }
        result = SafeSerializer.sanitize_dict(data)
        assert "sk-abc" not in json.dumps(result)

    def test_to_json_is_clean(self):
        data = {"config": {"api_key": "sk-test123", "name": "test"}}
        result = SafeSerializer.to_json(data)
        assert "sk-test" not in result
        assert "api_key" not in result

    def test_write_json_creates_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "test.json"
            data = {"name": "test", "score": 0.95}
            result = SafeSerializer.write_json(data, path)
            assert result.exists()
            content = json.loads(path.read_text())
            assert content["name"] == "test"

    def test_write_json_blocks_if_secrets_survive(self):
        """
        This tests the scan-before-write defense-in-depth.
        In practice, sanitize_dict should catch everything,
        but this verifies the final safety net works.
        """
        # We can't easily make sanitize_dict miss a secret,
        # so we test the validator directly
        assert validate_artifact("safe content") is True
        with pytest.raises(SecurityError):
            validate_artifact("key: sk-abc123def456ghi789jkl012mno345")


class TestRedactingFormatter:
    """Tests for log redaction formatter."""

    def test_formatter_redacts_in_log_message(self):
        import logging
        formatter = RedactingFormatter("%(message)s")
        record = logging.LogRecord(
            name="test", level=logging.INFO, pathname="", lineno=0,
            msg="Using key sk-abc123def456ghi789jkl012mno345", args=(), exc_info=None,
        )
        result = formatter.format(record)
        assert "sk-abc" not in result
        assert "[REDACTED]" in result


class TestAuditLogger:
    """Tests for audit trail."""

    def test_log_and_retrieve(self):
        logger = AuditLogger()
        logger.log("run_started", {"config": "test.yaml"})
        logger.log("model_completed", {"model_id": "a"})
        entries = logger.get_entries()
        assert len(entries) == 2
        assert entries[0]["event"] == "run_started"

    def test_audit_sanitizes_details(self):
        logger = AuditLogger()
        logger.log("config_loaded", {"api_key": "sk-secret123", "name": "test"})
        entries = logger.get_entries()
        assert "api_key" not in entries[0].get("details", {})


class TestNotificationSanitizer:
    """Tests for notification payload sanitization."""

    def test_strips_raw_response(self):
        payload = {
            "model_id": "test",
            "raw_response": {"full": "response"},
            "score": 0.95,
        }
        result = sanitize_notification_payload(payload)
        assert "raw_response" not in result
        assert result["model_id"] == "test"

    def test_strips_api_keys(self):
        payload = {"api_key": "sk-secret", "result": "pass"}
        result = sanitize_notification_payload(payload)
        assert "api_key" not in result


class TestRedactModelSpec:
    """Tests for model spec redaction in manifests."""

    def test_api_key_not_in_redacted_spec(self):
        spec = ModelSpec(
            id="test", name="Test", endpoint="http://localhost",
            api_key="sk-verysecretkey12345678901234"
        )
        redacted = redact_model_spec(spec)
        assert "api_key" not in redacted
        assert json.dumps(redacted).find("sk-very") == -1

    def test_header_secrets_redacted(self):
        spec = ModelSpec(
            id="test", name="Test", endpoint="http://localhost",
            headers={"Authorization": "Bearer sk-abc123def456ghi789jkl012"}
        )
        redacted = redact_model_spec(spec)
        assert "sk-abc" not in json.dumps(redacted)


# ═══════════════════════════════════════════════════════════
# 5. OTHER MODEL TESTS
# ═══════════════════════════════════════════════════════════

class TestCanonicalBotResponse:
    """Tests for CanonicalBotResponse."""

    def test_basic_response(self):
        r = CanonicalBotResponse(content="Hello!", latency_ms=150.5)
        assert r.content == "Hello!"
        assert r.is_error is False

    def test_error_response(self):
        r = CanonicalBotResponse(
            content="",
            error=NormalizedError(category=ErrorCategory.RATE_LIMIT, message="429", retryable=True),
        )
        assert r.is_error is True
        assert r.error.retryable is True

    def test_total_tokens(self):
        r = CanonicalBotResponse(input_tokens=100, output_tokens=50)
        assert r.total_tokens == 150

    def test_total_tokens_none_when_partial(self):
        r = CanonicalBotResponse(input_tokens=100)
        assert r.total_tokens is None

    def test_raw_response_excluded_from_serialization(self):
        r = CanonicalBotResponse(content="test", raw_response={"full": "body"})
        dumped = r.model_dump()
        assert "raw_response" not in dumped


class TestModelRunResult:
    """Tests for ModelRunResult."""

    def test_usable_on_success(self):
        r = ModelRunResult(model_id="a", model_name="A", status=ModelRunStatus.SUCCESS)
        assert r.is_usable() is True

    def test_usable_on_budget_terminated(self):
        r = ModelRunResult(model_id="a", model_name="A", status=ModelRunStatus.BUDGET_TERMINATED)
        assert r.is_usable() is True

    def test_not_usable_on_failed(self):
        r = ModelRunResult(model_id="a", model_name="A", status=ModelRunStatus.FAILED)
        assert r.is_usable() is False


class TestRunManifest:
    """Tests for RunManifest."""

    def test_compute_hash(self):
        h1 = RunManifest.compute_hash("same content")
        h2 = RunManifest.compute_hash("same content")
        h3 = RunManifest.compute_hash("different content")
        assert h1 == h2
        assert h1 != h3

    def test_manifest_has_uuid(self):
        m = RunManifest()
        assert len(m.manifest_id) == 36  # UUID4 format


class TestCheckpointState:
    """Tests for CheckpointState model."""

    def test_initial_state(self):
        cs = CheckpointState(
            manifest_id="test-uuid",
            config_hash="abc123",
            persona_set_id="def456",
            models_pending=["model-a", "model-b"],
        )
        assert cs.status == CheckpointStatus.IN_PROGRESS
        assert len(cs.models_pending) == 2
        assert cs.resume_count == 0


class TestGateResult:
    """Tests for GateResult."""

    def test_passing_gate(self):
        gr = GateResult(
            verdict=GateVerdict.PASS,
            checks=[GateCheck(name="regression", passed=True)],
        )
        assert gr.passed is True

    def test_failing_gate(self):
        gr = GateResult(
            verdict=GateVerdict.FAIL,
            checks=[
                GateCheck(name="regression", passed=False, reason="Pass rate dropped"),
            ],
        )
        assert gr.passed is False


class TestEnums:
    """Tests for enum values and inverted dimensions."""

    def test_inverted_dimensions(self):
        assert Dimension.RESPONSE_LATENCY in INVERTED_DIMENSIONS
        assert Dimension.CRITICAL_FAILURE_RATE in INVERTED_DIMENSIONS
        assert Dimension.QUALITY not in INVERTED_DIMENSIONS

    def test_all_dimensions_have_values(self):
        assert len(Dimension) == 10

    def test_evaluator_types(self):
        assert EvaluatorType.LLM_AS_JUDGE.value == "llm_as_judge"
        assert EvaluatorType.HEURISTIC.value == "heuristic"

    def test_root_cause_buckets(self):
        assert len(RootCauseBucket) == 10

# ═══════════════════════════════════════════════════════════
# 6. REVIEW FIX TESTS (Review #1–#8)
# ═══════════════════════════════════════════════════════════

class TestReviewFix1_ConfigLoadResult:
    """Review #1: Config loader returns ConfigLoadResult, not tuple."""

    def _write_yaml(self, content):
        import tempfile
        f = tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False)
        f.write(content)
        f.close()
        return Path(f.name)

    def test_returns_config_load_result(self):
        path = self._write_yaml("""
comparison:
  mode: head_to_head
models:
  - id: a
    name: A
    endpoint: http://localhost:8001
  - id: b
    name: B
    endpoint: http://localhost:8002
""")
        result = load_multi_compare_config(path)
        assert isinstance(result, ConfigLoadResult)
        assert hasattr(result, "config")
        assert hasattr(result, "warnings")
        assert hasattr(result, "config_hash")
        assert isinstance(result.config, MultiCompareConfig)
        assert isinstance(result.warnings, list)
        assert len(result.config_hash) == 64  # SHA-256 hex
        os.unlink(path)

    def test_config_hash_changes_with_content(self):
        p1 = self._write_yaml("comparison:\n  mode: head_to_head\nmodels:\n  - id: a\n    name: A\n    endpoint: http://a\n  - id: b\n    name: B\n    endpoint: http://b\n")
        p2 = self._write_yaml("comparison:\n  mode: head_to_head\nmodels:\n  - id: x\n    name: X\n    endpoint: http://x\n  - id: y\n    name: Y\n    endpoint: http://y\n")
        r1 = load_multi_compare_config(p1)
        r2 = load_multi_compare_config(p2)
        assert r1.config_hash != r2.config_hash
        os.unlink(p1)
        os.unlink(p2)


class TestReviewFix6_FailFast:
    """Review #6: Unknown decision profiles fail loudly."""

    def test_unknown_profile_raises(self):
        config = MultiCompareConfig(
            mode=ComparisonMode.HEAD_TO_HEAD,
            models=[
                ModelSpec(id="a", name="A", endpoint="http://localhost:1"),
                ModelSpec(id="b", name="B", endpoint="http://localhost:2"),
            ],
            decision_profile="nonexistent_profile",
        )
        with pytest.raises(ValueError, match="Unknown decision profile"):
            config.get_decision_profile()

    def test_known_profile_works(self):
        config = MultiCompareConfig(
            mode=ComparisonMode.HEAD_TO_HEAD,
            models=[
                ModelSpec(id="a", name="A", endpoint="http://localhost:1"),
                ModelSpec(id="b", name="B", endpoint="http://localhost:2"),
            ],
            decision_profile="safety_first",
        )
        profile = config.get_decision_profile()
        assert profile.name == "safety_first"

    def test_endpoint_url_validation(self):
        """ModelSpec rejects endpoints without http:// or https://."""
        with pytest.raises(Exception, match="http"):
            ModelSpec(id="bad", name="Bad", endpoint="not-a-url.com/api")

    def test_endpoint_http_valid(self):
        spec = ModelSpec(id="ok", name="Ok", endpoint="http://localhost:8000")
        assert spec.endpoint == "http://localhost:8000"

    def test_endpoint_https_valid(self):
        spec = ModelSpec(id="ok", name="Ok", endpoint="https://api.example.com/v1")
        assert spec.endpoint == "https://api.example.com/v1"
