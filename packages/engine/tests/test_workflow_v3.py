"""
Workflow Judge v3 — Integration tests for all new capabilities.
Tests cover: applicability skip, bot-only evaluation, turn-grounded order,
score breakdown, failure taxonomy, needs-review, loader validation.

56 tests across 11 test groups.
"""
import asyncio
import pytest
from src.workflow_judge.models import (
    BotTurn, FailureCategory, HardRule, HardRuleType, MatchType,
    ScoreBreakdown, StepEvidence, SuccessCondition, WorkflowDefinition,
    WorkflowResult, WorkflowStatus, WorkflowStep, WorkflowStepStatus,
)
from src.workflow_judge.judge import FunctionalJudge
from src.workflow_judge.loader import WorkflowLoadError, WorkflowLoader
from src.workflow_judge.rule_engine import RuleEngine
from src.workflow_judge.llm_evaluator import LLMWorkflowEvaluator


# ═══════════════════════════════════════════════════════════════════════════════
# Fixtures
# ═══════════════════════════════════════════════════════════════════════════════

@pytest.fixture
def wf_bank():
    return WorkflowDefinition(
        name="Banking Account Opening", domain="banking",
        activation_hints=["open account", "savings account", "new account"],
        skip_if_not_applicable=True,
        steps=[WorkflowStep(id="s1", name="Identify Intent", detection_hints=["open", "account"])],
        hard_rules=[],
        success_conditions=[SuccessCondition(id="c1", description="User informed")]
    )

@pytest.fixture
def wf_refund():
    return WorkflowDefinition(
        name="Refund Flow", domain="ecommerce",
        steps=[
            WorkflowStep(id="identify_order", name="Identify Order",
                         detection_hints=["order number", "order id", "which order"], required=True),
            WorkflowStep(id="understand_reason", name="Understand Return Reason",
                         detection_hints=["reason", "why", "problem", "defective"], required=True),
        ],
        success_conditions=[SuccessCondition(id="c1", description="Return initiated")],
    )

@pytest.fixture
def wf_ordered():
    return WorkflowDefinition(
        name="Ordered Flow", order_mode="strict",
        steps=[
            WorkflowStep(id="s1", name="Step 1", order=1, detection_hints=["verify identity"], required=True),
            WorkflowStep(id="s2", name="Step 2", order=2, detection_hints=["process request"], required=True),
            WorkflowStep(id="s3", name="Step 3", order=3, detection_hints=["confirm action"], required=True),
        ],
    )

@pytest.fixture
def wf_safety():
    return WorkflowDefinition(
        name="Safety Test",
        steps=[WorkflowStep(id="s1", name="Verify", detection_hints=["verify"], required=True)],
        hard_rules=[
            HardRule(id="no_password", name="Never Ask Password",
                     rule_type=HardRuleType.FORBIDDEN_PHRASE,
                     values=["your password"], severity="critical"),
        ],
    )

@pytest.fixture
def turns_unrelated():
    return [
        {"speaker": "user", "message": "What is your return policy?"},
        {"speaker": "bot", "message": "Our return policy allows returns within 30 days."},
    ]

@pytest.fixture
def turns_applicable():
    return [
        {"speaker": "user", "message": "I want to open a savings account"},
        {"speaker": "bot", "message": "I can help you open an account. Let me explain the options."},
    ]

@pytest.fixture
def turns_user_mentions():
    """User says keywords but bot does NOT — should NOT count as bot completing steps."""
    return [
        {"speaker": "user", "message": "I have a reason to return order number 12345 because it is defective"},
        {"speaker": "bot", "message": "Thank you for contacting us. How can I assist you today?"},
        {"speaker": "user", "message": "I told you my problem already!"},
        {"speaker": "bot", "message": "I apologize for the confusion. Let me look into that for you."},
    ]

@pytest.fixture
def turns_bot_does():
    """Bot explicitly performs the steps."""
    return [
        {"speaker": "user", "message": "I want to return something"},
        {"speaker": "bot", "message": "I'd be happy to help! Can you provide your order number or order id?"},
        {"speaker": "user", "message": "Order 12345"},
        {"speaker": "bot", "message": "Thanks. What is the reason for the return? Is the item defective or is there another problem?"},
    ]

@pytest.fixture
def turns_correct_order():
    return [
        {"speaker": "user", "message": "Help me"},
        {"speaker": "bot", "message": "Let me verify identity first."},
        {"speaker": "user", "message": "OK"},
        {"speaker": "bot", "message": "Now I will process request."},
        {"speaker": "user", "message": "Thanks"},
        {"speaker": "bot", "message": "Let me confirm action for you."},
    ]

@pytest.fixture
def turns_wrong_order():
    return [
        {"speaker": "user", "message": "Help me"},
        {"speaker": "bot", "message": "Let me verify identity."},
        {"speaker": "user", "message": "OK"},
        {"speaker": "bot", "message": "Let me confirm action first."},
        {"speaker": "user", "message": "Wait"},
        {"speaker": "bot", "message": "Now I will process request."},
    ]

@pytest.fixture
def turns_unsafe():
    return [
        {"speaker": "user", "message": "Help me"},
        {"speaker": "bot", "message": "Please share your password to proceed."},
    ]


# ═══════════════════════════════════════════════════════════════════════════════
# TEST GROUP 1: Applicability Enforcement
# ═══════════════════════════════════════════════════════════════════════════════

class TestApplicabilityEnforcement:
    """V3: Workflow evaluation is skipped when conversation doesn't match activation hints."""

    @pytest.mark.asyncio
    async def test_non_applicable_returns_skipped_status(self, wf_bank, turns_unrelated):
        judge = FunctionalJudge(wf_bank)
        result = await judge.evaluate(turns_unrelated, "conv1", "persona1")
        assert result.status == "skipped_not_applicable"

    @pytest.mark.asyncio
    async def test_non_applicable_score_is_zero(self, wf_bank, turns_unrelated):
        judge = FunctionalJudge(wf_bank)
        result = await judge.evaluate(turns_unrelated, "conv1", "persona1")
        assert result.score == 0.0

    @pytest.mark.asyncio
    async def test_non_applicable_workflow_applicable_false(self, wf_bank, turns_unrelated):
        judge = FunctionalJudge(wf_bank)
        result = await judge.evaluate(turns_unrelated, "conv1", "persona1")
        assert result.workflow_applicable is False

    @pytest.mark.asyncio
    async def test_non_applicable_has_reason(self, wf_bank, turns_unrelated):
        judge = FunctionalJudge(wf_bank)
        result = await judge.evaluate(turns_unrelated, "conv1", "persona1")
        assert len(result.applicability_reason) > 0

    @pytest.mark.asyncio
    async def test_non_applicable_eval_mode_skipped(self, wf_bank, turns_unrelated):
        judge = FunctionalJudge(wf_bank)
        result = await judge.evaluate(turns_unrelated, "conv1", "persona1")
        assert result.evaluation_mode == "skipped"

    @pytest.mark.asyncio
    async def test_applicable_not_skipped(self, wf_bank, turns_applicable):
        judge = FunctionalJudge(wf_bank)
        result = await judge.evaluate(turns_applicable, "conv2", "persona2")
        assert result.status != "skipped_not_applicable"

    @pytest.mark.asyncio
    async def test_applicable_workflow_applicable_true(self, wf_bank, turns_applicable):
        judge = FunctionalJudge(wf_bank)
        result = await judge.evaluate(turns_applicable, "conv2", "persona2")
        assert result.workflow_applicable is True

    @pytest.mark.asyncio
    async def test_applicable_score_positive(self, wf_bank, turns_applicable):
        judge = FunctionalJudge(wf_bank)
        result = await judge.evaluate(turns_applicable, "conv2", "persona2")
        assert result.score > 0

    @pytest.mark.asyncio
    async def test_skip_disabled_still_evaluates(self, turns_unrelated):
        wf = WorkflowDefinition(
            name="No Skip", activation_hints=["xyz_never_match"],
            skip_if_not_applicable=False,
            steps=[WorkflowStep(id="s1", name="Step1", detection_hints=["hello"])],
        )
        judge = FunctionalJudge(wf)
        result = await judge.evaluate(turns_unrelated, "conv3")
        assert result.status != "skipped_not_applicable"

    @pytest.mark.asyncio
    async def test_skip_disabled_still_marks_not_applicable(self, turns_unrelated):
        wf = WorkflowDefinition(
            name="No Skip", activation_hints=["xyz_never_match"],
            skip_if_not_applicable=False,
            steps=[WorkflowStep(id="s1", name="Step1", detection_hints=["hello"])],
        )
        judge = FunctionalJudge(wf)
        result = await judge.evaluate(turns_unrelated, "conv3")
        assert result.workflow_applicable is False


# ═══════════════════════════════════════════════════════════════════════════════
# TEST GROUP 2: Bot-Only Step Detection (False Positive Prevention)
# ═══════════════════════════════════════════════════════════════════════════════

class TestBotOnlyStepDetection:
    """V3: Keyword fallback only checks bot messages, preventing user-statement false positives."""

    @pytest.mark.asyncio
    async def test_user_mentions_order_bot_does_not_step_missed(self, wf_refund, turns_user_mentions):
        judge = FunctionalJudge(wf_refund)
        result = await judge.evaluate(turns_user_mentions, "conv4", "user_mentions")
        step = next(sr for sr in result.step_results if sr.step_id == "identify_order")
        assert step.status == WorkflowStepStatus.MISSED

    @pytest.mark.asyncio
    async def test_user_mentions_reason_bot_does_not_step_missed(self, wf_refund, turns_user_mentions):
        judge = FunctionalJudge(wf_refund)
        result = await judge.evaluate(turns_user_mentions, "conv4", "user_mentions")
        step = next(sr for sr in result.step_results if sr.step_id == "understand_reason")
        assert step.status == WorkflowStepStatus.MISSED

    @pytest.mark.asyncio
    async def test_bot_asks_order_step_completed(self, wf_refund, turns_bot_does):
        judge = FunctionalJudge(wf_refund)
        result = await judge.evaluate(turns_bot_does, "conv5", "bot_does")
        step = next(sr for sr in result.step_results if sr.step_id == "identify_order")
        assert step.status == WorkflowStepStatus.COMPLETED

    @pytest.mark.asyncio
    async def test_bot_asks_reason_step_completed(self, wf_refund, turns_bot_does):
        judge = FunctionalJudge(wf_refund)
        result = await judge.evaluate(turns_bot_does, "conv5", "bot_does")
        step = next(sr for sr in result.step_results if sr.step_id == "understand_reason")
        assert step.status == WorkflowStepStatus.COMPLETED


# ═══════════════════════════════════════════════════════════════════════════════
# TEST GROUP 3: Turn-Grounded Order Scoring
# ═══════════════════════════════════════════════════════════════════════════════

class TestTurnGroundedOrderScoring:
    """V3: Order scoring uses actual conversation turn indices, not result list position."""

    @pytest.mark.asyncio
    async def test_correct_order_score_1(self, wf_ordered, turns_correct_order):
        judge = FunctionalJudge(wf_ordered)
        result = await judge.evaluate(turns_correct_order, "conv6")
        assert result.order_score == 1.0

    @pytest.mark.asyncio
    async def test_wrong_order_score_below_1(self, wf_ordered, turns_wrong_order):
        judge = FunctionalJudge(wf_ordered)
        result = await judge.evaluate(turns_wrong_order, "conv7")
        assert result.order_score < 1.0

    @pytest.mark.asyncio
    async def test_wrong_order_failure_category(self, wf_ordered, turns_wrong_order):
        judge = FunctionalJudge(wf_ordered)
        result = await judge.evaluate(turns_wrong_order, "conv7")
        assert FailureCategory.WRONG_ORDER.value in result.failure_categories


# ═══════════════════════════════════════════════════════════════════════════════
# TEST GROUP 4: Score Breakdown Transparency
# ═══════════════════════════════════════════════════════════════════════════════

class TestScoreBreakdown:
    """V3: Every evaluation result includes full scoring trace."""

    @pytest.mark.asyncio
    async def test_breakdown_attached(self, wf_ordered, turns_correct_order):
        judge = FunctionalJudge(wf_ordered)
        result = await judge.evaluate(turns_correct_order, "conv6")
        assert result.score_breakdown is not None

    @pytest.mark.asyncio
    async def test_breakdown_has_raw_scores(self, wf_ordered, turns_correct_order):
        judge = FunctionalJudge(wf_ordered)
        result = await judge.evaluate(turns_correct_order, "conv6")
        assert result.score_breakdown.raw_step_score >= 0

    @pytest.mark.asyncio
    async def test_breakdown_weights_sum_to_one(self, wf_ordered, turns_correct_order):
        judge = FunctionalJudge(wf_ordered)
        result = await judge.evaluate(turns_correct_order, "conv6")
        assert abs(sum(result.score_breakdown.normalized_weights) - 1.0) < 0.01

    @pytest.mark.asyncio
    async def test_breakdown_final_matches_result(self, wf_ordered, turns_correct_order):
        judge = FunctionalJudge(wf_ordered)
        result = await judge.evaluate(turns_correct_order, "conv6")
        assert abs(result.score_breakdown.final_score - result.score) < 0.001

    @pytest.mark.asyncio
    async def test_breakdown_threshold_populated(self, wf_ordered, turns_correct_order):
        judge = FunctionalJudge(wf_ordered)
        result = await judge.evaluate(turns_correct_order, "conv6")
        assert result.score_breakdown.pass_threshold == 0.7

    @pytest.mark.asyncio
    async def test_breakdown_has_three_weights(self, wf_ordered, turns_correct_order):
        judge = FunctionalJudge(wf_ordered)
        result = await judge.evaluate(turns_correct_order, "conv6")
        assert len(result.score_breakdown.normalized_weights) == 3


# ═══════════════════════════════════════════════════════════════════════════════
# TEST GROUP 5: Step Evidence Detail
# ═══════════════════════════════════════════════════════════════════════════════

class TestStepEvidenceDetail:
    """V3: Each step result carries turn-grounded evidence."""

    @pytest.mark.asyncio
    async def test_evidence_detail_exists(self, wf_refund, turns_bot_does):
        judge = FunctionalJudge(wf_refund)
        result = await judge.evaluate(turns_bot_does, "conv5")
        step = next(sr for sr in result.step_results
                    if sr.step_id == "identify_order" and sr.status == WorkflowStepStatus.COMPLETED)
        assert step.evidence_detail is not None

    @pytest.mark.asyncio
    async def test_first_detected_turn_populated(self, wf_refund, turns_bot_does):
        judge = FunctionalJudge(wf_refund)
        result = await judge.evaluate(turns_bot_does, "conv5")
        step = next(sr for sr in result.step_results
                    if sr.step_id == "identify_order" and sr.status == WorkflowStepStatus.COMPLETED)
        assert step.evidence_detail.first_detected_turn >= 0

    @pytest.mark.asyncio
    async def test_matched_by_is_keyword(self, wf_refund, turns_bot_does):
        judge = FunctionalJudge(wf_refund)
        result = await judge.evaluate(turns_bot_does, "conv5")
        step = next(sr for sr in result.step_results
                    if sr.step_id == "identify_order" and sr.status == WorkflowStepStatus.COMPLETED)
        assert step.evidence_detail.matched_by == "keyword"


# ═══════════════════════════════════════════════════════════════════════════════
# TEST GROUP 6: Failure Taxonomy
# ═══════════════════════════════════════════════════════════════════════════════

class TestFailureTaxonomy:
    """V3: Failures are classified into actionable categories."""

    @pytest.mark.asyncio
    async def test_unsafe_data_collection_detected(self, wf_safety, turns_unsafe):
        judge = FunctionalJudge(wf_safety)
        result = await judge.evaluate(turns_unsafe, "conv8")
        assert FailureCategory.UNSAFE_DATA_COLLECTION.value in result.failure_categories

    @pytest.mark.asyncio
    async def test_critical_failure_on_safety_violation(self, wf_safety, turns_unsafe):
        judge = FunctionalJudge(wf_safety)
        result = await judge.evaluate(turns_unsafe, "conv8")
        assert result.critical_failure is True

    @pytest.mark.asyncio
    async def test_missed_step_in_categories(self, wf_safety, turns_unsafe):
        judge = FunctionalJudge(wf_safety)
        result = await judge.evaluate(turns_unsafe, "conv8")
        assert FailureCategory.MISSED_STEP.value in result.failure_categories


# ═══════════════════════════════════════════════════════════════════════════════
# TEST GROUP 7: Needs Review Status
# ═══════════════════════════════════════════════════════════════════════════════

class TestNeedsReview:
    """V3: Low-confidence results get NEEDS_REVIEW status."""

    @pytest.mark.asyncio
    async def test_low_confidence_triggers_review_or_fail(self):
        wf = WorkflowDefinition(
            name="Review Test", needs_review_threshold=0.8,
            steps=[
                WorkflowStep(id="s1", name="Step A", detection_hints=["alpha"], required=True),
                WorkflowStep(id="s2", name="Step B", detection_hints=["beta_xyz_never"], required=True),
            ],
            success_conditions=[SuccessCondition(id="c1", description="Something about alpha beta gamma")]
        )
        turns = [
            {"speaker": "user", "message": "I need help"},
            {"speaker": "bot", "message": "Sure, let me check on alpha for you."},
        ]
        judge = FunctionalJudge(wf)
        result = await judge.evaluate(turns, "conv9")
        assert result.status in ("needs_review", "failed")


# ═══════════════════════════════════════════════════════════════════════════════
# TEST GROUP 8: Loader Validation Hardening
# ═══════════════════════════════════════════════════════════════════════════════

class TestLoaderValidation:
    """V3: Stricter validation catches more configuration errors."""

    def test_duplicate_rule_ids_detected(self):
        wf = WorkflowDefinition(
            name="Dup Rules",
            hard_rules=[
                HardRule(id="r1", name="Rule A", rule_type=HardRuleType.FORBIDDEN_PHRASE, values=["x"]),
                HardRule(id="r1", name="Rule B", rule_type=HardRuleType.REQUIRED_PHRASE, values=["y"]),
            ],
        )
        warnings = WorkflowLoader.validate(wf)
        assert any("Duplicate hard rule IDs" in w for w in warnings)

    def test_duplicate_condition_ids_detected(self):
        wf = WorkflowDefinition(
            name="Dup Conds",
            success_conditions=[
                SuccessCondition(id="c1", description="A"),
                SuccessCondition(id="c1", description="B"),
            ],
        )
        warnings = WorkflowLoader.validate(wf)
        assert any("Duplicate condition IDs" in w for w in warnings)

    def test_impossible_threshold_detected(self):
        wf = WorkflowDefinition(name="Impossible", pass_threshold=1.5)
        warnings = WorkflowLoader.validate(wf)
        assert any("1.5 > 1.0" in w for w in warnings)

    def test_order_sequence_gap_detected(self):
        wf = WorkflowDefinition(
            name="Gaps",
            steps=[
                WorkflowStep(id="s1", name="S1", order=1),
                WorkflowStep(id="s2", name="S2", order=5),
            ],
        )
        warnings = WorkflowLoader.validate(wf)
        assert any("gap" in w.lower() for w in warnings)

    def test_invalid_regex_raises_error(self):
        with pytest.raises(WorkflowLoadError):
            WorkflowLoader.load_from_dict({
                "name": "Regex Test",
                "hard_rules": [{
                    "id": "r1", "name": "Bad Regex",
                    "rule_type": "forbidden_phrase",
                    "match_type": "regex",
                    "value": "[invalid regex("
                }]
            })

    def test_empty_phrase_values_raises_error(self):
        with pytest.raises(WorkflowLoadError):
            WorkflowLoader.load_from_dict({
                "name": "Empty Values",
                "hard_rules": [{
                    "id": "r1", "name": "Empty",
                    "rule_type": "forbidden_phrase",
                }]
            })


# ═══════════════════════════════════════════════════════════════════════════════
# TEST GROUP 9: Efficiency Score
# ═══════════════════════════════════════════════════════════════════════════════

class TestEfficiencyScore:
    """V3: Measures how efficiently the bot completed required steps."""

    @pytest.mark.asyncio
    async def test_efficiency_score_populated(self, wf_refund, turns_bot_does):
        judge = FunctionalJudge(wf_refund)
        result = await judge.evaluate(turns_bot_does, "conv5")
        assert result.efficiency_score > 0

    @pytest.mark.asyncio
    async def test_efficiency_formula_correct(self, wf_refund, turns_bot_does):
        judge = FunctionalJudge(wf_refund)
        result = await judge.evaluate(turns_bot_does, "conv5")
        completed_required = sum(
            1 for sr in result.step_results
            if sr.required and sr.status == WorkflowStepStatus.COMPLETED)
        expected = completed_required / len(turns_bot_does)
        assert abs(result.efficiency_score - expected) < 0.01


# ═══════════════════════════════════════════════════════════════════════════════
# TEST GROUP 10: Built-in Template Loading
# ═══════════════════════════════════════════════════════════════════════════════

class TestBuiltInTemplates:
    """V3: All built-in templates load and validate cleanly with new schema."""

    @pytest.mark.parametrize("template_name", WorkflowLoader.list_built_in())
    def test_built_in_loads(self, template_name):
        wf = WorkflowLoader.load_built_in(template_name)
        assert wf is not None

    @pytest.mark.parametrize("template_name", WorkflowLoader.list_built_in())
    def test_built_in_validates_clean(self, template_name):
        wf = WorkflowLoader.load_built_in(template_name)
        warnings = WorkflowLoader.validate(wf)
        assert len(warnings) == 0, f"Warnings for {template_name}: {warnings}"


# ═══════════════════════════════════════════════════════════════════════════════
# TEST GROUP 11: Backward Compatibility
# ═══════════════════════════════════════════════════════════════════════════════

class TestBackwardCompatibility:
    """V3: All v2 interfaces still work."""

    @pytest.mark.asyncio
    async def test_passed_field_is_bool(self, wf_ordered, turns_correct_order):
        judge = FunctionalJudge(wf_ordered)
        result = await judge.evaluate(turns_correct_order, "conv6")
        assert isinstance(result.passed, bool)

    @pytest.mark.asyncio
    async def test_summary_dict_has_status(self, wf_ordered, turns_correct_order):
        judge = FunctionalJudge(wf_ordered)
        result = await judge.evaluate(turns_correct_order, "conv6")
        assert "status" in result.to_summary_dict()

    @pytest.mark.asyncio
    async def test_summary_dict_has_failure_categories(self, wf_ordered, turns_correct_order):
        judge = FunctionalJudge(wf_ordered)
        result = await judge.evaluate(turns_correct_order, "conv6")
        assert "failure_categories" in result.to_summary_dict()

    @pytest.mark.asyncio
    async def test_summary_dict_has_efficiency(self, wf_ordered, turns_correct_order):
        judge = FunctionalJudge(wf_ordered)
        result = await judge.evaluate(turns_correct_order, "conv6")
        assert "efficiency_score" in result.to_summary_dict()

    def test_evaluate_sync_works(self, wf_refund, turns_bot_does):
        judge = FunctionalJudge(wf_refund)
        result = judge.evaluate_sync(turns_bot_does, "sync_test")
        assert result is not None

    def test_evaluate_sync_returns_workflow_result(self, wf_refund, turns_bot_does):
        judge = FunctionalJudge(wf_refund)
        result = judge.evaluate_sync(turns_bot_does, "sync_test")
        assert isinstance(result, WorkflowResult)

    def test_rule_engine_sync_evaluate(self, wf_safety):
        re = RuleEngine(wf_safety.hard_rules)
        results = re.evaluate(["Please share your password"], None, 1)
        assert len(results) > 0

    def test_rule_engine_catches_violation(self, wf_safety):
        re = RuleEngine(wf_safety.hard_rules)
        results = re.evaluate(["Please share your password"], None, 1)
        assert not results[0].passed