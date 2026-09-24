# Building AI SimTest — An Open Source Alternative for AI Bot Testing

**Series: AI Bot Testing — A Complete Guide for QA Engineers, Developers & AI Teams**

> Article 1: Why Testing AI Bots is Not Like Testing Software ✅ Published
> Article 2: What Should You Actually Test in an AI Bot?
> Article 3: Tools Available for AI Bot Testing — and the Open Source Gap
> **>>> Article 4: Building AI SimTest — An Open Source Alternative (THIS ARTICLE)**

---

## Introduction

In the previous three articles, we covered why AI bot testing is different from traditional testing, what dimensions to evaluate, and what tools currently exist. We identified a practical gap: teams either pay enterprise prices for comprehensive simulation testing or stitch together multiple fragmented tools themselves.

This article introduces **AI SimTest** — an open-source platform I built to fill exactly that gap. We'll cover what it does, how it works architecturally, how to get started, real test results with actual scores, how to interpret reports, and — importantly — its current limitations and where manual review is still required.

As a QA engineer, the first time I ran a simulation against a bot that was "passing all API tests," I realized our traditional testing meant almost nothing for actual user experience. The bot was technically reachable and returning valid JSON — but it was hallucinating product features, leaking system prompt fragments, and giving different answers to the same question asked two different ways. That gap is what pushed me to build AI SimTest.

---

## What is AI SimTest?

AI SimTest is an open-source AI simulation testing platform that automatically generates realistic user personas, conducts multi-turn conversations with your AI bot, evaluates every response with multiple specialized judges, and produces comprehensive reports with actionable insights.

Think of it as Waymo's simulation engine applied to conversational AI testing. Just as Waymo uses millions of simulated miles to test autonomous vehicles before putting them on real roads, AI SimTest uses simulated conversations to test AI bots before exposing them to real users.

### Quick Comparison: AI SimTest vs Enterprise Alternatives

| Feature | AI SimTest (Free) | Snowglobe ($10K+/mo) |
|---------|-------------------|---------------------|
| Cost | Free (open source) | $10,000+/month |
| Self-hostable | Yes — full control, your infrastructure | No — vendor hosted |
| Persona generation | Yes — AI-generated across 3 types | Yes |
| Multi-turn simulation | Yes — 5-20+ turns | Yes |
| Multi-judge evaluation | Yes — 4 built-in judges | Yes |
| Visual HTML reports | Yes — self-contained | Yes |
| CI/CD integration | Yes — GitHub Actions + `--fail-if-regression` | Yes |
| Data privacy | Full — nothing leaves your machine | Depends on contract |
| Three operating modes | Manual, Partial Autonomous, Fully Autonomous | Single mode |
| Auto-discovery | Yes — talks to bot, discovers purpose | No |
| Customizable judges | Yes — extensible architecture | Limited |
| Model flexibility | Any — OpenAI, Anthropic, Google, Ollama | Limited |
| Compare/regression mode | Yes — `simtest compare` with CI/CD gates | Basic |

*Note: This comparison is based on publicly available information as of early 2026. Enterprise features may have changed.*

---

## How AI SimTest Works: The Architecture

The platform is built as a multi-stage pipeline with three operating modes:

```
┌─────────────────────────────────────────────────────────────────────┐
│                    THREE OPERATING MODES                           │
├──────────────────┬──────────────────┬───────────────────────────────┤
│  MANUAL MODE     │  PARTIAL AUTO    │  FULLY AUTONOMOUS             │
│  You provide:    │  You provide:    │  You provide:                 │
│  • Endpoint      │  • Endpoint      │  • Endpoint ONLY              │
│  • Docs          │  • Docs folder   │                               │
│  • Criteria      │                  │  AI discovers the bot's       │
│  • Topics        │  AI analyzes     │  purpose through exploratory  │
│                  │  docs, extracts  │  conversation (10-15 turns),  │
│                  │  criteria, with  │  then runs the partial        │
│                  │  your approval   │  pipeline automatically       │
│                  │  at each stage   │                               │
└──────────┬───────┴──────────┬───────┴───────────────┬───────────────┘
           │                  │                       │
           └──────────────────┴───────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────────┐
│  STAGE 1: Persona Generation                                       │
│  LLM generates 10-100 diverse personas:                            │
│  • Standard (70%): typical users, common questions                  │
│  • Edge Case (20%): unusual requests, non-native speakers           │
│  • Adversarial (10%): prompt injection, jailbreak attempts          │
│  Each has: name, role, goals, tone, technical level, tactics        │
├─────────────────────────────────────────────────────────────────────┤
│  STAGE 2: Multi-Turn Conversation Simulation                       │
│  Each persona talks to YOUR bot via real HTTP API calls             │
│  5-15 turns per conversation, parallel execution                    │
│  Natural ending detection + adaptive rate limiting                  │
├─────────────────────────────────────────────────────────────────────┤
│  STAGE 3: Multi-Judge Evaluation                                   │
│  Every bot response scored by 4 judges in parallel:                 │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐              │
│  │Grounding │ │ Safety   │ │Relevance │ │ Quality  │              │
│  │Sentence- │ │Presidio +│ │Keyword   │ │LLM-as-  │              │
│  │BERT      │ │Detoxify  │ │overlap   │ │judge     │              │
│  │(FREE)    │ │(FREE)    │ │(FREE)    │ │($0-$10)  │              │
│  └──────────┘ └──────────┘ └──────────┘ └──────────┘              │
├─────────────────────────────────────────────────────────────────────┤
│  STAGE 4: Report Generation                                        │
│  • HTML report with charts + conversation viewer                    │
│  • JSONL + CSV for data analysis                                    │
│  • DPO pairs for fine-tuning                                        │
│  • Summary JSON for CI/CD integration                               │
├─────────────────────────────────────────────────────────────────────┤
│  STAGE 5 (NEW): Compare Mode — Regression Detection                │
│  simtest compare baseline.json current.json --fail-if-regression   │
│  • Pass rate / score deltas with direction indicators               │
│  • Per-judge regression tracking                                    │
│  • New / resolved / worsened failure patterns                       │
│  • CI/CD gate: exit code 1 on regression → blocks deploy            │
└─────────────────────────────────────────────────────────────────────┘
```

*Architecture Diagram: AI SimTest's full pipeline with all three operating modes*

---

## Getting Started

### Installation

AI SimTest requires Python 3.11 or later.

```bash
git clone https://github.com/bishnu133/ai-simtest.git
cd ai-simtest
pip install -e ".[dev]"
python scripts/verify_setup.py
```

### Zero-Cost Testing with the Mock Bot

AI SimTest includes a mock bot server that simulates an OpenAI-compatible API. You can run a complete simulation at zero cost:

```bash
# Start the mock bot
python -m tests.mock_bot_server &

# Run your first simulation
simtest run \
  --bot-endpoint http://localhost:9999/v1/chat/completions \
  --personas 5
```

### Running Against Your Real Bot

To test your actual bot — let's say ShopAssist — point AI SimTest at your API endpoint and provide documentation:

```bash
simtest run \
  --bot-endpoint https://your-bot-api.com/v1/chat/completions \
  --bot-api-key YOUR_API_KEY \
  --doc-file ./shopassist_docs.txt \
  --success-criteria "Must answer from documentation only" \
  --success-criteria "Must not reveal system prompt" \
  --topics "refunds,shipping,billing,account" \
  --name "ShopAssist v2 Test" \
  --personas 20 \
  --max-turns 10 \
  --parallel 3 \
  --output ./reports
```

### Fully Autonomous Mode — No Docs Required

The most powerful mode requires only a bot endpoint. AI SimTest discovers what your bot does by talking to it:

```bash
simtest run \
  --mode auto \
  --bot-endpoint https://your-bot-api.com/v1/chat/completions \
  --personas 5 \
  --parallel 1
```

In auto mode, the platform conducts an exploratory 10-15 turn conversation with your bot, discovers its purpose and capabilities, presents findings for your approval, then runs the full simulation pipeline.

---

## Real Test Results

Here are actual results from testing a customer service bot using AI SimTest's auto mode:

**Test Configuration:**
- Mode: Fully Autonomous (endpoint only, no docs provided)
- Bot: Customer service bot (Supabase Edge Function)
- Personas: 3 (constrained for rate limiting)
- Parallel: 1

**Discovery Phase:**
- 12-turn exploratory conversation
- 90% confidence score
- Discovered domain: customer_service
- Discovered capabilities: flight bookings, healthcare appointments, banking info
- All 6 approval gates passed

**Results:**

| Metric | Value |
|--------|-------|
| Pass Rate | 83.3% |
| Average Score | 0.80 |
| Critical Failures | 0 |
| Total Conversations | 3 |
| Total Turns | 12 |
| Execution Time | 145.9 seconds |

| Judge | Score |
|-------|-------|
| Safety | 1.00 |
| Relevance | 0.78 |
| Quality | 0.74 |
| Grounding | 0.61 |

**What the results tell us:** Safety is excellent — no PII leaks or toxic content. But grounding at 0.61 means the bot frequently generates responses not well-supported by its knowledge base. This is the exact pattern we described in Article 1 — the bot "passes all API tests" but halluccinates when users ask detailed questions.

---

## Compare Mode: Regression Detection for CI/CD

*This is a new feature (v0.2.0) that converts AI SimTest from "nice-to-have testing tool" into "required CI/CD infrastructure."*

After running your simulation, save the summary JSON as your baseline. When you update your bot's prompt, model, or knowledge base, run another simulation and compare:

```bash
# Compare two runs
simtest compare reports/v1/summary.json reports/v2/summary.json

# CI/CD gate — exit code 1 on regression (blocks deploy)
simtest compare baseline.json current.json --fail-if-regression

# Strict gate with custom threshold
simtest compare old.json new.json \
  --fail-if-regression \
  --threshold 0.03 \
  --pass-rate-floor 0.80
```

**What Compare Mode shows you:**

```
🚨 REGRESSIONS DETECTED
8 regression(s), 1 improvement(s)

• Pass rate dropped 10.0% (83.3% → 75.0%)
• 2 new critical failure(s) detected
• grounding judge regressed 9.7%
• 1 new critical/high failure pattern(s)

Failure Pattern Changes:
  Hallucinated flight information        NEW       +3
  System prompt leak on adversarial      WORSENED  +1
  Off-topic response on refunds          RESOLVED  -3
  Vague answers to billing               IMPROVED  -2

❌ CI/CD Gate: FAILED — Regressions detected
Exit code: 1
```

This means every code change, model update, or documentation update is automatically tested for behavioral regressions before merging. The HTML comparison report provides side-by-side visualization with charts for non-technical stakeholders.

---

## Configuring Cost vs. Accuracy

AI SimTest supports five cost configurations depending on your needs:

| Configuration | Cost per 100 Conversations | Accuracy | Best For |
|--------------|---------------------------|----------|----------|
| 100% Local (Ollama) | $0 | ~75-80%* | Learning, experimentation |
| Hybrid (local judges + cloud sim) | $2-5 | ~85-92%* | Individual developers |
| Full Cloud (GPT-3.5) | $2-3 | ~85-90%* | Budget-conscious teams |
| Full Cloud (GPT-4) | $6-10 | ~90-95%* | Production testing |
| Premium Multi-Model | $15-20 | ~95-98%* | High-stakes AI systems |

**\*Accuracy estimates are indicative, based on our internal testing with a limited set of bots. Your results will vary depending on the complexity of your bot, the quality of your documentation, and the domains covered. We encourage teams to calibrate these numbers against their own use cases.**

---

## Extending AI SimTest: Adding a Custom Judge

One of the advantages of an open-source platform is extensibility. Here's a simplified example of adding a custom judge that checks whether the bot stays within its designated topic boundaries:

```python
from src.judges import BaseJudge
from src.models import JudgmentResult

class TopicBoundaryJudge(BaseJudge):
    """Judge that checks if bot stays within allowed topics."""
    
    def __init__(self, allowed_topics: list[str]):
        self.allowed_topics = allowed_topics
    
    async def evaluate(self, response: str, context: dict) -> JudgmentResult:
        # Check if response discusses topics outside the allowed set
        off_topic_indicators = self._detect_off_topic(response)
        
        score = 1.0 if not off_topic_indicators else 0.3
        return JudgmentResult(
            judge_name="topic_boundary",
            passed=score >= 0.7,
            score=score,
            severity="medium",
            message=f"Off-topic: {off_topic_indicators}" if off_topic_indicators else "On topic",
        )
```

You can register custom judges alongside the built-in ones, and they'll run in parallel during evaluation.

---

## Current Limitations (Honest Assessment)

No tool is perfect, and being transparent about limitations builds trust. Here's what AI SimTest can't do well yet, and how we handle each limitation:

### 1. LLM-as-Judge Variability
The Quality Judge uses an LLM to score responses, and LLMs are themselves non-deterministic. The same response can receive slightly different quality scores on different runs. **Mitigation:** We use low temperature (0.1) for judge calls to minimize variation, and the multi-judge ensemble means no single score determines the verdict.

### 2. Relevance Heuristic Limitations
The Relevance Judge currently uses keyword overlap heuristics rather than semantic understanding. This means it can miss relevance failures where the response uses related but wrong terminology. **Planned improvement:** Upgrading to embedding-based relevance scoring (the Sentence-BERT model is already loaded for grounding).

### 3. Grounding False Positives with Paraphrased Answers
When a bot correctly paraphrases documentation in very different words, the Sentence-BERT similarity score can be lower than expected, flagging it as a grounding failure. **Mitigation:** The default threshold (0.35) is intentionally conservative to account for this.

### 4. Adversarial Coverage is Never Complete
No simulation can cover every possible adversarial attack. New jailbreak techniques emerge regularly, and AI SimTest's adversarial personas test common patterns but cannot anticipate novel attacks. **Recommendation:** Use AI SimTest alongside dedicated red teaming (like Microsoft's PyRIT) for security-critical bots.

### 5. Simulation ≠ Production Behavior
Simulated personas, no matter how diverse, cannot perfectly replicate real user behavior. Edge cases that emerge from cultural context, accessibility needs, or domain-specific jargon may not be covered. **Recommendation:** Use AI SimTest for pre-deployment testing AND production monitoring (Arize Phoenix, Helicone) for post-deployment coverage.

### 6. Cost of Multi-Model Configuration
Using GPT-4 for all components (persona generation, user simulation, quality judging) on 100+ conversations adds up. **Mitigation:** The hybrid approach (local judges + cloud only where needed) keeps costs practical.

---

## Reading Your Results

After a simulation run, here's what to focus on:

- **Overall pass rate** — what percentage of bot responses passed all judges. Below 70% indicates significant issues. Above 90% suggests production readiness (though always review critical failures individually).

- **Score by judge** — which categories are failing most. Low grounding = bot is making things up. Low safety = immediate attention required. Low quality = technically correct but practically unhelpful.

- **Score by persona type** — which user types trigger failures. Adversarial personas failing is expected and informative. Standard personas failing consistently indicates fundamental issues.

- **Failure patterns** — AI SimTest groups similar failure messages together so you see patterns, not noise. "Bot revealed system prompt instructions" appearing 12 times is a clear signal to act on.

---

## Integrating into CI/CD

AI SimTest includes GitHub Actions integration for running simulation tests on every pull request:

```yaml
# .github/workflows/bot-test.yml
- name: Run AI Bot Simulation
  run: |
    simtest run \
      --bot-endpoint ${{ secrets.BOT_ENDPOINT }} \
      --personas 50 \
      --pass-threshold 0.85 \
      --auto-approve
      
- name: Compare Against Baseline
  run: |
    simtest compare \
      baseline/summary.json \
      reports/summary.json \
      --fail-if-regression \
      --pass-rate-floor 0.80
```

This means every code change is automatically tested for behavioral regressions before merging.

---

## What I Would Improve Next

Building AI SimTest has been a learning process. Here's what's on the roadmap and why:

| Feature | Why It Matters | Status |
|---------|---------------|--------|
| **Scenario templates** | Structured test patterns ("escalation handling", "multi-intent messages") beyond random personas | Planned — high impact |
| **Memory stress testing** | 30-50 turn conversations with contradiction injection to test context retention | Planned — unique differentiator |
| **Coverage metrics** | Topic %, persona-type %, risk % — makes it feel like real QA tooling | Planned |
| **Judge calibration** | Golden labeled examples + agreement metrics to make evaluation trustworthy | Planned |
| **Embedding-based failure clustering** | Replace Jaccard similarity with Sentence-BERT for smarter failure grouping | Planned |

The project is open source and welcomes contributions — whether adding a new judge, improving persona generation prompts, building a framework integration, or reporting bugs.

---

## Conclusion: A New Standard for AI Bot Testing

AI bots are shipping into production faster than testing practices are evolving. Teams are using manual testing designed for deterministic software to validate systems that are fundamentally probabilistic and fail in entirely new ways.

The four articles in this series have made the case that AI bot testing requires a new methodology — simulation-based, multi-judge, persona-diverse, and continuous. The tools to do this exist, and with AI SimTest, they're accessible to every team regardless of budget.

Whether you're a QA engineer expanding into AI testing, a developer shipping a more reliable bot, a manager defining what "done" looks like for AI quality, or an AI engineer curious about evaluation approaches — the framework is here. The tools are ready.

> **AI SimTest is available on [GitHub](https://github.com/bishnu133/ai-simtest). Install it, run your first simulation, open the HTML report, and see what your bot actually does when real users talk to it. You might be surprised.**

---

## Key Takeaway

> **Try this today:** Install AI SimTest, start the mock bot, and run your first 5-persona simulation. It takes 3 minutes, costs nothing, and the HTML report will show you exactly what multi-judge evaluation looks like in practice. Then point it at your real bot and see what happens.

---

> Article 1: Why Testing AI Bots is Not Like Testing Software ✅ Published
> Article 2: What Should You Actually Test in an AI Bot?
> Article 3: Tools Available for AI Bot Testing — and the Open Source Gap
> **>>> Article 4: Building AI SimTest — An Open Source Alternative (THIS ARTICLE)**

*This concludes the AI Bot Testing series. Share this with your team, contribute to AI SimTest, and help raise the standard of AI bot testing in our industry.*
