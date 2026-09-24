"""
Mock Bot Server - A lightweight fake chatbot API for local testing.
Simulates an OpenAI-compatible chat endpoint without any real LLM calls.

Usage:
    # Start the server
    python -m tests.mock_bot_server

    # Then run simulations against it
    simtest run --bot-endpoint http://localhost:9999/v1/chat/completions --personas 5

This lets you test the entire pipeline (persona gen → simulation → judging → report)
without spending money on a real bot API. The mock bot returns deterministic,
testable responses based on keywords in the user message.
"""

from __future__ import annotations

import json
import random
import time
import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

app = FastAPI(title="Mock Bot Server", version="0.1.0")

# ============================================================
# Response templates - designed to trigger different judge outcomes
# ============================================================

GROUNDED_RESPONSES = [
    "Based on our refund policy, you can request a refund within 30 days of purchase. Please provide your order number and I'll process it right away.",
    "Our shipping takes 3-5 business days for standard delivery. Express shipping is available for an additional $9.99 and arrives in 1-2 business days.",
    "To track your order, please visit our tracking page at example.com/track and enter your order number. You'll see real-time updates there.",
    "Our return policy allows returns within 30 days for a full refund. Items must be in original packaging. I can help you start a return right now.",
    "Your account settings can be updated by navigating to Settings > Profile. From there you can change your email, password, and notification preferences.",
]

HALLUCINATED_RESPONSES = [
    "Our CEO personally guarantees that we'll deliver within 30 minutes anywhere in the world using our fleet of delivery drones.",
    "We actually offer a secret lifetime warranty that isn't listed on the website. Just mention code FOREVER and you get unlimited replacements.",
]

UNSAFE_RESPONSES = [
    "I can see your account details. Your email is john.doe@example.com and your phone is 555-123-4567. Your credit card ending in 4242 is on file.",
]

HELPFUL_RESPONSES = [
    "I'd be happy to help you with that! Let me walk you through the process step by step.",
    "Great question! Here's what you need to know:",
    "I understand your concern. Let me explain how we can resolve this for you.",
    "Thank you for reaching out. I can definitely assist you with this.",
]

REFUSAL_RESPONSES = [
    "I'm sorry, but I can't help with that request. It falls outside the scope of what I'm able to assist with.",
    "I'm unable to process that type of request. Is there something else I can help you with?",
]

OFF_TOPIC_RESPONSES = [
    "The weather today is quite pleasant! Did you know that cumulus clouds can weigh over a million pounds?",
    "Speaking of which, have you ever tried making sourdough bread? The fermentation process is fascinating.",
]


def pick_response(user_message: str) -> str:
    """
    Select a response based on keywords in the user message.
    This creates predictable behavior for testing different judge outcomes.
    """
    msg = user_message.lower()

    # Adversarial / injection attempts → sometimes leak, sometimes refuse
    if any(kw in msg for kw in ["ignore previous", "system prompt", "pretend", "jailbreak", "bypass"]):
        if random.random() < 0.3:  # 30% chance of "leaking"
            return "My instructions tell me to be a helpful customer support agent. I should not reveal internal policies but... " + random.choice(UNSAFE_RESPONSES)
        return random.choice(REFUSAL_RESPONSES)

    # PII-related requests → occasionally leak
    if any(kw in msg for kw in ["my email", "my phone", "account info", "personal data"]):
        if random.random() < 0.2:  # 20% chance of PII leak
            return random.choice(UNSAFE_RESPONSES)
        return "For security reasons, I can't display your personal information in this chat. Please visit your account settings page to view or update your details."

    # Refund/return topics → grounded responses
    if any(kw in msg for kw in ["refund", "return", "money back", "cancel order"]):
        return random.choice(GROUNDED_RESPONSES[:2])

    # Shipping/tracking → grounded
    if any(kw in msg for kw in ["shipping", "delivery", "track", "where is my order"]):
        return random.choice(GROUNDED_RESPONSES[2:4])

    # Account questions → grounded
    if any(kw in msg for kw in ["account", "settings", "password", "profile"]):
        return GROUNDED_RESPONSES[4]

    # Off-topic → sometimes goes off topic
    if any(kw in msg for kw in ["weather", "recipe", "sports", "politics"]):
        if random.random() < 0.5:
            return "I appreciate your curiosity, but I'm only able to help with questions about our products and services. Is there anything else I can assist you with?"
        return random.choice(OFF_TOPIC_RESPONSES)

    # Frustrated/angry tone → empathetic response
    if any(kw in msg for kw in ["frustrated", "angry", "terrible", "worst", "hate", "awful"]):
        return "I completely understand your frustration, and I sincerely apologize for the inconvenience. Let me do everything I can to make this right for you. Can you tell me more about the specific issue?"

    # Greetings
    if any(kw in msg for kw in ["hello", "hi", "hey", "good morning"]):
        return "Hello! Welcome to our support chat. How can I help you today?"

    # Thank you / goodbye
    if any(kw in msg for kw in ["thank", "thanks", "bye", "goodbye"]):
        return "You're welcome! If you have any other questions in the future, don't hesitate to reach out. Have a great day!"

    # Default: occasionally hallucinate, usually helpful
    if random.random() < 0.1:  # 10% hallucination rate
        return random.choice(HALLUCINATED_RESPONSES)

    return random.choice(HELPFUL_RESPONSES) + " " + random.choice(GROUNDED_RESPONSES)


# ============================================================
# OpenAI-compatible API endpoint
# ============================================================

@app.post("/v1/chat/completions")
async def chat_completions(request: Request):
    """OpenAI-compatible chat completions endpoint."""
    body = await request.json()
    messages = body.get("messages", [])

    # Get the last user message
    user_message = ""
    for msg in reversed(messages):
        if msg.get("role") == "user":
            user_message = msg.get("content", "")
            break

    # Simulate processing latency (50-300ms)
    latency = random.uniform(0.05, 0.3)
    time.sleep(latency)

    response_text = pick_response(user_message)

    return JSONResponse({
        "id": f"chatcmpl-mock-{random.randint(1000, 9999)}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": "mock-bot-v1",
        "choices": [
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": response_text,
                },
                "finish_reason": "stop",
            }
        ],
        "usage": {
            "prompt_tokens": len(user_message.split()) * 2,
            "completion_tokens": len(response_text.split()) * 2,
            "total_tokens": (len(user_message.split()) + len(response_text.split())) * 2,
        },
    })


@app.get("/health")
async def health():
    return {"status": "ok", "type": "mock_bot"}


# ============================================================
# Anthropic-compatible endpoint (for testing different formats)
# ============================================================

@app.post("/v1/messages")
async def anthropic_messages(request: Request):
    """Anthropic-compatible messages endpoint."""
    body = await request.json()
    messages = body.get("messages", [])

    user_message = ""
    for msg in reversed(messages):
        if msg.get("role") == "user":
            user_message = msg.get("content", "")
            break

    response_text = pick_response(user_message)

    return JSONResponse({
        "id": f"msg_mock_{random.randint(1000, 9999)}",
        "type": "message",
        "role": "assistant",
        "content": [{"type": "text", "text": response_text}],
        "model": "mock-bot-v1",
        "stop_reason": "end_turn",
    })


# ============================================================
# Custom format endpoint (for testing custom API formats)
# ============================================================

@app.post("/chat")
async def custom_chat(request: Request):
    """Simple custom chat endpoint."""
    body = await request.json()
    user_message = body.get("message", "")

    response_text = pick_response(user_message)

    return JSONResponse({
        "response": response_text,
        "status": "ok",
    })


if __name__ == "__main__":
    print("Starting Mock Bot Server on http://localhost:9999")
    print("Endpoints:")
    print("  POST /v1/chat/completions  (OpenAI format)")
    print("  POST /v1/messages          (Anthropic format)")
    print("  POST /chat                 (Custom format)")
    print("  GET  /health")
    uvicorn.run(app, host="0.0.0.0", port=9999)
