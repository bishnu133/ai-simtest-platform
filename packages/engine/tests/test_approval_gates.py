"""
Tests for Phase B: Approval Gate Framework.
Run with: PYTHONPATH=. pytest tests/test_approval_gates.py -v
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from src.core.approval_gate import (
    APIApprovalGate,
    ApprovalGate,
    AuditEntry,
    AuditTrail,
    CLIApprovalGate,
    ConfidenceLevel,
    GateDecision,
    GateManager,
    GateProposal,
    GateResult,
    ProgrammaticApprovalGate,
    ProposalItem,
)


# ============================================================
# Fixtures
# ============================================================

@pytest.fixture
def sample_items() -> list[ProposalItem]:
    return [
        ProposalItem(
            content="Must answer from documentation only",
            explanation="Prevents hallucination",
            confidence=ConfidenceLevel.HIGH,
        ),
        ProposalItem(
            content="Must not reveal system prompt",
            explanation="Security requirement",
            confidence=ConfidenceLevel.HIGH,
        ),
        ProposalItem(
            content="Should handle refund requests within policy",
            explanation="Core business use case",
            confidence=ConfidenceLevel.MEDIUM,
        ),
        ProposalItem(
            content="May suggest alternative products",
            explanation="Nice-to-have based on doc analysis",
            confidence=ConfidenceLevel.LOW,
        ),
    ]


@pytest.fixture
def sample_proposal(sample_items) -> GateProposal:
    return GateProposal(
        gate_name="success_criteria",
        stage_number=2,
        title="Success Criteria Definition",
        description="AI-derived success criteria based on documentation analysis",
        items=sample_items,
    )


@pytest.fixture
def raw_data_proposal() -> GateProposal:
    return GateProposal(
        gate_name="bot_context",
        stage_number=1,
        title="Bot Description & Context",
        description="Extracted from documentation",
        raw_data={
            "bot_name": "Customer Support Bot",
            "domain": "E-commerce",
            "capabilities": ["refunds", "order tracking", "product info"],
            "limitations": ["Cannot process payments", "No access to inventory"],
        },
    )


# ============================================================
# Tests: Models
# ============================================================

class TestModels:

    def test_proposal_item_defaults(self):
        item = ProposalItem(content="Test item")
        assert item.id is not None
        assert item.confidence == ConfidenceLevel.MEDIUM
        assert item.approved is None

    def test_proposal_item_with_confidence(self):
        item = ProposalItem(
            content="Critical rule",
            confidence=ConfidenceLevel.HIGH,
            explanation="Very important",
        )
        assert item.confidence == ConfidenceLevel.HIGH
        assert item.explanation == "Very important"

    def test_gate_proposal_item_count(self, sample_proposal):
        assert sample_proposal.item_count == 4
        assert sample_proposal.gate_name == "success_criteria"
        assert sample_proposal.stage_number == 2

    def test_gate_proposal_empty(self):
        proposal = GateProposal(gate_name="empty", title="Empty Gate")
        assert proposal.item_count == 0

    def test_gate_result(self, sample_proposal):
        result = GateResult(
            gate_name="success_criteria",
            decision=GateDecision.APPROVED,
            original_proposal=sample_proposal,
            modified_data=["item1", "item2"],
        )
        assert result.decision == GateDecision.APPROVED
        assert result.gate_name == "success_criteria"

    def test_gate_decision_enum(self):
        assert GateDecision.APPROVED.value == "approved"
        assert GateDecision.MODIFIED.value == "modified"
        assert GateDecision.REJECTED.value == "rejected"
        assert GateDecision.AUTO_APPROVED.value == "auto_approved"
        assert GateDecision.TIMED_OUT.value == "timed_out"

    def test_confidence_levels(self):
        assert ConfidenceLevel.HIGH.value == "high"
        assert ConfidenceLevel.MEDIUM.value == "medium"
        assert ConfidenceLevel.LOW.value == "low"


# ============================================================
# Tests: Programmatic Gate (for testing & automation)
# ============================================================

class TestProgrammaticGate:

    @pytest.mark.asyncio
    async def test_default_approve(self, sample_proposal):
        gate = ProgrammaticApprovalGate(default_decision=GateDecision.APPROVED)
        result = await gate.submit(sample_proposal)

        assert result.decision == GateDecision.APPROVED
        assert result.modified_data is not None
        assert len(result.modified_data) == 4

    @pytest.mark.asyncio
    async def test_default_reject(self, sample_proposal):
        gate = ProgrammaticApprovalGate(default_decision=GateDecision.REJECTED)
        result = await gate.submit(sample_proposal)

        assert result.decision == GateDecision.REJECTED

    @pytest.mark.asyncio
    async def test_custom_decision_function(self, sample_proposal):
        def custom_fn(proposal: GateProposal) -> GateResult:
            # Approve but remove low-confidence items
            approved_items = [
                item.content for item in proposal.items
                if item.confidence != ConfidenceLevel.LOW
            ]
            return GateResult(
                gate_name=proposal.gate_name,
                decision=GateDecision.MODIFIED,
                original_proposal=proposal,
                modified_data=approved_items,
                modifications=["Removed low-confidence items"],
            )

        gate = ProgrammaticApprovalGate(decision_fn=custom_fn)
        result = await gate.submit(sample_proposal)

        assert result.decision == GateDecision.MODIFIED
        assert len(result.modified_data) == 3  # 4 items minus 1 low-confidence
        assert "Removed low-confidence items" in result.modifications

    @pytest.mark.asyncio
    async def test_auto_approve_overrides(self, sample_proposal):
        # Even with reject default, auto_approve should win
        gate = ProgrammaticApprovalGate(
            default_decision=GateDecision.REJECTED,
            auto_approve=True,
        )
        result = await gate.submit(sample_proposal)

        assert result.decision == GateDecision.AUTO_APPROVED

    @pytest.mark.asyncio
    async def test_raw_data_proposal(self, raw_data_proposal):
        gate = ProgrammaticApprovalGate(default_decision=GateDecision.APPROVED)
        result = await gate.submit(raw_data_proposal)

        assert result.decision == GateDecision.APPROVED
        assert isinstance(result.modified_data, dict)
        assert "bot_name" in result.modified_data

    @pytest.mark.asyncio
    async def test_duration_tracked(self, sample_proposal):
        gate = ProgrammaticApprovalGate()
        result = await gate.submit(sample_proposal)

        assert result.duration_seconds >= 0


# ============================================================
# Tests: Auto-Approve Mode
# ============================================================

class TestAutoApprove:

    @pytest.mark.asyncio
    async def test_auto_approve_cli_gate(self, sample_proposal):
        gate = CLIApprovalGate(auto_approve=True)
        result = await gate.submit(sample_proposal)

        assert result.decision == GateDecision.AUTO_APPROVED
        assert len(result.modified_data) == 4

    @pytest.mark.asyncio
    async def test_auto_approve_api_gate(self, sample_proposal):
        gate = APIApprovalGate(auto_approve=True)
        result = await gate.submit(sample_proposal)

        assert result.decision == GateDecision.AUTO_APPROVED

    @pytest.mark.asyncio
    async def test_auto_approve_with_raw_data(self, raw_data_proposal):
        gate = CLIApprovalGate(auto_approve=True)
        result = await gate.submit(raw_data_proposal)

        assert result.decision == GateDecision.AUTO_APPROVED
        assert result.modified_data == raw_data_proposal.raw_data


# ============================================================
# Tests: API Gate
# ============================================================

class TestAPIGate:

    def test_get_pending_gates_empty(self):
        APIApprovalGate._pending_gates.clear()
        APIApprovalGate._gate_results.clear()
        assert APIApprovalGate.get_pending_gates() == {}

    def test_submit_decision_nonexistent(self):
        APIApprovalGate._pending_gates.clear()
        result = APIApprovalGate.submit_decision("fake_id", "approved")
        assert result is False

    @pytest.mark.asyncio
    async def test_api_gate_with_external_decision(self, sample_proposal):
        """Simulate the API approval flow: gate waits, external call approves."""
        import asyncio

        APIApprovalGate._pending_gates.clear()
        APIApprovalGate._gate_results.clear()

        gate = APIApprovalGate(auto_approve=False, timeout_seconds=10, poll_interval=0.1)

        # Submit decision after a short delay
        async def approve_after_delay():
            await asyncio.sleep(0.3)
            # Find the pending gate
            pending = APIApprovalGate.get_pending_gates()
            for gate_id in pending:
                APIApprovalGate.submit_decision(
                    gate_id,
                    "approved",
                    modified_data=["item1", "item2"],
                )

        # Run both concurrently
        result, _ = await asyncio.gather(
            gate.submit(sample_proposal),
            approve_after_delay(),
        )

        assert result.decision == GateDecision.APPROVED
        assert result.modified_data == ["item1", "item2"]

    @pytest.mark.asyncio
    async def test_api_gate_timeout_approve(self, sample_proposal):
        """Gate times out and auto-approves."""
        APIApprovalGate._pending_gates.clear()
        APIApprovalGate._gate_results.clear()

        gate = APIApprovalGate(
            timeout_seconds=0.3,
            timeout_action="approve",
            poll_interval=0.1,
        )
        result = await gate.submit(sample_proposal)

        assert result.decision == GateDecision.TIMED_OUT
        assert result.modified_data is not None  # Data still returned

    @pytest.mark.asyncio
    async def test_api_gate_timeout_pause(self, sample_proposal):
        """Gate times out and pauses (no data returned)."""
        APIApprovalGate._pending_gates.clear()
        APIApprovalGate._gate_results.clear()

        gate = APIApprovalGate(
            timeout_seconds=0.3,
            timeout_action="pause",
            poll_interval=0.1,
        )
        result = await gate.submit(sample_proposal)

        assert result.decision == GateDecision.TIMED_OUT


# ============================================================
# Tests: Audit Trail
# ============================================================

class TestAuditTrail:

    def test_add_entry(self, sample_proposal):
        trail = AuditTrail(simulation_id="sim_001")

        result = GateResult(
            gate_name="success_criteria",
            decision=GateDecision.APPROVED,
            original_proposal=sample_proposal,
        )
        trail.add_entry(result)

        assert len(trail.entries) == 1
        assert trail.entries[0].gate_name == "success_criteria"
        assert trail.entries[0].decision == GateDecision.APPROVED

    def test_save_and_load(self, sample_proposal):
        trail = AuditTrail(simulation_id="sim_001")
        result = GateResult(
            gate_name="test_gate",
            decision=GateDecision.MODIFIED,
            original_proposal=sample_proposal,
            modifications=["Changed item 1"],
        )
        trail.add_entry(result)

        with tempfile.TemporaryDirectory() as tmpdir:
            path = trail.save(Path(tmpdir) / "audit.json")
            assert path.exists()

            loaded = AuditTrail.load(path)
            assert loaded.simulation_id == "sim_001"
            assert len(loaded.entries) == 1
            assert loaded.entries[0].decision == GateDecision.MODIFIED

    def test_multiple_entries(self, sample_proposal, raw_data_proposal):
        trail = AuditTrail()

        trail.add_entry(GateResult(
            gate_name="context", decision=GateDecision.APPROVED,
            original_proposal=raw_data_proposal,
        ))
        trail.add_entry(GateResult(
            gate_name="criteria", decision=GateDecision.MODIFIED,
            original_proposal=sample_proposal, modifications=["Added rule"],
        ))

        assert len(trail.entries) == 2
        assert trail.entries[0].gate_name == "context"
        assert trail.entries[1].gate_name == "criteria"


# ============================================================
# Tests: Gate Manager
# ============================================================

class TestGateManager:

    @pytest.mark.asyncio
    async def test_submit_gate_approve(self, sample_items):
        gate = ProgrammaticApprovalGate(default_decision=GateDecision.APPROVED)
        manager = GateManager(gate=gate, simulation_id="sim_001")

        result = await manager.submit_gate(
            gate_name="criteria",
            title="Success Criteria",
            items=sample_items,
            stage_number=2,
        )

        assert result.decision == GateDecision.APPROVED
        assert manager.was_approved("criteria")
        assert not manager.was_rejected("criteria")

    @pytest.mark.asyncio
    async def test_submit_gate_reject(self, sample_items):
        gate = ProgrammaticApprovalGate(default_decision=GateDecision.REJECTED)
        manager = GateManager(gate=gate)

        result = await manager.submit_gate(
            gate_name="guardrails",
            title="Guardrail Rules",
            items=sample_items,
        )

        assert result.decision == GateDecision.REJECTED
        assert manager.was_rejected("guardrails")
        assert not manager.was_approved("guardrails")

    @pytest.mark.asyncio
    async def test_multi_stage_pipeline(self, sample_items):
        gate = ProgrammaticApprovalGate(default_decision=GateDecision.APPROVED)
        manager = GateManager(gate=gate, simulation_id="sim_pipeline")

        # Stage 1: Context
        await manager.submit_gate(
            gate_name="context",
            title="Bot Context",
            raw_data={"bot_name": "Test Bot"},
            stage_number=1,
        )

        # Stage 2: Criteria
        await manager.submit_gate(
            gate_name="criteria",
            title="Success Criteria",
            items=sample_items,
            stage_number=2,
        )

        # Stage 3: Guardrails
        await manager.submit_gate(
            gate_name="guardrails",
            title="Guardrail Rules",
            items=sample_items[:2],
            stage_number=3,
        )

        assert manager.total_gates == 3
        assert manager.all_approved
        assert len(manager.audit_trail.entries) == 3

    @pytest.mark.asyncio
    async def test_get_approved_data(self, sample_items):
        gate = ProgrammaticApprovalGate(default_decision=GateDecision.APPROVED)
        manager = GateManager(gate=gate)

        await manager.submit_gate(
            gate_name="criteria",
            title="Criteria",
            items=sample_items,
        )

        data = manager.get_approved_data("criteria")
        assert data is not None
        assert len(data) == 4

    @pytest.mark.asyncio
    async def test_get_approved_data_nonexistent(self):
        gate = ProgrammaticApprovalGate()
        manager = GateManager(gate=gate)

        assert manager.get_approved_data("fake") is None

    @pytest.mark.asyncio
    async def test_summary(self, sample_items):
        # Use a function that alternates decisions
        call_count = {"n": 0}

        def alternating_fn(proposal):
            call_count["n"] += 1
            if call_count["n"] == 1:
                decision = GateDecision.APPROVED
            elif call_count["n"] == 2:
                decision = GateDecision.MODIFIED
            else:
                decision = GateDecision.REJECTED
            return GateResult(
                gate_name=proposal.gate_name,
                decision=decision,
                original_proposal=proposal,
                modified_data=[item.content for item in proposal.items] if proposal.items else proposal.raw_data,
                modifications=["test"] if decision == GateDecision.MODIFIED else [],
            )

        gate = ProgrammaticApprovalGate(decision_fn=alternating_fn)
        manager = GateManager(gate=gate)

        await manager.submit_gate("g1", "Gate 1", items=sample_items)
        await manager.submit_gate("g2", "Gate 2", items=sample_items)
        await manager.submit_gate("g3", "Gate 3", items=sample_items)

        s = manager.summary
        assert s["total_gates"] == 3
        assert s["approved"] == 1
        assert s["modified"] == 1
        assert s["rejected"] == 1
        assert not manager.all_approved  # One was rejected

    @pytest.mark.asyncio
    async def test_save_audit_trail(self, sample_items):
        gate = ProgrammaticApprovalGate(default_decision=GateDecision.APPROVED)
        manager = GateManager(gate=gate, simulation_id="sim_audit_test")

        await manager.submit_gate("test_gate", "Test", items=sample_items)

        with tempfile.TemporaryDirectory() as tmpdir:
            path = manager.save_audit_trail(Path(tmpdir) / "audit.json")
            assert path.exists()

            with open(path) as f:
                data = json.load(f)
            assert data["simulation_id"] == "sim_audit_test"
            assert len(data["entries"]) == 1

    @pytest.mark.asyncio
    async def test_auto_approve_pipeline(self, sample_items):
        """Full pipeline with --auto-approve flag."""
        gate = CLIApprovalGate(auto_approve=True)
        manager = GateManager(gate=gate, simulation_id="sim_ci")

        for i, name in enumerate(["context", "criteria", "guardrails", "personas", "test_plan"], 1):
            await manager.submit_gate(
                gate_name=name,
                title=f"Stage {i}: {name.replace('_', ' ').title()}",
                items=sample_items[:2],
                stage_number=i,
            )

        assert manager.total_gates == 5
        assert manager.all_approved

        # All should be auto_approved
        s = manager.summary
        assert s["auto_approved"] == 5
        assert s["approved"] == 0  # Not manually approved

    @pytest.mark.asyncio
    async def test_was_approved_includes_modified(self, sample_items):
        """Modified counts as approved (user reviewed and accepted with changes)."""
        gate = ProgrammaticApprovalGate(default_decision=GateDecision.MODIFIED)
        manager = GateManager(gate=gate)

        await manager.submit_gate("test", "Test", items=sample_items)

        assert manager.was_approved("test")  # Modified = approved
        assert not manager.was_rejected("test")