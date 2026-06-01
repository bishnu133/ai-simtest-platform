"""
RAG Metric Evaluators — Phase 3 of P3 #14 RAG/Tool Evaluation Framework.

Consumes ResponseAnnotation from the Annotator and scores each RAG metric.

Metric classification (from approved design):
  DETERMINISTIC: citation_accuracy, source_coverage, context_utilization, attribution_completeness
  HYBRID:        faithfulness, answer_relevance, context_relevance, temporal_awareness
  LLM-ONLY:     hallucination, noise_robustness, multi_hop_reasoning, conflicting_evidence

Each evaluator function takes:
  - annotation: ResponseAnnotation (from Phase 2 annotator)
  - config: RAGEvalConfig (thresholds, speed mode)
  - llm_client: optional LLM for hybrid/LLM metrics

Returns: MetricResult with score, passed, evidence, issues.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from src.rag_eval.models import (
    EvalSpeed,
    JudgeReliability,
    MetricResult,
    RAGMetricType,
    RAG_METRIC_RELIABILITY,
    RAGEvalConfig,
    ResponseAnnotation,
    SPEED_MODE_INCLUDES,
)


# ============================================================================
# Base Evaluator Interface
# ============================================================================

class RAGMetricEvaluator:
    """
    Evaluates all 12 RAG metrics against a ResponseAnnotation.

    Usage:
        evaluator = RAGMetricEvaluator(config, llm_client=llm)
        results = await evaluator.evaluate(annotation)
        # results: list[MetricResult]
    """

    def __init__(
        self,
        config: Optional[RAGEvalConfig] = None,
        llm_client: Optional[Any] = None,
    ):
        self._config = config or RAGEvalConfig()
        self._llm = llm_client

        # Registry of metric evaluator functions
        self._evaluators = {
            RAGMetricType.CITATION_ACCURACY: self._eval_citation_accuracy,
            RAGMetricType.SOURCE_COVERAGE: self._eval_source_coverage,
            RAGMetricType.CONTEXT_UTILIZATION: self._eval_context_utilization,
            RAGMetricType.ATTRIBUTION_COMPLETENESS: self._eval_attribution_completeness,
            RAGMetricType.FAITHFULNESS: self._eval_faithfulness,
            RAGMetricType.ANSWER_RELEVANCE: self._eval_answer_relevance,
            RAGMetricType.CONTEXT_RELEVANCE: self._eval_context_relevance,
            RAGMetricType.TEMPORAL_AWARENESS: self._eval_temporal_awareness,
            RAGMetricType.HALLUCINATION: self._eval_hallucination,
            RAGMetricType.NOISE_ROBUSTNESS: self._eval_noise_robustness,
            RAGMetricType.MULTI_HOP_REASONING: self._eval_multi_hop_reasoning,
            RAGMetricType.CONFLICTING_EVIDENCE: self._eval_conflicting_evidence,
        }

    # ================================================================
    # Public API
    # ================================================================

    async def evaluate(self, annotation: ResponseAnnotation) -> List[MetricResult]:
        """
        Evaluate all eligible RAG metrics for this annotation.

        Only runs metrics that:
        1. Are eligible for the configured speed mode
        2. Are in the enabled list (if specified)
        3. Have relevant evidence to evaluate
        """
        results = []

        for metric_type, evaluator_fn in self._evaluators.items():
            # Check speed mode eligibility
            if not self._config.is_metric_eligible(metric_type.value):
                continue

            # Check enabled list (if specified)
            if (self._config.enabled_rag_metrics
                    and metric_type not in self._config.enabled_rag_metrics):
                continue

            # Check if LLM is needed but not available
            reliability = RAG_METRIC_RELIABILITY[metric_type]
            if reliability == JudgeReliability.LLM and not self._llm:
                continue

            try:
                result = await evaluator_fn(annotation)
                # Apply threshold
                threshold = self._config.get_threshold(metric_type.value)
                result.threshold = threshold
                result.passed = result.score >= threshold
                results.append(result)
            except Exception as e:
                results.append(MetricResult(
                    metric_name=metric_type.value,
                    metric_type="rag",
                    score=0.0,
                    passed=False,
                    reliability=reliability.value,
                    message=f"Evaluation error: {str(e)[:150]}",
                    issues=[f"Error: {str(e)[:200]}"],
                ))

        return results

    def evaluate_sync(self, annotation: ResponseAnnotation) -> List[MetricResult]:
        """
        Synchronous evaluation — only deterministic metrics.
        Useful for speed=deterministic mode and testing.
        """
        results = []

        for metric_type, evaluator_fn in self._evaluators.items():
            reliability = RAG_METRIC_RELIABILITY[metric_type]
            # Only run deterministic metrics in sync mode
            if reliability != JudgeReliability.DETERMINISTIC:
                continue

            if not self._config.is_metric_eligible(metric_type.value):
                continue

            if (self._config.enabled_rag_metrics
                    and metric_type not in self._config.enabled_rag_metrics):
                continue

            try:
                # Deterministic evaluators don't need await
                import asyncio
                result = asyncio.get_event_loop().run_until_complete(evaluator_fn(annotation))
                threshold = self._config.get_threshold(metric_type.value)
                result.threshold = threshold
                result.passed = result.score >= threshold
                results.append(result)
            except Exception as e:
                results.append(MetricResult(
                    metric_name=metric_type.value,
                    metric_type="rag",
                    score=0.0,
                    passed=False,
                    reliability=reliability.value,
                    message=f"Sync evaluation error: {str(e)[:150]}",
                ))

        return results

    # ================================================================
    # DETERMINISTIC METRICS
    # ================================================================

    async def _eval_citation_accuracy(
        self, annotation: ResponseAnnotation
    ) -> MetricResult:
        """
        Check if citations in the response are verifiable against retrieved chunks.

        Deterministic: compares citation source_references against chunk source_ids/names.
        Score = verified_citations / total_citations.
        """
        if not annotation.citations:
            return MetricResult(
                metric_name="citation_accuracy",
                metric_type="rag",
                score=1.0,  # No citations to verify — vacuously correct
                reliability="deterministic",
                confidence=annotation.overall_confidence,
                message="No citations found in response",
                evidence={"total_citations": 0},
            )

        # Build set of known source identifiers from retrieved chunks
        known_sources = set()
        for chunk in annotation.retrieved_chunks:
            if chunk.source_id:
                known_sources.add(chunk.source_id.lower())
            if chunk.source_name:
                known_sources.add(chunk.source_name.lower())

        verified = 0
        unverified = []

        for cit in annotation.citations:
            ref = cit.source_reference.lower().strip()
            # Check exact match or substring match against known sources
            matched = False
            if ref in known_sources:
                matched = True
            else:
                for ks in known_sources:
                    if ref in ks or ks in ref:
                        matched = True
                        break

            # If no retrieved chunks, citations from inferred mode are
            # considered unverifiable but not wrong
            if not annotation.retrieved_chunks:
                matched = True  # Can't disprove

            if matched:
                verified += 1
            else:
                unverified.append(cit.source_reference)

        total = len(annotation.citations)
        score = verified / total if total > 0 else 1.0

        return MetricResult(
            metric_name="citation_accuracy",
            metric_type="rag",
            score=score,
            reliability="deterministic",
            confidence=annotation.overall_confidence,
            message=f"{verified}/{total} citations verified" if total > 0 else "No citations",
            evidence={
                "total_citations": total,
                "verified": verified,
                "unverified_references": unverified[:5],
            },
            issues=[f"Unverified citation: {ref}" for ref in unverified[:3]],
        )

    async def _eval_source_coverage(
        self, annotation: ResponseAnnotation
    ) -> MetricResult:
        """
        Check if the response uses all relevant retrieved sources.

        Deterministic: for each retrieved chunk, check if its content appears
        in the response (fuzzy substring matching).
        Score = chunks_referenced / total_chunks.
        """
        if not annotation.retrieved_chunks:
            return MetricResult(
                metric_name="source_coverage",
                metric_type="rag",
                score=1.0,
                reliability="deterministic",
                confidence=annotation.overall_confidence,
                message="No retrieved sources to check coverage",
                evidence={"total_sources": 0},
            )

        response_lower = annotation.response_text.lower()
        referenced = 0
        unreferenced = []

        for chunk in annotation.retrieved_chunks:
            # Extract key phrases from chunk content
            chunk_words = set(self._extract_key_phrases(chunk.content))
            if not chunk_words:
                referenced += 1  # Empty chunk — skip
                continue

            # Check if enough key phrases appear in response
            matches = sum(1 for w in chunk_words if w in response_lower)
            coverage = matches / len(chunk_words) if chunk_words else 0

            if coverage >= 0.3:  # At least 30% of key phrases present
                referenced += 1
            else:
                unreferenced.append(chunk.source_name or chunk.source_id or f"chunk_{chunk.chunk_index}")

        total = len(annotation.retrieved_chunks)
        score = referenced / total if total > 0 else 1.0

        return MetricResult(
            metric_name="source_coverage",
            metric_type="rag",
            score=score,
            reliability="deterministic",
            confidence=annotation.overall_confidence,
            message=f"{referenced}/{total} sources referenced in response",
            evidence={
                "total_sources": total,
                "referenced": referenced,
                "unreferenced_sources": unreferenced[:5],
            },
            issues=[f"Source not referenced: {s}" for s in unreferenced[:3]],
        )

    async def _eval_context_utilization(
        self, annotation: ResponseAnnotation
    ) -> MetricResult:
        """
        How much of the retrieved context was actually used in the response.

        Deterministic: measures overlap between response and retrieved chunks.
        High utilization = good (response uses retrieved info).
        Very low utilization = retrieved docs may be irrelevant.
        """
        if not annotation.retrieved_chunks:
            return MetricResult(
                metric_name="context_utilization",
                metric_type="rag",
                score=1.0,
                reliability="deterministic",
                confidence=annotation.overall_confidence,
                message="No context to measure utilization",
                evidence={"total_context_words": 0},
            )

        # Collect all words from retrieved chunks
        context_words = set()
        for chunk in annotation.retrieved_chunks:
            context_words.update(self._tokenize(chunk.content))

        response_words = set(self._tokenize(annotation.response_text))

        if not context_words:
            return MetricResult(
                metric_name="context_utilization",
                metric_type="rag",
                score=1.0,
                reliability="deterministic",
                confidence=annotation.overall_confidence,
                message="Empty context",
            )

        # What fraction of context words appear in the response
        overlap = context_words & response_words
        utilization = len(overlap) / len(context_words)

        # Cap at 1.0
        score = min(1.0, utilization)

        return MetricResult(
            metric_name="context_utilization",
            metric_type="rag",
            score=score,
            reliability="deterministic",
            confidence=annotation.overall_confidence,
            message=f"{len(overlap)}/{len(context_words)} context terms used ({score:.0%})",
            evidence={
                "total_context_words": len(context_words),
                "overlap_words": len(overlap),
                "utilization_pct": round(score * 100, 1),
            },
        )

    async def _eval_attribution_completeness(
        self, annotation: ResponseAnnotation
    ) -> MetricResult:
        """
        Check if all factual claims in the response have attributions.

        Deterministic: counts sentences with factual content vs. those with citations.
        Heuristic — sentences with numbers, dates, proper nouns, or specific claims
        are considered "factual" and should have attribution.
        """
        if not annotation.response_text.strip():
            return MetricResult(
                metric_name="attribution_completeness",
                metric_type="rag",
                score=1.0,
                reliability="deterministic",
                confidence=annotation.overall_confidence,
                message="Empty response",
            )

        sentences = self._split_sentences(annotation.response_text)
        if not sentences:
            return MetricResult(
                metric_name="attribution_completeness",
                metric_type="rag",
                score=1.0,
                reliability="deterministic",
                confidence=annotation.overall_confidence,
                message="No sentences found",
            )

        factual_sentences = []
        attributed_sentences = []

        # Build set of cited text snippets
        cited_texts = set()
        for cit in annotation.citations:
            cited_texts.add(cit.text.lower()[:80])

        for sent in sentences:
            if self._is_factual_claim(sent):
                factual_sentences.append(sent)
                # Check if this sentence has attribution
                sent_lower = sent.lower()
                has_attr = (
                    any(ct in sent_lower for ct in cited_texts)
                    or re.search(r'\[\d+\]', sent)
                    or re.search(r'\[.+?\]', sent)
                    or any(p in sent_lower for p in (
                        "according to", "based on", "per the", "as stated",
                        "source:", "reference:",
                    ))
                )
                if has_attr:
                    attributed_sentences.append(sent)

        if not factual_sentences:
            return MetricResult(
                metric_name="attribution_completeness",
                metric_type="rag",
                score=1.0,
                reliability="deterministic",
                confidence=annotation.overall_confidence,
                message="No factual claims requiring attribution",
                evidence={"factual_claims": 0},
            )

        score = len(attributed_sentences) / len(factual_sentences)

        return MetricResult(
            metric_name="attribution_completeness",
            metric_type="rag",
            score=score,
            reliability="deterministic",
            confidence=annotation.overall_confidence,
            message=f"{len(attributed_sentences)}/{len(factual_sentences)} factual claims attributed",
            evidence={
                "factual_claims": len(factual_sentences),
                "attributed_claims": len(attributed_sentences),
                "unattributed_examples": [s[:80] for s in factual_sentences
                                          if s not in attributed_sentences][:3],
            },
            issues=[
                f"Unattributed claim: '{s[:60]}...'"
                for s in factual_sentences if s not in attributed_sentences
            ][:3],
        )

    # ================================================================
    # HYBRID METRICS (deterministic first, LLM for deeper analysis)
    # ================================================================

    async def _eval_faithfulness(
        self, annotation: ResponseAnnotation
    ) -> MetricResult:
        """
        Does the response stay true to the retrieved context?

        Hybrid: deterministic word overlap first, LLM for semantic check.
        """
        context_text = self._get_context_text(annotation)

        if not context_text:
            return MetricResult(
                metric_name="faithfulness",
                metric_type="rag",
                score=1.0,
                reliability="hybrid",
                confidence=annotation.overall_confidence * 0.5,
                message="No context available to check faithfulness",
            )

        # Deterministic: word overlap between response and context
        response_words = set(self._tokenize(annotation.response_text))
        context_words = set(self._tokenize(context_text))

        if not response_words:
            return MetricResult(
                metric_name="faithfulness",
                metric_type="rag",
                score=1.0,
                reliability="hybrid",
                confidence=annotation.overall_confidence,
                message="Empty response",
            )

        overlap = response_words & context_words
        deterministic_score = len(overlap) / len(response_words) if response_words else 0
        # Normalize — even faithful responses won't have 100% overlap
        deterministic_score = min(1.0, deterministic_score * 2.0)

        # LLM enhancement
        if self._llm and len(annotation.response_text) > 20:
            try:
                llm_score = await self._llm_faithfulness_check(
                    annotation.response_text, context_text
                )
                # Blend: 40% deterministic + 60% LLM
                score = 0.4 * deterministic_score + 0.6 * llm_score
                reliability = "hybrid"
            except Exception:
                score = deterministic_score
                reliability = "hybrid"
        else:
            score = deterministic_score
            reliability = "hybrid"

        return MetricResult(
            metric_name="faithfulness",
            metric_type="rag",
            score=min(1.0, score),
            reliability=reliability,
            confidence=annotation.overall_confidence,
            message=f"Faithfulness score: {score:.2f}",
            evidence={
                "deterministic_score": round(deterministic_score, 3),
                "word_overlap": len(overlap),
                "response_words": len(response_words),
            },
        )

    async def _eval_answer_relevance(
        self, annotation: ResponseAnnotation
    ) -> MetricResult:
        """
        Does the response actually address the user's question?

        Hybrid: keyword overlap first, LLM for semantic relevance.
        """
        if not annotation.user_query:
            return MetricResult(
                metric_name="answer_relevance",
                metric_type="rag",
                score=1.0,
                reliability="hybrid",
                confidence=annotation.overall_confidence * 0.5,
                message="No user query to check relevance against",
            )

        # Deterministic: keyword overlap between query and response
        query_words = set(self._tokenize(annotation.user_query))
        response_words = set(self._tokenize(annotation.response_text))

        if not query_words:
            return MetricResult(
                metric_name="answer_relevance",
                metric_type="rag",
                score=1.0,
                reliability="hybrid",
                confidence=0.5,
                message="Empty query",
            )

        overlap = query_words & response_words
        deterministic_score = len(overlap) / len(query_words) if query_words else 0
        deterministic_score = min(1.0, deterministic_score * 1.5)

        # LLM enhancement
        if self._llm and len(annotation.response_text) > 20:
            try:
                llm_score = await self._llm_relevance_check(
                    annotation.user_query, annotation.response_text
                )
                score = 0.3 * deterministic_score + 0.7 * llm_score
            except Exception:
                score = deterministic_score
        else:
            score = deterministic_score

        return MetricResult(
            metric_name="answer_relevance",
            metric_type="rag",
            score=min(1.0, score),
            reliability="hybrid",
            confidence=annotation.overall_confidence,
            message=f"Answer relevance: {score:.2f}",
            evidence={
                "query_terms": len(query_words),
                "overlap_terms": len(overlap),
                "deterministic_score": round(deterministic_score, 3),
            },
        )

    async def _eval_context_relevance(
        self, annotation: ResponseAnnotation
    ) -> MetricResult:
        """
        Were the right documents retrieved for the user's question?

        Hybrid: keyword overlap between query and retrieved chunks.
        """
        if not annotation.user_query or not annotation.retrieved_chunks:
            return MetricResult(
                metric_name="context_relevance",
                metric_type="rag",
                score=1.0,
                reliability="hybrid",
                confidence=annotation.overall_confidence * 0.5,
                message="No query or chunks to evaluate context relevance",
            )

        query_words = set(self._tokenize(annotation.user_query))
        if not query_words:
            return MetricResult(
                metric_name="context_relevance",
                metric_type="rag",
                score=1.0,
                reliability="hybrid",
                confidence=0.5,
                message="Empty query",
            )

        # Score each chunk's relevance to the query
        chunk_scores = []
        for chunk in annotation.retrieved_chunks:
            chunk_words = set(self._tokenize(chunk.content))
            overlap = query_words & chunk_words
            chunk_score = len(overlap) / len(query_words) if query_words else 0
            chunk_scores.append(min(1.0, chunk_score * 2.0))

        # Average relevance across all chunks
        avg_score = sum(chunk_scores) / len(chunk_scores) if chunk_scores else 0

        # Use retrieval scores if available
        if any(c.relevance_score > 0 for c in annotation.retrieved_chunks):
            retrieval_avg = sum(c.relevance_score for c in annotation.retrieved_chunks) / len(annotation.retrieved_chunks)
            # Blend keyword overlap with retrieval scores
            avg_score = 0.4 * avg_score + 0.6 * retrieval_avg

        return MetricResult(
            metric_name="context_relevance",
            metric_type="rag",
            score=min(1.0, avg_score),
            reliability="hybrid",
            confidence=annotation.overall_confidence,
            message=f"Context relevance: {avg_score:.2f} across {len(annotation.retrieved_chunks)} chunks",
            evidence={
                "num_chunks": len(annotation.retrieved_chunks),
                "chunk_scores": [round(s, 3) for s in chunk_scores[:5]],
            },
        )

    async def _eval_temporal_awareness(
        self, annotation: ResponseAnnotation
    ) -> MetricResult:
        """
        Does the response handle dated information correctly?

        Hybrid: checks for date mentions and temporal qualifiers.
        """
        text = annotation.response_text

        # Detect date patterns
        date_patterns = [
            r'\b\d{4}\b',  # Year
            r'\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+\d{1,2}',
            r'\b\d{1,2}/\d{1,2}/\d{2,4}\b',
            r'\b(?:today|yesterday|last week|last month|last year|currently|as of)\b',
        ]

        has_dates = any(re.search(p, text, re.IGNORECASE) for p in date_patterns)

        if not has_dates:
            return MetricResult(
                metric_name="temporal_awareness",
                metric_type="rag",
                score=1.0,
                reliability="hybrid",
                confidence=annotation.overall_confidence,
                message="No temporal content detected",
                evidence={"has_dates": False},
            )

        # Check for temporal qualifiers (good practice)
        qualifiers = [
            "as of", "at the time of writing", "currently", "was updated",
            "last updated", "effective", "valid until", "may have changed",
            "please verify", "subject to change",
        ]
        text_lower = text.lower()
        has_qualifier = any(q in text_lower for q in qualifiers)

        # Check if context documents have dates
        context_has_dates = False
        for chunk in annotation.retrieved_chunks:
            if any(re.search(p, chunk.content, re.IGNORECASE) for p in date_patterns):
                context_has_dates = True
                break

        # Score: higher if temporal content is qualified
        score = 0.8 if has_qualifier else 0.5
        if context_has_dates and has_qualifier:
            score = 1.0

        return MetricResult(
            metric_name="temporal_awareness",
            metric_type="rag",
            score=score,
            reliability="hybrid",
            confidence=annotation.overall_confidence,
            message=f"Temporal content {'with' if has_qualifier else 'without'} qualifiers",
            evidence={
                "has_dates": True,
                "has_temporal_qualifier": has_qualifier,
                "context_has_dates": context_has_dates,
            },
            issues=[] if has_qualifier else ["Response contains dates without temporal qualifiers"],
        )

    # ================================================================
    # LLM-ONLY METRICS
    # ================================================================

    async def _eval_hallucination(
        self, annotation: ResponseAnnotation
    ) -> MetricResult:
        """
        Does the response contain unsupported claims not in the retrieved context?

        LLM-only: requires LLM to check claims against sources.
        """
        context_text = self._get_context_text(annotation)

        if not self._llm:
            return MetricResult(
                metric_name="hallucination",
                metric_type="rag",
                score=0.0,
                reliability="llm",
                message="LLM required for hallucination detection",
            )

        if not context_text:
            return MetricResult(
                metric_name="hallucination",
                metric_type="rag",
                score=1.0,
                reliability="llm",
                confidence=0.3,
                message="No context to check hallucination against",
            )

        prompt = f"""Analyze the bot response for hallucinated content — claims NOT supported by the provided context.

CONTEXT (retrieved documents):
{context_text[:3000]}

BOT RESPONSE:
{annotation.response_text[:2000]}

Rate from 0.0 to 1.0 where:
- 1.0 = No hallucination, all claims are supported by context
- 0.5 = Some claims lack support but aren't contradictory
- 0.0 = Major hallucinations, claims contradict or aren't in context

Respond ONLY with valid JSON:
{{"score": 0.85, "hallucinated_claims": ["claim1"], "reasoning": "brief explanation"}}"""

        try:
            response = await self._llm.generate(prompt)
            parsed = self._parse_json(response)
            score = float(parsed.get("score", 0.5))
            claims = parsed.get("hallucinated_claims", [])

            return MetricResult(
                metric_name="hallucination",
                metric_type="rag",
                score=max(0.0, min(1.0, score)),
                reliability="llm",
                confidence=annotation.overall_confidence * 0.9,
                message=f"Hallucination score: {score:.2f}" + (f" ({len(claims)} issues)" if claims else ""),
                evidence={
                    "hallucinated_claims": claims[:5],
                    "reasoning": parsed.get("reasoning", ""),
                },
                issues=[f"Possible hallucination: {c}" for c in claims[:3]],
            )
        except Exception as e:
            return MetricResult(
                metric_name="hallucination",
                metric_type="rag",
                score=0.5,
                reliability="llm",
                confidence=0.3,
                message=f"Hallucination check failed: {str(e)[:100]}",
            )

    async def _eval_noise_robustness(
        self, annotation: ResponseAnnotation
    ) -> MetricResult:
        """
        Does irrelevant context cause errors in the response?

        LLM-only: checks if low-relevance chunks influenced the response.
        """
        if not self._llm or not annotation.retrieved_chunks:
            return MetricResult(
                metric_name="noise_robustness",
                metric_type="rag",
                score=1.0,
                reliability="llm",
                message="No context or LLM for noise robustness check",
            )

        # Identify low-relevance chunks (potential noise)
        noise_chunks = [c for c in annotation.retrieved_chunks if c.relevance_score < 0.5 and c.relevance_score > 0]
        if not noise_chunks:
            return MetricResult(
                metric_name="noise_robustness",
                metric_type="rag",
                score=1.0,
                reliability="llm",
                confidence=annotation.overall_confidence,
                message="No noisy (low-relevance) chunks detected",
                evidence={"noise_chunks": 0},
            )

        noise_text = "\n".join(c.content[:200] for c in noise_chunks[:3])
        prompt = f"""Analyze if the bot was misled by irrelevant context.

USER QUERY: {annotation.user_query}

LOW-RELEVANCE (NOISY) CONTEXT:
{noise_text}

BOT RESPONSE:
{annotation.response_text[:1500]}

Did the noisy context cause incorrect or irrelevant information in the response?
Respond ONLY with JSON: {{"score": 0.9, "noise_influence": "none|minor|major", "reasoning": "brief"}}"""

        try:
            response = await self._llm.generate(prompt)
            parsed = self._parse_json(response)
            score = float(parsed.get("score", 0.8))

            return MetricResult(
                metric_name="noise_robustness",
                metric_type="rag",
                score=max(0.0, min(1.0, score)),
                reliability="llm",
                confidence=annotation.overall_confidence * 0.85,
                message=f"Noise influence: {parsed.get('noise_influence', 'unknown')}",
                evidence={
                    "noise_chunks": len(noise_chunks),
                    "noise_influence": parsed.get("noise_influence", ""),
                },
            )
        except Exception as e:
            return MetricResult(
                metric_name="noise_robustness",
                metric_type="rag",
                score=0.7,
                reliability="llm",
                confidence=0.3,
                message=f"Noise check failed: {str(e)[:100]}",
            )

    async def _eval_multi_hop_reasoning(
        self, annotation: ResponseAnnotation
    ) -> MetricResult:
        """
        Can the bot combine information from multiple sources correctly?

        LLM-only: checks if multi-source info is synthesized properly.
        """
        if not self._llm or len(annotation.retrieved_chunks) < 2:
            return MetricResult(
                metric_name="multi_hop_reasoning",
                metric_type="rag",
                score=1.0,
                reliability="llm",
                message="Multi-hop requires 2+ sources and LLM",
                evidence={"num_sources": len(annotation.retrieved_chunks)},
            )

        chunks_text = "\n---\n".join(
            f"Source {i+1}: {c.content[:300]}"
            for i, c in enumerate(annotation.retrieved_chunks[:5])
        )

        prompt = f"""Evaluate if the bot correctly synthesized information from multiple sources.

SOURCES:
{chunks_text}

BOT RESPONSE:
{annotation.response_text[:1500]}

Score 0.0-1.0:
- 1.0 = Correctly combined info from multiple sources
- 0.5 = Used some sources but missed connections
- 0.0 = Failed to synthesize, contradictions between sources ignored

Respond ONLY with JSON: {{"score": 0.8, "sources_used": 2, "reasoning": "brief"}}"""

        try:
            response = await self._llm.generate(prompt)
            parsed = self._parse_json(response)
            score = float(parsed.get("score", 0.7))

            return MetricResult(
                metric_name="multi_hop_reasoning",
                metric_type="rag",
                score=max(0.0, min(1.0, score)),
                reliability="llm",
                confidence=annotation.overall_confidence * 0.85,
                message=f"Multi-hop score: {score:.2f}",
                evidence={
                    "num_sources": len(annotation.retrieved_chunks),
                    "sources_used": parsed.get("sources_used", 0),
                },
            )
        except Exception as e:
            return MetricResult(
                metric_name="multi_hop_reasoning",
                metric_type="rag",
                score=0.5,
                reliability="llm",
                confidence=0.3,
                message=f"Multi-hop check failed: {str(e)[:100]}",
            )

    async def _eval_conflicting_evidence(
        self, annotation: ResponseAnnotation
    ) -> MetricResult:
        """
        How does the bot handle contradictions between sources?

        LLM-only: checks if conflicting info is acknowledged or one-sided.
        """
        if not self._llm or len(annotation.retrieved_chunks) < 2:
            return MetricResult(
                metric_name="conflicting_evidence",
                metric_type="rag",
                score=1.0,
                reliability="llm",
                message="Conflict check requires 2+ sources and LLM",
            )

        chunks_text = "\n---\n".join(
            f"Source {i+1}: {c.content[:300]}"
            for i, c in enumerate(annotation.retrieved_chunks[:5])
        )

        prompt = f"""Check if the sources contain conflicting information and how the bot handled it.

SOURCES:
{chunks_text}

BOT RESPONSE:
{annotation.response_text[:1500]}

Score 0.0-1.0:
- 1.0 = No conflicts, or conflicts properly acknowledged
- 0.5 = Conflicts exist but bot picked one side without noting disagreement
- 0.0 = Bot presented conflicting info as consistent, creating confusion

Respond ONLY with JSON: {{"score": 0.9, "conflicts_found": false, "reasoning": "brief"}}"""

        try:
            response = await self._llm.generate(prompt)
            parsed = self._parse_json(response)
            score = float(parsed.get("score", 0.8))

            return MetricResult(
                metric_name="conflicting_evidence",
                metric_type="rag",
                score=max(0.0, min(1.0, score)),
                reliability="llm",
                confidence=annotation.overall_confidence * 0.8,
                message=f"Conflict handling: {score:.2f}",
                evidence={
                    "conflicts_found": parsed.get("conflicts_found", False),
                    "reasoning": parsed.get("reasoning", ""),
                },
            )
        except Exception as e:
            return MetricResult(
                metric_name="conflicting_evidence",
                metric_type="rag",
                score=0.7,
                reliability="llm",
                confidence=0.3,
                message=f"Conflict check failed: {str(e)[:100]}",
            )

    # ================================================================
    # LLM Helper Methods
    # ================================================================

    async def _llm_faithfulness_check(self, response: str, context: str) -> float:
        """LLM-based faithfulness scoring."""
        prompt = f"""Rate how faithful this response is to the provided context.
Context: {context[:2000]}
Response: {response[:1000]}
Score 0.0-1.0 where 1.0=perfectly faithful. Respond ONLY with JSON: {{"score": 0.85}}"""
        result = await self._llm.generate(prompt)
        parsed = self._parse_json(result)
        return float(parsed.get("score", 0.5))

    async def _llm_relevance_check(self, query: str, response: str) -> float:
        """LLM-based answer relevance scoring."""
        prompt = f"""Rate how relevant this response is to the user's question.
Question: {query[:500]}
Response: {response[:1000]}
Score 0.0-1.0 where 1.0=perfectly relevant. Respond ONLY with JSON: {{"score": 0.85}}"""
        result = await self._llm.generate(prompt)
        parsed = self._parse_json(result)
        return float(parsed.get("score", 0.5))

    # ================================================================
    # Utility Methods
    # ================================================================

    def _get_context_text(self, annotation: ResponseAnnotation) -> str:
        """Get combined context text from retrieved chunks or context_document."""
        if annotation.context_document:
            return annotation.context_document

        if annotation.retrieved_chunks:
            return "\n\n".join(c.content for c in annotation.retrieved_chunks)

        return ""

    def _tokenize(self, text: str) -> List[str]:
        """Simple word tokenization with stop word removal."""
        stop_words = {
            "the", "a", "an", "is", "are", "was", "were", "be", "been",
            "being", "have", "has", "had", "do", "does", "did", "will",
            "would", "could", "should", "may", "might", "can", "shall",
            "to", "of", "in", "for", "on", "with", "at", "by", "from",
            "as", "into", "about", "like", "through", "after", "over",
            "and", "but", "or", "nor", "not", "so", "yet", "both",
            "it", "its", "this", "that", "these", "those", "i", "you",
            "he", "she", "we", "they", "me", "him", "her", "us", "them",
            "my", "your", "his", "our", "their",
        }
        words = re.findall(r'\b[a-z]{2,}\b', text.lower())
        return [w for w in words if w not in stop_words]

    def _extract_key_phrases(self, text: str) -> List[str]:
        """Extract meaningful key phrases from text."""
        words = self._tokenize(text)
        # Keep words that are reasonably specific (3+ chars)
        return [w for w in words if len(w) >= 3]

    def _split_sentences(self, text: str) -> List[str]:
        """Split text into sentences."""
        sentences = re.split(r'(?<=[.!?])\s+', text)
        return [s.strip() for s in sentences if len(s.strip()) > 10]

    def _is_factual_claim(self, sentence: str) -> bool:
        """Heuristic: does this sentence contain a factual claim?"""
        indicators = [
            r'\b\d+\b',           # Numbers
            r'\b\d{4}\b',         # Years
            r'\$\d+',             # Prices
            r'\b\d+%\b',          # Percentages
            r'\b(?:is|are|was|were|will be)\s+\w+',  # Declarative statements
        ]
        # Skip questions and greetings
        if sentence.strip().endswith('?'):
            return False
        if any(g in sentence.lower() for g in ("hello", "hi ", "how can i help")):
            return False

        return any(re.search(p, sentence) for p in indicators)

    def _parse_json(self, text: str) -> Dict[str, Any]:
        """Parse JSON from LLM response, handling various formats."""
        import json
        text = text.strip()
        text = re.sub(r'^```(?:json)?\s*', '', text)
        text = re.sub(r'\s*```$', '', text)
        text = text.strip()

        try:
            return json.loads(text)
        except (json.JSONDecodeError, ValueError):
            pass

        match = re.search(r'\{[\s\S]*\}', text)
        if match:
            try:
                return json.loads(match.group(0))
            except (json.JSONDecodeError, ValueError):
                pass

        return {}
