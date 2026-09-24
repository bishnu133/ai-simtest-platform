"""
Tool Metric Evaluators — Phase 4 of P3 #14 RAG/Tool Evaluation Framework.

Consumes ResponseAnnotation from the Annotator and scores each Tool metric.

Metric classification (from approved design):
  DETERMINISTIC: parameter_accuracy, sequence_correctness, retry_behavior,
                 timeout_handling, parallel_execution, permission_respect
  HYBRID:        tool_selection, error_handling, unnecessary_calls
  LLM-ONLY:      result_integration, missing_calls, fallback_behavior, side_effect_awareness

Each evaluator takes a ResponseAnnotation and optional tool_definitions (expected
tools/schemas from config) and returns MetricResult.
"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional

from ai_simtest_engine.rag_eval.models import (
    EvalSpeed,
    JudgeReliability,
    MetricResult,
    RAGEvalConfig,
    ResponseAnnotation,
    ToolCall,
    ToolMetricType,
    TOOL_METRIC_RELIABILITY,
    SPEED_MODE_INCLUDES,
)


# ============================================================================
# Tool Definition Schema (what tools SHOULD exist)
# ============================================================================

class ToolDefinition:
    """Expected tool definition from config — what the bot SHOULD call."""

    def __init__(
        self,
        name: str,
        description: str = "",
        required_params: Optional[List[str]] = None,
        optional_params: Optional[List[str]] = None,
        param_types: Optional[Dict[str, str]] = None,
        has_side_effects: bool = False,
        requires_permission: bool = False,
        expected_sequence_position: Optional[int] = None,
    ):
        self.name = name
        self.description = description
        self.required_params = required_params or []
        self.optional_params = optional_params or []
        self.param_types = param_types or {}
        self.has_side_effects = has_side_effects
        self.requires_permission = requires_permission
        self.expected_sequence_position = expected_sequence_position

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "ToolDefinition":
        return cls(
            name=d.get("name", ""),
            description=d.get("description", ""),
            required_params=d.get("required_params", []),
            optional_params=d.get("optional_params", []),
            param_types=d.get("param_types", {}),
            has_side_effects=d.get("has_side_effects", False),
            requires_permission=d.get("requires_permission", False),
            expected_sequence_position=d.get("expected_sequence_position"),
        )


# ============================================================================
# Tool Metric Evaluator
# ============================================================================

class ToolMetricEvaluator:
    """
    Evaluates all 13 tool/function-calling metrics against a ResponseAnnotation.

    Usage:
        evaluator = ToolMetricEvaluator(config, tool_definitions=[...], llm_client=llm)
        results = await evaluator.evaluate(annotation)
    """

    def __init__(
        self,
        config: Optional[RAGEvalConfig] = None,
        tool_definitions: Optional[List[ToolDefinition]] = None,
        llm_client: Optional[Any] = None,
    ):
        self._config = config or RAGEvalConfig()
        self._tool_defs = tool_definitions or []
        self._tool_def_map = {td.name.lower(): td for td in self._tool_defs}
        self._llm = llm_client

        self._evaluators = {
            ToolMetricType.PARAMETER_ACCURACY: self._eval_parameter_accuracy,
            ToolMetricType.SEQUENCE_CORRECTNESS: self._eval_sequence_correctness,
            ToolMetricType.RETRY_BEHAVIOR: self._eval_retry_behavior,
            ToolMetricType.TIMEOUT_HANDLING: self._eval_timeout_handling,
            ToolMetricType.PARALLEL_EXECUTION: self._eval_parallel_execution,
            ToolMetricType.PERMISSION_RESPECT: self._eval_permission_respect,
            ToolMetricType.TOOL_SELECTION: self._eval_tool_selection,
            ToolMetricType.ERROR_HANDLING: self._eval_error_handling,
            ToolMetricType.UNNECESSARY_CALLS: self._eval_unnecessary_calls,
            ToolMetricType.RESULT_INTEGRATION: self._eval_result_integration,
            ToolMetricType.MISSING_CALLS: self._eval_missing_calls,
            ToolMetricType.FALLBACK_BEHAVIOR: self._eval_fallback_behavior,
            ToolMetricType.SIDE_EFFECT_AWARENESS: self._eval_side_effect_awareness,
        }

    # ================================================================
    # Public API
    # ================================================================

    async def evaluate(self, annotation: ResponseAnnotation) -> List[MetricResult]:
        """Evaluate all eligible tool metrics for this annotation."""
        results = []

        for metric_type, evaluator_fn in self._evaluators.items():
            if not self._config.is_metric_eligible(metric_type.value):
                continue

            if (self._config.enabled_tool_metrics
                    and metric_type not in self._config.enabled_tool_metrics):
                continue

            reliability = TOOL_METRIC_RELIABILITY[metric_type]
            if reliability == JudgeReliability.LLM and not self._llm:
                continue

            try:
                result = await evaluator_fn(annotation)
                threshold = self._config.get_threshold(metric_type.value)
                result.threshold = threshold
                result.passed = result.score >= threshold
                results.append(result)
            except Exception as e:
                results.append(MetricResult(
                    metric_name=metric_type.value,
                    metric_type="tool",
                    score=0.0,
                    passed=False,
                    reliability=reliability.value,
                    message=f"Evaluation error: {str(e)[:150]}",
                    issues=[f"Error: {str(e)[:200]}"],
                ))

        return results

    # ================================================================
    # DETERMINISTIC METRICS
    # ================================================================

    async def _eval_parameter_accuracy(
        self, annotation: ResponseAnnotation
    ) -> MetricResult:
        """
        Are tool call parameters correct against definitions?

        Checks: required params present, types match, no unknown params.
        """
        if not annotation.tool_calls:
            return self._no_tools_result("parameter_accuracy")

        if not self._tool_defs:
            return MetricResult(
                metric_name="parameter_accuracy",
                metric_type="tool",
                score=1.0,
                reliability="deterministic",
                confidence=annotation.overall_confidence * 0.5,
                message="No tool definitions to validate against",
            )

        total_checks = 0
        passed_checks = 0
        issues = []

        for tc in annotation.tool_calls:
            tool_def = self._tool_def_map.get(tc.tool_name.lower())
            if not tool_def:
                continue

            # Check required params
            for param in tool_def.required_params:
                total_checks += 1
                if param in tc.arguments:
                    passed_checks += 1
                else:
                    issues.append(f"{tc.tool_name}: missing required param '{param}'")

            # Check param types
            for param_name, expected_type in tool_def.param_types.items():
                if param_name in tc.arguments:
                    total_checks += 1
                    actual_value = tc.arguments[param_name]
                    if self._check_type(actual_value, expected_type):
                        passed_checks += 1
                    else:
                        issues.append(
                            f"{tc.tool_name}: param '{param_name}' expected {expected_type}, "
                            f"got {type(actual_value).__name__}"
                        )

        if total_checks == 0:
            return MetricResult(
                metric_name="parameter_accuracy",
                metric_type="tool",
                score=1.0,
                reliability="deterministic",
                confidence=annotation.overall_confidence,
                message="No parameter checks applicable",
            )

        score = passed_checks / total_checks

        return MetricResult(
            metric_name="parameter_accuracy",
            metric_type="tool",
            score=score,
            reliability="deterministic",
            confidence=annotation.overall_confidence,
            message=f"{passed_checks}/{total_checks} parameter checks passed",
            evidence={
                "total_checks": total_checks,
                "passed": passed_checks,
            },
            issues=issues[:5],
        )

    async def _eval_sequence_correctness(
        self, annotation: ResponseAnnotation
    ) -> MetricResult:
        """
        Were tools called in the correct order?

        Deterministic: compares actual call order against expected_sequence_position.
        """
        if not annotation.tool_calls or not self._tool_defs:
            return self._no_tools_result("sequence_correctness")

        # Build expected sequence from definitions
        expected_order = []
        for td in self._tool_defs:
            if td.expected_sequence_position is not None:
                expected_order.append((td.expected_sequence_position, td.name.lower()))
        expected_order.sort()

        if not expected_order:
            return MetricResult(
                metric_name="sequence_correctness",
                metric_type="tool",
                score=1.0,
                reliability="deterministic",
                confidence=annotation.overall_confidence,
                message="No sequence constraints defined",
            )

        # Get actual call order
        actual_order = [tc.tool_name.lower() for tc in annotation.tool_calls]

        # Check: are the sequenced tools in correct relative order?
        correct_pairs = 0
        total_pairs = 0
        issues = []

        for i in range(len(expected_order)):
            for j in range(i + 1, len(expected_order)):
                _, tool_a = expected_order[i]
                _, tool_b = expected_order[j]

                # Both must appear in actual calls
                if tool_a in actual_order and tool_b in actual_order:
                    total_pairs += 1
                    idx_a = actual_order.index(tool_a)
                    idx_b = actual_order.index(tool_b)
                    if idx_a < idx_b:
                        correct_pairs += 1
                    else:
                        issues.append(f"'{tool_a}' should come before '{tool_b}'")

        if total_pairs == 0:
            return MetricResult(
                metric_name="sequence_correctness",
                metric_type="tool",
                score=1.0,
                reliability="deterministic",
                confidence=annotation.overall_confidence,
                message="No sequenced tools found in actual calls",
            )

        score = correct_pairs / total_pairs

        return MetricResult(
            metric_name="sequence_correctness",
            metric_type="tool",
            score=score,
            reliability="deterministic",
            confidence=annotation.overall_confidence,
            message=f"{correct_pairs}/{total_pairs} sequence pairs correct",
            evidence={
                "expected_order": [name for _, name in expected_order],
                "actual_order": actual_order,
            },
            issues=issues[:3],
        )

    async def _eval_retry_behavior(
        self, annotation: ResponseAnnotation
    ) -> MetricResult:
        """
        Does the bot retry appropriately on transient failures?

        Deterministic: checks if failed tools were retried.
        """
        if not annotation.tool_calls:
            return self._no_tools_result("retry_behavior")

        failed_tools = [tc for tc in annotation.tool_calls if tc.error]
        if not failed_tools:
            return MetricResult(
                metric_name="retry_behavior",
                metric_type="tool",
                score=1.0,
                reliability="deterministic",
                confidence=annotation.overall_confidence,
                message="No tool failures to retry",
            )

        # Check for retries: same tool called after a failure
        tool_call_names = [tc.tool_name.lower() for tc in annotation.tool_calls]
        retried = 0
        not_retried = []

        for ftc in failed_tools:
            name = ftc.tool_name.lower()
            # Find position of this failure
            for i, tc in enumerate(annotation.tool_calls):
                if tc is ftc:
                    # Check if same tool called later
                    remaining = tool_call_names[i + 1:]
                    if name in remaining:
                        retried += 1
                    else:
                        not_retried.append(name)
                    break

        total = len(failed_tools)
        score = retried / total if total > 0 else 1.0

        return MetricResult(
            metric_name="retry_behavior",
            metric_type="tool",
            score=score,
            reliability="deterministic",
            confidence=annotation.overall_confidence,
            message=f"{retried}/{total} failed tools were retried",
            evidence={"failed_tools": total, "retried": retried},
            issues=[f"'{t}' failed but was not retried" for t in not_retried[:3]],
        )

    async def _eval_timeout_handling(
        self, annotation: ResponseAnnotation
    ) -> MetricResult:
        """
        Does the bot handle slow tool responses appropriately?

        Deterministic: checks for timeout errors and whether they're handled.
        """
        if not annotation.tool_calls:
            return self._no_tools_result("timeout_handling")

        timeout_errors = []
        for tc in annotation.tool_calls:
            if tc.error and any(kw in tc.error.lower() for kw in ("timeout", "timed out", "deadline")):
                timeout_errors.append(tc)
            elif tc.duration_ms and tc.duration_ms > 30000:
                timeout_errors.append(tc)

        if not timeout_errors:
            return MetricResult(
                metric_name="timeout_handling",
                metric_type="tool",
                score=1.0,
                reliability="deterministic",
                confidence=annotation.overall_confidence,
                message="No timeout issues detected",
            )

        # Check if response acknowledges the timeout gracefully
        response_lower = annotation.response_text.lower()
        graceful_indicators = [
            "taking longer", "please wait", "unable to", "try again",
            "experiencing delays", "temporarily unavailable", "couldn't retrieve",
            "sorry", "apologize", "issue",
        ]
        handled = any(ind in response_lower for ind in graceful_indicators)

        return MetricResult(
            metric_name="timeout_handling",
            metric_type="tool",
            score=1.0 if handled else 0.3,
            reliability="deterministic",
            confidence=annotation.overall_confidence,
            message=f"{len(timeout_errors)} timeout(s) {'handled gracefully' if handled else 'not acknowledged'}",
            evidence={"timeout_count": len(timeout_errors), "graceful": handled},
            issues=[] if handled else ["Timeout not acknowledged in response"],
        )

    async def _eval_parallel_execution(
        self, annotation: ResponseAnnotation
    ) -> MetricResult:
        """
        Were independent tools executed in parallel when possible?

        Deterministic: checks timestamps for overlapping execution.
        """
        if len(annotation.tool_calls) < 2:
            return self._no_tools_result("parallel_execution")

        # Check for timestamp overlap (parallel execution)
        timed_calls = [tc for tc in annotation.tool_calls if tc.timestamp]
        if len(timed_calls) < 2:
            return MetricResult(
                metric_name="parallel_execution",
                metric_type="tool",
                score=1.0,
                reliability="deterministic",
                confidence=annotation.overall_confidence * 0.5,
                message="Insufficient timestamp data to assess parallelism",
            )

        # Simple check: if all calls have similar timestamps, likely parallel
        # For now, just check if multiple tools exist (can't prove parallelism from
        # timestamps alone without more granular data)
        unique_tools = len(set(tc.tool_name for tc in annotation.tool_calls))

        return MetricResult(
            metric_name="parallel_execution",
            metric_type="tool",
            score=1.0,
            reliability="deterministic",
            confidence=annotation.overall_confidence * 0.5,
            message=f"{unique_tools} unique tools called — parallelism assessment limited",
            evidence={"unique_tools": unique_tools, "total_calls": len(annotation.tool_calls)},
        )

    async def _eval_permission_respect(
        self, annotation: ResponseAnnotation
    ) -> MetricResult:
        """
        Does the bot respect tool access controls?

        Deterministic: checks if permission-required tools were called without auth.
        """
        if not annotation.tool_calls or not self._tool_defs:
            return self._no_tools_result("permission_respect")

        permission_tools = {td.name.lower() for td in self._tool_defs if td.requires_permission}
        if not permission_tools:
            return MetricResult(
                metric_name="permission_respect",
                metric_type="tool",
                score=1.0,
                reliability="deterministic",
                confidence=annotation.overall_confidence,
                message="No permission-restricted tools defined",
            )

        violations = []
        for tc in annotation.tool_calls:
            if tc.tool_name.lower() in permission_tools:
                # Check if arguments contain auth/permission tokens
                has_auth = any(
                    k in tc.arguments for k in ("auth_token", "api_key", "permission", "token", "authorization")
                )
                if not has_auth:
                    violations.append(tc.tool_name)

        score = 1.0 if not violations else max(0.0, 1.0 - len(violations) / len(annotation.tool_calls))

        return MetricResult(
            metric_name="permission_respect",
            metric_type="tool",
            score=score,
            reliability="deterministic",
            confidence=annotation.overall_confidence,
            message=f"{len(violations)} permission violations" if violations else "All permissions respected",
            evidence={"violations": violations[:5]},
            issues=[f"'{t}' called without authorization" for t in violations[:3]],
        )

    # ================================================================
    # HYBRID METRICS
    # ================================================================

    async def _eval_tool_selection(
        self, annotation: ResponseAnnotation
    ) -> MetricResult:
        """
        Was the right tool chosen for the task?

        Hybrid: name matching against definitions + optional LLM check.
        """
        if not annotation.tool_calls:
            return self._no_tools_result("tool_selection")

        if not self._tool_defs:
            return MetricResult(
                metric_name="tool_selection",
                metric_type="tool",
                score=1.0,
                reliability="hybrid",
                confidence=annotation.overall_confidence * 0.5,
                message="No tool definitions to validate selection against",
            )

        known_tools = {td.name.lower() for td in self._tool_defs}
        matched = 0
        unknown = []

        for tc in annotation.tool_calls:
            if tc.tool_name.lower() in known_tools:
                matched += 1
            else:
                # Fuzzy match: check if tool name is close
                best_match = self._fuzzy_match_tool(tc.tool_name, known_tools)
                if best_match:
                    matched += 1
                else:
                    unknown.append(tc.tool_name)

        total = len(annotation.tool_calls)
        deterministic_score = matched / total if total > 0 else 1.0

        # LLM enhancement: check if tool choice was semantically correct
        if self._llm and annotation.user_query:
            try:
                llm_score = await self._llm_tool_selection_check(annotation)
                score = 0.4 * deterministic_score + 0.6 * llm_score
            except Exception:
                score = deterministic_score
        else:
            score = deterministic_score

        return MetricResult(
            metric_name="tool_selection",
            metric_type="tool",
            score=min(1.0, score),
            reliability="hybrid",
            confidence=annotation.overall_confidence,
            message=f"{matched}/{total} tools matched definitions",
            evidence={"matched": matched, "unknown_tools": unknown[:5]},
            issues=[f"Unknown tool: '{t}'" for t in unknown[:3]],
        )

    async def _eval_error_handling(
        self, annotation: ResponseAnnotation
    ) -> MetricResult:
        """
        Does the bot handle tool failures gracefully?

        Hybrid: checks error responses + response text for acknowledgment.
        """
        if not annotation.tool_calls:
            return self._no_tools_result("error_handling")

        failed_calls = [tc for tc in annotation.tool_calls if tc.error]
        if not failed_calls:
            return MetricResult(
                metric_name="error_handling",
                metric_type="tool",
                score=1.0,
                reliability="hybrid",
                confidence=annotation.overall_confidence,
                message="No tool errors to handle",
            )

        response_lower = annotation.response_text.lower()

        # Check if response acknowledges failures
        acknowledgment_patterns = [
            "sorry", "apologize", "unable to", "couldn't", "could not",
            "error", "issue", "problem", "try again", "not available",
            "unfortunately", "failed to", "having trouble",
        ]
        acknowledged = any(p in response_lower for p in acknowledgment_patterns)

        # Check if response provides alternatives or next steps
        alternative_patterns = [
            "alternatively", "instead", "you can", "please try",
            "another option", "manually", "contact", "help you with",
        ]
        has_alternative = any(p in response_lower for p in alternative_patterns)

        if acknowledged and has_alternative:
            score = 1.0
        elif acknowledged:
            score = 0.7
        elif has_alternative:
            score = 0.6
        else:
            score = 0.2

        return MetricResult(
            metric_name="error_handling",
            metric_type="tool",
            score=score,
            reliability="hybrid",
            confidence=annotation.overall_confidence,
            message=f"{len(failed_calls)} error(s): {'acknowledged' if acknowledged else 'not acknowledged'}",
            evidence={
                "failed_tools": [tc.tool_name for tc in failed_calls],
                "acknowledged": acknowledged,
                "has_alternative": has_alternative,
            },
            issues=[] if acknowledged else ["Tool errors not acknowledged in response"],
        )

    async def _eval_unnecessary_calls(
        self, annotation: ResponseAnnotation
    ) -> MetricResult:
        """
        Were there redundant tool invocations?

        Hybrid: checks for duplicate calls with same params.
        """
        if not annotation.tool_calls:
            return self._no_tools_result("unnecessary_calls")

        # Detect duplicate calls (same tool + same arguments)
        seen = set()
        duplicates = []

        for tc in annotation.tool_calls:
            key = (tc.tool_name.lower(), json.dumps(tc.arguments, sort_keys=True, default=str))
            if key in seen:
                duplicates.append(tc.tool_name)
            seen.add(key)

        total = len(annotation.tool_calls)
        unnecessary = len(duplicates)
        score = 1.0 - (unnecessary / total) if total > 0 else 1.0
        score = max(0.0, score)

        return MetricResult(
            metric_name="unnecessary_calls",
            metric_type="tool",
            score=score,
            reliability="hybrid",
            confidence=annotation.overall_confidence,
            message=f"{unnecessary} duplicate call(s) out of {total}",
            evidence={"total_calls": total, "duplicates": unnecessary},
            issues=[f"Duplicate call: '{t}'" for t in duplicates[:3]],
        )

    # ================================================================
    # LLM-ONLY METRICS
    # ================================================================

    async def _eval_result_integration(
        self, annotation: ResponseAnnotation
    ) -> MetricResult:
        """
        Is tool output used correctly in the response?

        LLM-only: checks if tool results appear in the final response.
        """
        if not self._llm or not annotation.tool_calls:
            return self._no_tools_result("result_integration")

        tools_with_results = [tc for tc in annotation.tool_calls if tc.result]
        if not tools_with_results:
            return MetricResult(
                metric_name="result_integration",
                metric_type="tool",
                score=1.0,
                reliability="llm",
                confidence=annotation.overall_confidence,
                message="No tool results to integrate",
            )

        results_text = "\n".join(
            f"- {tc.tool_name}: {tc.result[:200]}" for tc in tools_with_results
        )

        prompt = f"""Evaluate whether the bot correctly used tool results in its response.

TOOL RESULTS:
{results_text}

BOT RESPONSE:
{annotation.response_text[:1500]}

Score 0.0-1.0:
- 1.0 = All tool results correctly reflected in response
- 0.5 = Some results used, others ignored or misrepresented
- 0.0 = Tool results completely ignored or contradicted

Respond ONLY with JSON: {{"score": 0.9, "used_results": 2, "ignored_results": 0, "reasoning": "brief"}}"""

        try:
            response = await self._llm.generate(prompt)
            parsed = self._parse_json(response)
            score = float(parsed.get("score", 0.5))

            return MetricResult(
                metric_name="result_integration",
                metric_type="tool",
                score=max(0.0, min(1.0, score)),
                reliability="llm",
                confidence=annotation.overall_confidence * 0.9,
                message=f"Result integration: {score:.2f}",
                evidence={
                    "tools_with_results": len(tools_with_results),
                    "used": parsed.get("used_results", 0),
                    "ignored": parsed.get("ignored_results", 0),
                },
            )
        except Exception as e:
            return MetricResult(
                metric_name="result_integration",
                metric_type="tool",
                score=0.5,
                reliability="llm",
                confidence=0.3,
                message=f"Integration check failed: {str(e)[:100]}",
            )

    async def _eval_missing_calls(
        self, annotation: ResponseAnnotation
    ) -> MetricResult:
        """
        Should a tool have been called but wasn't?

        LLM-only: analyzes query to determine if tools were needed.
        """
        if not self._llm:
            return self._no_tools_result("missing_calls")

        tool_names = [td.name for td in self._tool_defs] if self._tool_defs else []
        called_tools = [tc.tool_name for tc in annotation.tool_calls]

        prompt = f"""Analyze whether the bot should have called additional tools to answer the user's query.

USER QUERY: {annotation.user_query}

AVAILABLE TOOLS: {', '.join(tool_names) if tool_names else 'Unknown'}

TOOLS ACTUALLY CALLED: {', '.join(called_tools) if called_tools else 'None'}

BOT RESPONSE:
{annotation.response_text[:1500]}

Were any necessary tool calls missing? Score 0.0-1.0:
- 1.0 = All necessary tools were called
- 0.5 = Some tools missing but response partially answers
- 0.0 = Critical tools missing, response likely inaccurate

Respond ONLY with JSON: {{"score": 0.9, "missing_tools": [], "reasoning": "brief"}}"""

        try:
            response = await self._llm.generate(prompt)
            parsed = self._parse_json(response)
            score = float(parsed.get("score", 0.7))
            missing = parsed.get("missing_tools", [])

            return MetricResult(
                metric_name="missing_calls",
                metric_type="tool",
                score=max(0.0, min(1.0, score)),
                reliability="llm",
                confidence=annotation.overall_confidence * 0.85,
                message=f"Missing calls: {len(missing)} tools" if missing else "No missing calls",
                evidence={"missing_tools": missing[:5]},
                issues=[f"Missing tool: '{t}'" for t in missing[:3]],
            )
        except Exception as e:
            return MetricResult(
                metric_name="missing_calls",
                metric_type="tool",
                score=0.5,
                reliability="llm",
                confidence=0.3,
                message=f"Missing calls check failed: {str(e)[:100]}",
            )

    async def _eval_fallback_behavior(
        self, annotation: ResponseAnnotation
    ) -> MetricResult:
        """
        Does the bot degrade gracefully when tools fail?

        LLM-only: checks if alternative approaches are attempted.
        """
        if not self._llm:
            return self._no_tools_result("fallback_behavior")

        failed_calls = [tc for tc in annotation.tool_calls if tc.error]
        if not failed_calls:
            return MetricResult(
                metric_name="fallback_behavior",
                metric_type="tool",
                score=1.0,
                reliability="llm",
                confidence=annotation.overall_confidence,
                message="No failures requiring fallback",
            )

        failures = "\n".join(f"- {tc.tool_name}: {tc.error[:100]}" for tc in failed_calls)

        prompt = f"""Evaluate the bot's fallback behavior after tool failures.

TOOL FAILURES:
{failures}

BOT RESPONSE:
{annotation.response_text[:1500]}

Score 0.0-1.0:
- 1.0 = Graceful degradation with alternative approach or helpful guidance
- 0.5 = Acknowledged failure but no alternative offered
- 0.0 = No acknowledgment, response pretends tools succeeded

Respond ONLY with JSON: {{"score": 0.8, "fallback_type": "alternative_offered|acknowledged_only|none", "reasoning": "brief"}}"""

        try:
            response = await self._llm.generate(prompt)
            parsed = self._parse_json(response)
            score = float(parsed.get("score", 0.5))

            return MetricResult(
                metric_name="fallback_behavior",
                metric_type="tool",
                score=max(0.0, min(1.0, score)),
                reliability="llm",
                confidence=annotation.overall_confidence * 0.85,
                message=f"Fallback: {parsed.get('fallback_type', 'unknown')}",
                evidence={"fallback_type": parsed.get("fallback_type", "")},
            )
        except Exception as e:
            return MetricResult(
                metric_name="fallback_behavior",
                metric_type="tool",
                score=0.5,
                reliability="llm",
                confidence=0.3,
                message=f"Fallback check failed: {str(e)[:100]}",
            )

    async def _eval_side_effect_awareness(
        self, annotation: ResponseAnnotation
    ) -> MetricResult:
        """
        Is the bot aware of write/delete effects of tools?

        LLM-only: checks if side-effect tools have confirmation.
        """
        if not self._llm:
            return self._no_tools_result("side_effect_awareness")

        # Identify tools with side effects
        se_tools = []
        for tc in annotation.tool_calls:
            td = self._tool_def_map.get(tc.tool_name.lower())
            if td and td.has_side_effects:
                se_tools.append(tc)

        if not se_tools:
            # Heuristic: check tool names for write/delete patterns
            write_patterns = ["create", "update", "delete", "remove", "modify", "write", "send", "post", "cancel"]
            for tc in annotation.tool_calls:
                if any(p in tc.tool_name.lower() for p in write_patterns):
                    se_tools.append(tc)

        if not se_tools:
            return MetricResult(
                metric_name="side_effect_awareness",
                metric_type="tool",
                score=1.0,
                reliability="llm",
                confidence=annotation.overall_confidence,
                message="No side-effect tools detected",
            )

        se_text = "\n".join(f"- {tc.tool_name}({json.dumps(tc.arguments, default=str)[:100]})" for tc in se_tools)

        prompt = f"""Evaluate if the bot appropriately handled tools with side effects (write/delete/modify operations).

SIDE-EFFECT TOOLS CALLED:
{se_text}

CONVERSATION CONTEXT: {annotation.user_query}

BOT RESPONSE:
{annotation.response_text[:1500]}

Score 0.0-1.0:
- 1.0 = Confirmed with user before making changes, or changes were explicitly requested
- 0.5 = Made changes without explicit confirmation but user likely intended it
- 0.0 = Made destructive changes without any user awareness

Respond ONLY with JSON: {{"score": 0.9, "confirmed": true, "reasoning": "brief"}}"""

        try:
            response = await self._llm.generate(prompt)
            parsed = self._parse_json(response)
            score = float(parsed.get("score", 0.6))

            return MetricResult(
                metric_name="side_effect_awareness",
                metric_type="tool",
                score=max(0.0, min(1.0, score)),
                reliability="llm",
                confidence=annotation.overall_confidence * 0.85,
                message=f"Side-effect awareness: {score:.2f}",
                evidence={
                    "side_effect_tools": [tc.tool_name for tc in se_tools],
                    "confirmed": parsed.get("confirmed", False),
                },
            )
        except Exception as e:
            return MetricResult(
                metric_name="side_effect_awareness",
                metric_type="tool",
                score=0.5,
                reliability="llm",
                confidence=0.3,
                message=f"Side-effect check failed: {str(e)[:100]}",
            )

    # ================================================================
    # LLM Helper
    # ================================================================

    async def _llm_tool_selection_check(self, annotation: ResponseAnnotation) -> float:
        tool_desc = "\n".join(f"- {td.name}: {td.description}" for td in self._tool_defs)
        called = ", ".join(tc.tool_name for tc in annotation.tool_calls)

        prompt = f"""Was the right tool selected for this query?
Available tools: {tool_desc}
Query: {annotation.user_query}
Tools called: {called}
Score 0.0-1.0 where 1.0=perfect selection. Respond ONLY with JSON: {{"score": 0.85}}"""

        result = await self._llm.generate(prompt)
        parsed = self._parse_json(result)
        return float(parsed.get("score", 0.5))

    # ================================================================
    # Utilities
    # ================================================================

    def _no_tools_result(self, metric_name: str) -> MetricResult:
        return MetricResult(
            metric_name=metric_name,
            metric_type="tool",
            score=1.0,
            reliability=TOOL_METRIC_RELIABILITY.get(
                ToolMetricType(metric_name), JudgeReliability.DETERMINISTIC
            ).value,
            message="No tool calls to evaluate",
        )

    def _fuzzy_match_tool(self, name: str, known: set) -> Optional[str]:
        name_lower = name.lower().replace("-", "_").replace(" ", "_")
        for k in known:
            k_normalized = k.replace("-", "_").replace(" ", "_")
            if name_lower == k_normalized:
                return k
            if name_lower in k_normalized or k_normalized in name_lower:
                return k
        return None

    def _check_type(self, value: Any, expected_type: str) -> bool:
        type_map = {
            "str": str, "string": str,
            "int": int, "integer": int,
            "float": float, "number": (int, float),
            "bool": bool, "boolean": bool,
            "list": list, "array": list,
            "dict": dict, "object": dict,
        }
        expected = type_map.get(expected_type.lower())
        if expected is None:
            return True
        return isinstance(value, expected)

    def _parse_json(self, text: str) -> Dict[str, Any]:
        import json as json_mod
        text = text.strip()
        text = re.sub(r'^```(?:json)?\s*', '', text)
        text = re.sub(r'\s*```$', '', text)
        text = text.strip()
        try:
            return json_mod.loads(text)
        except (json_mod.JSONDecodeError, ValueError):
            pass
        match = re.search(r'\{[\s\S]*\}', text)
        if match:
            try:
                return json_mod.loads(match.group(0))
            except (json_mod.JSONDecodeError, ValueError):
                pass
        return {}
