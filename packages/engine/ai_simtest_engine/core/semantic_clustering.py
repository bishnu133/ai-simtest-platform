"""
Semantic Failure Clustering — Embedding-based failure pattern grouping.

Replaces Jaccard word-overlap similarity with Sentence-BERT embeddings
for significantly better failure grouping. Groups failures by meaning
rather than shared words.

Example improvement:
  Jaccard MISSES: "Bot hallucinated a return policy" vs "Made up refund rules"
  Semantic CATCHES: Both have similar meaning → same cluster

The Sentence-BERT model (all-MiniLM-L6-v2) is already loaded by the
Grounding Judge, so this adds zero new dependencies.

Usage:
    clusterer = SemanticFailureClusterer()
    patterns = clusterer.cluster_failures(failure_messages)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class FailureCluster:
    """A cluster of semantically similar failure messages."""
    representative: str  # Most central message (closest to centroid)
    messages: list[str] = field(default_factory=list)
    count: int = 0
    conversation_ids: list[str] = field(default_factory=list)
    severities: list[str] = field(default_factory=list)
    judge_names: list[str] = field(default_factory=list)

    @property
    def primary_severity(self) -> str:
        """Most severe severity in the cluster."""
        priority = {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}
        if not self.severities:
            return "medium"
        return min(self.severities, key=lambda s: priority.get(s.lower(), 5))

    @property
    def primary_judge(self) -> str:
        """Most common judge name in the cluster."""
        if not self.judge_names:
            return "unknown"
        from collections import Counter
        return Counter(self.judge_names).most_common(1)[0][0]


@dataclass
class FailureMessage:
    """A single failure message with metadata."""
    message: str
    conversation_id: str = ""
    severity: str = "medium"
    judge_name: str = "unknown"
    persona_type: str = ""


class SemanticFailureClusterer:
    """
    Clusters failure messages using Sentence-BERT embeddings.
    
    Falls back to Jaccard word-overlap if Sentence-BERT is not available,
    ensuring the system degrades gracefully.
    """

    def __init__(self, similarity_threshold: float = 0.65, model_name: str = "all-MiniLM-L6-v2"):
        """
        Args:
            similarity_threshold: Cosine similarity threshold for clustering (0-1).
                Higher = stricter grouping (fewer, tighter clusters).
                Default 0.65 is a good balance.
            model_name: Sentence-BERT model name (same as grounding judge).
        """
        self.similarity_threshold = similarity_threshold
        self.model_name = model_name
        self._model = None
        self._use_semantic = True

    def _load_model(self):
        """Lazy-load Sentence-BERT model. Falls back to Jaccard if unavailable."""
        if self._model is not None:
            return

        try:
            from sentence_transformers import SentenceTransformer
            self._model = SentenceTransformer(self.model_name)
            logger.info(f"Loaded Sentence-BERT model: {self.model_name}")
        except (ImportError, Exception) as e:
            logger.warning(f"Sentence-BERT not available ({e}), falling back to Jaccard similarity")
            self._use_semantic = False

    def cluster_failures(self, failures: list[FailureMessage]) -> list[FailureCluster]:
        """
        Cluster a list of failure messages into semantic groups.

        Args:
            failures: List of FailureMessage objects to cluster.

        Returns:
            List of FailureCluster objects, sorted by count (descending).
        """
        if not failures:
            return []

        # Deduplicate exact matches first
        unique_failures = self._deduplicate_exact(failures)

        if len(unique_failures) <= 1:
            if unique_failures:
                f = unique_failures[0]
                return [FailureCluster(
                    representative=f.message,
                    messages=[f.message],
                    count=sum(1 for x in failures if x.message == f.message),
                    conversation_ids=[x.conversation_id for x in failures if x.message == f.message],
                    severities=[x.severity for x in failures if x.message == f.message],
                    judge_names=[x.judge_name for x in failures if x.message == f.message],
                )]
            return []

        self._load_model()

        if self._use_semantic:
            clusters = self._cluster_semantic(unique_failures, failures)
        else:
            clusters = self._cluster_jaccard(unique_failures, failures)

        # Sort by count descending
        clusters.sort(key=lambda c: c.count, reverse=True)
        return clusters

    def cluster_from_strings(
        self,
        messages: list[str],
        severities: list[str] | None = None,
        judge_names: list[str] | None = None,
    ) -> list[FailureCluster]:
        """
        Convenience method: cluster from plain string lists.

        Args:
            messages: List of failure message strings.
            severities: Optional severity per message.
            judge_names: Optional judge name per message.

        Returns:
            List of FailureCluster objects.
        """
        failures = []
        for i, msg in enumerate(messages):
            failures.append(FailureMessage(
                message=msg,
                conversation_id=f"conv-{i}",
                severity=(severities[i] if severities and i < len(severities) else "medium"),
                judge_name=(judge_names[i] if judge_names and i < len(judge_names) else "unknown"),
            ))
        return self.cluster_failures(failures)

    # ─── Semantic Clustering (Sentence-BERT) ────────────────────

    def _cluster_semantic(
        self,
        unique_failures: list[FailureMessage],
        all_failures: list[FailureMessage],
    ) -> list[FailureCluster]:
        """Cluster using Sentence-BERT cosine similarity."""
        import numpy as np

        messages = [f.message for f in unique_failures]
        embeddings = self._model.encode(messages, convert_to_numpy=True, normalize_embeddings=True)

        # Compute cosine similarity matrix (embeddings are already normalized)
        sim_matrix = np.dot(embeddings, embeddings.T)

        # Greedy clustering: assign each message to the first cluster it's similar enough to
        clusters: list[list[int]] = []
        assigned = set()

        for i in range(len(messages)):
            if i in assigned:
                continue

            # Start a new cluster with this message
            cluster_indices = [i]
            assigned.add(i)

            # Find all unassigned messages similar to this one
            for j in range(i + 1, len(messages)):
                if j in assigned:
                    continue
                if sim_matrix[i][j] >= self.similarity_threshold:
                    cluster_indices.append(j)
                    assigned.add(j)

            clusters.append(cluster_indices)

        # Build FailureCluster objects
        result = []
        for cluster_indices in clusters:
            cluster_messages = [messages[i] for i in cluster_indices]

            # Representative = the message most similar to all others in cluster
            if len(cluster_indices) == 1:
                representative = messages[cluster_indices[0]]
            else:
                sub_embeddings = embeddings[cluster_indices]
                centroid = np.mean(sub_embeddings, axis=0)
                centroid = centroid / np.linalg.norm(centroid)
                similarities = np.dot(sub_embeddings, centroid)
                best_idx = cluster_indices[int(np.argmax(similarities))]
                representative = messages[best_idx]

            # Collect all matching failures (including duplicates)
            matched_failures = [
                f for f in all_failures
                if f.message in cluster_messages
            ]

            result.append(FailureCluster(
                representative=representative,
                messages=list(set(cluster_messages)),
                count=len(matched_failures),
                conversation_ids=list(set(f.conversation_id for f in matched_failures if f.conversation_id)),
                severities=[f.severity for f in matched_failures],
                judge_names=[f.judge_name for f in matched_failures],
            ))

        return result

    # ─── Jaccard Fallback ───────────────────────────────────────

    def _cluster_jaccard(
        self,
        unique_failures: list[FailureMessage],
        all_failures: list[FailureMessage],
    ) -> list[FailureCluster]:
        """Fallback clustering using Jaccard word-overlap similarity."""
        messages = [f.message for f in unique_failures]
        word_sets = [set(m.lower().split()) for m in messages]

        clusters: list[list[int]] = []
        assigned = set()

        for i in range(len(messages)):
            if i in assigned:
                continue

            cluster_indices = [i]
            assigned.add(i)

            for j in range(i + 1, len(messages)):
                if j in assigned:
                    continue

                # Jaccard similarity
                intersection = len(word_sets[i] & word_sets[j])
                union = len(word_sets[i] | word_sets[j])
                similarity = intersection / union if union > 0 else 0

                if similarity >= 0.5:  # Jaccard threshold (matches original)
                    cluster_indices.append(j)
                    assigned.add(j)

            clusters.append(cluster_indices)

        # Build FailureCluster objects
        result = []
        for cluster_indices in clusters:
            cluster_messages = [messages[i] for i in cluster_indices]
            representative = cluster_messages[0]

            matched_failures = [
                f for f in all_failures
                if f.message in cluster_messages
            ]

            result.append(FailureCluster(
                representative=representative,
                messages=list(set(cluster_messages)),
                count=len(matched_failures),
                conversation_ids=list(set(f.conversation_id for f in matched_failures if f.conversation_id)),
                severities=[f.severity for f in matched_failures],
                judge_names=[f.judge_name for f in matched_failures],
            ))

        return result

    # ─── Helpers ────────────────────────────────────────────────

    @staticmethod
    def _deduplicate_exact(failures: list[FailureMessage]) -> list[FailureMessage]:
        """Remove exact duplicate messages, keeping first occurrence."""
        seen = set()
        unique = []
        for f in failures:
            if f.message not in seen:
                seen.add(f.message)
                unique.append(f)
        return unique
