"""Suite 7: ConversationService — hybrid PG-summary + R2-transcript storage."""
from __future__ import annotations

import pytest

from src.conversations.models import (
    ConversationTranscript,
    Turn,
)
from src.conversations.service import (
    ConversationNotFound,
    ConversationService,
    TranscriptIntegrityError,
)


pytestmark = pytest.mark.asyncio


@pytest.fixture
def conv_service(local_adapter):
    return ConversationService(storage=local_adapter)


def _sample_transcript(conversation_id: str = "conv_1") -> ConversationTranscript:
    return ConversationTranscript(
        conversation_id=conversation_id,
        run_id="run_123",
        persona_id="persona_alice",
        turns=[
            Turn(turn_number=1, role="user", content="I need a refund"),
            Turn(turn_number=2, role="assistant", content="I can help with that"),
            Turn(turn_number=3, role="user", content="Thanks"),
        ],
    )


class TestConversationService:
    async def test_store_and_fetch_summary(self, conv_service, ctx_tenant_a):
        transcript = _sample_transcript()
        summary = await conv_service.store_conversation(
            ctx_tenant_a,
            run_id="run_123",
            persona_id="persona_alice",
            persona_name="Alice the Refund Seeker",
            transcript=transcript,
            verdict="pass",
            judge_scores={"quality": 0.9, "safety": 1.0},
        )
        assert summary.id == "conv_1"
        assert summary.turn_count == 3
        assert summary.verdict == "pass"
        assert summary.transcript_ref is not None
        assert summary.pass_rate == pytest.approx(0.95)

        fetched = await conv_service.get_summary(ctx_tenant_a, "conv_1")
        assert fetched.id == "conv_1"

    async def test_get_transcript_round_trip_with_integrity_check(
        self, conv_service, ctx_tenant_a
    ):
        original = _sample_transcript()
        await conv_service.store_conversation(
            ctx_tenant_a,
            run_id="run_123",
            persona_id="persona_alice",
            persona_name="Alice",
            transcript=original,
        )
        fetched = await conv_service.get_transcript(ctx_tenant_a, "conv_1")
        assert fetched.conversation_id == "conv_1"
        assert len(fetched.turns) == 3
        assert fetched.turns[0].content == "I need a refund"

    async def test_delete_run_conversations_removes_all(
        self, conv_service, ctx_tenant_a
    ):
        for i in range(3):
            t = _sample_transcript(conversation_id=f"conv_{i}")
            await conv_service.store_conversation(
                ctx_tenant_a,
                run_id="run_gdpr",
                persona_id=f"p_{i}",
                persona_name=f"Persona {i}",
                transcript=t,
            )

        deleted = await conv_service.delete_run_conversations(
            ctx_tenant_a, run_id="run_gdpr"
        )
        assert deleted == 3
        with pytest.raises(ConversationNotFound):
            await conv_service.get_summary(ctx_tenant_a, "conv_0")

    async def test_cross_tenant_isolation_on_conversation_summary(
        self, conv_service, ctx_tenant_a, ctx_tenant_b
    ):
        transcript = _sample_transcript(conversation_id="conv_tenant_a")
        await conv_service.store_conversation(
            ctx_tenant_a,
            run_id="run_isolated",
            persona_id="p",
            persona_name="P",
            transcript=transcript,
        )
        # Tenant B must not see Tenant A's conversation
        with pytest.raises(ConversationNotFound):
            await conv_service.get_summary(ctx_tenant_b, "conv_tenant_a")
