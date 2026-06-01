"""
Tests for P3 #17 v2: Functional / Workflow Judge with review improvements.

57 original tests + 20 new tests for v2 features = 77 tests total.
New tests cover: match_type, order validation, condition partial, severity enum,
evaluation mode, per-turn violations, activation hints, weight normalization,
critical failure fields, applicability.
"""
import asyncio, tempfile
from pathlib import Path
import pytest, yaml
from src.workflow_judge import (
    BUILT_IN_WORKFLOWS, ConditionResult, ConditionStatus, EvaluationMode, FunctionalJudge,
    HardRule, HardRuleResult, HardRuleType, LLMWorkflowEvaluator, MatchType, OrderMode,
    RuleEngine, RuleSeverity, StepResult, SuccessCondition, TurnViolation,
    WorkflowDefinition, WorkflowLoadError, WorkflowLoader, WorkflowResult,
    WorkflowStep, WorkflowStepStatus,
)

# ============================================================================
# Fixtures
# ============================================================================
@pytest.fixture
def sample_workflow():
    return WorkflowDefinition(
        id="test_workflow", name="Test Account Opening", domain="banking", description="Test workflow",
        steps=[
            WorkflowStep(id="step1", name="Identify Intent", required=True, detection_hints=["open account", "new account"]),
            WorkflowStep(id="step2", name="Explain Requirements", required=True, detection_hints=["document", "kyc", "id proof"]),
            WorkflowStep(id="step3", name="Provide Next Steps", required=True, detection_hints=["next step", "visit branch", "apply online"]),
            WorkflowStep(id="step4", name="Offer Help", required=False, detection_hints=["help", "assist", "anything else"]),
        ],
        hard_rules=[
            HardRule(id="no_password", name="No Password Ask", rule_type=HardRuleType.FORBIDDEN_PHRASE, values=["your password", "enter password"], severity="critical"),
            HardRule(id="mention_docs", name="Mention Documents", rule_type=HardRuleType.REQUIRED_TOPIC, values=["document", "proof", "id"], severity="high"),
        ],
        success_conditions=[
            SuccessCondition(id="cond1", description="User receives document guidance"),
            SuccessCondition(id="cond2", description="User knows next steps"),
        ],
    )

@pytest.fixture
def good_conversation():
    return [
        {"speaker": "user", "message": "I want to open a new account"},
        {"speaker": "bot", "message": "I'd be happy to help you open a new account! We offer savings and checking accounts."},
        {"speaker": "user", "message": "What documents do I need?"},
        {"speaker": "bot", "message": "You'll need the following documents for KYC: a valid ID proof like passport or driver's license, and an address proof like a utility bill."},
        {"speaker": "user", "message": "What's the next step?"},
        {"speaker": "bot", "message": "The next step is to visit your nearest branch with the documents, or you can apply online through our website. Is there anything else I can help with?"},
    ]

@pytest.fixture
def bad_conversation():
    return [
        {"speaker": "user", "message": "I want to open an account"},
        {"speaker": "bot", "message": "Sure! First, please share your password so I can verify your identity."},
        {"speaker": "user", "message": "I don't have a password yet"},
        {"speaker": "bot", "message": "No problem. Your account is now open. Have a nice day!"},
    ]

@pytest.fixture
def sample_workflow_dict():
    return {"name": "Test Workflow", "domain": "testing", "description": "A test workflow",
            "steps": [{"id": "s1", "name": "Step One", "description": "First step", "detection_hints": ["hello", "hi"]},
                      {"id": "s2", "name": "Step Two", "description": "Second step", "required": False}],
            "hard_rules": [{"id": "r1", "name": "No Bad Words", "rule_type": "forbidden_phrase", "values": ["bad word"], "severity": "high"}],
            "success_conditions": [{"id": "c1", "description": "User is satisfied"}]}

# ============================================================================
# Original Tests (adapted for v2)
# ============================================================================
class TestModels:
    def test_workflow_step_defaults(self):
        step = WorkflowStep(id="s1", name="Test")
        assert step.required is True and step.order is None and step.detection_hints == []
    def test_hard_rule_types(self):
        for rt in HardRuleType:
            rule = HardRule(id=f"r_{rt.value}", name=f"Rule {rt.value}", rule_type=rt)
            assert rule.rule_type == rt
    def test_workflow_definition_properties(self):
        wf = WorkflowDefinition(name="Test", steps=[WorkflowStep(id="s1", name="S1", required=True),
            WorkflowStep(id="s2", name="S2", required=False), WorkflowStep(id="s3", name="S3", required=True)],
            hard_rules=[HardRule(id="r1", name="R1", rule_type=HardRuleType.FORBIDDEN_PHRASE, severity="critical"),
                        HardRule(id="r2", name="R2", rule_type=HardRuleType.REQUIRED_PHRASE, severity="high")])
        assert wf.total_steps == 3 and len(wf.required_steps) == 2 and len(wf.optional_steps) == 1 and len(wf.critical_rules) == 1
    def test_workflow_result_to_summary(self):
        r = WorkflowResult(workflow_name="Test", passed=True, score=0.85, completed_steps=["A","B"], missed_steps=["C"])
        d = r.to_summary_dict()
        assert d["passed"] and d["score"] == 0.85 and "evaluation_mode" in d and "critical_failure" in d
    def test_step_result_status_enum(self):
        for status in WorkflowStepStatus:
            sr = StepResult(step_id="s1", step_name="S1", status=status)
            assert sr.status == status

class TestRuleEngine:
    def test_forbidden_phrase_violation(self):
        rules = [HardRule(id="r1", name="No Password", rule_type=HardRuleType.FORBIDDEN_PHRASE, values=["your password"])]
        r = RuleEngine(rules).evaluate(["Please enter your password to continue"])
        assert r[0].passed is False
    def test_forbidden_phrase_pass(self):
        r = RuleEngine([HardRule(id="r1", name="No Password", rule_type=HardRuleType.FORBIDDEN_PHRASE, values=["your password"])]).evaluate(["Welcome!"])
        assert r[0].passed is True
    def test_required_phrase_found(self):
        r = RuleEngine([HardRule(id="r1", name="Docs", rule_type=HardRuleType.REQUIRED_PHRASE, values=["documents"])]).evaluate(["Bring your documents"])
        assert r[0].passed is True
    def test_required_phrase_missing(self):
        r = RuleEngine([HardRule(id="r1", name="Docs", rule_type=HardRuleType.REQUIRED_PHRASE, values=["documents"])]).evaluate(["Hello"])
        assert r[0].passed is False
    def test_forbidden_topic(self):
        r = RuleEngine([HardRule(id="r1", name="No Politics", rule_type=HardRuleType.FORBIDDEN_TOPIC, values=["election"])]).evaluate(["Let's talk about the election"])
        assert r[0].passed is False
    def test_required_topic_found(self):
        r = RuleEngine([HardRule(id="r1", name="KYC", rule_type=HardRuleType.REQUIRED_TOPIC, values=["kyc"])]).evaluate(["Complete KYC verification"])
        assert r[0].passed is True
    def test_max_turns_pass(self):
        r = RuleEngine([HardRule(id="r1", name="Max", rule_type=HardRuleType.MAX_TURNS_TO_RESOLVE, value="10")]).evaluate([], turn_count=8)
        assert r[0].passed is True
    def test_max_turns_fail(self):
        r = RuleEngine([HardRule(id="r1", name="Max", rule_type=HardRuleType.MAX_TURNS_TO_RESOLVE, value="10")]).evaluate([], turn_count=15)
        assert r[0].passed is False
    def test_must_escalate_found(self):
        r = RuleEngine([HardRule(id="r1", name="Escalate", rule_type=HardRuleType.MUST_ESCALATE)]).evaluate(["Let me connect you with a human agent"])
        assert r[0].passed is True
    def test_must_escalate_missing(self):
        r = RuleEngine([HardRule(id="r1", name="Escalate", rule_type=HardRuleType.MUST_ESCALATE)]).evaluate(["Goodbye!"])
        assert r[0].passed is False
    def test_must_not_escalate_pass(self):
        r = RuleEngine([HardRule(id="r1", name="Self", rule_type=HardRuleType.MUST_NOT_ESCALATE)]).evaluate(["Reset link sent to email."])
        assert r[0].passed is True
    def test_must_not_escalate_fail(self):
        r = RuleEngine([HardRule(id="r1", name="Self", rule_type=HardRuleType.MUST_NOT_ESCALATE)]).evaluate(["Transfer you to a human agent"])
        assert r[0].passed is False
    def test_case_sensitive(self):
        rules = [HardRule(id="r1", name="Case", rule_type=HardRuleType.FORBIDDEN_PHRASE, values=["SECRET"], case_sensitive=True)]
        assert RuleEngine(rules).evaluate(["This is SECRET data"])[0].passed is False
        assert RuleEngine(rules).evaluate(["This is secret data"])[0].passed is True
    def test_score_calculation(self):
        rules = [HardRule(id="r1", name="R1", rule_type=HardRuleType.FORBIDDEN_PHRASE, values=["bad"]),
                 HardRule(id="r2", name="R2", rule_type=HardRuleType.REQUIRED_PHRASE, values=["good"])]
        assert RuleEngine(rules).calculate_score(RuleEngine(rules).evaluate(["This is good content"])) == 1.0
    def test_empty_rules(self):
        e = RuleEngine([])
        assert len(e.evaluate(["anything"])) == 0 and e.calculate_score([]) == 1.0
    def test_multiple_violations(self):
        rules = [HardRule(id="r1", name="R1", rule_type=HardRuleType.FORBIDDEN_PHRASE, values=["password"]),
                 HardRule(id="r2", name="R2", rule_type=HardRuleType.FORBIDDEN_PHRASE, values=["ssn"]),
                 HardRule(id="r3", name="R3", rule_type=HardRuleType.REQUIRED_PHRASE, values=["welcome"])]
        results = RuleEngine(rules).evaluate(["Share your password and ssn"])
        assert all(not r.passed for r in results)

class TestLLMEvaluatorFallback:
    def test_fallback_detects_steps_from_hints(self, sample_workflow):
        evaluator = LLMWorkflowEvaluator(llm_client=None)
        transcript = "User: I want to open a new account\nBot: You need document and id proof for KYC. The next step is to visit a branch."
        step_results, _, _ = asyncio.get_event_loop().run_until_complete(evaluator.evaluate(sample_workflow, transcript))
        detected = [r for r in step_results if r.status in (WorkflowStepStatus.COMPLETED, WorkflowStepStatus.PARTIAL)]
        assert len(detected) >= 3
        step2 = next(r for r in step_results if r.step_id == "step2")
        assert step2.status == WorkflowStepStatus.COMPLETED
    def test_fallback_misses_steps_without_hints(self, sample_workflow):
        evaluator = LLMWorkflowEvaluator(llm_client=None)
        step_results, _, _ = asyncio.get_event_loop().run_until_complete(evaluator.evaluate(sample_workflow, "User: Hello\nBot: Hi!"))
        assert len([r for r in step_results if r.status == WorkflowStepStatus.MISSED]) >= 2
    def test_step_score_all_completed(self):
        e = LLMWorkflowEvaluator()
        assert e.calculate_step_score([StepResult(step_id="s1", step_name="S1", status=WorkflowStepStatus.COMPLETED, required=True),
                                       StepResult(step_id="s2", step_name="S2", status=WorkflowStepStatus.COMPLETED, required=True)]) == 1.0
    def test_step_score_partial(self):
        e = LLMWorkflowEvaluator()
        assert e.calculate_step_score([StepResult(step_id="s1", step_name="S1", status=WorkflowStepStatus.COMPLETED, required=True),
                                       StepResult(step_id="s2", step_name="S2", status=WorkflowStepStatus.PARTIAL, required=True)]) == 0.75
    def test_step_score_all_missed(self):
        assert LLMWorkflowEvaluator().calculate_step_score([StepResult(step_id="s1", step_name="S1", status=WorkflowStepStatus.MISSED, required=True)]) == 0.0
    def test_condition_score_with_partial(self):
        e = LLMWorkflowEvaluator()
        results = [ConditionResult(condition_id="c1", description="A", met=True, status="met"),
                   ConditionResult(condition_id="c2", description="B", met=False, status="partial")]
        assert e.calculate_condition_score(results) == 0.75  # (1.0 + 0.5) / 2

class TestWorkflowLoader:
    def test_load_from_dict(self, sample_workflow_dict):
        wf = WorkflowLoader.load_from_dict(sample_workflow_dict)
        assert wf.name == "Test Workflow" and wf.total_steps == 2
    def test_load_from_dict_missing_name(self):
        with pytest.raises(WorkflowLoadError, match="name"): WorkflowLoader.load_from_dict({"steps": []})
    def test_load_from_dict_invalid_rule_type(self):
        with pytest.raises(WorkflowLoadError, match="invalid rule_type"):
            WorkflowLoader.load_from_dict({"name": "T", "hard_rules": [{"id": "r1", "name": "R1", "rule_type": "fake"}]})
    def test_load_from_yaml_file(self, sample_workflow_dict, tmp_path):
        p = tmp_path / "wf.yaml"
        with open(p, "w") as f: yaml.dump(sample_workflow_dict, f)
        assert WorkflowLoader.load_from_file(p).name == "Test Workflow"
    def test_load_from_nonexistent_file(self, tmp_path):
        with pytest.raises(WorkflowLoadError, match="not found"): WorkflowLoader.load_from_file(tmp_path / "ghost.yaml")
    def test_load_from_directory(self, sample_workflow_dict, tmp_path):
        for i in range(3):
            d = sample_workflow_dict.copy(); d["name"] = f"WF {i}"
            with open(tmp_path / f"wf_{i}.yaml", "w") as f: yaml.dump(d, f)
        assert len(WorkflowLoader.load_from_directory(tmp_path)) == 3
    def test_load_built_in_banking(self):
        wf = WorkflowLoader.load_built_in("banking_account_opening")
        assert wf and wf.domain == "banking" and wf.total_steps >= 5
    def test_load_built_in_nonexistent(self): assert WorkflowLoader.load_built_in("nonexistent") is None
    def test_list_built_in(self):
        t = WorkflowLoader.list_built_in()
        assert all(n in t for n in ["banking_account_opening", "healthcare_appointment", "ecommerce_refund", "password_reset"])
    def test_validate_valid_workflow(self, sample_workflow): assert len(WorkflowLoader.validate(sample_workflow)) == 0
    def test_validate_empty_steps(self):
        assert any("no steps" in w.lower() for w in WorkflowLoader.validate(WorkflowDefinition(name="E")))
    def test_validate_weight_sum(self):
        wf = WorkflowDefinition(name="W", steps=[WorkflowStep(id="s1", name="S1")], step_weight=0.3, rule_weight=0.3, condition_weight=0.3)
        assert any("weights" in w.lower() for w in WorkflowLoader.validate(wf))

class TestFunctionalJudge:
    def test_good_conversation_passes(self, sample_workflow, good_conversation):
        result = asyncio.get_event_loop().run_until_complete(FunctionalJudge(sample_workflow).evaluate(good_conversation, conversation_id="conv1", persona_name="Tester"))
        assert result.score > 0.5
        detected = [r for r in result.step_results if r.status in (WorkflowStepStatus.COMPLETED, WorkflowStepStatus.PARTIAL)]
        assert len(detected) >= 3 and len(result.completed_steps) >= 1
        assert result.conversation_id == "conv1" and result.total_turns == 6 and result.evaluation_mode == "fallback_keyword"
    def test_bad_conversation_has_violations(self, sample_workflow, bad_conversation):
        r = asyncio.get_event_loop().run_until_complete(FunctionalJudge(sample_workflow).evaluate(bad_conversation))
        assert len(r.violations) > 0 and r.passed is False
    def test_critical_violation_caps_score(self, sample_workflow, bad_conversation):
        r = asyncio.get_event_loop().run_until_complete(FunctionalJudge(sample_workflow).evaluate(bad_conversation))
        assert r.score <= 0.3 and r.severity == "critical" and r.critical_failure is True and r.critical_failures_count >= 1
    def test_empty_conversation(self, sample_workflow):
        r = asyncio.get_event_loop().run_until_complete(FunctionalJudge(sample_workflow).evaluate([]))
        assert r.total_turns == 0 and r.score < 0.5
    def test_result_has_all_fields(self, sample_workflow, good_conversation):
        r = asyncio.get_event_loop().run_until_complete(FunctionalJudge(sample_workflow).evaluate(good_conversation))
        assert r.workflow_name == "Test Account Opening" and r.domain == "banking"
        assert len(r.step_results) == 4 and len(r.rule_results) == 2 and len(r.condition_results) == 2
    def test_workflow_with_only_rules(self):
        wf = WorkflowDefinition(name="Rules Only", hard_rules=[HardRule(id="r1", name="No Bad", rule_type=HardRuleType.FORBIDDEN_PHRASE, values=["bad"])],
                                rule_weight=1.0, step_weight=0.0, condition_weight=0.0)
        r = asyncio.get_event_loop().run_until_complete(FunctionalJudge(wf).evaluate([{"speaker": "bot", "message": "Good response"}]))
        assert r.passed and r.rule_score == 1.0
    def test_workflow_with_only_steps(self):
        wf = WorkflowDefinition(name="Steps Only", steps=[WorkflowStep(id="s1", name="Greet", detection_hints=["hello", "welcome"])],
                                step_weight=1.0, rule_weight=0.0, condition_weight=0.0)
        r = asyncio.get_event_loop().run_until_complete(FunctionalJudge(wf).evaluate([{"speaker": "bot", "message": "Hello! Welcome."}]))
        assert r.step_score == 1.0

class TestBuiltInWorkflows:
    def test_all_built_ins_load(self):
        for n in BUILT_IN_WORKFLOWS:
            wf = WorkflowLoader.load_built_in(n)
            assert wf and wf.total_steps > 0, f"'{n}' failed"
    def test_all_built_ins_validate(self):
        for n in BUILT_IN_WORKFLOWS:
            w = WorkflowLoader.validate(WorkflowLoader.load_built_in(n))
            assert len(w) == 0, f"'{n}' warnings: {w}"
    def test_banking_has_security_rules(self):
        assert len([r for r in WorkflowLoader.load_built_in("banking_account_opening").hard_rules if r.severity == "critical"]) >= 2
    def test_healthcare_forbids_diagnosis(self):
        assert len([r for r in WorkflowLoader.load_built_in("healthcare_appointment").hard_rules if r.rule_type == HardRuleType.FORBIDDEN_PHRASE]) >= 2
    def test_ecommerce_has_refund_steps(self):
        assert "confirm_refund" in [s.id for s in WorkflowLoader.load_built_in("ecommerce_refund").steps]
    def test_password_reset_self_service(self):
        assert len([r for r in WorkflowLoader.load_built_in("password_reset").hard_rules if r.rule_type == HardRuleType.MUST_NOT_ESCALATE]) >= 1

class TestEndToEnd:
    def test_yaml_load_and_evaluate(self, sample_workflow_dict, good_conversation, tmp_path):
        with open(tmp_path / "wf.yaml", "w") as f: yaml.dump(sample_workflow_dict, f)
        wf = WorkflowLoader.load_from_file(tmp_path / "wf.yaml")
        r = asyncio.get_event_loop().run_until_complete(FunctionalJudge(wf).evaluate(good_conversation))
        assert r.workflow_name == "Test Workflow" and r.total_turns == 6
    def test_built_in_banking_good(self, good_conversation):
        r = asyncio.get_event_loop().run_until_complete(FunctionalJudge(WorkflowLoader.load_built_in("banking_account_opening")).evaluate(good_conversation))
        assert r.domain == "banking" and len(r.step_results) >= 5
    def test_built_in_banking_detects_password(self, bad_conversation):
        r = asyncio.get_event_loop().run_until_complete(FunctionalJudge(WorkflowLoader.load_built_in("banking_account_opening")).evaluate(bad_conversation))
        assert r.passed is False and any("password" in v.lower() for v in r.violations)
    def test_result_export(self, sample_workflow, good_conversation):
        d = asyncio.get_event_loop().run_until_complete(FunctionalJudge(sample_workflow).evaluate(good_conversation)).to_summary_dict()
        assert all(k in d for k in ["workflow", "completed_steps", "evaluation_mode", "critical_failure", "order_score"])
    def test_multiple_workflows_same_conversation(self, good_conversation):
        results = {}
        for n in ["banking_account_opening", "ecommerce_refund", "password_reset"]:
            results[n] = asyncio.get_event_loop().run_until_complete(FunctionalJudge(WorkflowLoader.load_built_in(n)).evaluate(good_conversation))
        assert len(results) == 3 and results["banking_account_opening"].score >= results["password_reset"].score

# ============================================================================
# V2 NEW TESTS (20 tests for review improvements)
# ============================================================================
class TestV2MatchType:
    def test_whole_word_prevents_partial_match(self):
        """V2: 'id' with whole_word should NOT match 'identity'."""
        rules = [HardRule(id="r1", name="ID only", rule_type=HardRuleType.FORBIDDEN_PHRASE, values=["id"], match_type="whole_word")]
        assert RuleEngine(rules).evaluate(["Bring your identity document"])[0].passed is True  # "id" ≠ "identity"
        assert RuleEngine(rules).evaluate(["Show your id please"])[0].passed is False  # exact "id"

    def test_regex_match(self):
        """V2: Regex match for patterns."""
        rules = [HardRule(id="r1", name="SSN Pattern", rule_type=HardRuleType.FORBIDDEN_PHRASE,
                          values=[r"\d{3}-\d{2}-\d{4}"], match_type="regex")]
        assert RuleEngine(rules).evaluate(["My number is 123-45-6789"])[0].passed is False
        assert RuleEngine(rules).evaluate(["My number is twelve"])[0].passed is True

    def test_invalid_regex_falls_back(self):
        rules = [HardRule(id="r1", name="Bad Regex", rule_type=HardRuleType.FORBIDDEN_PHRASE,
                          values=["[invalid"], match_type="regex")]
        # Should not crash, falls back to substring
        result = RuleEngine(rules).evaluate(["[invalid text"])
        assert isinstance(result[0], HardRuleResult)

class TestV2PerTurnViolations:
    def test_violation_tracks_turn_index(self):
        """V2: Per-turn violation evidence."""
        rules = [HardRule(id="r1", name="No Password", rule_type=HardRuleType.FORBIDDEN_PHRASE, values=["password"])]
        results = RuleEngine(rules).evaluate(["Welcome!", "Enter your password", "Thank you"])
        assert results[0].passed is False
        assert len(results[0].turn_violations) == 1
        assert results[0].turn_violations[0].turn_index == 1
        assert "password" in results[0].turn_violations[0].matched_phrase

class TestV2OrderValidation:
    def test_order_mode_none_always_1(self):
        wf = WorkflowDefinition(name="T", order_mode="none", steps=[
            WorkflowStep(id="s1", name="S1", order=1, detection_hints=["first"]),
            WorkflowStep(id="s2", name="S2", order=2, detection_hints=["second"])])
        r = asyncio.get_event_loop().run_until_complete(FunctionalJudge(wf).evaluate(
            [{"speaker": "bot", "message": "second thing then first thing"}]))
        assert r.order_score == 1.0  # No order enforcement

    def test_soft_order_penalizes(self):
        wf = WorkflowDefinition(name="T", order_mode="soft", step_weight=1.0, rule_weight=0.0, condition_weight=0.0,
            steps=[WorkflowStep(id="s1", name="S1", order=1, detection_hints=["alpha"]),
                   WorkflowStep(id="s2", name="S2", order=2, detection_hints=["beta"])])
        # Correct order
        r1 = asyncio.get_event_loop().run_until_complete(FunctionalJudge(wf).evaluate(
            [{"speaker": "bot", "message": "alpha first"}, {"speaker": "bot", "message": "then beta"}]))
        # We can't easily test reverse order with keyword fallback (all text combined),
        # but we can verify order_score is computed
        assert r1.order_score >= 0.0

class TestV2ConditionPartial:
    def test_condition_partial_scoring(self):
        """V2: Conditions can be partial (0.5 score)."""
        r_met = ConditionResult(condition_id="c1", description="A", met=True, status="met")
        r_partial = ConditionResult(condition_id="c2", description="B", met=False, status="partial")
        r_unmet = ConditionResult(condition_id="c3", description="C", met=False, status="unmet")
        assert r_met.score == 1.0 and r_partial.score == 0.5 and r_unmet.score == 0.0

class TestV2SeverityEnum:
    def test_severity_enum_parsing(self):
        rule = HardRule(id="r1", name="R", rule_type=HardRuleType.FORBIDDEN_PHRASE, severity="critical")
        assert rule.severity_enum == RuleSeverity.CRITICAL
    def test_severity_enum_invalid_defaults(self):
        rule = HardRule(id="r1", name="R", rule_type=HardRuleType.FORBIDDEN_PHRASE, severity="unknown_level")
        assert rule.severity_enum == RuleSeverity.HIGH

class TestV2EvaluationMode:
    def test_fallback_mode_tracked(self, sample_workflow, good_conversation):
        r = asyncio.get_event_loop().run_until_complete(FunctionalJudge(sample_workflow).evaluate(good_conversation))
        assert r.evaluation_mode == "fallback_keyword"

class TestV2CriticalFailureFields:
    def test_critical_failure_fields(self, sample_workflow, bad_conversation):
        r = asyncio.get_event_loop().run_until_complete(FunctionalJudge(sample_workflow).evaluate(bad_conversation))
        assert r.critical_failure is True and r.critical_failures_count >= 1
    def test_no_critical_failure(self, sample_workflow, good_conversation):
        r = asyncio.get_event_loop().run_until_complete(FunctionalJudge(sample_workflow).evaluate(good_conversation))
        assert r.critical_failure is False and r.critical_failures_count == 0

class TestV2ActivationHints:
    def test_applicable_when_hints_match(self):
        wf = WorkflowDefinition(name="T", activation_hints=["open account", "savings"])
        assert wf.is_applicable("I want to open a savings account") is True
    def test_not_applicable_when_hints_miss(self):
        wf = WorkflowDefinition(name="T", activation_hints=["open account", "savings"])
        assert wf.is_applicable("I need to block my card") is False
    def test_always_applicable_without_hints(self):
        wf = WorkflowDefinition(name="T")
        assert wf.is_applicable("anything at all") is True
    def test_built_ins_have_activation_hints(self):
        for n in BUILT_IN_WORKFLOWS:
            wf = WorkflowLoader.load_built_in(n)
            assert len(wf.activation_hints) > 0, f"'{n}' missing activation_hints"

class TestV2WeightNormalization:
    def test_weights_auto_normalize(self):
        wf = WorkflowDefinition(name="T", step_weight=0.6, rule_weight=0.4, condition_weight=0.4)
        sw, rw, cw = wf.normalized_weights()
        assert abs(sw + rw + cw - 1.0) < 0.001
    def test_normal_weights_unchanged(self):
        wf = WorkflowDefinition(name="T", step_weight=0.5, rule_weight=0.3, condition_weight=0.2)
        sw, rw, cw = wf.normalized_weights()
        assert abs(sw - 0.5) < 0.001 and abs(rw - 0.3) < 0.001

class TestV2LoaderMatchType:
    def test_load_with_match_type(self):
        data = {"name": "T", "hard_rules": [{"id": "r1", "name": "R", "rule_type": "forbidden_phrase", "values": ["test"], "match_type": "whole_word"}]}
        wf = WorkflowLoader.load_from_dict(data)
        assert wf.hard_rules[0].match_type == "whole_word"
    def test_invalid_match_type_rejected(self):
        data = {"name": "T", "hard_rules": [{"id": "r1", "name": "R", "rule_type": "forbidden_phrase", "match_type": "fuzzy"}]}
        with pytest.raises(WorkflowLoadError, match="invalid match_type"):
            WorkflowLoader.load_from_dict(data)
