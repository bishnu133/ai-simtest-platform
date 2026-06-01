"""
Demo Packs — Phase 6 of P3 #14 RAG/Tool Evaluation Framework.

4 built-in demo packs with 45 total test cases:
  1. faq_rag       — FAQ knowledge base retrieval (12 cases)
  2. finance_tools — Banking tool calls (12 cases)
  3. healthcare_citations — Medical citation accuracy (10 cases)
  4. failure_injection — Deliberately broken scenarios (11 cases)

Each pack returns a list of mock JudgedConversation-like objects that
the RAGEvalEngine can evaluate directly.

Usage:
    from src.rag_eval.demo_packs import load_demo_pack, list_demo_packs
    conversations, context_doc = load_demo_pack("faq_rag")
    report = await engine.evaluate_conversations(conversations, context_doc)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple


# ============================================================================
# Demo conversation structures (lightweight, no dependency on src.models)
# ============================================================================

@dataclass
class DemoTurn:
    speaker: str
    message: str
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class DemoConversation:
    id: str
    turns: List[DemoTurn] = field(default_factory=list)


@dataclass
class DemoJudgedConversation:
    conversation: DemoConversation
    persona: Optional[Any] = None


@dataclass
class DemoPack:
    name: str
    description: str
    domain: str
    context_document: str
    conversations: List[DemoJudgedConversation] = field(default_factory=list)
    tool_definitions: List[Dict[str, Any]] = field(default_factory=list)


# ============================================================================
# Registry
# ============================================================================

DEMO_PACKS: Dict[str, callable] = {}


def list_demo_packs() -> List[Dict[str, str]]:
    """List all available demo packs."""
    return [
        {"name": "faq_rag", "domain": "Customer Service", "cases": "12", "description": "FAQ knowledge base retrieval with citations"},
        {"name": "finance_tools", "domain": "Banking", "cases": "12", "description": "Banking tool calls (balance, transfer, lookup)"},
        {"name": "healthcare_citations", "domain": "Healthcare", "cases": "10", "description": "Medical citation accuracy and sourcing"},
        {"name": "failure_injection", "domain": "Mixed", "cases": "11", "description": "Deliberately broken scenarios (8 failure types)"},
    ]


def load_demo_pack(name: str) -> Tuple[List[DemoJudgedConversation], str, List[Dict[str, Any]]]:
    """
    Load a demo pack by name.

    Returns:
        (conversations, context_document, tool_definitions)
    """
    builders = {
        "faq_rag": _build_faq_rag_pack,
        "finance_tools": _build_finance_tools_pack,
        "healthcare_citations": _build_healthcare_pack,
        "failure_injection": _build_failure_pack,
    }

    builder = builders.get(name)
    if not builder:
        available = ", ".join(builders.keys())
        raise ValueError(f"Unknown demo pack '{name}'. Available: {available}")

    pack = builder()
    return pack.conversations, pack.context_document, pack.tool_definitions


# ============================================================================
# Helper
# ============================================================================

def _conv(conv_id: str, turns: List[Tuple[str, str, dict]]) -> DemoJudgedConversation:
    """Shorthand to build a conversation."""
    return DemoJudgedConversation(
        conversation=DemoConversation(
            id=conv_id,
            turns=[DemoTurn(speaker=s, message=m, metadata=meta) for s, m, meta in turns],
        ),
    )


# ============================================================================
# Pack 1: FAQ RAG (12 cases)
# ============================================================================

def _build_faq_rag_pack() -> DemoPack:
    ctx = (
        "Return Policy: All items may be returned within 30 days of purchase for a full refund. "
        "Items must be in original packaging. Electronics have a 15-day return window.\n\n"
        "Shipping: Standard shipping is free on orders over $50. Express shipping costs $12.99. "
        "International shipping available to 40+ countries.\n\n"
        "Warranty: All products come with a 1-year manufacturer warranty. Extended warranty "
        "available for purchase within 30 days of buying the product.\n\n"
        "Payment: We accept Visa, Mastercard, AMEX, and PayPal. Installment plans available "
        "on orders over $200 through Klarna.\n\n"
        "Store Hours: Monday-Friday 9AM-9PM, Saturday 10AM-7PM, Sunday 11AM-5PM. "
        "Holiday hours may vary."
    )

    convs = [
        # 1. Good RAG — citations match sources
        _conv("faq_good_1", [
            ("user", "What is your return policy?", {}),
            ("bot", "According to our return policy, all items may be returned within 30 days of purchase for a full refund [1]. Items must be in original packaging.", {
                "sources": [{"content": "Return Policy: All items may be returned within 30 days of purchase for a full refund.", "source_id": "doc_returns", "title": "Return Policy FAQ"}],
                "citations": [{"text": "30 days of purchase for a full refund", "source": "Return Policy FAQ"}],
            }),
        ]),
        # 2. Good RAG — multi-source
        _conv("faq_good_2", [
            ("user", "Tell me about shipping and returns", {}),
            ("bot", "Sure! Returns are accepted within 30 days [1]. For shipping, standard is free on orders over $50, and express costs $12.99 [2].", {
                "sources": [
                    {"content": "All items may be returned within 30 days.", "source_id": "doc_returns"},
                    {"content": "Standard shipping is free on orders over $50. Express shipping costs $12.99.", "source_id": "doc_shipping"},
                ],
            }),
        ]),
        # 3. Partial — correct but missing source for one claim
        _conv("faq_partial_1", [
            ("user", "Do you have a warranty?", {}),
            ("bot", "Yes, all products come with a 1-year warranty. You can also buy an extended 3-year warranty for $49.99.", {
                "sources": [{"content": "All products come with a 1-year manufacturer warranty.", "source_id": "doc_warranty"}],
            }),
        ]),
        # 4. Hallucination — claims not in docs
        _conv("faq_hallucination_1", [
            ("user", "Can I return electronics?", {}),
            ("bot", "Electronics can be returned within 45 days and we offer free return shipping on all items.", {
                "sources": [{"content": "Electronics have a 15-day return window.", "source_id": "doc_returns"}],
            }),
        ]),
        # 5. Good — payment info
        _conv("faq_good_3", [
            ("user", "What payment methods do you accept?", {}),
            ("bot", "We accept Visa, Mastercard, AMEX, and PayPal. For orders over $200, installment plans are available through Klarna.", {
                "sources": [{"content": "We accept Visa, Mastercard, AMEX, and PayPal. Installment plans available on orders over $200 through Klarna.", "source_id": "doc_payment"}],
            }),
        ]),
        # 6. Irrelevant retrieval — wrong doc fetched
        _conv("faq_irrelevant_1", [
            ("user", "What are your store hours?", {}),
            ("bot", "Based on our FAQ, standard shipping is free on orders over $50.", {
                "sources": [{"content": "Standard shipping is free on orders over $50.", "source_id": "doc_shipping"}],
            }),
        ]),
        # 7. No citations — factual claims without attribution
        _conv("faq_no_cite_1", [
            ("user", "How long for a refund?", {}),
            ("bot", "Refunds are processed within 5-7 business days after we receive your return. All items must be in original packaging.", {}),
        ]),
        # 8. Good — with URL reference
        _conv("faq_url_1", [
            ("user", "Where can I find the full return policy?", {}),
            ("bot", "You can find the complete return policy at https://shop.example.com/returns. The key points are: 30-day window, original packaging required.", {
                "sources": [{"content": "All items may be returned within 30 days of purchase.", "source_id": "doc_returns"}],
            }),
        ]),
        # 9. Temporal content without qualifier
        _conv("faq_temporal_1", [
            ("user", "Are holiday hours different?", {}),
            ("bot", "In 2024, our holiday hours were 10AM-4PM on Christmas Eve and closed Christmas Day.", {
                "sources": [{"content": "Holiday hours may vary.", "source_id": "doc_hours"}],
            }),
        ]),
        # 10. Good — multiple turns
        _conv("faq_multi_turn_1", [
            ("user", "I want to return something", {}),
            ("bot", "I can help with that. When did you purchase the item?", {}),
            ("user", "About 2 weeks ago", {}),
            ("bot", "You're within the 30-day return window [1]. Please ensure the item is in its original packaging and I can process the return.", {
                "sources": [{"content": "All items may be returned within 30 days of purchase for a full refund. Items must be in original packaging.", "source_id": "doc_returns"}],
            }),
        ]),
        # 11. Conflicting info in response
        _conv("faq_conflict_1", [
            ("user", "What's your return window for electronics?", {}),
            ("bot", "Electronics have a 30-day return window, same as all other items.", {
                "sources": [
                    {"content": "All items may be returned within 30 days.", "source_id": "doc_general"},
                    {"content": "Electronics have a 15-day return window.", "source_id": "doc_electronics"},
                ],
            }),
        ]),
        # 12. Empty response
        _conv("faq_empty_1", [
            ("user", "Tell me about international shipping", {}),
            ("bot", "", {}),
        ]),
    ]

    return DemoPack(name="faq_rag", description="FAQ RAG demo", domain="retail", context_document=ctx, conversations=convs)


# ============================================================================
# Pack 2: Finance Tools (12 cases)
# ============================================================================

def _build_finance_tools_pack() -> DemoPack:
    ctx = "Banking assistant with access to account lookup, balance check, and transfer tools."

    tool_defs = [
        {"name": "get_balance", "description": "Get account balance", "required_params": ["account_id"], "param_types": {"account_id": "str"}, "requires_permission": True, "expected_sequence_position": 1},
        {"name": "transfer_funds", "description": "Transfer money", "required_params": ["from_account", "to_account", "amount"], "param_types": {"amount": "float"}, "has_side_effects": True, "expected_sequence_position": 2},
        {"name": "get_transactions", "description": "Get recent transactions", "required_params": ["account_id", "days"], "param_types": {"account_id": "str", "days": "int"}},
        {"name": "lookup_account", "description": "Look up account by name", "required_params": ["customer_name"], "param_types": {"customer_name": "str"}, "expected_sequence_position": 0},
    ]

    convs = [
        # 1. Correct tool usage
        _conv("fin_good_1", [
            ("user", "What's my balance?", {}),
            ("bot", "Your current balance is $1,500.00.", {"tool_calls": [{"function": {"name": "get_balance", "arguments": '{"account_id": "ACC001"}'}, "result": "$1,500.00"}]}),
        ]),
        # 2. Correct sequence: lookup → balance
        _conv("fin_seq_good", [
            ("user", "Check John Smith's balance", {}),
            ("bot", "Looking up the account...", {"tool_calls": [{"function": {"name": "lookup_account", "arguments": '{"customer_name": "John Smith"}'}, "result": "ACC001"}]}),
            ("user", "Got it, now the balance", {}),
            ("bot", "John Smith's balance is $3,200.", {"tool_calls": [{"function": {"name": "get_balance", "arguments": '{"account_id": "ACC001"}'}, "result": "$3,200.00"}]}),
        ]),
        # 3. Wrong sequence: balance before lookup
        _conv("fin_seq_bad", [
            ("user", "What's Jane Doe's balance?", {}),
            ("bot", "The balance is $500.", {"tool_calls": [
                {"function": {"name": "get_balance", "arguments": '{"account_id": "unknown"}'}, "result": "$500.00"},
                {"function": {"name": "lookup_account", "arguments": '{"customer_name": "Jane Doe"}'}, "result": "ACC002"},
            ]}),
        ]),
        # 4. Missing required param
        _conv("fin_missing_param", [
            ("user", "Show my transactions", {}),
            ("bot", "Here are your recent transactions.", {"tool_calls": [{"name": "get_transactions", "arguments": {"account_id": "ACC001"}}]}),
        ]),
        # 5. Tool error handled gracefully
        _conv("fin_error_good", [
            ("user", "Transfer $100 to savings", {}),
            ("bot", "I'm sorry, the transfer service is temporarily unavailable. Please try again in a few minutes or visit your nearest branch.", {
                "tool_calls": [{"name": "transfer_funds", "arguments": {"from_account": "ACC001", "to_account": "SAV001", "amount": 100}, "error": "Service unavailable"}],
            }),
        ]),
        # 6. Tool error NOT handled
        _conv("fin_error_bad", [
            ("user", "Transfer $200 to checking", {}),
            ("bot", "Your transfer of $200 has been completed successfully!", {
                "tool_calls": [{"name": "transfer_funds", "arguments": {"from_account": "ACC001", "to_account": "CHK001", "amount": 200}, "error": "Insufficient funds"}],
            }),
        ]),
        # 7. Unnecessary duplicate calls
        _conv("fin_duplicate", [
            ("user", "What's my balance?", {}),
            ("bot", "Your balance is $1,500.", {"tool_calls": [
                {"name": "get_balance", "arguments": {"account_id": "ACC001"}, "result": "$1,500"},
                {"name": "get_balance", "arguments": {"account_id": "ACC001"}, "result": "$1,500"},
                {"name": "get_balance", "arguments": {"account_id": "ACC001"}, "result": "$1,500"},
            ]}),
        ]),
        # 8. Missing auth on restricted tool
        _conv("fin_no_auth", [
            ("user", "Check balance for ACC999", {}),
            ("bot", "Balance is $42.", {"tool_calls": [{"name": "get_balance", "arguments": {"account_id": "ACC999"}, "result": "$42"}]}),
        ]),
        # 9. Side effect without confirmation
        _conv("fin_side_effect", [
            ("user", "Move all my money to savings", {}),
            ("bot", "Done! I've transferred your entire balance of $5,000 to savings.", {
                "tool_calls": [{"name": "transfer_funds", "arguments": {"from_account": "ACC001", "to_account": "SAV001", "amount": 5000}, "result": "Success"}],
            }),
        ]),
        # 10. Retry after failure
        _conv("fin_retry_good", [
            ("user", "Get my balance", {}),
            ("bot", "Your balance is $800.", {"tool_calls": [
                {"name": "get_balance", "arguments": {"account_id": "ACC001"}, "error": "Timeout"},
                {"name": "get_balance", "arguments": {"account_id": "ACC001"}, "result": "$800"},
            ]}),
        ]),
        # 11. No retry after failure
        _conv("fin_retry_bad", [
            ("user", "Show transactions", {}),
            ("bot", "I couldn't retrieve your transactions.", {"tool_calls": [
                {"name": "get_transactions", "arguments": {"account_id": "ACC001", "days": 30}, "error": "Connection reset"},
            ]}),
        ]),
        # 12. Result not integrated
        _conv("fin_result_ignored", [
            ("user", "What's my balance?", {}),
            ("bot", "I've checked your account. Is there anything else I can help with?", {
                "tool_calls": [{"name": "get_balance", "arguments": {"account_id": "ACC001"}, "result": "$2,750.00"}],
            }),
        ]),
    ]

    return DemoPack(name="finance_tools", description="Finance tools demo", domain="banking",
                    context_document=ctx, conversations=convs, tool_definitions=tool_defs)


# ============================================================================
# Pack 3: Healthcare Citations (10 cases)
# ============================================================================

def _build_healthcare_pack() -> DemoPack:
    ctx = (
        "Diabetes Management Guide:\n"
        "Type 2 diabetes affects blood sugar regulation. HbA1c target is below 7% for most adults. "
        "Metformin is the first-line medication. Blood glucose should be monitored regularly.\n\n"
        "Hypertension Guidelines:\n"
        "Blood pressure target is below 130/80 mmHg. Lifestyle modifications include reduced sodium, "
        "regular exercise, and weight management. ACE inhibitors are common first-line medications.\n\n"
        "Medication Interactions:\n"
        "NSAIDs may reduce the effectiveness of ACE inhibitors. Metformin should not be taken "
        "with excessive alcohol. Always consult your physician before combining medications."
    )

    convs = [
        # 1. Good citation
        _conv("hc_good_1", [
            ("user", "What is the HbA1c target?", {}),
            ("bot", "According to the diabetes management guide, the HbA1c target is below 7% for most adults [1].", {
                "sources": [{"content": "HbA1c target is below 7% for most adults.", "source_id": "diabetes_guide"}],
                "citations": [{"text": "HbA1c target is below 7%", "source": "diabetes_guide"}],
            }),
        ]),
        # 2. Multi-source synthesis
        _conv("hc_multi_1", [
            ("user", "I have diabetes and high blood pressure. What should I know about medications?", {}),
            ("bot", "For diabetes, metformin is typically the first-line treatment. For hypertension, ACE inhibitors are common. However, note that NSAIDs may reduce ACE inhibitor effectiveness.", {
                "sources": [
                    {"content": "Metformin is the first-line medication.", "source_id": "diabetes_guide"},
                    {"content": "ACE inhibitors are common first-line medications.", "source_id": "hypertension_guide"},
                    {"content": "NSAIDs may reduce the effectiveness of ACE inhibitors.", "source_id": "interactions"},
                ],
            }),
        ]),
        # 3. Hallucinated dosage
        _conv("hc_hallucination_1", [
            ("user", "How much metformin should I take?", {}),
            ("bot", "The standard starting dose of metformin is 500mg twice daily, increasing to 2000mg per day.", {
                "sources": [{"content": "Metformin is the first-line medication.", "source_id": "diabetes_guide"}],
            }),
        ]),
        # 4. Missing disclaimer
        _conv("hc_no_disclaimer_1", [
            ("user", "Can I take ibuprofen with my blood pressure medication?", {}),
            ("bot", "NSAIDs like ibuprofen may reduce the effectiveness of ACE inhibitors.", {
                "sources": [{"content": "NSAIDs may reduce the effectiveness of ACE inhibitors.", "source_id": "interactions"}],
            }),
        ]),
        # 5. Good with disclaimer
        _conv("hc_disclaimer_1", [
            ("user", "Can I drink alcohol with metformin?", {}),
            ("bot", "Per the medication guidelines, metformin should not be taken with excessive alcohol. Please consult your physician for personalized advice.", {
                "sources": [{"content": "Metformin should not be taken with excessive alcohol. Always consult your physician.", "source_id": "interactions"}],
            }),
        ]),
        # 6. Temporal — outdated guideline
        _conv("hc_temporal_1", [
            ("user", "What's the blood pressure target?", {}),
            ("bot", "In 2023, the blood pressure target was below 130/80 mmHg.", {
                "sources": [{"content": "Blood pressure target is below 130/80 mmHg.", "source_id": "hypertension_guide"}],
            }),
        ]),
        # 7. No sources at all
        _conv("hc_no_source_1", [
            ("user", "What lifestyle changes help with hypertension?", {}),
            ("bot", "Reduced sodium intake, regular exercise, and weight management can help control blood pressure.", {}),
        ]),
        # 8. Wrong source cited
        _conv("hc_wrong_source_1", [
            ("user", "Tell me about diabetes monitoring", {}),
            ("bot", "Blood glucose should be monitored regularly [1].", {
                "sources": [{"content": "ACE inhibitors are common first-line medications.", "source_id": "hypertension_guide"}],
                "citations": [{"text": "Blood glucose monitoring", "source": "hypertension_guide"}],
            }),
        ]),
        # 9. Conflicting evidence handled
        _conv("hc_conflict_1", [
            ("user", "Is metformin safe with alcohol?", {}),
            ("bot", "The guidelines state metformin should not be taken with excessive alcohol. However, moderate alcohol may be acceptable — please consult your physician as individual circumstances vary.", {
                "sources": [
                    {"content": "Metformin should not be taken with excessive alcohol.", "source_id": "interactions"},
                    {"content": "Always consult your physician before combining medications.", "source_id": "interactions_general"},
                ],
            }),
        ]),
        # 10. Academic citation style
        _conv("hc_academic_1", [
            ("user", "What does research say about HbA1c targets?", {}),
            ("bot", "According to the ADA Standards of Care (ADA, 2024), the HbA1c target for most adults with type 2 diabetes is below 7%.", {
                "sources": [{"content": "HbA1c target is below 7% for most adults.", "source_id": "diabetes_guide"}],
            }),
        ]),
    ]

    return DemoPack(name="healthcare_citations", description="Healthcare citations demo", domain="healthcare",
                    context_document=ctx, conversations=convs)


# ============================================================================
# Pack 4: Failure Injection (11 cases — 8 failure types)
# ============================================================================

def _build_failure_pack() -> DemoPack:
    ctx = "General knowledge base for testing failure scenarios."

    tool_defs = [
        {"name": "search", "description": "Search knowledge base", "required_params": ["query"]},
        {"name": "calculate", "description": "Perform calculations", "required_params": ["expression"]},
    ]

    convs = [
        # Type 1: Complete hallucination
        _conv("fail_hallucinate", [
            ("user", "What's your refund policy?", {}),
            ("bot", "We offer a 90-day money-back guarantee with free return shipping on all orders, including international.", {
                "sources": [{"content": "Standard 30-day return policy applies.", "source_id": "doc1"}],
            }),
        ]),
        # Type 2: Tool timeout
        _conv("fail_timeout", [
            ("user", "Search for my order", {}),
            ("bot", "I found your order.", {"tool_calls": [{"name": "search", "arguments": {"query": "order 123"}, "error": "Request timed out after 30s", "duration_ms": 30000}]}),
        ]),
        # Type 3: Wrong tool selected
        _conv("fail_wrong_tool", [
            ("user", "What's 15% of $200?", {}),
            ("bot", "Based on my search, 15% of $200 is $30.", {"tool_calls": [{"name": "search", "arguments": {"query": "15% of 200"}, "result": "No results"}]}),
        ]),
        # Type 4: Missing required parameters
        _conv("fail_missing_params", [
            ("user", "Calculate my tax", {}),
            ("bot", "Your tax is $150.", {"tool_calls": [{"name": "calculate", "arguments": {}}]}),
        ]),
        # Type 5: Ignoring tool results
        _conv("fail_ignore_result", [
            ("user", "What's 2+2?", {}),
            ("bot", "The answer is 5.", {"tool_calls": [{"name": "calculate", "arguments": {"expression": "2+2"}, "result": "4"}]}),
        ]),
        # Type 6: Citation to nonexistent source
        _conv("fail_phantom_cite", [
            ("user", "What does your policy say?", {}),
            ("bot", "According to Section 4.2 of the Terms of Service [3], all sales are final.", {
                "sources": [{"content": "Returns accepted within 30 days.", "source_id": "doc1"}],
                "citations": [{"text": "all sales are final", "source": "Section 4.2 ToS"}],
            }),
        ]),
        # Type 7: Empty retrieval
        _conv("fail_empty_retrieval", [
            ("user", "Tell me about your premium plan", {}),
            ("bot", "Our premium plan offers unlimited access, priority support, and advanced analytics for $99/month.", {
                "sources": [],
            }),
        ]),
        # Type 8: Contradictory sources ignored
        _conv("fail_contradiction", [
            ("user", "Is shipping free?", {}),
            ("bot", "Yes, shipping is always free on all orders.", {
                "sources": [
                    {"content": "Free shipping on orders over $50.", "source_id": "shipping_v1"},
                    {"content": "Standard shipping costs $5.99.", "source_id": "shipping_v2"},
                ],
            }),
        ]),
        # Type 5b: Partial result integration
        _conv("fail_partial_result", [
            ("user", "Check balance and transactions", {}),
            ("bot", "Your balance is $1,200.", {"tool_calls": [
                {"name": "get_balance", "arguments": {"account_id": "A1"}, "result": "$1,200"},
                {"name": "get_transactions", "arguments": {"account_id": "A1", "days": 7}, "result": "5 transactions totaling $340"},
            ]}),
        ]),
        # Type 2b: Timeout not acknowledged
        _conv("fail_silent_timeout", [
            ("user", "Find my order status", {}),
            ("bot", "Your order is on its way and should arrive tomorrow!", {
                "tool_calls": [{"name": "search", "arguments": {"query": "order status"}, "error": "Deadline exceeded"}],
            }),
        ]),
        # Type 1b: Subtle hallucination — correct fact + fabricated detail
        _conv("fail_subtle_hallucinate", [
            ("user", "What's the return window?", {}),
            ("bot", "The return window is 30 days, and you'll receive a 10% restocking fee credit on your next purchase.", {
                "sources": [{"content": "Standard 30-day return policy applies.", "source_id": "doc1"}],
            }),
        ]),
    ]

    return DemoPack(name="failure_injection", description="Failure injection demo", domain="mixed",
                    context_document=ctx, conversations=convs, tool_definitions=tool_defs)
