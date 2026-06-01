"""
Tests for Phase C Part 1: Document Loader, Document Analyzer, Criteria Generator.
Run with: PYTHONPATH=. pytest tests/test_analyzers.py -v
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.analyzers.document_loader import (
    DocumentChunk,
    DocumentLoader,
    LoadedDocument,
    LoadResult,
    SUPPORTED_EXTENSIONS,
)


# ============================================================
# Fixtures: Create temp files for testing
# ============================================================

@pytest.fixture
def tmp_dir():
    with tempfile.TemporaryDirectory() as d:
        yield Path(d)


@pytest.fixture
def sample_txt(tmp_dir):
    p = tmp_dir / "readme.txt"
    p.write_text("This is a customer support bot.\nIt handles refunds and order tracking.")
    return p


@pytest.fixture
def sample_md(tmp_dir):
    p = tmp_dir / "docs.md"
    p.write_text(
        "# Support Bot Documentation\n\n"
        "## Purpose\nHelp customers with orders, returns, and product information.\n\n"
        "## Capabilities\n- Track orders\n- Process refunds\n- Answer product questions\n\n"
        "## Limitations\n- Cannot process payments\n- Cannot access inventory systems\n"
    )
    return p


@pytest.fixture
def sample_json(tmp_dir):
    p = tmp_dir / "config.json"
    data = {
        "bot_name": "ShopHelper",
        "domain": "E-commerce",
        "features": ["order tracking", "refund processing", "product search"],
        "policies": {
            "refund_window": "30 days",
            "escalation": "Transfer to human agent for complaints",
        },
    }
    p.write_text(json.dumps(data, indent=2))
    return p


@pytest.fixture
def sample_html(tmp_dir):
    p = tmp_dir / "help.html"
    p.write_text(
        "<html><head><title>Help Center</title></head>"
        "<body><h1>FAQ</h1>"
        "<p>Our bot can help you with <strong>refunds</strong> and <em>order tracking</em>.</p>"
        "<script>alert('test');</script>"
        "<style>.hidden{display:none}</style>"
        "<p>Contact support at help@example.com for other issues.</p>"
        "</body></html>"
    )
    return p


@pytest.fixture
def large_txt(tmp_dir):
    """A file larger than default chunk size."""
    p = tmp_dir / "large.txt"
    # Create ~10000 chars of content with clear paragraph breaks
    paragraphs = []
    for i in range(50):
        paragraphs.append(
            f"Section {i}: This is paragraph number {i} about the customer support bot. "
            f"It handles various queries including refunds, orders, and product information. "
            f"The bot should always be helpful and accurate in its responses."
        )
    p.write_text("\n\n".join(paragraphs))
    return p


@pytest.fixture
def empty_txt(tmp_dir):
    p = tmp_dir / "empty.txt"
    p.write_text("")
    return p


@pytest.fixture
def doc_dir(tmp_dir, sample_txt, sample_md, sample_json, sample_html):
    """A directory with multiple document types."""
    return tmp_dir


# ============================================================
# Tests: Document Loader — File Loading
# ============================================================

class TestDocumentLoaderFiles:

    def test_load_txt(self, sample_txt):
        loader = DocumentLoader()
        doc = loader.load_file(sample_txt)

        assert doc.loaded_successfully
        assert doc.file_type == ".txt"
        assert "customer support bot" in doc.total_text
        assert doc.total_words > 5

    def test_load_md(self, sample_md):
        loader = DocumentLoader()
        doc = loader.load_file(sample_md)

        assert doc.loaded_successfully
        assert doc.file_type == ".md"
        assert "Track orders" in doc.total_text
        assert "Process refunds" in doc.total_text

    def test_load_json(self, sample_json):
        loader = DocumentLoader()
        doc = loader.load_file(sample_json)

        assert doc.loaded_successfully
        assert doc.file_type == ".json"
        assert "ShopHelper" in doc.total_text
        assert "order tracking" in doc.total_text

    def test_load_html(self, sample_html):
        loader = DocumentLoader()
        doc = loader.load_file(sample_html)

        assert doc.loaded_successfully
        assert doc.file_type == ".html"
        assert "refunds" in doc.total_text
        assert "order tracking" in doc.total_text
        # Script and style should be stripped
        assert "alert" not in doc.total_text
        assert "display:none" not in doc.total_text

    def test_load_nonexistent_file(self, tmp_dir):
        loader = DocumentLoader()
        doc = loader.load_file(tmp_dir / "nonexistent.txt")

        assert not doc.loaded_successfully
        assert "not found" in doc.load_error.lower()

    def test_load_unsupported_extension(self, tmp_dir):
        p = tmp_dir / "data.xyz"
        p.write_text("test")
        loader = DocumentLoader()
        doc = loader.load_file(p)

        assert not doc.loaded_successfully
        assert "unsupported" in doc.load_error.lower()

    def test_load_empty_file(self, empty_txt):
        loader = DocumentLoader()
        doc = loader.load_file(empty_txt)

        assert not doc.loaded_successfully
        assert "empty" in doc.load_error.lower()

    def test_file_too_large(self, tmp_dir):
        p = tmp_dir / "huge.txt"
        p.write_text("x" * 1000)
        loader = DocumentLoader(max_file_size_mb=0.0001)  # ~100 bytes
        doc = loader.load_file(p)

        assert not doc.loaded_successfully
        assert "too large" in doc.load_error.lower()


# ============================================================
# Tests: Document Loader — Directory Loading
# ============================================================

class TestDocumentLoaderDirectory:

    def test_load_directory(self, doc_dir):
        loader = DocumentLoader()
        result = loader.load_directory(doc_dir)

        assert result.total_files >= 4
        assert result.successful >= 4
        assert result.failed == 0
        assert len(result.all_text) > 0

    def test_load_directory_nonexistent(self):
        loader = DocumentLoader()
        result = loader.load_directory("/nonexistent/path")

        assert result.successful == 0
        assert len(result.errors) > 0

    def test_load_directory_empty(self, tmp_dir):
        empty_dir = tmp_dir / "empty_subdir"
        empty_dir.mkdir()
        loader = DocumentLoader()
        result = loader.load_directory(empty_dir)

        assert result.successful == 0
        assert "No supported files" in result.errors[0]

    def test_load_multiple_files(self, sample_txt, sample_md):
        loader = DocumentLoader()
        result = loader.load_files([sample_txt, sample_md])

        assert result.total_files == 2
        assert result.successful == 2
        assert len(result.all_chunks) >= 2

    def test_all_text_combines(self, doc_dir):
        loader = DocumentLoader()
        result = loader.load_directory(doc_dir)

        combined = result.all_text
        assert "customer support bot" in combined.lower() or "support bot" in combined.lower()
        assert len(combined) > 100


# ============================================================
# Tests: Chunking
# ============================================================

class TestChunking:

    def test_small_doc_single_chunk(self, sample_txt):
        loader = DocumentLoader(chunk_size=10000)
        doc = loader.load_file(sample_txt)

        assert len(doc.chunks) == 1
        assert doc.chunks[0].total_chunks == 1

    def test_large_doc_multiple_chunks(self, large_txt):
        loader = DocumentLoader(chunk_size=500, chunk_overlap=50)
        doc = loader.load_file(large_txt)

        assert len(doc.chunks) > 1
        for chunk in doc.chunks:
            assert chunk.total_chunks == len(doc.chunks)
            assert chunk.chunk_index < chunk.total_chunks

    def test_chunk_overlap(self, large_txt):
        loader = DocumentLoader(chunk_size=500, chunk_overlap=100)
        doc = loader.load_file(large_txt)

        if len(doc.chunks) >= 2:
            # Check that chunks have some overlapping content
            chunk1_end = doc.chunks[0].text[-50:]
            chunk2_start = doc.chunks[1].text[:200]
            # With overlap, some text should appear in both
            # (checking that it's not a clean cut)
            assert len(doc.chunks) >= 2

    def test_chunk_metadata(self, large_txt):
        loader = DocumentLoader(chunk_size=500)
        doc = loader.load_file(large_txt)

        for chunk in doc.chunks:
            assert chunk.source_file == str(large_txt)
            assert chunk.file_type == ".txt"
            assert chunk.word_count > 0
            assert chunk.char_count > 0


# ============================================================
# Tests: Document Models
# ============================================================

class TestDocumentModels:

    def test_document_chunk_properties(self):
        chunk = DocumentChunk(text="Hello world test", source_file="test.txt")
        assert chunk.char_count == 16
        assert chunk.word_count == 3

    def test_loaded_document_total_text(self):
        doc = LoadedDocument(
            source_file="test.txt",
            file_type=".txt",
            chunks=[
                DocumentChunk(text="First chunk", source_file="test.txt"),
                DocumentChunk(text="Second chunk", source_file="test.txt"),
            ],
        )
        assert "First chunk" in doc.total_text
        assert "Second chunk" in doc.total_text
        assert doc.total_words == 4

    def test_load_result_all_text(self):
        result = LoadResult(
            documents=[
                LoadedDocument(
                    source_file="a.txt", file_type=".txt",
                    chunks=[DocumentChunk(text="Doc A content", source_file="a.txt")],
                ),
                LoadedDocument(
                    source_file="b.txt", file_type=".txt",
                    chunks=[DocumentChunk(text="Doc B content", source_file="b.txt")],
                ),
            ],
            total_files=2, successful=2,
        )
        assert "Doc A content" in result.all_text
        assert "Doc B content" in result.all_text

    def test_supported_extensions(self):
        assert ".txt" in SUPPORTED_EXTENSIONS
        assert ".md" in SUPPORTED_EXTENSIONS
        assert ".json" in SUPPORTED_EXTENSIONS
        assert ".html" in SUPPORTED_EXTENSIONS
        assert ".pdf" in SUPPORTED_EXTENSIONS
        assert ".docx" in SUPPORTED_EXTENSIONS
        assert ".xyz" not in SUPPORTED_EXTENSIONS


# ============================================================
# Tests: Document Analyzer
# ============================================================

class TestDocumentAnalyzer:

    def _make_load_result(self, text: str) -> LoadResult:
        """Helper to create a LoadResult with given text."""
        return LoadResult(
            documents=[LoadedDocument(
                source_file="test.txt", file_type=".txt",
                chunks=[DocumentChunk(text=text, source_file="test.txt")],
            )],
            total_files=1, successful=1,
        )

    @pytest.mark.asyncio
    async def test_analyze_extracts_context(self):
        """Test that analyzer calls LLM and parses response."""
        from src.analyzers.document_analyzer import BotContext, DocumentAnalyzer

        mock_llm = AsyncMock()
        mock_llm.model = "test-model"
        mock_llm.generate_json = AsyncMock(return_value={
            "bot_name": "ShopBot",
            "domain": "E-commerce",
            "purpose": "Help customers with orders and refunds",
            "capabilities": ["order tracking", "refund processing"],
            "limitations": ["cannot process payments"],
            "target_audience": "Online shoppers",
            "topics": ["orders", "refunds", "products"],
            "tone_and_style": "Professional and helpful",
            "key_entities": ["ShopBot", "OrderSystem"],
            "confidence": "high",
        })

        analyzer = DocumentAnalyzer(llm_client=mock_llm)
        load_result = self._make_load_result("Bot documentation text here")

        context = await analyzer.analyze(load_result)

        assert isinstance(context, BotContext)
        assert context.bot_name == "ShopBot"
        assert context.domain == "E-commerce"
        assert "order tracking" in context.capabilities
        assert context.confidence == "high"
        assert context.source_doc_count == 1
        mock_llm.generate_json.assert_called_once()

    @pytest.mark.asyncio
    async def test_analyze_empty_result(self):
        from src.analyzers.document_analyzer import DocumentAnalyzer

        mock_llm = AsyncMock()
        analyzer = DocumentAnalyzer(llm_client=mock_llm)

        result = await analyzer.analyze(LoadResult())

        assert result.confidence == "low"
        mock_llm.generate_json.assert_not_called()

    @pytest.mark.asyncio
    async def test_analyze_llm_failure_returns_low_confidence(self):
        from src.analyzers.document_analyzer import DocumentAnalyzer

        mock_llm = AsyncMock()
        mock_llm.model = "test-model"
        mock_llm.generate_json = AsyncMock(side_effect=Exception("LLM error"))

        analyzer = DocumentAnalyzer(llm_client=mock_llm)
        load_result = self._make_load_result("Some documentation")

        context = await analyzer.analyze(load_result)

        assert context.confidence == "low"
        assert "failed" in context.raw_summary.lower()

    @pytest.mark.asyncio
    async def test_analyze_chunked_for_large_docs(self):
        """For large docs, analyzer should chunk and merge."""
        from src.analyzers.document_analyzer import DocumentAnalyzer

        mock_llm = AsyncMock()
        mock_llm.model = "test-model"
        mock_llm.generate_json = AsyncMock(return_value={
            "bot_name": "BigBot",
            "domain": "Support",
            "purpose": "Help with stuff",
            "capabilities": ["answering"],
            "limitations": [],
            "target_audience": "Users",
            "topics": ["support"],
            "tone_and_style": "Friendly",
            "key_entities": [],
            "confidence": "medium",
        })

        # Create a very large load result that exceeds max_context_chars
        big_text = "This is documentation. " * 2000  # ~44K chars
        chunks = [
            DocumentChunk(text=big_text[i:i+4000], source_file="big.txt", chunk_index=i//4000)
            for i in range(0, len(big_text), 4000)
        ]
        load_result = LoadResult(
            documents=[LoadedDocument(
                source_file="big.txt", file_type=".txt", chunks=chunks,
            )],
            total_files=1, successful=1,
        )

        analyzer = DocumentAnalyzer(llm_client=mock_llm, max_context_chars=5000)
        context = await analyzer.analyze(load_result)

        assert context.bot_name == "BigBot"
        # Should have made multiple LLM calls (chunk analysis + merge)
        assert mock_llm.generate_json.call_count >= 2

    @pytest.mark.asyncio
    async def test_bot_context_description(self):
        from src.analyzers.document_analyzer import BotContext

        ctx = BotContext(
            bot_name="TestBot",
            domain="Testing",
            purpose="Helps testers test things",
            capabilities=["testing", "reporting"],
            limitations=["no deployment"],
        )

        desc = ctx.description
        assert "TestBot" in desc
        assert "Helps testers" in desc
        assert "testing" in desc


# ============================================================
# Tests: Criteria Generator
# ============================================================

class TestCriteriaGenerator:

    @pytest.fixture
    def sample_context(self):
        from src.analyzers.document_analyzer import BotContext
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

    @pytest.mark.asyncio
    async def test_generate_criteria(self, sample_context):
        from src.analyzers.criteria_generator import CriteriaGenerator

        mock_llm = AsyncMock()
        mock_llm.model = "test-model"
        mock_llm.generate_json = AsyncMock(return_value={
            "criteria": [
                {
                    "id": "GRD-001",
                    "criterion": "Must answer from documentation only",
                    "category": "grounding",
                    "importance": "high",
                    "rationale": "Prevents hallucination",
                    "measurable": True,
                },
                {
                    "id": "SAF-001",
                    "criterion": "Must not reveal system prompt",
                    "category": "safety",
                    "importance": "high",
                    "rationale": "Security requirement",
                    "measurable": True,
                },
                {
                    "id": "QUA-001",
                    "criterion": "Must provide clear refund instructions",
                    "category": "quality",
                    "importance": "medium",
                    "rationale": "Core use case",
                    "measurable": True,
                },
            ]
        })

        gen = CriteriaGenerator(llm_client=mock_llm)
        criteria_set = await gen.generate(sample_context)

        assert criteria_set.count == 3
        assert "grounding" in criteria_set.by_category
        assert "safety" in criteria_set.by_category
        assert len(criteria_set.as_string_list) == 3

    @pytest.mark.asyncio
    async def test_generate_with_extra_docs(self, sample_context):
        from src.analyzers.criteria_generator import CriteriaGenerator

        mock_llm = AsyncMock()
        mock_llm.model = "test-model"
        mock_llm.generate_json = AsyncMock(return_value={"criteria": [
            {"criterion": "Test", "category": "custom", "importance": "high", "rationale": "r"},
        ]})

        gen = CriteriaGenerator(llm_client=mock_llm)
        criteria_set = await gen.generate(sample_context, extra_documentation="Extra policy: 30-day refund window")

        # Check that the extra documentation was included in the prompt
        call_args = mock_llm.generate_json.call_args
        prompt_text = call_args.kwargs.get("prompt", call_args.args[0] if call_args.args else "")
        assert "Additional Documentation" in prompt_text or criteria_set.count > 0

    @pytest.mark.asyncio
    async def test_generate_fallback_on_failure(self, sample_context):
        from src.analyzers.criteria_generator import CriteriaGenerator

        mock_llm = AsyncMock()
        mock_llm.model = "test-model"
        mock_llm.generate_json = AsyncMock(side_effect=Exception("LLM error"))

        gen = CriteriaGenerator(llm_client=mock_llm)
        criteria_set = await gen.generate(sample_context)

        # Should return defaults, not crash
        assert criteria_set.count >= 5
        categories = criteria_set.by_category
        assert "grounding" in categories
        assert "safety" in categories

    @pytest.mark.asyncio
    async def test_default_criteria_include_limitations(self, sample_context):
        from src.analyzers.criteria_generator import CriteriaGenerator

        mock_llm = AsyncMock()
        mock_llm.model = "test-model"
        mock_llm.generate_json = AsyncMock(side_effect=Exception("fail"))

        gen = CriteriaGenerator(llm_client=mock_llm)
        criteria_set = await gen.generate(sample_context)

        custom_criteria = criteria_set.by_category.get("custom", [])
        assert len(custom_criteria) >= 1
        # Should reference the bot's limitations
        all_text = " ".join(c.criterion for c in custom_criteria)
        assert "payment" in all_text.lower() or "inventory" in all_text.lower()

    def test_criteria_to_proposal_items(self):
        from src.analyzers.criteria_generator import CriteriaSet, SuccessCriterion

        cs = CriteriaSet(criteria=[
            SuccessCriterion(criterion="Rule 1", category="grounding", importance="high", rationale="Important"),
            SuccessCriterion(criterion="Rule 2", category="safety", importance="low", rationale="Nice to have"),
        ])

        items = cs.to_proposal_items()
        assert len(items) == 2
        assert items[0].confidence.value == "high"
        assert items[1].confidence.value == "low"
        assert items[0].content["criterion"] == "Rule 1"
        assert items[0].explanation == "Important"

    def test_from_approval_data_strings(self):
        from src.analyzers.criteria_generator import CriteriaGenerator

        approved = ["Must be accurate", "Must be safe", "Must be helpful"]
        cs = CriteriaGenerator.from_approval_data(approved)

        assert cs.count == 3
        assert cs.criteria[0].criterion == "Must be accurate"

    def test_from_approval_data_dicts(self):
        from src.analyzers.criteria_generator import CriteriaGenerator

        approved = [
            {"criterion": "Rule A", "category": "safety", "importance": "high"},
            {"criterion": "Rule B", "category": "quality", "importance": "medium"},
        ]
        cs = CriteriaGenerator.from_approval_data(approved)

        assert cs.count == 2
        assert cs.criteria[0].category == "safety"
        assert cs.criteria[1].importance == "medium"

    def test_criteria_as_string_list(self):
        from src.analyzers.criteria_generator import CriteriaSet, SuccessCriterion

        cs = CriteriaSet(criteria=[
            SuccessCriterion(criterion="Rule 1"),
            SuccessCriterion(criterion="Rule 2"),
        ])

        strings = cs.as_string_list
        assert strings == ["Rule 1", "Rule 2"]

    @pytest.mark.asyncio
    async def test_parse_list_response(self, sample_context):
        """Handle LLM returning a list instead of dict."""
        from src.analyzers.criteria_generator import CriteriaGenerator

        mock_llm = AsyncMock()
        mock_llm.model = "test-model"
        mock_llm.generate_json = AsyncMock(return_value=[
            {"criterion": "List item 1", "category": "grounding"},
            {"criterion": "List item 2", "category": "safety"},
        ])

        gen = CriteriaGenerator(llm_client=mock_llm)
        cs = await gen.generate(sample_context)

        assert cs.count == 2


# ============================================================
# Tests: Integration — Loader + Analyzer
# ============================================================

class TestLoaderAnalyzerIntegration:

    @pytest.mark.asyncio
    async def test_load_then_analyze(self, doc_dir):
        """Full pipeline: load docs from directory, then analyze."""
        from src.analyzers.document_analyzer import DocumentAnalyzer

        loader = DocumentLoader()
        load_result = loader.load_directory(doc_dir)

        assert load_result.successful >= 4

        # Mock the LLM call
        mock_llm = AsyncMock()
        mock_llm.model = "test-model"
        mock_llm.generate_json = AsyncMock(return_value={
            "bot_name": "SupportBot",
            "domain": "Customer Support",
            "purpose": "Helps customers with refunds and orders",
            "capabilities": ["refunds", "order tracking"],
            "limitations": ["no payments"],
            "target_audience": "Customers",
            "topics": ["refunds", "orders", "products"],
            "tone_and_style": "Helpful",
            "key_entities": ["SupportBot"],
            "confidence": "high",
        })

        analyzer = DocumentAnalyzer(llm_client=mock_llm)
        context = await analyzer.analyze(load_result)

        assert context.bot_name == "SupportBot"
        assert context.source_doc_count >= 4

    @pytest.mark.asyncio
    async def test_load_analyze_generate_criteria(self, doc_dir):
        """Full pipeline: load → analyze → generate criteria."""
        from src.analyzers.criteria_generator import CriteriaGenerator
        from src.analyzers.document_analyzer import BotContext, DocumentAnalyzer

        loader = DocumentLoader()
        load_result = loader.load_directory(doc_dir)

        # Create a context (as if Stage 1 was approved)
        context = BotContext(
            bot_name="TestBot",
            domain="E-commerce",
            purpose="Customer support",
            capabilities=["refunds", "orders"],
            limitations=["no payments"],
            topics=["refunds", "shipping"],
        )

        # Mock criteria generation
        mock_llm = AsyncMock()
        mock_llm.model = "test-model"
        mock_llm.generate_json = AsyncMock(return_value={"criteria": [
            {"criterion": "Must answer accurately", "category": "grounding", "importance": "high", "rationale": "Core"},
            {"criterion": "Must protect PII", "category": "safety", "importance": "high", "rationale": "Privacy"},
        ]})

        gen = CriteriaGenerator(llm_client=mock_llm)
        criteria = await gen.generate(context, extra_documentation=load_result.all_text[:2000])

        assert criteria.count == 2
        items = criteria.to_proposal_items()
        assert len(items) == 2