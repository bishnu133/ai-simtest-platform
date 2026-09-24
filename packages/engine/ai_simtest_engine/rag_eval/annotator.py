"""
Response Annotator — Phase 2 of P3 #14 RAG/Tool Evaluation Framework.

Examines bot responses and extracts RAG/tool evidence across 3 evidence modes:

1. TRACE mode:  Parses OTel / OpenInference span data for retrieval + tool details.
2. STRUCTURED mode:  Extracts from bot response JSON metadata (sources, tool_calls).
3. INFERRED mode:  Uses heuristics + optional LLM to extract evidence from plain text.

The annotator auto-detects the evidence mode if not specified, based on what data
is available in the turn. It produces a ResponseAnnotation for each bot turn, which
downstream metric evaluators consume.

Key design decisions:
- Confidence scoring: trace=1.0, structured=0.7-0.9, inferred=0.3-0.7
- Graceful degradation: if trace parsing fails, falls back to structured → inferred
- Zero-dependency for deterministic mode (no LLM calls)
- LLM is optional — only used for inferred mode when available
"""

from __future__ import annotations

import json
import re
from typing import Any, Callable, Dict, List, Optional

from ai_simtest_engine.rag_eval.models import (
    Citation,
    EvidenceMode,
    ResponseAnnotation,
    RetrievedChunk,
    ToolCall,
)


class ResponseAnnotator:
    """
    Extracts RAG and tool evidence from bot responses.

    Usage:
        annotator = ResponseAnnotator(evidence_mode=EvidenceMode.INFERRED)
        annotation = await annotator.annotate(
            response_text="Based on our FAQ, the return policy is 30 days...",
            user_query="What is your return policy?",
            context_document="Return policy: 30 days for all items...",
            raw_metadata={}
        )
    """

    def __init__(
        self,
        evidence_mode: Optional[EvidenceMode] = None,
        llm_client: Optional[Any] = None,
    ):
        """
        Args:
            evidence_mode: Force a specific mode, or None for auto-detection.
            llm_client: Optional LLM client for inferred mode deep extraction.
                        Must have an async `generate(prompt: str) -> str` method.
        """
        self._forced_mode = evidence_mode
        self._llm_client = llm_client

    # ================================================================
    # Public API
    # ================================================================

    async def annotate(
        self,
        response_text: str,
        user_query: str = "",
        context_document: str = "",
        raw_metadata: Optional[Dict[str, Any]] = None,
        trace_spans: Optional[List[Dict[str, Any]]] = None,
        conversation_history: Optional[List[Dict[str, str]]] = None,
    ) -> ResponseAnnotation:
        """
        Annotate a single bot response with RAG/tool evidence.

        Args:
            response_text: The bot's response text.
            user_query: The user message that prompted this response.
            context_document: Ground-truth document text (for grounding checks).
            raw_metadata: Structured metadata from the bot response JSON.
            trace_spans: OTel/OpenInference span data (list of span dicts).
            conversation_history: Prior turns for context.

        Returns:
            ResponseAnnotation with extracted evidence.
        """
        metadata = raw_metadata or {}
        spans = trace_spans or []

        # Detect or use forced evidence mode
        mode = self._forced_mode or self._detect_mode(metadata, spans, response_text)

        # Extract evidence based on mode, with fallback chain
        annotation = await self._extract(
            mode=mode,
            response_text=response_text,
            user_query=user_query,
            context_document=context_document,
            metadata=metadata,
            spans=spans,
        )

        return annotation

    def annotate_sync(
        self,
        response_text: str,
        user_query: str = "",
        context_document: str = "",
        raw_metadata: Optional[Dict[str, Any]] = None,
        trace_spans: Optional[List[Dict[str, Any]]] = None,
    ) -> ResponseAnnotation:
        """
        Synchronous annotation — deterministic extraction only (no LLM).
        Useful for speed modes and testing.
        """
        metadata = raw_metadata or {}
        spans = trace_spans or []

        mode = self._forced_mode or self._detect_mode(metadata, spans, response_text)

        # Build annotation without LLM
        annotation = ResponseAnnotation(
            evidence_mode=mode,
            response_text=response_text,
            user_query=user_query,
            context_document=context_document,
        )

        if mode == EvidenceMode.TRACE:
            self._extract_from_traces(spans, annotation)
        elif mode == EvidenceMode.STRUCTURED:
            self._extract_from_structured(metadata, annotation)
        else:
            self._extract_from_text_heuristics(response_text, annotation)

        self._set_confidence(annotation)
        return annotation

    # ================================================================
    # Mode Detection
    # ================================================================

    def _detect_mode(
        self,
        metadata: Dict[str, Any],
        spans: List[Dict[str, Any]],
        response_text: str,
    ) -> EvidenceMode:
        """
        Auto-detect the best evidence mode based on available data.

        Priority: trace > structured > inferred.
        """
        # Check for trace data
        if spans and len(spans) > 0:
            # Validate at least one span has retrieval or tool info
            for span in spans:
                span_name = span.get("name", "").lower()
                attrs = span.get("attributes", {})
                if any(k in span_name for k in ("retriev", "tool", "function", "search", "query")):
                    return EvidenceMode.TRACE
                # OpenInference attributes
                if any(k.startswith(("retrieval.", "tool.", "openinference.")) for k in attrs):
                    return EvidenceMode.TRACE

        # Check for structured metadata
        structured_keys = {
            "sources", "references", "citations", "retrieved_documents",
            "tool_calls", "function_calls", "tools_used", "context_documents",
            "retrieval_results", "search_results",
        }
        if metadata and any(k in metadata for k in structured_keys):
            return EvidenceMode.STRUCTURED

        # Check if response JSON was embedded in text
        if self._looks_like_embedded_json(response_text):
            return EvidenceMode.STRUCTURED

        return EvidenceMode.INFERRED

    def _looks_like_embedded_json(self, text: str) -> bool:
        """Check if the response text contains embedded JSON with evidence keys."""
        try:
            # Try to find JSON block in text
            json_match = re.search(r'\{[^{}]*"(?:sources|tool_calls|citations)"[^{}]*\}', text)
            return json_match is not None
        except Exception:
            return False

    # ================================================================
    # Extraction: TRACE mode
    # ================================================================

    def _extract_from_traces(
        self,
        spans: List[Dict[str, Any]],
        annotation: ResponseAnnotation,
    ) -> None:
        """
        Extract RAG/tool evidence from OTel/OpenInference trace spans.

        Supported span patterns:
        - Retrieval spans: name contains "retriev", "search", "query"
          - attributes: retrieval.documents, retrieval.query
        - Tool spans: name contains "tool", "function"
          - attributes: tool.name, tool.parameters, tool.output
        - OpenInference format: openinference.span.kind = "RETRIEVER" or "TOOL"
        """
        for span in spans:
            span_name = span.get("name", "")
            attrs = span.get("attributes", {})
            span_kind = attrs.get("openinference.span.kind", "").upper()

            # --- Retrieval spans ---
            if self._is_retrieval_span(span_name, attrs, span_kind):
                self._extract_retrieval_from_span(attrs, annotation)

            # --- Tool/function spans ---
            if self._is_tool_span(span_name, attrs, span_kind):
                self._extract_tool_from_span(span_name, attrs, span, annotation)

    def _is_retrieval_span(
        self, name: str, attrs: Dict[str, Any], kind: str
    ) -> bool:
        name_lower = name.lower()
        return (
            kind == "RETRIEVER"
            or any(k in name_lower for k in ("retriev", "search", "query", "embed"))
            or any(k.startswith("retrieval.") for k in attrs)
        )

    def _is_tool_span(
        self, name: str, attrs: Dict[str, Any], kind: str
    ) -> bool:
        name_lower = name.lower()
        return (
            kind == "TOOL"
            or any(k in name_lower for k in ("tool", "function_call", "func_call"))
            or any(k.startswith("tool.") for k in attrs)
        )

    def _extract_retrieval_from_span(
        self, attrs: Dict[str, Any], annotation: ResponseAnnotation
    ) -> None:
        """Extract retrieved chunks from a retrieval span's attributes."""
        # OpenInference format: retrieval.documents is a list
        docs = attrs.get("retrieval.documents", [])
        if isinstance(docs, str):
            try:
                docs = json.loads(docs)
            except (json.JSONDecodeError, TypeError):
                docs = [{"content": docs}]

        if not isinstance(docs, list):
            docs = [docs] if docs else []

        for i, doc in enumerate(docs):
            if isinstance(doc, str):
                doc = {"content": doc}
            if not isinstance(doc, dict):
                continue

            chunk = RetrievedChunk(
                content=str(doc.get("content", doc.get("text", doc.get("page_content", "")))),
                source_id=str(doc.get("id", doc.get("source_id", doc.get("document_id", "")))),
                source_name=str(doc.get("source", doc.get("name", doc.get("title", "")))),
                relevance_score=float(doc.get("score", doc.get("relevance_score", doc.get("similarity", 0.0)))),
                chunk_index=i,
                metadata={k: v for k, v in doc.items() if k not in ("content", "text", "page_content")},
            )
            annotation.retrieved_chunks.append(chunk)

        if annotation.retrieved_chunks:
            annotation.has_rag_evidence = True

    def _extract_tool_from_span(
        self,
        span_name: str,
        attrs: Dict[str, Any],
        span: Dict[str, Any],
        annotation: ResponseAnnotation,
    ) -> None:
        """Extract a tool call from a tool/function span."""
        # Tool name: from attributes or span name
        tool_name = (
            attrs.get("tool.name")
            or attrs.get("function.name")
            or attrs.get("openinference.tool.name")
            or span_name
        )

        # Arguments
        raw_args = attrs.get("tool.parameters", attrs.get("tool.input", attrs.get("function.arguments", {})))
        if isinstance(raw_args, str):
            try:
                raw_args = json.loads(raw_args)
            except (json.JSONDecodeError, TypeError):
                raw_args = {"raw": raw_args}

        # Result
        raw_result = attrs.get("tool.output", attrs.get("tool.result", attrs.get("function.response")))
        if isinstance(raw_result, dict):
            raw_result = json.dumps(raw_result)

        # Error
        error = attrs.get("tool.error", attrs.get("error.message"))
        if span.get("status", {}).get("status_code") == "ERROR":
            error = error or span.get("status", {}).get("message", "Unknown error")

        # Duration
        duration_ms = None
        start = span.get("start_time_unix_nano") or span.get("startTimeUnixNano")
        end = span.get("end_time_unix_nano") or span.get("endTimeUnixNano")
        if start and end:
            try:
                duration_ms = (int(end) - int(start)) / 1_000_000
            except (ValueError, TypeError):
                pass

        tc = ToolCall(
            tool_name=str(tool_name),
            arguments=raw_args if isinstance(raw_args, dict) else {},
            result=str(raw_result) if raw_result is not None else None,
            error=str(error) if error else None,
            duration_ms=duration_ms,
            timestamp=span.get("start_time", span.get("timestamp")),
        )
        annotation.tool_calls.append(tc)
        annotation.has_tool_evidence = True

    # ================================================================
    # Extraction: STRUCTURED mode
    # ================================================================

    def _extract_from_structured(
        self,
        metadata: Dict[str, Any],
        annotation: ResponseAnnotation,
    ) -> None:
        """
        Extract evidence from structured bot response metadata.

        Supports common patterns:
        - OpenAI-style: {tool_calls: [{function: {name, arguments}}]}
        - LangChain-style: {source_documents: [{page_content, metadata}]}
        - Generic: {sources: [...], citations: [...], retrieved_documents: [...]}
        """
        # --- RAG evidence ---
        self._extract_structured_sources(metadata, annotation)
        self._extract_structured_citations(metadata, annotation)

        # --- Tool evidence ---
        self._extract_structured_tool_calls(metadata, annotation)

    def _extract_structured_sources(
        self, metadata: Dict[str, Any], annotation: ResponseAnnotation
    ) -> None:
        """Extract retrieved sources/documents from structured metadata."""
        # Try multiple common keys
        source_keys = [
            "sources", "references", "retrieved_documents",
            "source_documents", "context_documents",
            "retrieval_results", "search_results", "documents",
        ]

        sources = None
        for key in source_keys:
            if key in metadata:
                sources = metadata[key]
                break

        if sources is None:
            return

        if isinstance(sources, str):
            try:
                sources = json.loads(sources)
            except (json.JSONDecodeError, TypeError):
                sources = [{"content": sources}]

        if not isinstance(sources, list):
            sources = [sources]

        for i, src in enumerate(sources):
            if isinstance(src, str):
                src = {"content": src}
            if not isinstance(src, dict):
                continue

            chunk = RetrievedChunk(
                content=str(src.get("content", src.get("text", src.get("page_content", src.get("snippet", ""))))),
                source_id=str(src.get("id", src.get("source_id", src.get("document_id", "")))),
                source_name=str(src.get("source", src.get("name", src.get("title", src.get("url", ""))))),
                relevance_score=float(src.get("score", src.get("relevance_score", src.get("similarity", 0.0)))),
                chunk_index=i,
                metadata={k: v for k, v in src.items()
                          if k not in ("content", "text", "page_content", "snippet", "id", "source_id")},
            )
            annotation.retrieved_chunks.append(chunk)

        if annotation.retrieved_chunks:
            annotation.has_rag_evidence = True

    def _extract_structured_citations(
        self, metadata: Dict[str, Any], annotation: ResponseAnnotation
    ) -> None:
        """Extract citation/attribution info from structured metadata."""
        citations_raw = metadata.get("citations", metadata.get("attributions", []))

        if isinstance(citations_raw, str):
            try:
                citations_raw = json.loads(citations_raw)
            except (json.JSONDecodeError, TypeError):
                citations_raw = []

        if not isinstance(citations_raw, list):
            return

        for cit in citations_raw:
            if isinstance(cit, str):
                cit = {"text": cit}
            if not isinstance(cit, dict):
                continue

            annotation.citations.append(Citation(
                text=str(cit.get("text", cit.get("claim", cit.get("quote", "")))),
                source_reference=str(cit.get("source", cit.get("reference", cit.get("url", "")))),
                source_id=str(cit.get("source_id", cit.get("document_id", ""))),
                verified=cit.get("verified"),
                confidence=0.85,  # Structured citations are fairly reliable
            ))

        if annotation.citations:
            annotation.has_rag_evidence = True

    def _extract_structured_tool_calls(
        self, metadata: Dict[str, Any], annotation: ResponseAnnotation
    ) -> None:
        """Extract tool/function calls from structured metadata."""
        # OpenAI-style tool_calls
        tool_calls = metadata.get("tool_calls", metadata.get("function_calls", metadata.get("tools_used", [])))

        if isinstance(tool_calls, str):
            try:
                tool_calls = json.loads(tool_calls)
            except (json.JSONDecodeError, TypeError):
                tool_calls = []

        if not isinstance(tool_calls, list):
            tool_calls = [tool_calls] if tool_calls else []

        for tc_raw in tool_calls:
            if isinstance(tc_raw, str):
                tc_raw = {"name": tc_raw}
            if not isinstance(tc_raw, dict):
                continue

            # OpenAI format: {function: {name: ..., arguments: ...}}
            func_obj = tc_raw.get("function", {})
            if func_obj and isinstance(func_obj, dict):
                name = func_obj.get("name", "")
                args_raw = func_obj.get("arguments", {})
            else:
                name = tc_raw.get("name", tc_raw.get("tool_name", tc_raw.get("function_name", "")))
                args_raw = tc_raw.get("arguments", tc_raw.get("parameters", tc_raw.get("input", {})))

            # Parse arguments if string
            if isinstance(args_raw, str):
                try:
                    args_raw = json.loads(args_raw)
                except (json.JSONDecodeError, TypeError):
                    args_raw = {"raw": args_raw}

            tc = ToolCall(
                tool_name=str(name),
                arguments=args_raw if isinstance(args_raw, dict) else {},
                result=str(tc_raw.get("result", tc_raw.get("output", ""))) or None,
                error=str(tc_raw.get("error", "")) or None,
                duration_ms=tc_raw.get("duration_ms"),
            )
            # Only set result/error if non-empty
            if tc.result == "" or tc.result == "None":
                tc.result = None
            if tc.error == "" or tc.error == "None":
                tc.error = None

            annotation.tool_calls.append(tc)

        if annotation.tool_calls:
            annotation.has_tool_evidence = True

    # ================================================================
    # Extraction: INFERRED mode (heuristics + optional LLM)
    # ================================================================

    async def _extract_from_inferred(
        self,
        response_text: str,
        user_query: str,
        annotation: ResponseAnnotation,
    ) -> None:
        """
        Infer RAG/tool evidence from plain bot response text.

        Heuristic extraction first, then optional LLM for deeper analysis.
        """
        # Phase 1: Heuristic extraction (always runs)
        self._extract_from_text_heuristics(response_text, annotation)

        # Phase 2: LLM extraction (only if client available and text has signals)
        if self._llm_client and self._has_extraction_signals(response_text):
            await self._extract_with_llm(response_text, user_query, annotation)

    def _extract_from_text_heuristics(
        self,
        response_text: str,
        annotation: ResponseAnnotation,
    ) -> None:
        """
        Rule-based extraction of citations, sources, and tool references.

        Patterns detected:
        - "According to [source]..."
        - "[1], [2], [source name]" citation markers
        - "Based on the document/FAQ/policy..."
        - "I called/used the X tool/function..."
        - URL references
        - "Source: ...", "Reference: ..."
        """
        # --- Citation patterns ---
        self._extract_citation_patterns(response_text, annotation)

        # --- Source references ---
        self._extract_source_references(response_text, annotation)

        # --- Tool mention patterns ---
        self._extract_tool_mentions(response_text, annotation)

    def _extract_citation_patterns(
        self, text: str, annotation: ResponseAnnotation
    ) -> None:
        """Extract citation markers from text."""
        patterns = [
            # [1], [2], [3] style
            (r'\[(\d+)\]', "numeric_citation"),
            # [Source Name] style
            (r'\[([A-Z][^[\]]{2,50})\]', "named_citation"),
            # (Source, Year) academic style
            (r'\(([A-Z][a-zA-Z]+(?:\s+(?:et\s+al\.?|and\s+[A-Z][a-zA-Z]+))?,?\s*\d{4})\)', "academic_citation"),
        ]

        seen = set()
        for pattern, cit_type in patterns:
            for match in re.finditer(pattern, text):
                ref = match.group(1)
                if ref in seen:
                    continue
                seen.add(ref)

                # Get surrounding text as the cited claim
                start = max(0, match.start() - 100)
                end = min(len(text), match.end() + 50)
                context_text = text[start:end].strip()

                # Find the sentence containing the citation
                sentence = self._extract_containing_sentence(text, match.start())

                annotation.citations.append(Citation(
                    text=sentence or context_text,
                    source_reference=ref,
                    confidence=0.5,  # Inferred, lower confidence
                ))

        if annotation.citations:
            annotation.has_rag_evidence = True

    def _extract_source_references(
        self, text: str, annotation: ResponseAnnotation
    ) -> None:
        """Extract source references like 'According to...', 'Based on...'."""
        patterns = [
            # "According to X, ..."
            r'[Aa]ccording to (?:the |our |your )?([^,.\n]{3,60})',
            # "Based on X, ..."
            r'[Bb]ased on (?:the |our |your )?([^,.\n]{3,60})',
            # "As stated in X, ..."
            r'[Aa]s (?:stated|mentioned|described|noted|outlined) in (?:the |our |your )?([^,.\n]{3,60})',
            # "Source: X"
            r'[Ss]ource:\s*([^\n]{3,80})',
            # "Reference: X"
            r'[Rr]eference:\s*([^\n]{3,80})',
            # "Per the X..."
            r'[Pp]er (?:the |our |your )?([^,.\n]{3,60})',
        ]

        for pattern in patterns:
            for match in re.finditer(pattern, text):
                source_name = match.group(1).strip().rstrip(".,;:")
                if len(source_name) < 3:
                    continue

                # Avoid duplicates
                existing_names = {c.source_reference for c in annotation.citations}
                if source_name in existing_names:
                    continue

                sentence = self._extract_containing_sentence(text, match.start())

                annotation.citations.append(Citation(
                    text=sentence or match.group(0),
                    source_reference=source_name,
                    confidence=0.4,
                ))

        # URL detection
        url_pattern = r'https?://[^\s<>"\')]+[^\s<>"\').,;]'
        for match in re.finditer(url_pattern, text):
            url = match.group(0)
            existing_refs = {c.source_reference for c in annotation.citations}
            if url not in existing_refs:
                annotation.citations.append(Citation(
                    text=self._extract_containing_sentence(text, match.start()) or url,
                    source_reference=url,
                    confidence=0.6,  # URLs are fairly explicit
                ))

        if annotation.citations:
            annotation.has_rag_evidence = True

    def _extract_tool_mentions(
        self, text: str, annotation: ResponseAnnotation
    ) -> None:
        """Extract tool/function call mentions from natural language text."""
        patterns = [
            # "I called/used the X tool/function/API"
            r'(?:called|used|invoked|executed|ran|queried)\s+(?:the\s+)?["\']?(\w[\w\s]{1,30}?)["\']?\s+(?:tool|function|api|service|endpoint)',
            # "using the X tool"
            r'using\s+(?:the\s+)?["\']?(\w[\w\s]{1,30}?)["\']?\s+(?:tool|function|api|service)',
            # "the X function returned..."
            r'(?:the\s+)?["\']?(\w[\w_]{1,30}?)["\']?\s+(?:function|tool|api)\s+(?:returned|responded|gave|provided)',
            # code-style: function_name() or FunctionName()
            r'`(\w{2,30})\(\)`',
            # "looked up", "searched for", "retrieved" (RAG-tool hybrid)
            r'(?:looked up|searched for|retrieved|fetched|queried)\s+(.{3,50?})\s+(?:from|in|using)',
        ]

        seen_tools = set()
        for pattern in patterns:
            for match in re.finditer(pattern, text, re.IGNORECASE):
                tool_name = match.group(1).strip().rstrip(".,;:")
                if tool_name.lower() in seen_tools:
                    continue
                if len(tool_name) < 2 or len(tool_name) > 40:
                    continue
                seen_tools.add(tool_name.lower())

                annotation.tool_calls.append(ToolCall(
                    tool_name=tool_name,
                    metadata={"extraction": "heuristic", "pattern": pattern[:50]},
                ))

        if annotation.tool_calls:
            annotation.has_tool_evidence = True

    async def _extract_with_llm(
        self,
        response_text: str,
        user_query: str,
        annotation: ResponseAnnotation,
    ) -> None:
        """
        Use LLM to extract deeper RAG/tool evidence from natural language.

        Only called when:
        1. LLM client is available
        2. Response has signals suggesting RAG/tool usage
        """
        prompt = self._build_extraction_prompt(response_text, user_query)

        try:
            llm_response = await self._llm_client.generate(prompt)
            parsed = self._parse_llm_extraction(llm_response)

            # Merge LLM-extracted evidence (higher confidence than pure heuristic)
            if parsed.get("citations"):
                for cit in parsed["citations"]:
                    # Avoid duplicates
                    existing_texts = {c.text[:50] for c in annotation.citations}
                    if cit.get("text", "")[:50] not in existing_texts:
                        annotation.citations.append(Citation(
                            text=cit.get("text", ""),
                            source_reference=cit.get("source", ""),
                            confidence=0.6,  # LLM-inferred
                        ))
                        annotation.has_rag_evidence = True

            if parsed.get("tool_calls"):
                for tc in parsed["tool_calls"]:
                    existing_tools = {t.tool_name.lower() for t in annotation.tool_calls}
                    name = tc.get("name", tc.get("tool_name", ""))
                    if name.lower() not in existing_tools:
                        annotation.tool_calls.append(ToolCall(
                            tool_name=name,
                            arguments=tc.get("arguments", {}),
                            metadata={"extraction": "llm"},
                        ))
                        annotation.has_tool_evidence = True

            if parsed.get("sources"):
                for src in parsed["sources"]:
                    annotation.retrieved_chunks.append(RetrievedChunk(
                        content=src.get("content", ""),
                        source_name=src.get("name", ""),
                        metadata={"extraction": "llm"},
                    ))
                    annotation.has_rag_evidence = True

            annotation.extraction_metadata["llm_extraction"] = True

        except Exception as e:
            annotation.warnings.append(f"LLM extraction failed: {str(e)[:100]}")
            annotation.extraction_metadata["llm_extraction_error"] = str(e)[:200]

    def _has_extraction_signals(self, text: str) -> bool:
        """Check if text likely contains RAG/tool evidence worth LLM analysis."""
        signals = [
            "according to", "based on", "source:", "reference:",
            "retrieved", "looked up", "searched", "tool", "function",
            "api", "[1]", "[2]", "as stated in", "per the",
            "documentation", "knowledge base", "FAQ",
        ]
        text_lower = text.lower()
        return any(s in text_lower for s in signals)

    def _build_extraction_prompt(self, response_text: str, user_query: str) -> str:
        """Build the LLM prompt for evidence extraction."""
        return f"""Analyze this bot response and extract any evidence of:
1. Retrieved documents/sources (RAG): citations, references, source mentions
2. Tool/function calls: any tools the bot used to generate this response

User question: {user_query}

Bot response:
{response_text[:2000]}

Respond ONLY with valid JSON (no markdown, no backticks):
{{
  "citations": [
    {{"text": "the claimed text", "source": "source name or reference"}}
  ],
  "tool_calls": [
    {{"name": "tool_name", "arguments": {{}}}}
  ],
  "sources": [
    {{"name": "source name", "content": "what was retrieved"}}
  ],
  "has_rag": true,
  "has_tools": false
}}

If no evidence found, return empty arrays. Be conservative — only extract what is clearly stated."""

    def _parse_llm_extraction(self, llm_response: str) -> Dict[str, Any]:
        """Parse LLM extraction response, handling various formats."""
        # Strip markdown code fences
        text = llm_response.strip()
        text = re.sub(r'^```(?:json)?\s*', '', text)
        text = re.sub(r'\s*```$', '', text)
        text = text.strip()

        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass

        # Try extracting JSON from mixed text
        match = re.search(r'\{[\s\S]*\}', text)
        if match:
            try:
                return json.loads(match.group(0))
            except json.JSONDecodeError:
                pass

        return {"citations": [], "tool_calls": [], "sources": []}

    # ================================================================
    # Helpers
    # ================================================================

    def _extract_containing_sentence(self, text: str, position: int) -> str:
        """Extract the sentence containing the given position."""
        # Find sentence boundaries around position
        sentence_ends = [m.end() for m in re.finditer(r'[.!?]\s', text)]
        sentence_ends.append(len(text))

        start = 0
        end = len(text)

        for se in sentence_ends:
            if se <= position:
                start = se
            elif se > position:
                end = se
                break

        sentence = text[start:end].strip()
        # Cap length
        if len(sentence) > 200:
            sentence = sentence[:200] + "..."
        return sentence

    def _set_confidence(self, annotation: ResponseAnnotation) -> None:
        """Set overall confidence based on evidence mode."""
        if annotation.evidence_mode == EvidenceMode.TRACE:
            annotation.overall_confidence = 1.0
        elif annotation.evidence_mode == EvidenceMode.STRUCTURED:
            # Structured is reliable if data is well-formed
            has_content = (annotation.has_rag_evidence or annotation.has_tool_evidence)
            annotation.overall_confidence = 0.85 if has_content else 0.7
        else:
            # Inferred — depends on what was found
            if annotation.has_rag_evidence and annotation.has_tool_evidence:
                annotation.overall_confidence = 0.5
            elif annotation.has_rag_evidence or annotation.has_tool_evidence:
                annotation.overall_confidence = 0.4
            else:
                annotation.overall_confidence = 0.3

        # Boost confidence if LLM was used
        if annotation.extraction_metadata.get("llm_extraction"):
            annotation.overall_confidence = min(1.0, annotation.overall_confidence + 0.15)

    async def _extract(
        self,
        mode: EvidenceMode,
        response_text: str,
        user_query: str,
        context_document: str,
        metadata: Dict[str, Any],
        spans: List[Dict[str, Any]],
    ) -> ResponseAnnotation:
        """
        Main extraction dispatcher with fallback chain.

        trace → structured → inferred
        If a mode produces no evidence, tries the next mode down.
        """
        annotation = ResponseAnnotation(
            evidence_mode=mode,
            response_text=response_text,
            user_query=user_query,
            context_document=context_document,
        )

        if mode == EvidenceMode.TRACE:
            self._extract_from_traces(spans, annotation)
            # Fallback: also check structured metadata
            if not annotation.has_rag_evidence and not annotation.has_tool_evidence:
                if metadata:
                    self._extract_from_structured(metadata, annotation)
                    if annotation.has_rag_evidence or annotation.has_tool_evidence:
                        annotation.warnings.append("Trace spans empty; fell back to structured metadata")
                        annotation.evidence_mode = EvidenceMode.STRUCTURED

        elif mode == EvidenceMode.STRUCTURED:
            self._extract_from_structured(metadata, annotation)

        else:  # INFERRED
            await self._extract_from_inferred(response_text, user_query, annotation)

        # If still nothing found in non-inferred modes, try heuristic extraction
        if mode != EvidenceMode.INFERRED:
            if not annotation.has_rag_evidence and not annotation.has_tool_evidence:
                self._extract_from_text_heuristics(response_text, annotation)
                if annotation.has_rag_evidence or annotation.has_tool_evidence:
                    annotation.warnings.append(
                        f"No evidence from {mode.value} mode; fell back to text heuristics"
                    )

        self._set_confidence(annotation)
        return annotation
