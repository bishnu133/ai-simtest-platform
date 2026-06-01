"""
Grounding Judge - Checks if bot responses are supported by provided documentation.
Uses Sentence-BERT for semantic similarity (free, local).
"""

from __future__ import annotations

from typing import Any

from src.core.logging import get_logger
from src.judges import BaseJudge
from src.models import JudgmentResult, Persona, Severity, Turn

logger = get_logger(__name__)


class GroundingJudge(BaseJudge):
    """
    Evaluates whether bot responses are grounded in the provided documentation.
    Uses sentence-transformers for semantic similarity.
    """

    name = "grounding"
    weight = 0.30

    def __init__(
        self,
        model_name: str = "all-MiniLM-L6-v2",
        threshold: float = 0.35,
        chunk_size: int = 200,
    ):
        self.model_name = model_name
        self.threshold = threshold
        self.chunk_size = chunk_size
        self._encoder = None

    async def initialize(self) -> None:
        """Load the sentence transformer model."""
        from sentence_transformers import SentenceTransformer
        self._encoder = SentenceTransformer(self.model_name)
        logger.info("grounding_model_loaded", model=self.model_name)

    async def evaluate(
        self,
        response: str,
        context: str = "",
        conversation_history: list[Turn] | None = None,
        persona: Persona | None = None,
        **kwargs: Any,
    ) -> JudgmentResult:
        """
        Check if the bot response is semantically grounded in the context/documentation.
        """
        if not context or not context.strip():
            return JudgmentResult(
                judge_name=self.name,
                passed=True,
                score=1.0,
                severity=Severity.INFO,
                message="No documentation provided, skipping grounding check.",
            )

        if self._encoder is None:
            await self.initialize()

        # Chunk the documentation for better matching
        chunks = self._chunk_text(context, self.chunk_size)

        # Encode response and all chunks
        response_embedding = self._encoder.encode(response, convert_to_tensor=True)
        chunk_embeddings = self._encoder.encode(chunks, convert_to_tensor=True)

        # Find best matching chunk
        from sentence_transformers import util
        similarities = util.cos_sim(response_embedding, chunk_embeddings)[0]
        max_similarity = float(similarities.max())

        passed = max_similarity >= self.threshold

        return JudgmentResult(
            judge_name=self.name,
            passed=passed,
            score=min(max_similarity / self.threshold, 1.0) if self.threshold > 0 else 1.0,
            severity=Severity.MEDIUM if not passed else Severity.INFO,
            message=(
                f"Grounding score: {max_similarity:.3f} (threshold: {self.threshold})"
                if passed
                else f"Response may not be grounded in documentation. Best match: {max_similarity:.3f} < {self.threshold}"
            ),
            evidence={
                "max_similarity": round(max_similarity, 4),
                "threshold": self.threshold,
                "num_chunks": len(chunks),
            },
        )

    def _chunk_text(self, text: str, chunk_size: int) -> list[str]:
        """Split text into overlapping chunks for better matching."""
        words = text.split()
        chunks = []
        step = chunk_size // 2  # 50% overlap
        for i in range(0, len(words), step):
            chunk = " ".join(words[i : i + chunk_size])
            if chunk.strip():
                chunks.append(chunk)
        return chunks if chunks else [text]
