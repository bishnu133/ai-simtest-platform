# AI SimTest — Web Dashboard

Set up and run AI SimTest simulations from the browser instead of the engine CLI.
The UI follows the Replit design: an 8-step wizard where each AI-generated step
(domain context → success criteria → guardrails → test plan → personas) waits for
human approval before the simulation runs, followed by basic and detailed reports.

Stack: Next.js 16 (App Router), React 19, TypeScript, Tailwind v4, shadcn/Radix UI, pnpm.

## How it connects

```
Browser ──/api/engine/*──▶ Next.js route handler ──/wizard/*──▶ AI SimTest engine (simtest serve)
                          (server-side proxy)                  src/api/wizard.py
```

- The browser only calls `/api/engine/...`. The route handler
  (`src/app/api/engine/[...path]/route.ts`) forwards to `${ENGINE_API_URL}/wizard/...`,
  so the engine URL and any bot API key never ship in client code.
- Only the `/wizard/simulations...` surface is proxied; everything else returns 404.
- If the engine is down, the proxy returns `502 {code: "engine_unreachable"}` and the UI says so.

| Screen | Engine call |
|---|---|
| Setup | `POST /wizard/simulations` |
| Context / Criteria / Guardrails / Test Plan / Personas | `GET /wizard/simulations/{id}/gate` → `POST .../gate/{key}/decision` |
| Loader | polls `GET /wizard/simulations/{id}` every 1.5 s |
| Setup evaluation options | `GET /wizard/options` (built-in workflows and policies) |
| Report | `GET /wizard/simulations/{id}/report` (report + `analysis` + approved `inputs`), downloads via `.../exports/{fmt}` |

The report page has four tabs:
- **Overview**: release-readiness verdict (rule shown on the page), headline numbers with change since the bot's previous run, latency p50/p95, coverage, cost, judge scores, turn outcomes, score by persona type, Fix These First, workflows
- **Failures**: the ranked Fix These First queue, all failure patterns (filter by severity, expand for the judge's full reasoning), recommendations
- **Conversations**: searchable, filterable, sortable table; a row opens the transcript with every judge's score and reasoning per bot reply
- **Inputs**: the context, criteria, guardrails and test plan that were approved, and the personas that ran

Button → decision mapping on review screens: **Approve** → `approved` (or `modified`
with your edits), **Reject** → `regenerate` (the AI drafts a fresh proposal),
**Stop simulation** → `cancel`. Personas can only be removed, not edited — that's what
the engine supports.

Mapping between engine proposals and table rows lives in `src/lib/engine/gates.ts`.

## Platform shell pages

`/overview`, `/dashboard`, `/conversations` and `/comparisons` (the `(shell)` route group, with
sidebar + topbar) read from the platform API (`packages/api`) through a second server-side proxy:
`/api/*` → `${API_INTERNAL_URL}/v1/*`, which injects the dev-auth headers
(`src/app/api/[...path]/route.ts`, `src/lib/api/`). The `/api/engine/*` route is more specific, so
engine calls never hit that proxy. Both proxies are server-only; see `.env.example`.

## Run locally

**Terminal A — engine** (in the `ai-simtest` repo, with an LLM key in `.env`):

```bash
pip install -e .                # once
simtest install-models          # once: spaCy model for finding/masking personal data
API_HOST=127.0.0.1 API_PORT=8100 simtest serve
```

Without `install-models`, personal data is found by pattern matching only: names are masked
where people introduce themselves and places not at all. The replay setup screen warns when
that is the case.

**Terminal B — dashboard** (this folder):

```bash
cp .env.example .env.local      # ENGINE_API_URL=http://127.0.0.1:8100
pnpm install
pnpm dev                        # http://localhost:3000
```

Uses pnpm 11 (pinned in `package.json`). pnpm 11 refuses packages published in the last 24 h and
only runs build scripts listed under `allowBuilds` in `pnpm-workspace.yaml`.

Corporate `~/.npmrc` breaking installs? Run with `NPM_CONFIG_USERCONFIG=/dev/null pnpm install`
(the committed `.npmrc` pins the public registry).

## Checks

```bash
pnpm lint && pnpm typecheck && pnpm build
```

## Limitations

- The engine keeps runs in memory: restarting it loses in-flight and finished runs
  (the Runs page only lists runs since the last engine start).
- The engine API has no auth — keep it bound to `127.0.0.1` behind this proxy.
- Results are not yet written into the platform API's Postgres (Phase 2), so the
  platform's Conversations/Comparisons endpoints don't see dashboard runs.
