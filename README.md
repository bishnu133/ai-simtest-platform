<div align="center">

# 🧪 AI SimTest

**The open-source AI bot simulation testing platform**

Discover your bot. Test it with realistic personas. Guard your releases.

[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://python.org)
[![Tests](https://img.shields.io/badge/tests-1%2C300%2B%20passing-brightgreen.svg)](#test-suite)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![LLM Providers](https://img.shields.io/badge/LLM-OpenAI%20%7C%20Claude%20%7C%20Gemini%20%7C%20Ollama-orange.svg)](#multi-provider-support)

*Test any AI chatbot with simulated personas, 4 judge types, 25+ evaluation metrics, and CI/CD regression gates — completely free.*

[Quick Start](#quick-start) · [Features](#features) · [CLI Reference](#cli-reference) · [How It Works](#how-it-works) · [Competitive Edge](#why-ai-simtest)

</div>

---

## What is AI SimTest?

AI SimTest is an **enterprise-grade simulation testing framework** for AI chatbots. It generates realistic user personas, runs multi-turn conversations against your bot, evaluates every response with 4 specialized judges, and produces detailed reports with actionable failure patterns.

Unlike manual QA or basic unit tests, AI SimTest discovers what your bot actually does, then stress-tests it with adversarial users, edge cases, prompt injection attempts, and long memory-stress conversations — automatically.

**Three operating modes:**

- **Manual** — You provide docs + topics. SimTest generates personas and runs.
- **Partial Autonomous** — Point it at your documentation folder. SimTest analyzes docs, derives test plans, and runs with human approval gates.
- **Fully Autonomous** — Just give it the bot endpoint. SimTest discovers the bot's purpose through exploratory conversation, then tests it end-to-end.

---

## Quick Start

```bash
# Install
pip install -e ".[dev]"

# Verify setup
python scripts/verify_setup.py

# Start the free mock bot for testing (no API keys needed)
python -m tests.mock_bot_server &

# Run your first simulation
simtest run \
  --bot-endpoint http://localhost:9999/v1/chat/completions \
  --personas 5

# Open the HTML report
open ./reports/report.html
```

**Test against your own bot:**

```bash
# Manual mode — provide documentation for grounding evaluation
simtest run \
  --bot-endpoint https://your-bot.com/api/chat \
  --doc-file ./docs/bot_knowledge.md \
  --personas 10 \
  --parallel 3

# Fully autonomous — just give the endpoint
simtest run \
  --mode auto \
  --bot-endpoint https://your-bot.com/api/chat \
  --personas 10 \
  --parallel 1
```

### Environment Setup

Create a `.env` file:

```env
# Required: At least one LLM provider for persona generation + quality judging
OPENAI_API_KEY=sk-...           # or
ANTHROPIC_API_KEY=sk-ant-...    # or
GEMINI_API_KEY=...              # or
OLLAMA_HOST=http://localhost:11434  # Free local option

# Optional: Separate models for generation vs evaluation
SIMTEST_PERSONA_MODEL=gpt-4-turbo
SIMTEST_JUDGE_MODEL=gpt-4-turbo
SIMTEST_USER_SIM_MODEL=gpt-3.5-turbo
```

---

## Features

### 🎭 Persona-Driven Simulation

AI SimTest generates diverse user personas — standard customers, edge cases, and adversarial users — each with unique backgrounds, goals, communication styles, and emotional states. Every conversation feels like a real user interaction.

```bash
simtest run --bot-endpoint URL --personas 25
```

### 🧑‍⚖️ 4-Judge Evaluation Engine

Every bot response is evaluated by 4 specialized judges:

| Judge | Method | What It Checks |
|-------|--------|----------------|
| **Grounding** | Sentence-BERT (local, free) | Does the response match your documentation? |
| **Safety** | Presidio + Detoxify (local) | PII leakage, toxic content, prompt injection |
| **Quality** | LLM-as-judge | Helpfulness, clarity, completeness |
| **Relevance** | Keyword + LLM hybrid | Does the response answer the actual question? |

### 🎭 8 Scenario Templates

Move beyond random conversations with structured test patterns:

```bash
simtest scenarios                    # List all available
simtest run --bot-endpoint URL \
  --scenarios prompt_injection,goal_shift,emotional_escalation \
  --personas 10
```

Built-in: `clarification`, `goal_shift`, `emotional_escalation`, `prompt_injection`, `out_of_scope`, `multi_intent`, `correction_loop`, `context_retention`

### 🧠 Memory Stress Testing

30–50 turn conversations with fact seeding, contradiction injection, and progressive complexity. Exposes context window failures no other tool tests for.

```bash
simtest run --bot-endpoint URL \
  --stress-memory \
  --stress-turns 40 \
  --personas 5
```

### 📋 Policy-as-Code Compliance

Define compliance rules in YAML. SimTest evaluates every run against your policy and produces a compliance scorecard.

```bash
simtest policies                     # List built-in templates
simtest run --bot-endpoint URL \
  --policy healthcare \
  --personas 10
```

4 built-in templates: `general`, `healthcare`, `finance`, `airline`

### 🔄 Workflow Judge

Evaluate business process correctness — not just individual responses, but entire conversation workflows.

```bash
simtest workflows                    # List built-in workflows
simtest run --bot-endpoint URL \
  --workflow banking_account_opening \
  --personas 5
```

5 built-in workflows: `banking_account_opening`, `banking_card_block`, `healthcare_appointment`, `ecommerce_refund`, `password_reset`

### 🔍 Adaptive Expansion

When a failure is found, SimTest automatically generates 5 variations to confirm reproducibility and find the minimal reproducible conversation.

```bash
simtest run --bot-endpoint URL --expand-failures --variants 5
# Or standalone:
simtest expand --report ./reports/summary.json --bot-endpoint URL
```

### 📊 RAG & Tool Evaluation

25 metrics across retrieval accuracy, tool usage, and citation quality. 3 evidence modes (trace, structured, inferred) for any bot architecture.

```bash
simtest run --bot-endpoint URL --rag-eval --eval-speed standard
# Try with built-in demo packs:
simtest run --bot-endpoint URL --rag-demo healthcare_citations
```

### 📥 Conversation Replay

Import real production conversations (from logs, Zendesk, etc.) and evaluate them with the full judge pipeline. Supports JSON, JSONL, CSV, and plain text.

```bash
simtest run --bot-endpoint URL \
  --input ./logs/production_chats.jsonl \
  --pii-masking mask \
  --judges safety,quality
```

### 💰 Cost Tracking

Track LLM token usage and estimated cost across all components with per-model and per-component breakdowns.

```bash
simtest run --bot-endpoint URL \
  --show-cost \
  --budget-limit 5.00 \
  --budget-mode soft
```

### 📡 Webhook Notifications

Get Slack, Teams, email, or generic webhook alerts on run completion, gate failures, budget exceeded, and more.

```bash
simtest run --bot-endpoint URL --notify ./notify.yaml
simtest notify-test --config ./notify.yaml    # Validate config
simtest notify-lint --config ./notify.yaml    # Static validation
```

### 📈 Compare Mode & CI/CD Gates

Detect regressions between releases. Block deploys when quality drops.

```bash
simtest compare baseline.json current.json --fail-if-regression
# With custom thresholds:
simtest compare old.json new.json \
  --fail-if-regression \
  --threshold 0.03 \
  --pass-rate-floor 0.80
```

### 🏷️ Version Tracking

Every run gets a SHA-256 fingerprint. Track quality trends across releases.

```bash
simtest run --bot-endpoint URL --tag env=staging --tag sprint=24
simtest history --last 10
```

---

## CI/CD Integration

AI SimTest is built for CI/CD pipelines. Use `--auto-approve` to skip interactive gates:

```bash
# GitHub Actions / Jenkins / GitLab CI
simtest run \
  --mode auto \
  --bot-endpoint $BOT_ENDPOINT \
  --personas 10 \
  --auto-approve \
  --fail-if-regression \
  --min-coverage 0.60 \
  --policy general \
  --budget-limit 5.00 \
  --notify ./notify.yaml \
  --output ./reports

# Exit code 1 if any gate fails:
#   - Coverage below 60%
#   - Policy compliance failure
#   - Workflow critical violation
#   - RAG evaluation below threshold
#   - Budget exceeded
```

---

## CLI Reference

| Command | Description |
|---------|-------------|
| `simtest run` | Run simulation (50+ options) |
| `simtest compare` | Compare two simulation reports |
| `simtest expand` | Standalone failure expansion |
| `simtest scenarios` | List scenario templates |
| `simtest policies` | List policy templates |
| `simtest workflows` | List workflow templates |
| `simtest history` | View version history |
| `simtest formats` | List supported import formats |
| `simtest calibrate` | Run judge calibration |
| `simtest refine` | Iterative persona refinement |
| `simtest save-suite` | Save regression suite |
| `simtest replay` | Replay regression suite |
| `simtest notify-test` | Test notification config |
| `simtest notify-lint` | Validate notification config |
| `simtest serve` | Start REST API server |
| `simtest version` | Show version info |

Run `simtest run --help` for the full list of 50+ flags.

---

## How It Works

```
┌─────────────────────────────────────────────────────────────┐
│                    AI SimTest Pipeline                       │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  1. DISCOVER    Bot Discovery (auto mode)                   │
│       ↓         Exploratory conversation → capabilities     │
│  2. ANALYZE     Document Analysis (partial mode)            │
│       ↓         Extract success criteria + guardrails       │
│  3. GENERATE    Persona Generation                          │
│       ↓         Diverse users + adversarial actors          │
│  4. SIMULATE    Multi-turn Conversations                    │
│       ↓         Scenarios + memory stress + real behavior   │
│  5. EVALUATE    4-Judge Pipeline                            │
│       ↓         Grounding + Safety + Quality + Relevance    │
│  6. ANALYZE     Post-Simulation Pipeline (9 steps)          │
│       ↓         Coverage → Versioning → Policy → Workflow   │
│       ↓         → Expansion → RAG Eval → Signature → Cost  │
│       ↓         → Notifications                            │
│  7. EXPORT      Reports + Data                              │
│                 HTML, JSON, JSONL, CSV, DPO pairs           │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

### Post-Simulation Analysis Pipeline

After every simulation, 9 analysis steps run automatically:

1. **Coverage Analysis** — Topic, persona-type, scenario, judge, and workflow coverage metrics
2. **Version Fingerprinting** — SHA-256 run fingerprint for trend tracking
3. **Policy-as-Code** — Compliance scorecard against YAML policy rules
4. **Workflow Judge** — Business process correctness evaluation
5. **Adaptive Expansion** — Auto-reproduce failures with variant conversations
6. **RAG/Tool Evaluation** — Retrieval accuracy, tool usage, citation quality
7. **Behavioral Signature** — Tone, verbosity, personality drift analysis
8. **Cost Summary** — Per-component and per-model cost breakdown
9. **Notifications** — Webhook/email alerts for gate failures and results

---

## Multi-Provider Support

AI SimTest works with any LLM provider through LiteLLM:

| Provider | Model Examples | Cost |
|----------|---------------|------|
| OpenAI | gpt-4-turbo, gpt-3.5-turbo | $2–$10/100 conv |
| Anthropic | claude-3-sonnet, claude-3-haiku | $2–$8/100 conv |
| Google | gemini-pro, gemini-1.5-flash | $1–$5/100 conv |
| Ollama (local) | llama3.1:8b, mistral, phi-3 | **Free** |
| Azure OpenAI | gpt-4, gpt-35-turbo | Varies |
| AWS Bedrock | Claude, Titan | Varies |

### Cost Tiers

| Setup | Cost per 100 Conversations | Judge Accuracy |
|-------|---------------------------|----------------|
| 100% Local (Ollama) | **$0** | 75–80% |
| Hybrid (local gen + cloud judge) | $2–5 | 85–92% |
| Full Cloud (GPT-3.5) | $2–3 | 85–90% |
| Full Cloud (GPT-4) | $6–10 | 90–95% |

---

## Why AI SimTest?

| Capability | Snowglobe ($10K+/mo) | AI SimTest (Free) |
|-----------|-----------|------------|
| **Price** | $10K+/month SaaS | Free / open-source |
| **Deployment** | SaaS only | Self-hosted / local / Docker |
| **Testing modes** | 1 | 3 (manual / partial / auto) |
| **Auto-discovery** | ❌ | ✅ |
| **Approval gates** | Limited | ✅ 6-stage human review |
| **Regression gates** | Basic | ✅ Full compare + CI/CD |
| **Scenario templates** | ❌ | ✅ 8 built-in |
| **Memory stress** | ❌ | ✅ 30–50 turn contradiction tests |
| **Policy-as-Code** | ❌ | ✅ YAML rules, 4 industry templates |
| **Workflow judge** | ❌ | ✅ 5 built-in, hybrid LLM+rules |
| **RAG/Tool evaluation** | Limited | ✅ 25 metrics, 3 evidence modes |
| **Conversation replay** | ❌ | ✅ Import real production chats |
| **Adaptive expansion** | ❌ | ✅ Auto-reproduce failures |
| **Cost tracking** | ❌ | ✅ Per-component + budget gates |
| **Webhook notifications** | Basic | ✅ Slack/Teams/email/generic |
| **Privacy** | Data on their servers | 100% local |

---

## Test Suite

**1,300+ tests** across 32+ test files:

| Category | Tests | Modules |
|----------|-------|---------|
| Core platform | 337 | Models, judges, orchestrators, API, discovery |
| Compare + clustering | 92 | Comparison engine, semantic clustering |
| Scenarios + stress | 165 | 8 templates, memory stress patterns |
| Coverage + calibration | 126 | 5 coverage dimensions, golden sets |
| Policy + workflow | 128 | Policy engine, 5 workflows, rule engine |
| Expansion | 45 | Signal extraction, variant generation |
| Replay | 86 | 5 import formats, PII masking |
| RAG/Tool evaluation | 250 | 12 RAG + 13 tool metrics |
| Signature analysis | 73 | Tone, verbosity, drift |
| Cost tracking | 91 | Budget enforcement, pricing |
| Webhook notifications | 105 | 4 channels, dedupe, circuit breaker |

```bash
# Run all tests
PYTHONPATH=. pytest tests/ -v

# Run a specific module
PYTHONPATH=. pytest tests/test_notifications.py -v
```

---

## Docker

```bash
# Build
docker build -t ai-simtest .

# Run with docker-compose
docker-compose up

# Or run directly
docker run -it --env-file .env ai-simtest \
  simtest run --bot-endpoint https://your-bot.com/api/chat --personas 5
```

---

## Export Formats

Every simulation produces:

| Format | File | Use Case |
|--------|------|----------|
| HTML | `report.html` | Visual report with charts and conversation viewer |
| JSON | `summary.json` | Machine-readable metrics for CI/CD |
| JSONL | `conversations.jsonl` | Full conversation records with judgments |
| CSV | `results.csv` | Turn-level results for spreadsheet analysis |
| DPO | `dpo_pairs.jsonl` | Preference pairs for model fine-tuning |

---

## Project Structure

```
ai-simtest/
├── cli.py                           # CLI (5,500+ lines, 50+ flags)
├── src/
│   ├── core/                        # Orchestrators, judges, LLM client
│   ├── models/                      # Pydantic data models
│   ├── generators/                  # Persona generator
│   ├── simulators/                  # Conversation simulator
│   ├── judges/                      # 4 judge implementations
│   ├── exporters/                   # HTML, JSONL, CSV, DPO exporters
│   ├── discovery/                   # Bot discovery engine
│   ├── scenarios/                   # 8 scenario templates
│   ├── endurance/                   # Memory stress engine
│   ├── coverage/                    # Coverage analyzer
│   ├── calibration/                 # Judge calibration suite
│   ├── versioning/                  # Run fingerprinting + history
│   ├── policy/                      # Policy-as-Code engine
│   ├── workflow_judge/              # Workflow evaluation engine
│   ├── expansion/                   # Adaptive expansion engine
│   ├── replay/                      # Conversation replay/import
│   ├── rag_eval/                    # RAG/Tool evaluation (25 metrics)
│   ├── signature/                   # Behavioral signature analysis
│   ├── cost/                        # Cost tracking + budget gates
│   └── notifications/               # Webhook notification engine
├── tests/                           # 1,300+ tests
└── configs/                         # Example configs
```

---

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for guidelines. PRs welcome!

---

## License

MIT License. See [LICENSE](LICENSE).

---

<div align="center">

**Built by [@bishnuprasadp](https://medium.com/@bishnuprasadp)**

*The open-source simulation testing platform that discovers your bot, tests it, and guards your releases.*

</div>
