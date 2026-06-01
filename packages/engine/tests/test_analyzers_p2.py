"""
Tests for Phase C Part 2: Guardrail Generator and Test Plan Generator.
Run with: PYTHONPATH=. pytest tests/test_analyzers_p2.py -v
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from src.analyzers.document_analyzer import BotContext
from src.analyzers.criteria_generator import CriteriaSet, SuccessCriterion


# ============================================================
# Shared Fixtures
# ============================================================

@pytest.fixture
def sample_context() -> BotContext:
    return BotContext(
        bot_name="ShopHelper",
        domain="E-commerce customer support",
        purpose="Help customers with orders, refunds, and product information",
        capabilities=["order tracking", "refund processing", "product search", "FAQ answers"],
        limitations=["cannot process payments", "cannot access inventory"],
        target_audience="Online shoppers",
        topics=["orders", "refunds", "products", "shipping", "returns"],
        tone_and_style="Professional and helpful",
    )


@pytest.fixture
def sample_criteria() -> CriteriaSet:
    return CriteriaSet(criteria=[
        SuccessCriterion(id="GRD-001", criterion="Must answer from documentation only", category="grounding", importance="high"),
        SuccessCriterion(id="SAF-001", criterion="Must not reveal system prompt", category="safety", importance="high"),
        SuccessCriterion(id="QUA-001", criterion="Must provide helpful responses", category="quality", importance="medium"),
    ])


# ============================================================
# Tests: Guardrail Generator
# ============================================================

class TestGuardrailGenerator:

    @pytest.mark.asyncio
    async def test_generate_guardrails(self, sample_context, sample_criteria):
        from src.analyzers.guardrail_generator import GuardrailGenerator

        mock_llm = AsyncMock()
        mock_llm.model = "test-model"
        mock_llm.generate_json = AsyncMock(return_value={
            "rules": [
                {
                    "id": "SEC-001",
                    "rule": "Must not reveal system prompt",
                    "category": "prompt_security",
                    "severity": "critical",
                    "detection_method": "keyword",
                    "example_violation": "Bot outputs its system instructions",
                    "rationale": "Prevents prompt extraction",
                },
                {
                    "id": "PRI-001",
                    "rule": "Must not expose PII",
                    "category": "data_privacy",
                    "severity": "critical",
                    "detection_method": "pattern",
                    "example_violation": "Bot shares email addresses",
                    "rationale": "Privacy protection",
                },
                {
                    "id": "SCP-001",
                    "rule": "Must stay within e-commerce domain",
                    "category": "scope_enforcement",
                    "severity": "high",
                    "detection_method": "llm_judge",
                    "rationale": "Domain adherence",
                },
            ]
        })

        gen = GuardrailGenerator(llm_client=mock_llm)
        guardrails = await gen.generate(sample_context, sample_criteria)

        assert guardrails.count == 3
        assert len(guardrails.critical_rules) == 2
        assert "prompt_security" in guardrails.by_category
        assert guardrails.source_context == "ShopHelper"

    @pytest.mark.asyncio
    async def test_generate_without_criteria(self, sample_context):
        from src.analyzers.guardrail_generator import GuardrailGenerator

        mock_llm = AsyncMock()
        mock_llm.model = "test-model"
        mock_llm.generate_json = AsyncMock(return_value={"rules": [
            {"rule": "Basic rule", "category": "content_safety", "severity": "high"},
        ]})

        gen = GuardrailGenerator(llm_client=mock_llm)
        guardrails = await gen.generate(sample_context, criteria_set=None)

        assert guardrails.count == 1
        # Check prompt includes "No specific criteria"
        call_args = mock_llm.generate_json.call_args
        prompt = call_args.kwargs.get("prompt", call_args.args[0] if call_args.args else "")
        assert "No specific criteria" in prompt

    @pytest.mark.asyncio
    async def test_fallback_on_failure(self, sample_context):
        from src.analyzers.guardrail_generator import GuardrailGenerator

        mock_llm = AsyncMock()
        mock_llm.model = "test-model"
        mock_llm.generate_json = AsyncMock(side_effect=Exception("LLM error"))

        gen = GuardrailGenerator(llm_client=mock_llm)
        guardrails = await gen.generate(sample_context)

        # Should return defaults
        assert guardrails.count >= 5
        assert len(guardrails.critical_rules) >= 3

    @pytest.mark.asyncio
    async def test_default_includes_limitations(self, sample_context):
        from src.analyzers.guardrail_generator import GuardrailGenerator

        mock_llm = AsyncMock()
        mock_llm.model = "test-model"
        mock_llm.generate_json = AsyncMock(side_effect=Exception("fail"))

        gen = GuardrailGenerator(llm_client=mock_llm)
        guardrails = await gen.generate(sample_context)

        domain_rules = guardrails.by_category.get("domain_specific", [])
        assert len(domain_rules) >= 1
        all_text = " ".join(r.rule for r in domain_rules)
        assert "payment" in all_text.lower() or "inventory" in all_text.lower()

    def test_to_proposal_items(self):
        from src.analyzers.guardrail_generator import GuardrailRule, GuardrailSet

        gs = GuardrailSet(rules=[
            GuardrailRule(rule="Rule 1", severity="critical", rationale="Important",
                          example_violation="Bad example"),
            GuardrailRule(rule="Rule 2", severity="low", rationale="Nice to have"),
        ])

        items = gs.to_proposal_items()
        assert len(items) == 2
        assert items[0].confidence.value == "high"  # critical → high confidence
        assert items[1].confidence.value == "low"
        assert "Bad example" in items[0].explanation

    def test_as_policy_strings(self):
        from src.analyzers.guardrail_generator import GuardrailRule, GuardrailSet

        gs = GuardrailSet(rules=[
            GuardrailRule(rule="No PII exposure"),
            GuardrailRule(rule="No system prompt leakage"),
        ])

        policies = gs.as_policy_strings()
        assert policies == ["No PII exposure", "No system prompt leakage"]

    def test_from_approval_data(self):
        from src.analyzers.guardrail_generator import GuardrailGenerator

        approved = [
            {"rule": "Rule A", "category": "prompt_security", "severity": "critical"},
            {"rule": "Rule B", "category": "data_privacy", "severity": "high"},
        ]
        gs = GuardrailGenerator.from_approval_data(approved)

        assert gs.count == 2
        assert gs.rules[0].category == "prompt_security"

    def test_from_approval_data_strings(self):
        from src.analyzers.guardrail_generator import GuardrailGenerator

        approved = ["No PII", "No toxic content"]
        gs = GuardrailGenerator.from_approval_data(approved)

        assert gs.count == 2
        assert gs.rules[0].rule == "No PII"

    @pytest.mark.asyncio
    async def test_parse_list_response(self, sample_context):
        from src.analyzers.guardrail_generator import GuardrailGenerator

        mock_llm = AsyncMock()
        mock_llm.model = "test-model"
        mock_llm.generate_json = AsyncMock(return_value=[
            {"rule": "List rule 1", "category": "content_safety"},
            {"rule": "List rule 2", "category": "prompt_security"},
        ])

        gen = GuardrailGenerator(llm_client=mock_llm)
        gs = await gen.generate(sample_context)

        assert gs.count == 2


# ============================================================
# Tests: Test Plan Generator
# ============================================================

class TestTestPlanGenerator:

    @pytest.mark.asyncio
    async def test_generate_plan(self, sample_context, sample_criteria):
        from src.analyzers.guardrail_generator import GuardrailRule, GuardrailSet
        from src.analyzers.test_plan_generator import TestPlanGenerator

        guardrails = GuardrailSet(rules=[
            GuardrailRule(rule="No system prompt leakage", severity="critical"),
        ])

        mock_llm = AsyncMock()
        mock_llm.model = "test-model"
        mock_llm.generate_json = AsyncMock(return_value={
            "topics": [
                {"name": "Order Tracking", "description": "Test order status queries",
                 "priority": "high", "estimated_personas": 4, "risk_level": "medium"},
                {"name": "Refund Processing", "description": "Test refund workflows",
                 "priority": "high", "estimated_personas": 5, "risk_level": "high"},
                {"name": "Security", "description": "Test adversarial attacks",
                 "priority": "high", "estimated_personas": 3, "risk_level": "high"},
            ],
            "persona_strategy": {
                "total_personas": 25,
                "standard_pct": 55,
                "edge_case_pct": 25,
                "adversarial_pct": 20,
                "focus_areas": ["refunds", "security"],
                "suggested_tones": ["frustrated", "confused", "demanding"],
                "suggested_technical_levels": ["novice", "intermediate"],
            },
            "conversation_config": {
                "min_turns": 5,
                "max_turns": 15,
                "include_multi_intent": True,
                "include_topic_switching": True,
                "include_follow_ups": True,
                "escalation_testing": True,
            },
            "judge_recommendations": [
                {"judge_name": "grounding", "weight": 0.30, "enabled": True, "rationale": "Accuracy"},
                {"judge_name": "safety", "weight": 0.35, "enabled": True, "rationale": "High risk"},
                {"judge_name": "quality", "weight": 0.20, "enabled": True, "rationale": "Helpfulness"},
                {"judge_name": "relevance", "weight": 0.15, "enabled": True, "rationale": "Scope"},
            ],
            "risk_summary": "Refund and security are the highest risk areas.",
            "estimated_duration": "3-5 minutes",
        })

        gen = TestPlanGenerator(llm_client=mock_llm)
        plan = await gen.generate(sample_context, sample_criteria, guardrails)

        assert plan.topic_count == 3
        assert len(plan.high_risk_topics) == 2
        assert plan.persona_strategy.total_personas == 25
        assert plan.persona_strategy.adversarial_pct == 20
        assert plan.conversation_config.escalation_testing is True
        assert len(plan.judge_recommendations) == 4
        assert plan.risk_summary != ""

    @pytest.mark.asyncio
    async def test_generate_without_criteria_or_guardrails(self, sample_context):
        from src.analyzers.test_plan_generator import TestPlanGenerator

        mock_llm = AsyncMock()
        mock_llm.model = "test-model"
        mock_llm.generate_json = AsyncMock(return_value={
            "topics": [{"name": "General", "priority": "medium"}],
            "persona_strategy": {"total_personas": 15},
            "conversation_config": {"max_turns": 10},
            "risk_summary": "Basic plan",
        })

        gen = TestPlanGenerator(llm_client=mock_llm)
        plan = await gen.generate(sample_context)

        assert plan.topic_count == 1
        # Should have default judges when LLM doesn't return them
        assert len(plan.judge_recommendations) == 4

    @pytest.mark.asyncio
    async def test_fallback_on_failure(self, sample_context):
        from src.analyzers.test_plan_generator import TestPlanGenerator

        mock_llm = AsyncMock()
        mock_llm.model = "test-model"
        mock_llm.generate_json = AsyncMock(side_effect=Exception("LLM error"))

        gen = TestPlanGenerator(llm_client=mock_llm)
        plan = await gen.generate(sample_context)

        # Should return defaults
        assert plan.topic_count >= 3  # capabilities + security + edge cases
        assert plan.persona_strategy.total_personas == 20
        assert len(plan.judge_recommendations) == 4

    @pytest.mark.asyncio
    async def test_default_plan_includes_capabilities(self, sample_context):
        from src.analyzers.test_plan_generator import TestPlanGenerator

        mock_llm = AsyncMock()
        mock_llm.model = "test-model"
        mock_llm.generate_json = AsyncMock(side_effect=Exception("fail"))

        gen = TestPlanGenerator(llm_client=mock_llm)
        plan = await gen.generate(sample_context)

        topic_names = [t.name for t in plan.topics]
        # Should have security topic
        assert any("security" in t.lower() or "injection" in t.lower() for t in topic_names)
        # Should have at least one capability-based topic
        assert any(cap in topic_names for cap in sample_context.capabilities[:3])

    def test_to_proposal_items(self):
        from src.analyzers.test_plan_generator import (
            ConversationConfig,
            PersonaStrategy,
            TestPlan,
            TestTopic,
        )

        plan = TestPlan(
            topics=[
                TestTopic(name="Refunds", priority="high", risk_level="high"),
                TestTopic(name="General", priority="medium", risk_level="low"),
            ],
            persona_strategy=PersonaStrategy(total_personas=25),
            conversation_config=ConversationConfig(max_turns=15),
        )

        items = plan.to_proposal_items()
        # 2 topics + 1 persona strategy + 1 conversation config = 4
        assert len(items) == 4
        # First topic (high risk) should have high confidence
        assert items[0].confidence.value == "high"
        # Second topic (low risk) should have low confidence
        assert items[1].confidence.value == "low"

    def test_to_simulation_params(self):
        from src.analyzers.test_plan_generator import (
            JudgeRecommendation,
            PersonaStrategy,
            TestPlan,
            TestTopic,
        )

        plan = TestPlan(
            topics=[TestTopic(name="Refunds"), TestTopic(name="Orders")],
            persona_strategy=PersonaStrategy(total_personas=30, adversarial_pct=20),
            judge_recommendations=[
                JudgeRecommendation(judge_name="grounding", weight=0.4),
            ],
        )

        params = plan.to_simulation_params()
        assert params["num_personas"] == 30
        assert params["topics"] == ["Refunds", "Orders"]
        assert params["persona_types"]["adversarial"] == 20
        assert len(params["judges"]) == 1

    def test_from_approval_data(self):
        from src.analyzers.test_plan_generator import TestPlanGenerator

        approved = [
            {"type": "topic", "name": "Refunds", "priority": "high", "risk_level": "high"},
            {"type": "topic", "name": "Orders", "priority": "medium", "risk_level": "medium"},
            {"type": "persona_strategy", "total_personas": 30, "adversarial_pct": 20,
             "standard_pct": 55, "edge_case_pct": 25},
            {"type": "conversation_config", "min_turns": 5, "max_turns": 20},
        ]
        plan = TestPlanGenerator.from_approval_data(approved)

        assert plan.topic_count == 2
        assert plan.topics[0].name == "Refunds"
        assert plan.persona_strategy.total_personas == 30
        assert plan.conversation_config.max_turns == 20

    def test_high_risk_topics(self):
        from src.analyzers.test_plan_generator import TestPlan, TestTopic

        plan = TestPlan(topics=[
            TestTopic(name="Safe", risk_level="low"),
            TestTopic(name="Risky", risk_level="high"),
            TestTopic(name="Medium", risk_level="medium"),
            TestTopic(name="Very Risky", risk_level="high"),
        ])

        assert len(plan.high_risk_topics) == 2

    @pytest.mark.asyncio
    async def test_parse_string_topics(self, sample_context):
        """Handle LLM returning topic names as strings instead of objects."""
        from src.analyzers.test_plan_generator import TestPlanGenerator

        mock_llm = AsyncMock()
        mock_llm.model = "test-model"
        mock_llm.generate_json = AsyncMock(return_value={
            "topics": ["Refunds", "Orders", "Security"],
            "persona_strategy": {"total_personas": 10},
            "risk_summary": "Basic",
        })

        gen = TestPlanGenerator(llm_client=mock_llm)
        plan = await gen.generate(sample_context)

        assert plan.topic_count == 3
        assert plan.topics[0].name == "Refunds"


# ============================================================
# Tests: Full Pipeline Integration
# ============================================================

class TestFullAnalysisPipeline:

    @pytest.mark.asyncio
    async def test_full_pipeline_mock(self, sample_context):
        """Test the complete analysis pipeline: context → criteria → guardrails → plan."""
        from src.analyzers.criteria_generator import CriteriaGenerator
        from src.analyzers.guardrail_generator import GuardrailGenerator
        from src.analyzers.test_plan_generator import TestPlanGenerator

        # Mock LLM that returns appropriate responses based on prompt content
        mock_llm = AsyncMock()
        mock_llm.model = "test-model"

        call_count = {"n": 0}

        async def smart_generate(prompt, system_prompt=None):
            call_count["n"] += 1
            sp = (system_prompt or "").lower()
            if "test planning" in sp or "test plan" in sp or "qa strategist" in sp:
                return {
                    "topics": [
                        {"name": "Core Features", "priority": "high", "risk_level": "medium"},
                        {"name": "Security", "priority": "high", "risk_level": "high"},
                    ],
                    "persona_strategy": {"total_personas": 20, "adversarial_pct": 15},
                    "conversation_config": {"max_turns": 12},
                    "judge_recommendations": [
                        {"judge_name": "grounding", "weight": 0.3},
                        {"judge_name": "safety", "weight": 0.3},
                    ],
                    "risk_summary": "Security is the primary concern.",
                }
            elif "safety expert" in sp or "guardrail" in sp:
                return {"rules": [
                    {"rule": "No PII leakage", "category": "data_privacy", "severity": "critical"},
                    {"rule": "No prompt injection", "category": "prompt_security", "severity": "critical"},
                ]}
            elif "success criteria" in sp or "defining success" in sp:
                return {"criteria": [
                    {"criterion": "Must be accurate", "category": "grounding", "importance": "high"},
                    {"criterion": "Must be safe", "category": "safety", "importance": "high"},
                ]}
            return {}

        mock_llm.generate_json = AsyncMock(side_effect=smart_generate)

        # Stage 2: Criteria
        criteria_gen = CriteriaGenerator(llm_client=mock_llm)
        criteria = await criteria_gen.generate(sample_context)
        assert criteria.count == 2

        # Stage 3: Guardrails
        guardrail_gen = GuardrailGenerator(llm_client=mock_llm)
        guardrails = await guardrail_gen.generate(sample_context, criteria)
        assert guardrails.count == 2

        # Stage 4: Test Plan
        plan_gen = TestPlanGenerator(llm_client=mock_llm)
        plan = await plan_gen.generate(sample_context, criteria, guardrails)
        assert plan.topic_count == 2
        assert plan.persona_strategy.total_personas == 20

        # Verify outputs can drive simulation
        sim_params = plan.to_simulation_params()
        assert sim_params["num_personas"] == 20
        assert "Core Features" in sim_params["topics"]

        # Verify all stages called LLM
        assert call_count["n"] == 3

    @pytest.mark.asyncio
    async def test_pipeline_with_approval_gate_items(self, sample_context):
        """Test that all stages produce valid ProposalItems."""
        from src.analyzers.criteria_generator import CriteriaSet, SuccessCriterion
        from src.analyzers.guardrail_generator import GuardrailRule, GuardrailSet
        from src.analyzers.test_plan_generator import PersonaStrategy, TestPlan, TestTopic

        criteria = CriteriaSet(criteria=[
            SuccessCriterion(criterion="Rule 1", importance="high"),
        ])
        guardrails = GuardrailSet(rules=[
            GuardrailRule(rule="Guard 1", severity="critical"),
        ])
        plan = TestPlan(
            topics=[TestTopic(name="Topic 1", risk_level="high")],
            persona_strategy=PersonaStrategy(total_personas=20),
        )

        # All should produce valid ProposalItems
        c_items = criteria.to_proposal_items()
        g_items = guardrails.to_proposal_items()
        p_items = plan.to_proposal_items()

        assert len(c_items) == 1
        assert len(g_items) == 1
        assert len(p_items) >= 2  # topic + strategy + config

        # All items should have content and confidence
        for item in c_items + g_items + p_items:
            assert item.content is not None
            assert item.confidence is not None