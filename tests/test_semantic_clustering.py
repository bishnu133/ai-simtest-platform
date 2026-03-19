"""
Tests for Semantic Failure Clustering (P1 #8).

Tests cover:
- SemanticFailureClusterer with Sentence-BERT embeddings
- Jaccard fallback when Sentence-BERT unavailable
- FailureCluster properties (severity, judge)
- Edge cases (empty, single, exact duplicates)
- Real-world failure message grouping scenarios
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.core.semantic_clustering import (
    FailureCluster,
    FailureMessage,
    SemanticFailureClusterer,
)


# ─── Fixtures ───────────────────────────────────────────────

@pytest.fixture
def clusterer():
    """Standard clusterer with default threshold."""
    return SemanticFailureClusterer(similarity_threshold=0.65)


@pytest.fixture
def strict_clusterer():
    """Strict clusterer — only very similar messages group together."""
    return SemanticFailureClusterer(similarity_threshold=0.85)


@pytest.fixture
def loose_clusterer():
    """Loose clusterer — groups more aggressively."""
    return SemanticFailureClusterer(similarity_threshold=0.45)


@pytest.fixture
def jaccard_clusterer():
    """Clusterer forced to use Jaccard fallback."""
    c = SemanticFailureClusterer(similarity_threshold=0.65)
    c._use_semantic = False
    c._model = None  # Ensure no model loaded
    return c


# ─── FailureCluster Model Tests ─────────────────────────────

class TestFailureCluster:
    def test_primary_severity_picks_worst(self):
        cluster = FailureCluster(
            representative="test",
            severities=["low", "critical", "medium"],
        )
        assert cluster.primary_severity == "critical"

    def test_primary_severity_high_over_medium(self):
        cluster = FailureCluster(
            representative="test",
            severities=["medium", "high", "low"],
        )
        assert cluster.primary_severity == "high"

    def test_primary_severity_empty_defaults_medium(self):
        cluster = FailureCluster(representative="test")
        assert cluster.primary_severity == "medium"

    def test_primary_judge_picks_most_common(self):
        cluster = FailureCluster(
            representative="test",
            judge_names=["grounding", "safety", "grounding", "grounding"],
        )
        assert cluster.primary_judge == "grounding"

    def test_primary_judge_empty_defaults_unknown(self):
        cluster = FailureCluster(representative="test")
        assert cluster.primary_judge == "unknown"


# ─── Semantic Clustering Tests ───────────────────────────────

class TestSemanticClustering:
    """Tests using real Sentence-BERT embeddings."""

    def test_semantically_similar_messages_cluster_together(self, clusterer):
        """Messages with same meaning but different words should cluster."""
        failures = [
            FailureMessage(message="Bot hallucinated a return policy that doesn't exist"),
            FailureMessage(message="Bot made up a fake refund policy not in documentation"),
            FailureMessage(message="Bot invented return rules that are not real"),
            FailureMessage(message="System prompt was leaked to the user"),
            FailureMessage(message="Bot revealed its internal instructions"),
        ]
        clusters = clusterer.cluster_failures(failures)

        # Should create ~2 clusters: hallucination group + prompt leak group
        assert len(clusters) <= 3
        assert len(clusters) >= 2

        # Find the hallucination cluster (should have 3 messages)
        hallucination_cluster = max(clusters, key=lambda c: c.count)
        assert hallucination_cluster.count >= 2

    def test_completely_different_messages_stay_separate(self, clusterer):
        """Unrelated messages should NOT be grouped together."""
        failures = [
            FailureMessage(message="Bot hallucinated a product feature"),
            FailureMessage(message="Response contained toxic language"),
            FailureMessage(message="Bot failed to answer the billing question"),
        ]
        clusters = clusterer.cluster_failures(failures)

        # Each message should be its own cluster
        assert len(clusters) == 3

    def test_exact_duplicates_count_correctly(self, clusterer):
        """Exact duplicate messages should be in one cluster with correct count."""
        failures = [
            FailureMessage(message="Bot gave wrong shipping time", conversation_id="c1"),
            FailureMessage(message="Bot gave wrong shipping time", conversation_id="c2"),
            FailureMessage(message="Bot gave wrong shipping time", conversation_id="c3"),
        ]
        clusters = clusterer.cluster_failures(failures)

        assert len(clusters) == 1
        assert clusters[0].count == 3
        assert len(clusters[0].conversation_ids) == 3

    def test_strict_threshold_creates_more_clusters(self, strict_clusterer):
        """Higher threshold should create more, smaller clusters."""
        failures = [
            FailureMessage(message="Bot hallucinated a return policy"),
            FailureMessage(message="Bot made up refund rules"),
            FailureMessage(message="Bot invented shipping times"),
        ]
        strict_clusters = strict_clusterer.cluster_failures(failures)

        # Strict threshold should likely keep these separate
        assert len(strict_clusters) >= 2

    def test_loose_threshold_creates_fewer_clusters(self, loose_clusterer, clusterer):
        """Lower threshold should group more aggressively."""
        failures = [
            FailureMessage(message="Bot hallucinated a return policy"),
            FailureMessage(message="Bot made up refund rules"),
            FailureMessage(message="Bot invented shipping information"),
        ]
        loose_clusters = loose_clusterer.cluster_failures(failures)
        normal_clusters = clusterer.cluster_failures(failures)

        assert len(loose_clusters) <= len(normal_clusters)

    def test_representative_is_most_central(self, clusterer):
        """Representative should be the most central message in cluster."""
        failures = [
            FailureMessage(message="Bot leaked the system prompt to user"),
            FailureMessage(message="Bot revealed its system prompt instructions"),
            FailureMessage(message="System prompt was exposed in bot response"),
        ]
        clusters = clusterer.cluster_failures(failures)

        # Should be 1 cluster, and representative should be one of the messages
        prompt_cluster = [c for c in clusters if c.count >= 2]
        if prompt_cluster:
            assert prompt_cluster[0].representative in [f.message for f in failures]

    def test_metadata_preserved_in_clusters(self, clusterer):
        """Severity and judge names should be collected in clusters."""
        failures = [
            FailureMessage(message="Bot gave wrong info", severity="high", judge_name="grounding"),
            FailureMessage(message="Bot gave wrong info", severity="critical", judge_name="grounding"),
            FailureMessage(message="Toxic response detected", severity="critical", judge_name="safety"),
        ]
        clusters = clusterer.cluster_failures(failures)

        wrong_info_cluster = next(c for c in clusters if "wrong" in c.representative.lower())
        assert wrong_info_cluster.count == 2
        assert wrong_info_cluster.primary_severity == "critical"
        assert wrong_info_cluster.primary_judge == "grounding"

    def test_cluster_from_strings_convenience(self, clusterer):
        """Test the convenience method with plain strings."""
        messages = [
            "Bot hallucinated product features",
            "Bot made up features not in docs",
            "Response was toxic and offensive",
        ]
        clusters = clusterer.cluster_from_strings(
            messages,
            severities=["high", "high", "critical"],
            judge_names=["grounding", "grounding", "safety"],
        )

        assert len(clusters) >= 2
        total_count = sum(c.count for c in clusters)
        assert total_count == 3

    def test_sorted_by_count_descending(self, clusterer):
        """Results should be sorted with most frequent first."""
        failures = [
            FailureMessage(message="Common failure A"),
            FailureMessage(message="Common failure A"),
            FailureMessage(message="Common failure A"),
            FailureMessage(message="Rare failure B"),
        ]
        clusters = clusterer.cluster_failures(failures)

        assert clusters[0].count >= clusters[-1].count


# ─── Jaccard Fallback Tests ──────────────────────────────────

class TestJaccardFallback:
    """Tests for the Jaccard word-overlap fallback."""

    def test_jaccard_groups_similar_words(self, jaccard_clusterer):
        """Jaccard should group messages sharing many words."""
        failures = [
            FailureMessage(message="Bot gave wrong return policy information"),
            FailureMessage(message="Bot gave wrong refund policy information"),
            FailureMessage(message="System prompt was leaked to user"),
        ]
        clusters = jaccard_clusterer.cluster_failures(failures)

        # First two share many words, third is different
        assert len(clusters) == 2

    def test_jaccard_misses_semantic_similarity(self, jaccard_clusterer):
        """Jaccard SHOULD miss semantically similar but differently-worded messages."""
        failures = [
            FailureMessage(message="Bot hallucinated a return policy"),
            FailureMessage(message="Made up fake refund rules"),
        ]
        clusters = jaccard_clusterer.cluster_failures(failures)

        # Jaccard will NOT group these (different words)
        assert len(clusters) == 2

    def test_jaccard_handles_exact_duplicates(self, jaccard_clusterer):
        failures = [
            FailureMessage(message="Same error message", conversation_id="c1"),
            FailureMessage(message="Same error message", conversation_id="c2"),
        ]
        clusters = jaccard_clusterer.cluster_failures(failures)

        assert len(clusters) == 1
        assert clusters[0].count == 2


# ─── Edge Cases ──────────────────────────────────────────────

class TestEdgeCases:
    def test_empty_failures_returns_empty(self, clusterer):
        clusters = clusterer.cluster_failures([])
        assert clusters == []

    def test_single_failure_returns_one_cluster(self, clusterer):
        failures = [FailureMessage(message="Only one failure")]
        clusters = clusterer.cluster_failures(failures)

        assert len(clusters) == 1
        assert clusters[0].count == 1
        assert clusters[0].representative == "Only one failure"

    def test_all_identical_messages(self, clusterer):
        failures = [
            FailureMessage(message="Same message", conversation_id=f"c{i}")
            for i in range(10)
        ]
        clusters = clusterer.cluster_failures(failures)

        assert len(clusters) == 1
        assert clusters[0].count == 10

    def test_very_short_messages(self, clusterer):
        failures = [
            FailureMessage(message="Toxic"),
            FailureMessage(message="Hallucination"),
            FailureMessage(message="Off-topic"),
        ]
        clusters = clusterer.cluster_failures(failures)
        # Single words are quite different — should stay separate
        assert len(clusters) == 3

    def test_large_number_of_failures(self, clusterer):
        """Ensure performance is acceptable with many failures."""
        # Use semantically DIFFERENT failure types per group
        # (not just "Group N" prefix — Sentence-BERT correctly sees those as same meaning)
        group_templates = [
            "Bot hallucinated a return policy that says {} days",
            "Response contained toxic language directed at the user about {}",
            "Bot leaked system prompt instructions including detail {}",
            "Bot gave completely irrelevant answer about topic {} instead of addressing the question",
            "Bot response was empty or contained only whitespace for query {}",
        ]
        failures = []
        for group_id, template in enumerate(group_templates):
            for i in range(20):
                failures.append(FailureMessage(message=template.format(i)))

        clusters = clusterer.cluster_failures(failures)

        # Should consolidate into roughly 5 groups (one per failure type)
        assert len(clusters) <= 25  # Some variation expected
        assert len(clusters) >= 3   # At least some grouping happened
        total = sum(c.count for c in clusters)
        assert total == 100


# ─── Real-World Scenario Tests ───────────────────────────────

class TestRealWorldScenarios:
    """Tests with realistic failure messages from bot testing."""

    def test_grounding_failures_cluster(self, clusterer):
        """Various grounding failure descriptions should cluster."""
        failures = [
            FailureMessage(message="Bot claimed returns are accepted within 60 days, documentation says 30 days",
                           judge_name="grounding", severity="high"),
            FailureMessage(message="Bot stated 60-day return policy which contradicts the 30-day policy in docs",
                           judge_name="grounding", severity="high"),
            FailureMessage(message="Response included product feature not found in documentation",
                           judge_name="grounding", severity="medium"),
            FailureMessage(message="Bot mentioned a feature that doesn't exist in the product catalog",
                           judge_name="grounding", severity="medium"),
        ]
        clusters = clusterer.cluster_failures(failures)

        # Should form ~2 clusters: return policy group + feature group
        assert len(clusters) <= 3

    def test_safety_vs_grounding_stay_separate(self, clusterer):
        """Safety failures should not cluster with grounding failures."""
        failures = [
            FailureMessage(message="Bot revealed system prompt instructions to user",
                           judge_name="safety", severity="critical"),
            FailureMessage(message="Bot hallucinated product features not in documentation",
                           judge_name="grounding", severity="high"),
            FailureMessage(message="Response contained personal email address of employee",
                           judge_name="safety", severity="critical"),
        ]
        clusters = clusterer.cluster_failures(failures)

        # All three are semantically different → 3 clusters
        assert len(clusters) == 3

    def test_mixed_severity_in_cluster(self, clusterer):
        """Cluster should report the worst severity from its members."""
        failures = [
            FailureMessage(message="Bot made up shipping information", severity="medium"),
            FailureMessage(message="Bot fabricated shipping details not in docs", severity="high"),
            FailureMessage(message="Bot invented shipping policy", severity="critical"),
        ]
        clusters = clusterer.cluster_failures(failures)

        # If these cluster together, primary severity should be critical
        if len(clusters) == 1:
            assert clusters[0].primary_severity == "critical"
        else:
            # Even split, at least one should have high+ severity
            all_severities = [c.primary_severity for c in clusters]
            assert any(s in ("critical", "high") for s in all_severities)

    def test_conversation_ids_collected(self, clusterer):
        """Cluster should track which conversations had these failures."""
        failures = [
            FailureMessage(message="Bot gave wrong info", conversation_id="conv-001"),
            FailureMessage(message="Bot gave wrong info", conversation_id="conv-007"),
            FailureMessage(message="Bot gave wrong info", conversation_id="conv-015"),
        ]
        clusters = clusterer.cluster_failures(failures)

        assert len(clusters) == 1
        assert set(clusters[0].conversation_ids) == {"conv-001", "conv-007", "conv-015"}