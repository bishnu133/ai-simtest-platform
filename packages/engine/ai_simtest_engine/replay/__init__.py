"""
Conversation Replay Module — Import real production conversations
and evaluate them with AI SimTest's judge pipeline.

Usage:
    from ai_simtest_engine.replay import ConversationLoader, ReplayEvaluator, ReplayConfig

    config = ReplayConfig(input_paths=["./logs/chats.json"])
    loader = ConversationLoader()
    conversations, personas, sources = loader.load(config)

    evaluator = ReplayEvaluator()
    report, replay_result = await evaluator.evaluate(
        conversations, personas, sources, config
    )
"""

from ai_simtest_engine.replay.evaluator import ReplayEvaluator
from ai_simtest_engine.replay.loader import ConversationLoader
from ai_simtest_engine.replay.models import (
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
from ai_simtest_engine.replay.parsers import detect_format, get_parser, register_parser

__all__ = [
    "ConversationLoader",
    "ReplayEvaluator",
    "ReplayConfig",
    "ReplayMode",
    "ReplayResult",
    "InputFormat",
    "PIIMaskingConfig",
    "PIIMaskingStrategy",
    "ConversationSource",
    "ConversationEvalResult",
    "ImportedConversation",
    "detect_format",
    "get_parser",
    "register_parser",
]
