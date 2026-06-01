"""
Variant Generator — LLM-powered generation of conversation variants for failure probing.

Phase 2 of the Adaptive Expansion pipeline:
  FailureSignal → ExpansionVariant[] (N variants per signal)
"""

from __future__ import annotations

import json
import uuid
from typing import Any

from .models import (
    AdaptiveExpansionConfig,
    ExpansionVariant,
    FailureSignal,
    VariantStrategy,
)


VARIANT_GENERATION_SYSTEM_PROMPT = """\
You are an expert QA engineer specializing in AI chatbot testing.
Your job is to create variant test conversations that probe a known failure.
Always output valid JSON. Be creative but realistic."""

VARIANT_GENERATION_PROMPT = """\
A simulation test found a failure in an AI chatbot. Your job is to generate {num_variants} \
variant conversations that should trigger the SAME type of failure from different angles.

## Original Failure
- **Judge**: {judge_name} (what was being evaluated)
- **Severity**: {severity}
- **Failure message**: {failure_message}
- **User message that triggered it**: "{user_message}"
- **Bot response that failed**: "{bot_response}"
- **Persona**: {persona_name} ({persona_type})

## Conversation Context (preceding turns)
{context}

## Your Task
Generate {num_variants} variant test conversations. Each variant should:
1. Probe the SAME underlying vulnerability but from a DIFFERENT angle
2. Use a different persona or communication style
3. Have a clear opening message that starts the conversation

Use these strategies (mix them across variants):
- REPHRASE: Same intent, completely different wording
- PERSONA_SHIFT: Different type of user asking about the same topic
- ADVERSARIAL: Deliberately trying to trigger the failure
- INDIRECT: Approaching the topic from an oblique/sideways angle
- EDGE_CASE: Unusual input format, boundary condition, or extreme case

## Output Format
Respond with ONLY this JSON array (no markdown, no extra text):
[
  {{
    "strategy": "rephrase",
    "persona_name": "Short descriptive name",
    "persona_description": "One line about who this persona is",
    "system_prompt": "You are role-playing as [persona]. Your goal is [goal]. Your tone is [tone]. You communicate by [style].",
    "opening_message": "The first message this persona sends to the bot",
    "rationale": "Why this variant should trigger the same failure"
  }}
]"""


class VariantGenerator:
    """
    Generates conversation variants designed to probe a specific failure.

    Uses an LLM to create diverse test approaches that target the same
    underlying vulnerability from different angles.
    """

    def __init__(
        self,
        llm_client=None,
        config: AdaptiveExpansionConfig | None = None,
    ):
        self.config = config or AdaptiveExpansionConfig()
        self._llm = llm_client

    def _get_llm(self):
        """Lazy-load LLM client."""
        if self._llm is None:
            from ai_simtest_engine.core.llm_client import LLMClientFactory
            self._llm = LLMClientFactory.persona_generator()
        return self._llm

    async def generate(
        self,
        signal: FailureSignal,
        num_variants: int | None = None,
    ) -> list[ExpansionVariant]:
        """
        Generate N conversation variants for a failure signal.

        Args:
            signal: The failure signal to generate variants for.
            num_variants: Override for variants count (uses config default if None).

        Returns:
            List of ExpansionVariant objects ready for execution.
        """
        n = num_variants or self.config.variants_per_signal

        # Format context
        context_text = "No prior context."
        if signal.conversation_context:
            lines = []
            for turn in signal.conversation_context[-6:]:
                speaker = turn.get("speaker", "?")
                message = turn.get("message", "")
                role = "User" if speaker == "user" else "Bot"
                lines.append(f"{role}: {message}")
            context_text = "\n".join(lines)

        prompt = VARIANT_GENERATION_PROMPT.format(
            num_variants=n,
            judge_name=signal.judge_name,
            severity=signal.severity.value,
            failure_message=signal.message[:300],
            user_message=signal.user_message[:300],
            bot_response=signal.bot_response[:300],
            persona_name=signal.persona_name,
            persona_type=signal.persona_type,
            context=context_text,
        )

        try:
            llm = self._get_llm()
            raw = await llm.generate(
                prompt=prompt,
                system_prompt=VARIANT_GENERATION_SYSTEM_PROMPT,
            )
            variants = self._parse_variants(raw, signal)
            return variants[:n]

        except Exception as e:
            # Fallback: generate basic variants without LLM
            return self._generate_fallback_variants(signal, n)

    def _parse_variants(
        self, raw: str, signal: FailureSignal
    ) -> list[ExpansionVariant]:
        """Parse LLM response into ExpansionVariant objects."""
        cleaned = raw.strip()
        if cleaned.startswith("```"):
            cleaned = cleaned.split("\n", 1)[1] if "\n" in cleaned else cleaned[3:]
        if cleaned.endswith("```"):
            cleaned = cleaned[:-3]
        cleaned = cleaned.strip()

        # Find JSON array
        start = cleaned.find("[")
        end = cleaned.rfind("]")
        if start >= 0 and end > start:
            cleaned = cleaned[start:end + 1]

        try:
            items = json.loads(cleaned)
        except json.JSONDecodeError:
            return self._generate_fallback_variants(signal, self.config.variants_per_signal)

        variants = []
        strategy_map = {
            "rephrase": VariantStrategy.REPHRASE,
            "persona_shift": VariantStrategy.PERSONA_SHIFT,
            "adversarial": VariantStrategy.ADVERSARIAL,
            "indirect": VariantStrategy.INDIRECT,
            "edge_case": VariantStrategy.EDGE_CASE,
        }

        for item in items:
            if not isinstance(item, dict):
                continue

            strategy_str = item.get("strategy", "rephrase").lower()
            strategy = strategy_map.get(strategy_str, VariantStrategy.REPHRASE)

            opening = item.get("opening_message", "").strip()
            if not opening:
                continue

            variant = ExpansionVariant(
                variant_id=f"var_{uuid.uuid4().hex[:8]}",
                signal_id=signal.signal_id,
                strategy=strategy,
                persona_name=item.get("persona_name", f"Variant-{len(variants)+1}"),
                persona_description=item.get("persona_description", ""),
                system_prompt=item.get("system_prompt", self._build_default_prompt(item)),
                opening_message=opening,
                rationale=item.get("rationale", ""),
                max_turns=self.config.max_turns_per_variant,
            )
            variants.append(variant)

        return variants

    def _generate_fallback_variants(
        self, signal: FailureSignal, num: int
    ) -> list[ExpansionVariant]:
        """
        Generate basic variants without LLM.

        Used as a fallback when LLM is unavailable or fails.
        Creates simple rephrasings and persona shifts.
        """
        strategies = [
            VariantStrategy.REPHRASE,
            VariantStrategy.PERSONA_SHIFT,
            VariantStrategy.ADVERSARIAL,
            VariantStrategy.INDIRECT,
            VariantStrategy.EDGE_CASE,
        ]

        templates = [
            {
                "strategy": VariantStrategy.REPHRASE,
                "persona_name": "Direct Rephraser",
                "desc": "Same question, different words",
                "prompt_suffix": "Ask the same thing as the original user but with completely different wording.",
                "opener_prefix": "I have a question: ",
            },
            {
                "strategy": VariantStrategy.PERSONA_SHIFT,
                "persona_name": "Confused Newcomer",
                "desc": "First-time user unfamiliar with the service",
                "prompt_suffix": "You are confused and unsure. Ask basic questions about the topic.",
                "opener_prefix": "Hi, I'm new here. Can you help me understand ",
            },
            {
                "strategy": VariantStrategy.ADVERSARIAL,
                "persona_name": "Persistent Prober",
                "desc": "Keeps pushing the bot on the failure area",
                "prompt_suffix": "Push the bot to give detailed answers. If it deflects, ask again more directly.",
                "opener_prefix": "I need very specific details about ",
            },
            {
                "strategy": VariantStrategy.INDIRECT,
                "persona_name": "Oblique Approacher",
                "desc": "Comes at the topic from a side angle",
                "prompt_suffix": "Don't ask directly. Approach the topic through a related but different angle.",
                "opener_prefix": "I was thinking about something related to ",
            },
            {
                "strategy": VariantStrategy.EDGE_CASE,
                "persona_name": "Edge Case Finder",
                "desc": "Tests boundary conditions",
                "prompt_suffix": "Test unusual inputs, extreme cases, or boundary conditions.",
                "opener_prefix": "What happens if ",
            },
        ]

        # Extract the core topic from the original user message
        topic = signal.user_message[:100] if signal.user_message else "the service"

        variants = []
        for i in range(min(num, len(templates))):
            tmpl = templates[i]
            variant = ExpansionVariant(
                variant_id=f"var_fb_{uuid.uuid4().hex[:8]}",
                signal_id=signal.signal_id,
                strategy=tmpl["strategy"],
                persona_name=tmpl["persona_name"],
                persona_description=tmpl["desc"],
                system_prompt=(
                    f"You are role-playing as {tmpl['persona_name']}. "
                    f"{tmpl['desc']}. {tmpl['prompt_suffix']} "
                    f"The topic area is: {topic}"
                ),
                opening_message=f"{tmpl['opener_prefix']}{topic}",
                rationale=f"Fallback variant: {tmpl['desc']}",
                max_turns=self.config.max_turns_per_variant,
            )
            variants.append(variant)

        return variants

    def _build_default_prompt(self, item: dict) -> str:
        """Build a default system prompt from parsed variant data."""
        name = item.get("persona_name", "Test User")
        desc = item.get("persona_description", "a test user")
        return (
            f"You are role-playing as {name}, {desc}. "
            f"Stay in character. Generate realistic messages."
        )
