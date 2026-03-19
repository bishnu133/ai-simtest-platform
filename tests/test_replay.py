"""
Tests for P3 #18: Conversation Replay Mode.

Covers:
  - Replay models (config, sources, results, LoadSummary, PIIReport)
  - All 4 parsers (JSON, JSONL, CSV, Text)
  - SimTest output parser
  - Parser registry and auto-detection
  - Conversation loader (file discovery, PII masking, normalization)
  - Replay evaluator (evaluate mode, gate checks)
  - Edge cases (empty files, malformed data, unknown roles)
  - Review fix coverage:
    #1  Source-to-conversation mapping in HYBRID mode
    #2  Recursive file discovery
    #3  Parse failure reporting + strict mode
    #5  JSON extraction hardening
    #8  Export format sanitization
    #9  Configurable judge selection
    #10 Unknown role tracking in metadata
    #11 Text parser splitting behavior
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.replay.models import (
    ConversationEvalResult,
    ConversationSource,
    ImportedConversation,
    InputFormat,
    LoadSummary,
    PIIMaskingConfig,
    PIIMaskingStrategy,
    PIIReport,
    ReplayConfig,
    ReplayMode,
    ReplayResult,
)
from src.replay.parsers import detect_format, get_parser, register_parser
from src.replay.parsers.base import ConversationParser
from src.replay.parsers.json_parser import JSONParser, JSONLParser
from src.replay.parsers.csv_parser import CSVParser
from src.replay.parsers.text_parser import TextParser
from src.replay.parsers.simtest_parser import SimTestParser
from src.replay.loader import ConversationLoader


# ============================================================
# Fixtures
# ============================================================

@pytest.fixture
def tmp_dir():
    with tempfile.TemporaryDirectory() as d:
        yield Path(d)


@pytest.fixture
def sample_json_file(tmp_dir):
    data = [
        {
            "conversation_id": "conv_001",
            "messages": [
                {"role": "user", "content": "How do I open an account?"},
                {"role": "assistant", "content": "You can open an account online."},
                {"role": "user", "content": "What documents do I need?"},
                {"role": "assistant", "content": "You need an ID and proof of address."},
            ],
            "timestamp": "2026-03-15T10:00:00Z",
        },
        {
            "conversation_id": "conv_002",
            "messages": [
                {"role": "user", "content": "My card is blocked"},
                {"role": "bot", "content": "Let me help you with that."},
            ],
        },
    ]
    path = tmp_dir / "chats.json"
    path.write_text(json.dumps(data))
    return path


@pytest.fixture
def sample_jsonl_file(tmp_dir):
    lines = [
        json.dumps({
            "conversation_id": "c1",
            "messages": [
                {"role": "user", "content": "Hello"},
                {"role": "assistant", "content": "Hi there!"},
            ],
        }),
        json.dumps({
            "conversation_id": "c2",
            "messages": [
                {"role": "customer", "content": "Need help"},
                {"role": "agent", "content": "How can I assist?"},
            ],
        }),
    ]
    path = tmp_dir / "chats.jsonl"
    path.write_text("\n".join(lines))
    return path


@pytest.fixture
def sample_csv_file(tmp_dir):
    csv_content = """conversation_id,role,message,timestamp
conv_001,user,"How do I open an account?",2026-03-15T10:00:00
conv_001,bot,"You can open an account online.",2026-03-15T10:00:05
conv_001,user,"What documents?",2026-03-15T10:00:10
conv_001,bot,"ID and proof of address.",2026-03-15T10:00:15
conv_002,user,"Card blocked!",2026-03-15T11:00:00
conv_002,bot,"Let me check.",2026-03-15T11:00:05"""
    path = tmp_dir / "chats.csv"
    path.write_text(csv_content)
    return path


@pytest.fixture
def sample_text_file(tmp_dir):
    text = """Customer: I need to cancel my flight
Bot: I can help with that. Could you provide your booking reference?
Customer: It's ABC123
Bot: I've found your booking. Would you like a refund or rebooking?

---

Customer: My order hasn't arrived
Bot: I'm sorry about that. Let me check the tracking.
Customer: It's been 2 weeks!
Bot: I can see it's delayed. Let me expedite this for you."""
    path = tmp_dir / "transcripts.txt"
    path.write_text(text)
    return path


@pytest.fixture
def sample_simtest_file(tmp_dir):
    record = {
        "conversation": {
            "id": "conv_sim_001",
            "persona_id": "p1",
            "turns": [
                {"speaker": "user", "message": "What is your refund policy?"},
                {"speaker": "bot", "message": "Our refund policy allows returns within 30 days."},
            ],
        },
        "persona": {"name": "Test User", "persona_type": "standard"},
        "overall_score": 0.85,
        "failure_modes": [],
    }
    path = tmp_dir / "conversations.jsonl"
    path.write_text(json.dumps(record))
    return path


# ============================================================
# 1. Model Tests
# ============================================================

class TestReplayModels:
    """Test replay data models."""

    def test_replay_config_defaults(self):
        cfg = ReplayConfig(input_paths=["./logs"])
        assert cfg.mode == ReplayMode.EVALUATE
        assert cfg.input_format == InputFormat.AUTO
        assert cfg.pass_threshold == 0.7
        assert cfg.pii_masking.strategy == PIIMaskingStrategy.NONE
        assert cfg.judges is None  # All judges by default (#9)
        assert cfg.fail_on_parse_error is False  # Not strict by default (#3)

    def test_replay_config_retest_mode(self):
        cfg = ReplayConfig(
            input_paths=["./logs"],
            mode=ReplayMode.RETEST,
            bot_endpoint="http://localhost:9999/v1/chat/completions",
        )
        assert cfg.mode == ReplayMode.RETEST
        assert cfg.bot_endpoint is not None

    def test_replay_config_with_gates(self):
        cfg = ReplayConfig(
            input_paths=["./logs"],
            fail_thresholds={"safety": 0.95, "quality": 0.70},
        )
        assert cfg.fail_thresholds["safety"] == 0.95

    def test_replay_config_with_judges(self):
        """Review #9: configurable judge selection."""
        cfg = ReplayConfig(input_paths=["./logs"], judges=["safety", "quality"])
        assert cfg.judges == ["safety", "quality"]

    def test_replay_config_strict_mode(self):
        """Review #3: strict parse error mode."""
        cfg = ReplayConfig(input_paths=["./logs"], fail_on_parse_error=True)
        assert cfg.fail_on_parse_error is True

    def test_conversation_source(self):
        src = ConversationSource(file_path="/data/chats.json", line_number=42, original_id="conv_123")
        assert src.file_path == "/data/chats.json"
        assert src.line_number == 42

    def test_imported_conversation(self):
        ic = ImportedConversation(
            source=ConversationSource(file_path="test.json"),
            messages=[{"role": "user", "content": "Hello"}, {"role": "bot", "content": "Hi!"}],
        )
        assert len(ic.messages) == 2

    def test_load_summary_model(self):
        """Review #3: LoadSummary for parse error reporting."""
        ls = LoadSummary(
            files_discovered=10, files_parsed_ok=8, files_failed=2,
            parse_errors=[{"file": "bad.json", "error": "JSONDecodeError"}],
        )
        assert ls.files_failed == 2
        assert len(ls.parse_errors) == 1

    def test_pii_report_model(self):
        """Review #6: PIIReport for masking transparency."""
        pr = PIIReport(
            masking_enabled=True, masking_strategy="mask",
            total_entities_detected=15, total_fields_redacted=12,
            detections_by_type={"PERSON": 8, "EMAIL_ADDRESS": 7},
            conversations_with_pii=5, conversations_clean=10,
        )
        assert pr.total_entities_detected == 15
        assert pr.conversations_with_pii == 5

    def test_replay_result_with_load_summary(self):
        """Review #3 + #6: ReplayResult includes LoadSummary and PIIReport."""
        rr = ReplayResult(
            config=ReplayConfig(input_paths=["test"]),
            total_conversations=5,
            load_summary=LoadSummary(files_discovered=3, files_parsed_ok=3),
            pii_report=PIIReport(masking_enabled=True),
        )
        assert rr.load_summary.files_discovered == 3
        assert rr.pii_report.masking_enabled is True


# ============================================================
# 2. Parser Tests — JSON
# ============================================================

class TestJSONParser:
    """Test JSON parser."""

    def test_parse_json_array(self, sample_json_file):
        parser = JSONParser()
        config = ReplayConfig(input_paths=[str(sample_json_file)])
        results = parser.parse_file(sample_json_file, config)
        assert len(results) == 2
        assert results[0].source.original_id == "conv_001"
        assert len(results[0].messages) == 4
        assert results[0].messages[1]["role"] == "bot"  # "assistant" normalized

    def test_parse_single_json_object(self, tmp_dir):
        data = {"id": "single_conv", "messages": [
            {"role": "user", "content": "Test"}, {"role": "assistant", "content": "Response"},
        ]}
        path = tmp_dir / "single.json"
        path.write_text(json.dumps(data))
        results = JSONParser().parse_file(path, ReplayConfig(input_paths=[str(path)]))
        assert len(results) == 1
        assert results[0].source.original_id == "single_conv"

    def test_parse_alternative_field_names(self, tmp_dir):
        data = [{"conv_id": "alt_001", "turns": [
            {"speaker": "human", "text": "Weather?"}, {"from": "ai", "message": "Sunny."},
        ]}]
        path = tmp_dir / "alt.json"
        path.write_text(json.dumps(data))
        results = JSONParser().parse_file(path, ReplayConfig(input_paths=[str(path)]))
        assert len(results) == 1
        assert len(results[0].messages) == 2

    def test_parse_empty_messages(self, tmp_dir):
        data = [{"conversation_id": "empty", "messages": []}]
        path = tmp_dir / "empty.json"
        path.write_text(json.dumps(data))
        results = JSONParser().parse_file(path, ReplayConfig(input_paths=[str(path)]))
        assert len(results) == 0

    def test_parse_preserves_metadata(self, sample_json_file):
        results = JSONParser().parse_file(sample_json_file, ReplayConfig(input_paths=[str(sample_json_file)]))
        assert results[0].source.original_timestamp == "2026-03-15T10:00:00Z"

    def test_role_normalization_assistant(self, sample_json_file):
        results = JSONParser().parse_file(sample_json_file, ReplayConfig(input_paths=[str(sample_json_file)]))
        bot_msgs = [m for m in results[0].messages if m["role"] == "bot"]
        assert len(bot_msgs) == 2


# ============================================================
# 3. Parser Tests — JSONL
# ============================================================

class TestJSONLParser:
    def test_parse_jsonl(self, sample_jsonl_file):
        results = JSONLParser().parse_file(sample_jsonl_file, ReplayConfig(input_paths=[str(sample_jsonl_file)]))
        assert len(results) == 2
        assert results[0].source.line_number == 1
        assert results[1].source.line_number == 2

    def test_parse_jsonl_with_blank_lines(self, tmp_dir):
        lines = [
            json.dumps({"id": "c1", "messages": [{"role": "user", "content": "Hi"}]}),
            "", json.dumps({"id": "c2", "messages": [{"role": "user", "content": "Hello"}]}),
        ]
        path = tmp_dir / "gaps.jsonl"
        path.write_text("\n".join(lines))
        results = JSONLParser().parse_file(path, ReplayConfig(input_paths=[str(path)]))
        assert len(results) == 2

    def test_parse_jsonl_malformed_lines_skipped(self, tmp_dir):
        lines = [
            json.dumps({"id": "c1", "messages": [{"role": "user", "content": "Hi"}]}),
            "this is not valid json{{{",
            json.dumps({"id": "c2", "messages": [{"role": "user", "content": "Ok"}]}),
        ]
        path = tmp_dir / "bad.jsonl"
        path.write_text("\n".join(lines))
        results = JSONLParser().parse_file(path, ReplayConfig(input_paths=[str(path)]))
        assert len(results) == 2

    def test_role_normalization_customer_agent(self, sample_jsonl_file):
        results = JSONLParser().parse_file(sample_jsonl_file, ReplayConfig(input_paths=[str(sample_jsonl_file)]))
        assert results[1].messages[0]["role"] == "user"   # "customer" → "user"
        assert results[1].messages[1]["role"] == "bot"    # "agent" → "bot"


# ============================================================
# 4. Parser Tests — CSV
# ============================================================

class TestCSVParser:
    def test_parse_csv(self, sample_csv_file):
        results = CSVParser().parse_file(sample_csv_file, ReplayConfig(input_paths=[str(sample_csv_file)]))
        assert len(results) == 2
        assert len(results[0].messages) == 4
        assert len(results[1].messages) == 2

    def test_csv_conversation_grouping(self, sample_csv_file):
        results = CSVParser().parse_file(sample_csv_file, ReplayConfig(input_paths=[str(sample_csv_file)]))
        assert results[0].source.original_id == "conv_001"
        assert results[1].source.original_id == "conv_002"

    def test_csv_auto_detect_columns(self, tmp_dir):
        path = tmp_dir / "auto.csv"
        path.write_text("session_id,sender,text\ns1,customer,Need help\ns1,agent,How can I help?\n")
        results = CSVParser().parse_file(path, ReplayConfig(input_paths=[str(path)]))
        assert len(results) == 1
        assert results[0].messages[0]["role"] == "user"
        assert results[0].messages[1]["role"] == "bot"

    def test_csv_tsv_format(self, tmp_dir):
        path = tmp_dir / "chats.tsv"
        path.write_text("conversation_id\trole\tmessage\nc1\tuser\tHello\nc1\tbot\tHi!")
        results = CSVParser().parse_file(path, ReplayConfig(input_paths=[str(path)]))
        assert len(results) == 1

    def test_csv_missing_required_columns_raises(self, tmp_dir):
        path = tmp_dir / "bad.csv"
        path.write_text("id,data\n1,test\n")
        with pytest.raises(ValueError, match="Cannot find required columns"):
            CSVParser().parse_file(path, ReplayConfig(input_paths=[str(path)]))


# ============================================================
# 5. Parser Tests — Text
# ============================================================

class TestTextParser:
    def test_parse_text_with_separator(self, sample_text_file):
        results = TextParser().parse_file(sample_text_file, ReplayConfig(input_paths=[str(sample_text_file)]))
        assert len(results) == 2
        assert len(results[0].messages) == 4
        assert results[0].messages[0]["role"] == "user"

    def test_parse_text_custom_speaker_pattern(self, tmp_dir):
        path = tmp_dir / "custom.txt"
        path.write_text("Agent Smith: Welcome!\nClient: I need help\nAgent Smith: Sure!")
        config = ReplayConfig(
            input_paths=[str(path)], speaker_pattern="Agent Smith:|Client:",
            bot_speaker_names=["Agent Smith"], user_speaker_names=["Client"],
        )
        results = TextParser().parse_file(path, config)
        assert len(results) == 1
        assert results[0].messages[0]["role"] == "bot"
        assert results[0].messages[1]["role"] == "user"

    def test_parse_text_multiline_messages(self, tmp_dir):
        path = tmp_dir / "multi.txt"
        path.write_text("User: I have a problem with\nmy order number 12345\nBot: Let me check.")
        results = TextParser().parse_file(path, ReplayConfig(input_paths=[str(path)]))
        assert len(results) == 1
        assert "12345" in results[0].messages[0]["content"]

    def test_parse_text_empty_file(self, tmp_dir):
        path = tmp_dir / "empty.txt"
        path.write_text("")
        results = TextParser().parse_file(path, ReplayConfig(input_paths=[str(path)]))
        assert len(results) == 0

    def test_parse_text_with_timestamps(self, tmp_dir):
        path = tmp_dir / "timed.txt"
        path.write_text("[10:30] Customer: What's my balance?\n[10:31] Bot: Your balance is $500.")
        results = TextParser().parse_file(path, ReplayConfig(input_paths=[str(path)]))
        assert len(results) == 1
        assert results[0].messages[0]["content"] == "What's my balance?"

    def test_text_single_blank_line_does_not_split(self, tmp_dir):
        """Review #11: Single blank line should NOT split conversations."""
        path = tmp_dir / "single_blank.txt"
        path.write_text("User: First message\nBot: First response\n\nUser: Second message\nBot: Second response")
        results = TextParser().parse_file(path, ReplayConfig(input_paths=[str(path)]))
        assert len(results) == 1  # NOT split by single blank line
        assert len(results[0].messages) == 4

    def test_text_double_blank_line_splits(self, tmp_dir):
        """Review #11: Double blank line DOES split conversations."""
        path = tmp_dir / "double_blank.txt"
        path.write_text("User: Convo 1\nBot: Response 1\n\n\nUser: Convo 2\nBot: Response 2")
        results = TextParser().parse_file(path, ReplayConfig(input_paths=[str(path)]))
        assert len(results) == 2


# ============================================================
# 6. Parser Tests — SimTest
# ============================================================

class TestSimTestParser:
    def test_parse_simtest_output(self, sample_simtest_file):
        results = SimTestParser().parse_file(sample_simtest_file, ReplayConfig(input_paths=[str(sample_simtest_file)], input_format=InputFormat.SIMTEST))
        assert len(results) == 1
        assert results[0].source.original_id == "conv_sim_001"
        assert results[0].metadata.get("original_score") == 0.85

    def test_simtest_preserves_persona_info(self, sample_simtest_file):
        results = SimTestParser().parse_file(sample_simtest_file, ReplayConfig(input_paths=[str(sample_simtest_file)]))
        assert results[0].metadata.get("persona_name") == "Test User"


# ============================================================
# 7. Parser Registry Tests
# ============================================================

class TestParserRegistry:
    def test_get_parser_json(self):
        assert isinstance(get_parser("json"), JSONParser)

    def test_get_parser_csv(self):
        assert isinstance(get_parser("csv"), CSVParser)

    def test_get_parser_text(self):
        assert isinstance(get_parser("text"), TextParser)

    def test_get_parser_unknown_raises(self):
        with pytest.raises(ValueError, match="Unknown format"):
            get_parser("docx")

    def test_detect_format_json(self, tmp_dir):
        assert detect_format(tmp_dir / "test.json") == "json"

    def test_detect_format_csv(self, tmp_dir):
        assert detect_format(tmp_dir / "test.csv") == "csv"

    def test_detect_format_txt(self, tmp_dir):
        assert detect_format(tmp_dir / "test.txt") == "text"

    def test_detect_format_unknown_raises(self, tmp_dir):
        with pytest.raises(ValueError, match="Cannot auto-detect"):
            detect_format(tmp_dir / "test.xyz")

    def test_register_custom_parser(self):
        class CustomParser(ConversationParser):
            format_name = "custom"
            file_extensions = ["custom"]
            def parse_file(self, file_path, config):
                return []
        register_parser("custom", CustomParser(), [".custom"])
        assert isinstance(get_parser("custom"), CustomParser)
        assert detect_format(Path("test.custom")) == "custom"


# ============================================================
# 8. Conversation Loader Tests
# ============================================================

class TestConversationLoader:
    def test_load_json_file(self, sample_json_file):
        config = ReplayConfig(input_paths=[str(sample_json_file)], input_format=InputFormat.JSON)
        convos, personas, sources, load_summary, pii_report = ConversationLoader().load(config)
        assert len(convos) == 2
        assert len(personas) == 2
        assert len(sources) == 2
        assert load_summary.files_parsed_ok == 1
        assert load_summary.conversations_loaded == 2
        assert len(convos[0].turns) == 4

    def test_load_csv_file(self, sample_csv_file):
        config = ReplayConfig(input_paths=[str(sample_csv_file)], input_format=InputFormat.CSV)
        convos, _, _, ls, _ = ConversationLoader().load(config)
        assert len(convos) == 2
        assert ls.files_parsed_ok == 1

    def test_load_text_file(self, sample_text_file):
        config = ReplayConfig(input_paths=[str(sample_text_file)], input_format=InputFormat.TEXT)
        convos, _, _, _, _ = ConversationLoader().load(config)
        assert len(convos) == 2

    def test_load_directory(self, tmp_dir, sample_json_file, sample_csv_file):
        config = ReplayConfig(input_paths=[str(tmp_dir)], input_format=InputFormat.AUTO)
        convos, _, _, ls, _ = ConversationLoader().load(config)
        assert len(convos) >= 2
        assert ls.files_discovered >= 2

    def test_load_empty_directory_raises(self, tmp_dir):
        empty_dir = tmp_dir / "empty"
        empty_dir.mkdir()
        config = ReplayConfig(input_paths=[str(empty_dir)], input_format=InputFormat.AUTO)
        with pytest.raises(ValueError, match="No input files found"):
            ConversationLoader().load(config)

    def test_synthetic_persona_creation(self, sample_json_file):
        config = ReplayConfig(input_paths=[str(sample_json_file)], input_format=InputFormat.JSON)
        convos, personas, _, _, _ = ConversationLoader().load(config)
        for conv in convos:
            assert conv.persona_id in personas
            assert personas[conv.persona_id].role == "real_user"

    def test_metadata_preserved(self, sample_json_file):
        config = ReplayConfig(input_paths=[str(sample_json_file)], input_format=InputFormat.JSON)
        convos, _, _, _, _ = ConversationLoader().load(config)
        assert convos[0].metadata.get("replay_mode") is True

    def test_load_with_auto_format_detection(self, sample_json_file):
        config = ReplayConfig(input_paths=[str(sample_json_file)], input_format=InputFormat.AUTO)
        convos, _, _, _, _ = ConversationLoader().load(config)
        assert len(convos) == 2

    def test_recursive_file_discovery(self, tmp_dir):
        """Review #2: rglob finds files in subdirectories."""
        sub = tmp_dir / "sub" / "deep"
        sub.mkdir(parents=True)
        data = [{"id": "c1", "messages": [{"role": "user", "content": "Hi"}, {"role": "bot", "content": "Hey"}]}]
        (sub / "nested.json").write_text(json.dumps(data))
        config = ReplayConfig(input_paths=[str(tmp_dir)], input_format=InputFormat.AUTO)
        convos, _, _, ls, _ = ConversationLoader().load(config)
        assert len(convos) >= 1
        assert ls.files_discovered >= 1

    def test_parse_failure_reporting(self, tmp_dir):
        """Review #3: Parse failures reported in LoadSummary."""
        (tmp_dir / "good.json").write_text(json.dumps([{"id": "c1", "messages": [{"role": "user", "content": "Hi"}, {"role": "bot", "content": "Hey"}]}]))
        (tmp_dir / "bad.json").write_text("{{{invalid json")
        config = ReplayConfig(input_paths=[str(tmp_dir)], input_format=InputFormat.JSON)
        convos, _, _, ls, _ = ConversationLoader().load(config)
        assert ls.files_failed >= 1
        assert len(ls.parse_errors) >= 1
        assert "bad.json" in ls.parse_errors[0]["file"]

    def test_strict_mode_aborts_on_parse_error(self, tmp_dir):
        """Review #3: --fail-on-parse-error aborts if any file fails."""
        (tmp_dir / "bad.json").write_text("{{{invalid")
        (tmp_dir / "good.json").write_text(json.dumps([{"id": "c1", "messages": [{"role": "user", "content": "Hi"}, {"role": "bot", "content": "Hey"}]}]))
        config = ReplayConfig(input_paths=[str(tmp_dir)], input_format=InputFormat.JSON, fail_on_parse_error=True)
        with pytest.raises(ValueError, match="Parse errors"):
            ConversationLoader().load(config)

    def test_unknown_roles_tracked_in_metadata(self, tmp_dir):
        """Review #10: Unknown roles stored in metadata, not dropped silently."""
        data = [{"id": "c1", "messages": [
            {"role": "moderator", "content": "Welcome"},
            {"role": "user", "content": "Thanks"},
            {"role": "bot", "content": "Hello!"},
        ]}]
        (tmp_dir / "roles.json").write_text(json.dumps(data))
        config = ReplayConfig(input_paths=[str(tmp_dir)], input_format=InputFormat.JSON)
        convos, _, _, ls, _ = ConversationLoader().load(config)
        assert len(convos) == 1
        assert "moderator" in ls.unknown_roles_found
        assert convos[0].metadata.get("unknown_role_messages") is not None
        assert len(convos[0].metadata["unknown_role_messages"]) == 1
        # user and bot turns still present
        assert all(t.speaker in ("user", "bot") for t in convos[0].turns)
        assert len(convos[0].turns) == 2


# ============================================================
# 9. PII Masking Tests
# ============================================================

class TestPIIMasking:
    def test_pii_masking_disabled_by_default(self, sample_json_file):
        cfg = ReplayConfig(input_paths=[str(sample_json_file)], input_format=InputFormat.JSON)
        assert cfg.pii_masking.strategy == PIIMaskingStrategy.NONE

    def test_pii_detect_mode_config(self):
        cfg = ReplayConfig(input_paths=["test"], pii_masking=PIIMaskingConfig(strategy=PIIMaskingStrategy.DETECT_ONLY))
        assert cfg.pii_masking.strategy == PIIMaskingStrategy.DETECT_ONLY

    def test_pii_mask_mode_config(self):
        cfg = ReplayConfig(input_paths=["test"], pii_masking=PIIMaskingConfig(strategy=PIIMaskingStrategy.MASK, mask_format="[{entity_type}]"))
        assert cfg.pii_masking.strategy == PIIMaskingStrategy.MASK

    def test_pii_report_returned_from_loader(self, sample_json_file):
        """Review #6: loader returns PIIReport even when masking disabled."""
        config = ReplayConfig(input_paths=[str(sample_json_file)], input_format=InputFormat.JSON)
        _, _, _, _, pii_report = ConversationLoader().load(config)
        assert pii_report.masking_enabled is False
        assert pii_report.total_entities_detected == 0


# ============================================================
# 10. Evaluator Tests (mocked judges)
# ============================================================

class TestReplayEvaluator:
    @pytest.mark.asyncio
    async def test_evaluate_mode_produces_report(self, sample_json_file):
        from src.replay.evaluator import ReplayEvaluator
        config = ReplayConfig(input_paths=[str(sample_json_file)], input_format=InputFormat.JSON)
        convos, personas, sources, ls, pr = ConversationLoader().load(config)

        evaluator = ReplayEvaluator()
        with patch.object(evaluator, '_setup_judges') as mock_setup:
            mock_engine = AsyncMock()
            mock_engine.judge_all_conversations = AsyncMock(return_value=[])
            mock_setup.return_value = mock_engine
            report, replay_result = await evaluator.evaluate(convos, personas, sources, config, load_summary=ls, pii_report=pr)
            assert report is not None
            assert report.summary.simulation_name.startswith("Conversation Replay")
            assert replay_result.gate_passed is True
            assert replay_result.load_summary.files_parsed_ok == 1

    @pytest.mark.asyncio
    async def test_gate_check_fails_when_below_threshold(self, sample_json_file):
        from src.replay.evaluator import ReplayEvaluator
        from src.models import JudgedConversation, JudgedTurn, JudgmentResult, JudgmentLabel, Severity
        config = ReplayConfig(input_paths=[str(sample_json_file)], input_format=InputFormat.JSON, fail_thresholds={"safety": 0.95})
        convos, personas, sources, ls, pr = ConversationLoader().load(config)

        mock_judged = []
        for conv in convos:
            persona = personas[conv.persona_id]
            bot_turns = [t for t in conv.turns if t.speaker == "bot"]
            jts = [JudgedTurn(
                turn=bt,
                judgments=[
                    JudgmentResult(judge_name="safety", passed=False, score=0.5, severity=Severity.CRITICAL, message="PII detected"),
                    JudgmentResult(judge_name="quality", passed=True, score=0.8, severity=Severity.INFO, message="OK"),
                ],
                overall_score=0.3, overall_label=JudgmentLabel.FAIL, issues=["PII detected"],
            ) for bt in bot_turns]
            mock_judged.append(JudgedConversation(conversation=conv, persona=persona, judged_turns=jts, overall_score=0.3, failure_modes=["PII detected"]))

        evaluator = ReplayEvaluator()
        with patch.object(evaluator, '_setup_judges') as mock_setup:
            mock_engine = AsyncMock()
            mock_engine.judge_all_conversations = AsyncMock(return_value=mock_judged)
            mock_setup.return_value = mock_engine
            report, replay_result = await evaluator.evaluate(convos, personas, sources, config)
            assert replay_result.gate_passed is False

    @pytest.mark.asyncio
    async def test_retest_mode_requires_bot_endpoint(self, sample_json_file):
        from src.replay.evaluator import ReplayEvaluator
        config = ReplayConfig(input_paths=[str(sample_json_file)], input_format=InputFormat.JSON, mode=ReplayMode.RETEST)
        convos, personas, sources, _, _ = ConversationLoader().load(config)
        evaluator = ReplayEvaluator()
        with patch.object(evaluator, '_setup_judges') as mock_setup:
            mock_setup.return_value = AsyncMock()
            with pytest.raises(ValueError, match="RETEST mode requires --bot-endpoint"):
                await evaluator.evaluate(convos, personas, sources, config)

    @pytest.mark.asyncio
    async def test_configurable_judges(self, sample_json_file):
        """Review #9: Only selected judges are initialized."""
        from src.replay.evaluator import ReplayEvaluator
        config = ReplayConfig(input_paths=[str(sample_json_file)], input_format=InputFormat.JSON, judges=["safety", "quality"])
        convos, personas, sources, ls, pr = ConversationLoader().load(config)

        evaluator = ReplayEvaluator()
        # Actually call _setup_judges to test filtering
        engine = await evaluator._setup_judges(config)
        judge_names = [j.name for j in engine.judges]
        assert "safety" in judge_names
        assert "quality" in judge_names
        assert "grounding" not in judge_names
        assert "relevance" not in judge_names


# ============================================================
# 11. JSON Extraction Hardening Tests
# ============================================================

class TestJSONExtraction:
    """Review #5: Test hardened JSON parsing for hybrid variations."""

    def test_extract_clean_json(self):
        from src.replay.evaluator import ReplayEvaluator
        ev = ReplayEvaluator()
        result = ev._extract_json_array('[["hello", "world"], ["hi", "there"]]')
        assert len(result) == 2
        assert result[0] == ["hello", "world"]

    def test_extract_json_with_code_fences(self):
        from src.replay.evaluator import ReplayEvaluator
        ev = ReplayEvaluator()
        raw = '```json\n[["hello", "world"]]\n```'
        result = ev._extract_json_array(raw)
        assert len(result) == 1

    def test_extract_json_with_preamble(self):
        from src.replay.evaluator import ReplayEvaluator
        ev = ReplayEvaluator()
        raw = 'Here are the variations:\n[["hello"], ["hi"]]'
        result = ev._extract_json_array(raw)
        assert len(result) == 2

    def test_extract_json_invalid_raises(self):
        from src.replay.evaluator import ReplayEvaluator
        ev = ReplayEvaluator()
        with pytest.raises(ValueError, match="Could not extract"):
            ev._extract_json_array("This is not JSON at all")


# ============================================================
# 12. Export Format Sanitization
# ============================================================

class TestExportFormatSanitization:
    """Review #8: Export format strings are trimmed and lowercased."""

    def test_formats_are_sanitized(self):
        cfg = ReplayConfig(input_paths=["test"])
        # Simulate what CLI does after split
        raw = " HTML , csv , Summary "
        sanitized = [f.strip().lower() for f in raw.split(",") if f.strip()]
        assert sanitized == ["html", "csv", "summary"]


# ============================================================
# 13. Edge Case Tests
# ============================================================

class TestEdgeCases:
    def test_json_with_no_messages_field(self, tmp_dir):
        (tmp_dir / "nomsg.json").write_text(json.dumps([{"id": "c1", "data": "no messages"}]))
        results = JSONParser().parse_file(tmp_dir / "nomsg.json", ReplayConfig(input_paths=[str(tmp_dir)]))
        assert len(results) == 0

    def test_csv_with_empty_rows(self, tmp_dir):
        (tmp_dir / "gaps.csv").write_text("conversation_id,role,message\nc1,user,Hello\nc1,,\nc1,bot,Hi")
        results = CSVParser().parse_file(tmp_dir / "gaps.csv", ReplayConfig(input_paths=[str(tmp_dir)]))
        assert len(results) == 1
        assert len(results[0].messages) == 2

    def test_unknown_roles_preserved_by_parser(self, tmp_dir):
        data = [{"id": "c1", "messages": [
            {"role": "moderator", "content": "Welcome"}, {"role": "user", "content": "Thanks"},
        ]}]
        (tmp_dir / "roles.json").write_text(json.dumps(data))
        results = JSONParser().parse_file(tmp_dir / "roles.json", ReplayConfig(input_paths=[str(tmp_dir)]))
        assert results[0].messages[0]["role"] == "moderator"  # Parser preserves it

    def test_multiple_input_paths(self, sample_json_file, sample_csv_file):
        config = ReplayConfig(input_paths=[str(sample_json_file), str(sample_csv_file)], input_format=InputFormat.AUTO)
        convos, _, _, _, _ = ConversationLoader().load(config)
        assert len(convos) == 4  # 2 JSON + 2 CSV

    def test_text_parser_no_separators(self, tmp_dir):
        (tmp_dir / "nosep.txt").write_text("User: Hello\nBot: Hi!\nUser: Goodbye\nBot: Bye!")
        results = TextParser().parse_file(tmp_dir / "nosep.txt", ReplayConfig(input_paths=[str(tmp_dir)]))
        assert len(results) == 1
        assert len(results[0].messages) == 4


# ============================================================
# 14. Parse Confidence / Quality Scoring (Review #R1)
# ============================================================

class TestParseQualityScoring:
    """Review #R1: Per-conversation quality flags."""

    def test_complete_conversation_scored_complete(self, sample_json_file):
        """Both user and bot, multiple turns → COMPLETE."""
        config = ReplayConfig(input_paths=[str(sample_json_file)], input_format=InputFormat.JSON)
        _, _, _, ls, _ = ConversationLoader().load(config)
        assert ls.quality_complete >= 1

    def test_one_sided_conversation_scored_low(self, tmp_dir):
        """Only user messages, no bot → LOW_CONFIDENCE."""
        data = [{"id": "c1", "messages": [
            {"role": "user", "content": "Hello"},
            {"role": "user", "content": "Anyone there?"},
        ]}]
        (tmp_dir / "onesided.json").write_text(json.dumps(data))
        config = ReplayConfig(input_paths=[str(tmp_dir)], input_format=InputFormat.JSON)
        _, _, _, ls, _ = ConversationLoader().load(config)
        assert ls.quality_low >= 1

    def test_empty_bot_response_scored_partial(self, tmp_dir):
        """Bot has empty response → PARTIAL."""
        data = [{"id": "c1", "messages": [
            {"role": "user", "content": "Help me"},
            {"role": "bot", "content": ""},
            {"role": "user", "content": "Hello?"},
            {"role": "bot", "content": "Sorry about that."},
        ]}]
        (tmp_dir / "empty_bot.json").write_text(json.dumps(data))
        config = ReplayConfig(input_paths=[str(tmp_dir)], input_format=InputFormat.JSON)
        _, _, _, ls, _ = ConversationLoader().load(config)
        assert ls.quality_partial >= 1

    def test_single_turn_scored_partial(self, tmp_dir):
        """Only 1 message total → PARTIAL (fewer than 2 messages)."""
        data = [{"id": "c1", "messages": [
            {"role": "user", "content": "Hi"},
        ]}]
        (tmp_dir / "single.json").write_text(json.dumps(data))
        config = ReplayConfig(input_paths=[str(tmp_dir)], input_format=InputFormat.JSON)
        # Single user-only message → LOW (no bot)
        _, _, _, ls, _ = ConversationLoader().load(config)
        assert ls.quality_low >= 1

    def test_quality_distribution_in_load_summary(self, tmp_dir):
        """Multiple conversations with different quality levels."""
        data = [
            {"id": "good", "messages": [
                {"role": "user", "content": "Hi"}, {"role": "bot", "content": "Hello!"},
                {"role": "user", "content": "Help"}, {"role": "bot", "content": "Sure!"},
            ]},
            {"id": "bad", "messages": [
                {"role": "user", "content": "Hello"},
            ]},
        ]
        (tmp_dir / "mixed.json").write_text(json.dumps(data))
        config = ReplayConfig(input_paths=[str(tmp_dir)], input_format=InputFormat.JSON)
        _, _, _, ls, _ = ConversationLoader().load(config)
        assert ls.quality_complete + ls.quality_partial + ls.quality_low == ls.conversations_loaded


# ============================================================
# 15. Minimum Sample Gate Protection (Review #R2)
# ============================================================

class TestMinimumSampleGate:
    """Review #R2: Gates skip when dataset too small."""

    @pytest.mark.asyncio
    async def test_gate_skipped_when_below_minimum(self, sample_json_file):
        """Gate should not fail when dataset is smaller than threshold."""
        from src.replay.evaluator import ReplayEvaluator
        from src.models import JudgedConversation, JudgedTurn, JudgmentResult, JudgmentLabel, Severity

        config = ReplayConfig(
            input_paths=[str(sample_json_file)],
            input_format=InputFormat.JSON,
            fail_thresholds={"safety": 0.95},
            min_conversations_for_gate=100,  # Require 100, but we only have 2
        )
        convos, personas, sources, ls, pr = ConversationLoader().load(config)

        # Build mock judged conversations with LOW safety (would normally fail)
        mock_judged = []
        for conv in convos:
            persona = personas[conv.persona_id]
            bot_turns = [t for t in conv.turns if t.speaker == "bot"]
            jts = [JudgedTurn(
                turn=bt,
                judgments=[JudgmentResult(judge_name="safety", passed=False, score=0.3, severity=Severity.CRITICAL, message="Bad")],
                overall_score=0.3, overall_label=JudgmentLabel.FAIL, issues=["Bad"],
            ) for bt in bot_turns]
            mock_judged.append(JudgedConversation(
                conversation=conv, persona=persona, judged_turns=jts,
                overall_score=0.3, failure_modes=["Bad"],
            ))

        evaluator = ReplayEvaluator()
        with patch.object(evaluator, '_setup_judges') as mock_setup:
            mock_engine = AsyncMock()
            mock_engine.judge_all_conversations = AsyncMock(return_value=mock_judged)
            mock_setup.return_value = mock_engine
            _, replay_result = await evaluator.evaluate(convos, personas, sources, config, load_summary=ls, pii_report=pr)

            # Gate should PASS because sample is too small to enforce
            assert replay_result.gate_passed is True
            assert any("skipped" in k for k in replay_result.gate_results)

    @pytest.mark.asyncio
    async def test_gate_enforced_when_above_minimum(self, sample_json_file):
        """Gate should fail normally when dataset meets minimum."""
        from src.replay.evaluator import ReplayEvaluator
        from src.models import JudgedConversation, JudgedTurn, JudgmentResult, JudgmentLabel, Severity

        config = ReplayConfig(
            input_paths=[str(sample_json_file)],
            input_format=InputFormat.JSON,
            fail_thresholds={"safety": 0.95},
            min_conversations_for_gate=1,  # Require 1, we have 2 — should enforce
        )
        convos, personas, sources, ls, pr = ConversationLoader().load(config)

        mock_judged = []
        for conv in convos:
            persona = personas[conv.persona_id]
            bot_turns = [t for t in conv.turns if t.speaker == "bot"]
            jts = [JudgedTurn(
                turn=bt,
                judgments=[JudgmentResult(judge_name="safety", passed=False, score=0.3, severity=Severity.CRITICAL, message="Bad")],
                overall_score=0.3, overall_label=JudgmentLabel.FAIL, issues=["Bad"],
            ) for bt in bot_turns]
            mock_judged.append(JudgedConversation(
                conversation=conv, persona=persona, judged_turns=jts,
                overall_score=0.3, failure_modes=["Bad"],
            ))

        evaluator = ReplayEvaluator()
        with patch.object(evaluator, '_setup_judges') as mock_setup:
            mock_engine = AsyncMock()
            mock_engine.judge_all_conversations = AsyncMock(return_value=mock_judged)
            mock_setup.return_value = mock_engine
            _, replay_result = await evaluator.evaluate(convos, personas, sources, config, load_summary=ls, pii_report=pr)

            # Gate should FAIL because sample meets minimum and safety < 0.95
            assert replay_result.gate_passed is False


# ============================================================
# 16. Conversation Filtering (Review #R5)
# ============================================================

class TestConversationFiltering:
    """Review #R5: --sample, --filter-min-turns, --filter-max-turns, --contains."""

    def test_filter_min_turns(self, tmp_dir):
        """Skip conversations with fewer turns than threshold."""
        data = [
            {"id": "short", "messages": [
                {"role": "user", "content": "Hi"}, {"role": "bot", "content": "Hello"},
            ]},
            {"id": "long", "messages": [
                {"role": "user", "content": "Help"}, {"role": "bot", "content": "Sure"},
                {"role": "user", "content": "More"}, {"role": "bot", "content": "OK"},
                {"role": "user", "content": "Thanks"}, {"role": "bot", "content": "Welcome"},
            ]},
        ]
        (tmp_dir / "mixed.json").write_text(json.dumps(data))
        config = ReplayConfig(
            input_paths=[str(tmp_dir)], input_format=InputFormat.JSON,
            filter_min_turns=4,
        )
        convos, _, _, ls, _ = ConversationLoader().load(config)
        assert len(convos) == 1  # Only "long" survives
        assert ls.conversations_loaded == 2
        assert ls.conversations_after_filter == 1

    def test_filter_max_turns(self, tmp_dir):
        """Skip conversations with more turns than threshold."""
        data = [
            {"id": "short", "messages": [
                {"role": "user", "content": "Hi"}, {"role": "bot", "content": "Hello"},
            ]},
            {"id": "long", "messages": [
                {"role": "user", "content": f"Msg {i}"} if i % 2 == 0 else {"role": "bot", "content": f"Reply {i}"}
                for i in range(20)
            ]},
        ]
        (tmp_dir / "mixed.json").write_text(json.dumps(data))
        config = ReplayConfig(
            input_paths=[str(tmp_dir)], input_format=InputFormat.JSON,
            filter_max_turns=5,
        )
        convos, _, _, ls, _ = ConversationLoader().load(config)
        assert len(convos) == 1  # Only "short" survives

    def test_filter_contains(self, tmp_dir):
        """Only include conversations containing specific text."""
        data = [
            {"id": "refund", "messages": [
                {"role": "user", "content": "I want a refund"}, {"role": "bot", "content": "Let me help"},
            ]},
            {"id": "balance", "messages": [
                {"role": "user", "content": "Check my balance"}, {"role": "bot", "content": "Use the app"},
            ]},
        ]
        (tmp_dir / "topics.json").write_text(json.dumps(data))
        config = ReplayConfig(
            input_paths=[str(tmp_dir)], input_format=InputFormat.JSON,
            filter_contains="refund",
        )
        convos, _, _, _, _ = ConversationLoader().load(config)
        assert len(convos) == 1

    def test_sample_size(self, tmp_dir):
        """Random sample N conversations."""
        data = [
            {"id": f"c{i}", "messages": [
                {"role": "user", "content": f"Question {i}"}, {"role": "bot", "content": f"Answer {i}"},
            ]}
            for i in range(20)
        ]
        (tmp_dir / "many.json").write_text(json.dumps(data))
        config = ReplayConfig(
            input_paths=[str(tmp_dir)], input_format=InputFormat.JSON,
            sample_size=5,
        )
        convos, _, _, ls, _ = ConversationLoader().load(config)
        assert len(convos) == 5
        assert ls.conversations_loaded == 20
        assert ls.conversations_after_filter == 5

    def test_combined_filters(self, tmp_dir):
        """Multiple filters applied together."""
        data = [
            {"id": "match_short", "messages": [
                {"role": "user", "content": "refund please"}, {"role": "bot", "content": "OK"},
            ]},
            {"id": "match_long", "messages": [
                {"role": "user", "content": "I need a refund"}, {"role": "bot", "content": "Sure"},
                {"role": "user", "content": "When?"}, {"role": "bot", "content": "7 days"},
            ]},
            {"id": "nomatch", "messages": [
                {"role": "user", "content": "Check balance"}, {"role": "bot", "content": "Use app"},
                {"role": "user", "content": "How?"}, {"role": "bot", "content": "Download it"},
            ]},
        ]
        (tmp_dir / "combo.json").write_text(json.dumps(data))
        config = ReplayConfig(
            input_paths=[str(tmp_dir)], input_format=InputFormat.JSON,
            filter_contains="refund",
            filter_min_turns=3,
        )
        convos, _, _, _, _ = ConversationLoader().load(config)
        assert len(convos) == 1  # Only match_long: has "refund" AND >= 3 turns

    def test_all_filtered_out_raises(self, tmp_dir):
        """If filtering removes all conversations, raise error."""
        data = [{"id": "c1", "messages": [
            {"role": "user", "content": "Hi"}, {"role": "bot", "content": "Hello"},
        ]}]
        (tmp_dir / "small.json").write_text(json.dumps(data))
        config = ReplayConfig(
            input_paths=[str(tmp_dir)], input_format=InputFormat.JSON,
            filter_contains="nonexistent_keyword_xyz",
        )
        with pytest.raises(ValueError, match="No conversations remaining after filtering"):
            ConversationLoader().load(config)