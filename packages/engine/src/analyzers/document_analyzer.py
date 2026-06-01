"""
Document Analyzer — LLM-powered bot context extraction.

Stage 1 of the Partial Autonomous pipeline:
  Documents → DocumentAnalyzer → Bot Description & Context

Takes loaded document chunks and uses an LLM to synthesize a comprehensive
description of the bot's purpose, domain, capabilities, limitations,
and target audience.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from src.analyzers.document_loader import DocumentChunk, LoadResult
from src.core.llm_client import LLMClient, LLMClientFactory
from src.core.logging import get_logger

logger = get_logger(__name__)


# ============================================================
# Output Models
# ============================================================

class BotContext(BaseModel):
    """Extracted bot context from documentation analysis."""
    bot_name: str = "Unknown Bot"
    domain: str = ""
    purpose: str = ""
    capabilities: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    target_audience: str = ""
    topics: list[str] = Field(default_factory=list)
    tone_and_style: str = ""
    key_entities: list[str] = Field(default_factory=list)
    raw_summary: str = ""
    confidence: str = "medium"  # high / medium / low
    source_doc_count: int = 0
    source_chunk_count: int = 0

    @property
    def description(self) -> str:
        """Human-readable bot description."""
        parts = []
        if self.bot_name != "Unknown Bot":
            parts.append(f"**{self.bot_name}**")
        if self.purpose:
            parts.append(self.purpose)
        if self.domain:
            parts.append(f"Domain: {self.domain}")
        if self.capabilities:
            parts.append(f"Capabilities: {', '.join(self.capabilities[:5])}")
        if self.limitations:
            parts.append(f"Limitations: {', '.join(self.limitations[:3])}")
        return "\n".join(parts) if parts else self.raw_summary


# ============================================================
# Prompts
# ============================================================

ANALYSIS_SYSTEM_PROMPT = """\
You are an expert technical analyst specializing in AI chatbot systems.
Your job is to analyze documentation and extract a comprehensive understanding
of what a chatbot does, its domain, capabilities, and limitations.
Always output valid JSON. Be thorough but concise."""

ANALYSIS_PROMPT = """\
Analyze the following documentation for an AI chatbot and extract a comprehensive context profile.

## Documentation Content
{doc_text}

## Instructions
From the documentation above, extract:

1. **bot_name**: The name of the bot/system (if mentioned, otherwise "Unknown Bot")
2. **domain**: The primary domain (e.g., "E-commerce support", "Healthcare FAQ", "Technical documentation")
3. **purpose**: A 1-2 sentence description of what this bot does
4. **capabilities**: List of specific things the bot can do (3-10 items)
5. **limitations**: List of things the bot cannot or should not do (2-5 items)
6. **target_audience**: Who uses this bot (e.g., "Customers", "Employees", "Developers")
7. **topics**: Key topics/subjects the bot handles (5-15 items)
8. **tone_and_style**: The expected communication style (e.g., "Professional and helpful", "Casual and friendly")
9. **key_entities**: Important names, products, policies, or concepts mentioned (3-10 items)
10. **confidence**: Your confidence in this analysis: "high" (clear docs), "medium" (some ambiguity), "low" (very limited info)

Output as a JSON object with these exact keys. If information is not available for a field, use empty string or empty list."""

MERGE_PROMPT = """\
You have multiple context extractions from different parts of a bot's documentation.
Merge them into a single, comprehensive context profile.

## Extractions
{extractions_json}

## Instructions
Combine all extractions into one unified profile:
- Merge capabilities lists (deduplicate)
- Merge topics lists (deduplicate)
- Merge limitations (deduplicate)
- Keep the most specific bot_name
- Combine purpose descriptions into one comprehensive statement
- Set confidence to "high" if extractions are consistent, "medium" if some conflict, "low" if contradictory

Output as a single JSON object with keys: bot_name, domain, purpose, capabilities, limitations, 
target_audience, topics, tone_and_style, key_entities, confidence"""


# ============================================================
# Document Analyzer
# ============================================================

class DocumentAnalyzer:
    """
    Analyzes loaded documents to extract bot context using LLM.

    For large document sets, analyzes chunks individually then merges results.
    """

    def __init__(
            self,
            llm_client: LLMClient | None = None,
            max_context_chars: int = 12000,
    ):
        self.llm = llm_client or LLMClientFactory.persona_generator()
        self.max_context_chars = max_context_chars

    async def analyze(self, load_result: LoadResult) -> BotContext:
        """
        Analyze loaded documents and extract bot context.

        For small doc sets: single LLM call.
        For large doc sets: analyze chunks, then merge.
        """
        if not load_result.documents or load_result.successful == 0:
            logger.warning("no_documents_to_analyze")
            return BotContext(
                raw_summary="No documents available for analysis",
                confidence="low",
            )

        all_text = load_result.all_text
        total_chars = len(all_text)

        logger.info(
            "analyzing_documents",
            doc_count=load_result.successful,
            total_chars=total_chars,
        )

        if total_chars <= self.max_context_chars:
            # Small enough for single analysis
            return await self._analyze_single(all_text, load_result)
        else:
            # Chunk-based analysis with merge
            return await self._analyze_chunked(load_result)

    async def _analyze_single(self, text: str, load_result: LoadResult) -> BotContext:
        """Analyze all text in a single LLM call."""
        prompt = ANALYSIS_PROMPT.format(doc_text=text[:self.max_context_chars])

        try:
            result = await self.llm.generate_json(
                prompt=prompt,
                system_prompt=ANALYSIS_SYSTEM_PROMPT,
            )
            context = self._parse_context(result, load_result)
            logger.info("analysis_complete", confidence=context.confidence)
            return context
        except Exception as e:
            logger.error("analysis_failed", error=str(e))
            return BotContext(
                raw_summary=f"Analysis failed: {e}",
                confidence="low",
                source_doc_count=load_result.successful,
            )

    async def _analyze_chunked(self, load_result: LoadResult) -> BotContext:
        """Analyze document chunks individually, then merge."""
        chunks = load_result.all_chunks

        # Select representative chunks (first, last, and evenly spaced)
        max_chunks_to_analyze = 5
        if len(chunks) <= max_chunks_to_analyze:
            selected = chunks
        else:
            step = len(chunks) // max_chunks_to_analyze
            selected = [chunks[i * step] for i in range(max_chunks_to_analyze)]
            if chunks[-1] not in selected:
                selected[-1] = chunks[-1]

        # Analyze each chunk
        extractions = []
        for chunk in selected:
            prompt = ANALYSIS_PROMPT.format(doc_text=chunk.text[:self.max_context_chars])
            try:
                result = await self.llm.generate_json(
                    prompt=prompt,
                    system_prompt=ANALYSIS_SYSTEM_PROMPT,
                )
                extractions.append(result)
            except Exception as e:
                logger.warning("chunk_analysis_failed", chunk=chunk.chunk_index, error=str(e))

        if not extractions:
            return BotContext(
                raw_summary="All chunk analyses failed",
                confidence="low",
                source_doc_count=load_result.successful,
                source_chunk_count=len(chunks),
            )

        if len(extractions) == 1:
            return self._parse_context(extractions[0], load_result)

        # Merge multiple extractions
        return await self._merge_extractions(extractions, load_result)

    async def _merge_extractions(
            self,
            extractions: list[dict],
            load_result: LoadResult,
    ) -> BotContext:
        """Merge multiple chunk extractions into one context."""
        import json

        prompt = MERGE_PROMPT.format(
            extractions_json=json.dumps(extractions, indent=2, default=str)
        )

        try:
            merged = await self.llm.generate_json(
                prompt=prompt,
                system_prompt=ANALYSIS_SYSTEM_PROMPT,
            )
            context = self._parse_context(merged, load_result)
            context.source_chunk_count = len(extractions)
            return context
        except Exception as e:
            logger.error("merge_failed", error=str(e))
            # Fallback: use the first extraction
            return self._parse_context(extractions[0], load_result)

    def _parse_context(self, data: dict | list, load_result: LoadResult) -> BotContext:
        """Parse LLM output into BotContext model."""
        if isinstance(data, list):
            data = data[0] if data else {}

        return BotContext(
            bot_name=data.get("bot_name", "Unknown Bot"),
            domain=data.get("domain", ""),
            purpose=data.get("purpose", ""),
            capabilities=self._ensure_list(data.get("capabilities", [])),
            limitations=self._ensure_list(data.get("limitations", [])),
            target_audience=data.get("target_audience", ""),
            topics=self._ensure_list(data.get("topics", [])),
            tone_and_style=data.get("tone_and_style", ""),
            key_entities=self._ensure_list(data.get("key_entities", [])),
            raw_summary=data.get("purpose", ""),
            confidence=data.get("confidence", "medium"),
            source_doc_count=load_result.successful,
            source_chunk_count=len(load_result.all_chunks),
        )

    @staticmethod
    def _ensure_list(val) -> list[str]:
        """Ensure a value is a list of strings."""
        if isinstance(val, list):
            return [str(v) for v in val]
        if isinstance(val, str):
            return [v.strip() for v in val.split(",") if v.strip()]
        return []