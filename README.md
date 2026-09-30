# AI SimTest Platform

**Test your AI chatbot the way real customers use it, from the browser.**

AI SimTest Platform is the web dashboard for the open-source
[AI SimTest engine](https://github.com/bishnu133/ai-simtest). It works like this:

1. Point it at your bot and (optionally) its documentation.
2. The AI drafts what to test: success criteria, guardrails, a test plan and simulated customers. You review each step.
3. Watch those customers talk to your bot live.
4. Every reply is scored by independent judges: grounding, safety, quality and relevance, plus your compliance policies and business workflows.
5. The report tells you whether the release is ready and what to fix first.

No sign-in, no cloud account: run the engine and the dashboard on your own machine or server,
and your conversations stay there.

## What you can do

- **Test a bot in minutes.** Guided setup from your documentation, or from the endpoint alone. A
  health check tells you what's wrong with the connection before the run starts.
- **Watch it run.** A live screen shows who is talking, the latest exchanges and verdicts as they land.
- **Know what to fix.** Release verdict, risk overview, "Fix these first", chat-style transcripts
  with each judge's reasoning, and failed replies exported as a dataset.
- **Trust the verdicts.** Borderline replies are judged three times and marked **Unsure** when the
  runs disagree. Judge calibration shows how often each judge agrees with your team.
- **Guard every release.**
  - Regression suites replay past failures against new builds.
  - Templates run the same test again, by hand or on a schedule in your time zone.
  - CI pipeline steps fail the build when quality drops.
- **Test what matters to you.**
  - Scenario packs (prompt injection, goal shift, …) and memory stress.
  - Model comparison and production replay (personal data masked).
  - RAG and tool checks.
  - Your own personas, compliance policies and business workflows.
- **Your AI, your keys.**
  - OpenAI, Anthropic, Google Gemini or local Ollama models, set on the Settings page, with one-click recommended models.
  - Keys are write-only.
  - Bot keys are never stored.
- **Stay informed.** Slack, Teams, email or webhook notifications.

## Quick start

You need Python 3.11+, Node.js 20+ and pnpm, plus an API key for one AI provider (or Ollama).

**1. Engine** ([bishnu133/ai-simtest](https://github.com/bishnu133/ai-simtest)):

```bash
git clone https://github.com/bishnu133/ai-simtest.git
cd ai-simtest
pip install -e .
simtest install-models                            # once
API_HOST=127.0.0.1 API_PORT=8100 simtest serve
```

**2. Dashboard** (this repo, new terminal):

```bash
git clone https://github.com/bishnu133/ai-simtest-platform.git
cd ai-simtest-platform/packages/web
cp .env.example .env.local                        # ENGINE_API_URL=http://127.0.0.1:8100
pnpm install
pnpm dev                                          # http://localhost:3000
```

**3. In the browser:**
1. Open **Settings**, paste an AI key, click "Use … recommended", then Save.
2. Click **New test**.
3. No bot to try yet? The engine ships a free mock bot: `python -m tests.mock_bot_server`
   (at `http://localhost:9999/v1/chat/completions`).

Details, troubleshooting and running the engine for CI: [packages/web/README.md](packages/web/README.md).

## How it fits together

```
Browser ──▶ Dashboard (Next.js, packages/web) ──server-side proxy──▶ AI SimTest engine (simtest serve)
                                                                      runs, judges, reports, schedules
```

The browser never talks to the engine directly. The dashboard's server forwards only the
dashboard's own API calls. The engine address, its token and any bot key stay on the server. Keep
the engine bound to `127.0.0.1`. To reach it from CI, give it a token (`SIMTEST_API_TOKEN`); it
refuses to listen beyond the machine without one.

## Repository structure

```
ai-simtest-platform/
├── packages/
│   ├── web/        # The dashboard: Next.js 16, React 19, Tailwind v4 (start here)
│   ├── api/        # Platform API (FastAPI + PostgreSQL): tenancy, audit, asset registry; not needed to run the dashboard
│   └── engine/     # An older snapshot of the engine; use github.com/bishnu133/ai-simtest instead
├── infra/          # Docker files for the platform API
└── docs/           # Design notes
```

## Checks

```bash
cd packages/web && pnpm lint && pnpm typecheck && pnpm build     # dashboard
cd ai-simtest && ruff check src/ tests/ && pytest tests/          # engine
```

## Contributing

Issues and pull requests are welcome, especially from QA and testing folks: new scenario packs,
policies, workflows and judge improvements help everyone.

## License

- **Engine** ([ai-simtest](https://github.com/bishnu133/ai-simtest), and `packages/engine/`): MIT License (open source)
- **Platform** (everything else): Proprietary
