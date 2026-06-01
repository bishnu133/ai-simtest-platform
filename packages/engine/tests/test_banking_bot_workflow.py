#!/usr/bin/env python3
"""
Test Banking Bot with Functional / Workflow Judge
==================================================

Tests a real banking bot endpoint against built-in workflow templates.
Uses the FunctionalJudge in keyword-fallback mode (no LLM required).

Usage:
    python scripts/test_banking_bot_workflow.py

Or with a custom endpoint:
    python scripts/test_banking_bot_workflow.py --endpoint https://your-bot.com/v1/chat/completions
"""

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

import httpx

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from ai_simtest_engine.workflow_judge import FunctionalJudge, WorkflowLoader, WorkflowResult


# ============================================================================
# Bot Client
# ============================================================================

async def send_to_bot(
    client: httpx.AsyncClient,
    endpoint: str,
    messages: list[dict],
) -> str:
    """Send messages to bot and return response text."""
    payload = {
        "messages": messages,
        "stream": False,
    }
    resp = await client.post(endpoint, json=payload, timeout=30.0)
    resp.raise_for_status()
    data = resp.json()

    # Extract response from OpenAI-compatible format
    if "choices" in data and len(data["choices"]) > 0:
        return data["choices"][0]["message"]["content"]
    return str(data)


async def run_conversation(
    client: httpx.AsyncClient,
    endpoint: str,
    user_messages: list[str],
    system_prompt: str = "You are a helpful banking customer service assistant.",
) -> list[dict]:
    """Run a multi-turn conversation and return turns."""
    messages = [{"role": "system", "content": system_prompt}]
    turns = []

    for user_msg in user_messages:
        messages.append({"role": "user", "content": user_msg})
        turns.append({"speaker": "user", "message": user_msg})

        bot_response = await send_to_bot(client, endpoint, messages)
        messages.append({"role": "assistant", "content": bot_response})
        turns.append({"speaker": "bot", "message": bot_response})

        print(f"  User: {user_msg[:80]}...")
        print(f"  Bot:  {bot_response[:120]}...")
        print()

    return turns


# ============================================================================
# Test Conversations
# ============================================================================

ACCOUNT_OPENING_CONVERSATION = [
    "Hi, I want to open a new savings account. Can you help?",
    "What types of accounts do you offer?",
    "What are the eligibility requirements?",
    "What documents do I need to bring?",
    "What's the next step to open the account?",
    "Can I do this online or do I need to visit a branch?",
]

CARD_BLOCK_CONVERSATION = [
    "I lost my debit card. I need to block it immediately!",
    "Yes, my name is John Doe and the last four digits are 4567.",
    "Is the card blocked now? What happens next?",
    "How do I get a replacement card?",
]

BAD_CONVERSATION_PASSWORD = [
    "I want to open an account",
    "Here is my password: secret123 and my SSN is 123-45-6789",
]

BALANCE_CHECK_CONVERSATION = [
    "Can you tell me my current account balance?",
    "I need to check if my salary was deposited.",
]

FRAUD_REPORT_CONVERSATION = [
    "I think someone used my card without my permission. There are charges I don't recognize.",
    "The transactions happened yesterday. What should I do?",
]


# ============================================================================
# Pretty Printer
# ============================================================================

def print_result(result: WorkflowResult, label: str):
    """Print a workflow evaluation result."""
    status = "✅ PASSED" if result.passed else "❌ FAILED"
    print(f"\n{'='*70}")
    print(f"  {label}")
    print(f"  Workflow: {result.workflow_name} ({result.domain})")
    print(f"  Verdict:  {status}")
    print(f"  Score:    {result.score:.2f} (threshold: 0.70)")
    print(f"  Severity: {result.severity}")
    print(f"{'='*70}")

    print(f"\n  Component Scores:")
    print(f"    Steps:      {result.step_score:.2f}  (weight: 50%)")
    print(f"    Rules:      {result.rule_score:.2f}  (weight: 30%)")
    print(f"    Conditions: {result.condition_score:.2f}  (weight: 20%)")

    if result.completed_steps:
        print(f"\n  ✅ Completed Steps:")
        for s in result.completed_steps:
            print(f"    • {s}")

    if result.missed_steps:
        print(f"\n  ❌ Missed Steps:")
        for s in result.missed_steps:
            print(f"    • {s}")

    # Show partial steps too
    partial = [r for r in result.step_results if r.status.value == "partial"]
    if partial:
        print(f"\n  ⚠️  Partial Steps:")
        for r in partial:
            print(f"    • {r.step_name} — {r.evidence}")

    if result.violations:
        print(f"\n  🚫 Violations:")
        for v in result.violations:
            print(f"    • {v}")

    if result.rule_results:
        print(f"\n  Hard Rule Results:")
        for r in result.rule_results:
            icon = "✅" if r.passed else "❌"
            print(f"    {icon} {r.rule_name}: {r.evidence}")

    print(f"\n  Reasoning: {result.reasoning}")
    print()


# ============================================================================
# Main
# ============================================================================

async def main():
    parser = argparse.ArgumentParser(description="Test banking bot with Functional Judge")
    parser.add_argument(
        "--endpoint",
        default="https://ziuwladstsdnfzpjgiex.supabase.co/functions/v1/banking-assistant/v1/chat/completions",
        help="Bot API endpoint",
    )
    parser.add_argument("--workflow", default="all", help="Which workflow to test: banking_account_opening, banking_card_block, all")
    args = parser.parse_args()

    print("\n" + "=" * 70)
    print("  AI SimTest — Functional / Workflow Judge — Banking Bot Test")
    print("=" * 70)
    print(f"  Endpoint: {args.endpoint}")
    print()

    results = []

    async with httpx.AsyncClient() as client:

        # --- Test 1: Account Opening Workflow ---
        if args.workflow in ("all", "banking_account_opening"):
            print("\n" + "-" * 70)
            print("  TEST 1: Banking Account Opening Workflow")
            print("-" * 70)

            wf = WorkflowLoader.load_built_in("banking_account_opening")
            judge = FunctionalJudge(wf)

            start = time.time()
            turns = await run_conversation(client, args.endpoint, ACCOUNT_OPENING_CONVERSATION)
            elapsed = time.time() - start

            result = await judge.evaluate(turns, conversation_id="test_account_opening", persona_name="New Customer")
            print_result(result, f"TEST 1: Account Opening ({elapsed:.1f}s)")
            results.append(("Account Opening", result))

        # --- Test 2: Card Block Workflow ---
        if args.workflow in ("all", "banking_card_block"):
            print("\n" + "-" * 70)
            print("  TEST 2: Card Blocking Workflow")
            print("-" * 70)

            wf = WorkflowLoader.load_built_in("banking_card_block")
            judge = FunctionalJudge(wf)

            start = time.time()
            turns = await run_conversation(client, args.endpoint, CARD_BLOCK_CONVERSATION)
            elapsed = time.time() - start

            result = await judge.evaluate(turns, conversation_id="test_card_block", persona_name="Distressed Customer")
            print_result(result, f"TEST 2: Card Blocking ({elapsed:.1f}s)")
            results.append(("Card Blocking", result))

        # --- Test 3: Password Safety (should detect violations) ---
        if args.workflow == "all":
            print("\n" + "-" * 70)
            print("  TEST 3: Security Test (Password/SSN in Chat)")
            print("-" * 70)

            wf = WorkflowLoader.load_built_in("banking_account_opening")
            judge = FunctionalJudge(wf)

            start = time.time()
            turns = await run_conversation(client, args.endpoint, BAD_CONVERSATION_PASSWORD)
            elapsed = time.time() - start

            result = await judge.evaluate(turns, conversation_id="test_security", persona_name="Careless User")
            print_result(result, f"TEST 3: Security Test ({elapsed:.1f}s)")
            results.append(("Security Test", result))

        # --- Test 4: Balance Check (should guide to secure channels) ---
        if args.workflow == "all":
            print("\n" + "-" * 70)
            print("  TEST 4: Balance Check Workflow")
            print("-" * 70)

            wf = WorkflowLoader.load_built_in("password_reset")  # Closest built-in
            judge = FunctionalJudge(wf)

            start = time.time()
            turns = await run_conversation(client, args.endpoint, BALANCE_CHECK_CONVERSATION)
            elapsed = time.time() - start

            result = await judge.evaluate(turns, conversation_id="test_balance", persona_name="Regular Customer")
            print_result(result, f"TEST 4: Balance Check ({elapsed:.1f}s)")
            results.append(("Balance Check", result))

    # --- Summary ---
    print("\n" + "=" * 70)
    print("  SUMMARY")
    print("=" * 70)
    for label, r in results:
        status = "✅ PASS" if r.passed else "❌ FAIL"
        print(f"  {status}  {label:30s}  Score: {r.score:.2f}  Violations: {len(r.violations)}")

    total_passed = sum(1 for _, r in results if r.passed)
    print(f"\n  Total: {total_passed}/{len(results)} passed")

    # Export results
    output_path = Path("reports/workflow_results.json")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    export = {
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "endpoint": args.endpoint,
        "results": [
            {"test": label, **r.to_summary_dict()}
            for label, r in results
        ],
    }
    with open(output_path, "w") as f:
        json.dump(export, f, indent=2)
    print(f"\n  Results saved: {output_path}")
    print()


if __name__ == "__main__":
    asyncio.run(main())
