"""
Dataset Exporter - Export simulation results in various formats for evaluation and fine-tuning.
Supports JSONL, CSV, DPO pairs, and eval platform integrations.
"""

from __future__ import annotations

import csv
import json
from io import StringIO
from pathlib import Path

from src.core.logging import get_logger
from src.models import JudgedConversation, JudgmentLabel, SimulationReport

logger = get_logger(__name__)


class DatasetExporter:
    """
    Export judged conversations in multiple formats.
    """

    def export_jsonl(
        self,
        report: SimulationReport,
        output_path: str | Path,
    ) -> Path:
        """Export all conversations as JSONL (one conversation per line)."""
        path = Path(output_path)
        with open(path, "w") as f:
            for jc in report.judged_conversations:
                record = {
                    "conversation_id": jc.conversation.id,
                    "persona_id": jc.conversation.persona_id,
                    "persona_name": jc.persona.name,
                    "overall_score": jc.overall_score,
                    "pass_rate": jc.pass_rate,
                    "failure_modes": jc.failure_modes,
                    "turns": [
                        {
                            "speaker": t.speaker,
                            "message": t.message,
                            "latency_ms": t.latency_ms,
                        }
                        for t in jc.conversation.turns
                    ],
                    "judgments": [
                        {
                            "turn_id": jt.turn.id,
                            "overall_score": jt.overall_score,
                            "overall_label": jt.overall_label.value,
                            "issues": jt.issues,
                            "judge_results": [
                                {
                                    "judge": j.judge_name,
                                    "passed": j.passed,
                                    "score": j.score,
                                    "message": j.message,
                                }
                                for j in jt.judgments
                            ],
                        }
                        for jt in jc.judged_turns
                    ],
                }
                f.write(json.dumps(record) + "\n")

        logger.info("exported_jsonl", path=str(path), conversations=len(report.judged_conversations))
        return path

    def export_csv(
        self,
        report: SimulationReport,
        output_path: str | Path,
    ) -> Path:
        """Export turn-level results as CSV."""
        path = Path(output_path)

        rows = []
        for jc in report.judged_conversations:
            for jt in jc.judged_turns:
                row = {
                    "conversation_id": jc.conversation.id,
                    "persona_name": jc.persona.name,
                    "persona_type": jc.persona.persona_type.value,
                    "bot_response": jt.turn.message[:500],
                    "overall_score": jt.overall_score,
                    "overall_label": jt.overall_label.value,
                    "latency_ms": jt.turn.latency_ms,
                    "issues": "; ".join(jt.issues),
                }
                # Add per-judge scores
                for j in jt.judgments:
                    row[f"{j.judge_name}_score"] = j.score
                    row[f"{j.judge_name}_passed"] = j.passed
                rows.append(row)

        if rows:
            fieldnames = list(rows[0].keys())
            with open(path, "w", newline="") as f:
                writer = csv.DictWriter(f, fieldnames=fieldnames)
                writer.writeheader()
                writer.writerows(rows)

        logger.info("exported_csv", path=str(path), rows=len(rows))
        return path

    def export_dpo_pairs(
        self,
        report: SimulationReport,
        output_path: str | Path,
    ) -> Path:
        """
        Export as DPO (Direct Preference Optimization) preference pairs.
        Pairs good responses (PASS) with bad responses (FAIL) for the same context.
        """
        path = Path(output_path)
        pairs = []

        for jc in report.judged_conversations:
            good_turns = [jt for jt in jc.judged_turns if jt.overall_label == JudgmentLabel.PASS]
            bad_turns = [jt for jt in jc.judged_turns if jt.overall_label == JudgmentLabel.FAIL]

            for good in good_turns:
                for bad in bad_turns:
                    # Find the user message preceding each
                    good_context = self._get_context_before(jc, good.turn.id)
                    bad_context = self._get_context_before(jc, bad.turn.id)

                    if good_context and bad_context:
                        pairs.append({
                            "prompt": good_context,
                            "chosen": good.turn.message,
                            "rejected": bad.turn.message,
                            "chosen_score": good.overall_score,
                            "rejected_score": bad.overall_score,
                        })

        with open(path, "w") as f:
            for pair in pairs:
                f.write(json.dumps(pair) + "\n")

        logger.info("exported_dpo_pairs", path=str(path), pairs=len(pairs))
        return path

    def export_summary_json(
        self,
        report: SimulationReport,
        output_path: str | Path,
    ) -> Path:
        """Export the report summary as JSON."""
        path = Path(output_path)
        summary_data = {
            "summary": report.summary.model_dump(mode="json"),
            "score_by_judge": report.score_by_judge,
            "score_by_persona_type": report.score_by_persona_type,
            "most_problematic_personas": report.most_problematic_personas,
            "failure_patterns": [fp.model_dump() for fp in report.failure_patterns],
            "recommendations": report.recommendations,
        }
        with open(path, "w") as f:
            json.dump(summary_data, f, indent=2, default=str)

        logger.info("exported_summary", path=str(path))
        return path

    def _get_context_before(self, jc: JudgedConversation, turn_id: str) -> str | None:
        """Get the conversation context before a specific turn."""
        turns = jc.conversation.turns
        for i, t in enumerate(turns):
            if t.id == turn_id and i > 0:
                # Return the preceding user message
                return turns[i - 1].message if turns[i - 1].speaker == "user" else None
        return None
