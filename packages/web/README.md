# AI SimTest — Web Dashboard

A browser UI for the [AI SimTest engine](https://github.com/bishnu133/ai-simtest).
Set up a test, review what the AI drafts, watch simulated customers talk to your bot live, and read
a report that says what to fix first, all without the CLI.

Stack: Next.js 16 (App Router), React 19, TypeScript, Tailwind v4, shadcn/Radix UI, TanStack Query, pnpm.

## What's in it

| Page | What it does |
|---|---|
| **Home** | Getting-started checklist, latest runs, pass-rate trend |
| **New test** | One form for every test type: persona simulation, scenario packs, memory stress, model comparison, regression suite, production replay, RAG & tools. Test size presets, strictness, recheck of borderline verdicts, your own personas, policies and workflows. The bot is checked before the run starts. |
| **Run** | Each AI draft waits for review (context, criteria, guardrails, test plan, personas), then the live screen shows who is talking, the latest exchanges and verdicts as they land |
| **Report** | Release verdict, risk overview, "Fix these first", failure patterns, chat-style transcripts with every judge's score and reasoning, judge trust (re-checked and unsure verdicts), the approved inputs. Exports: HTML, JSONL, CSV, failed replies as a dataset. Run again, or save as a template. |
| **Runs** | Every run, filterable; pick two to compare side by side |
| **Bots** | Saved bot connections (keys stay in the engine's environment, never stored), health status, and a trend per bot across builds |
| **Regression suites** | Failures kept as a suite and replayed against later builds |
| **Templates & schedules** | A run's setup, run again with one click or on a schedule (daily, weekdays, weekly) in your time zone |
| **Judge calibration** | Label a sample of replies; see how often each judge agrees with people and the pass mark that would agree best |
| **Personas** | Personas seen in your runs (hardest first) and your own library |
| **Policies & workflows** | Built-in compliance policies and business workflows in plain words; write your own in YAML, checked before saving, then use them in a test |
| **Integrations** | Slack, Teams, email and webhook notifications, with a test message per channel |
| **CI pipelines** | Generated GitHub Actions / GitLab CI / shell steps that replay a suite on every build, and every CI run's verdict |
| **Settings** | AI models per role (customers, test setup, judges) with one-click recommended models, AI requests at the same time, and provider keys (write-only) |

Dark mode, a command palette (Ctrl/⌘ K) and a collapsible sidebar are built in. There is no sign-in:
run it for yourself or your team on a machine you control.

## How it connects

```
Browser ──/api/engine/*──▶ Next.js route handler ──/wizard/*──▶ AI SimTest engine (simtest serve)
                          (server-side proxy)                  src/api/wizard.py
```

- The browser only calls `/api/engine/...`. The route handler
  (`src/app/api/engine/[...path]/route.ts`) forwards to `${ENGINE_API_URL}/wizard/...`, so the
  engine address, its token and any bot API key never reach client code.
- Only the dashboard's roots are proxied (simulations, options, suites, compare-runs, calibration,
  notifications, ci, ai-settings, bot-check, bots, personas, templates, library). Anything else is 404.
  `PUT` and `DELETE` are allowed only where the dashboard edits or removes something.
- The live run screen is a server-sent event stream (`/simulations/{id}/events`). If it can't open,
  the screen falls back to polling every 1.5 s.
- If the engine is down, the proxy returns `502 {code: "engine_unreachable"}` and the UI says so.

Main engine calls:

| Screen | Engine call |
|---|---|
| New test | `POST /wizard/simulations`; `GET /wizard/options`; `POST /wizard/bot-check` |
| Review steps | `GET /wizard/simulations/{id}/gate` → `POST .../gate/{key}/decision` |
| Live run | `GET /wizard/simulations/{id}/events` (SSE), falling back to `GET /wizard/simulations/{id}` |
| Report | `GET /wizard/simulations/{id}/report`, downloads via `.../exports/{fmt}` |
| Settings | `GET` / `PUT /wizard/ai-settings`, `POST /wizard/ai-settings/check` |

The engine README lists the full `/wizard` API.

Button → decision on review screens: **Approve** → `approved` (or `modified` with your edits),
**Reject** → `regenerate` (the AI drafts a fresh proposal), **Stop simulation** → `cancel`.
Mapping between engine proposals and table rows lives in `src/lib/engine/gates.ts`.

## Run locally

**Terminal A: engine** (in the `ai-simtest` repo):

```bash
pip install -e .                # once
simtest install-models          # once: models for finding/masking personal data and for grounding
API_HOST=127.0.0.1 API_PORT=8100 simtest serve
```

AI keys and models can go in the engine's `.env`, or on this dashboard's **Settings** page after it
starts.

**Terminal B: dashboard** (this folder):

```bash
cp .env.example .env.local      # ENGINE_API_URL=http://127.0.0.1:8100
pnpm install
pnpm dev                        # http://localhost:3000
```

Then open **Settings** → "Use … recommended" → Save. Then click **New test**.

**Engine beyond this machine (CI pipelines).** Start the engine with a token and a reachable
address, and give the dashboard the same token (server-side only, never `NEXT_PUBLIC_`):

```bash
SIMTEST_API_TOKEN=$(openssl rand -hex 24) API_HOST=0.0.0.0 API_PORT=8100 simtest serve   # engine
ENGINE_API_TOKEN=<the same value>                                                          # .env.local
```

The engine refuses to listen beyond this machine without a token.

**Production build:** `pnpm build && pnpm start`.

### Troubleshooting

- **A red "Runtime Error" naming `chrome-extension://…`** comes from a browser extension, not the
  dashboard. It only shows in `pnpm dev`. Use an Incognito window, or turn the extension off for localhost.
- **Type errors about `.next/types` after pulling:** `rm -rf .next && pnpm exec next typegen`.
- **Corporate `~/.npmrc` breaking installs:** `NPM_CONFIG_USERCONFIG=/dev/null pnpm install` (the
  committed `.npmrc` pins the public registry).
- Uses pnpm 11 (pinned in `package.json`). pnpm 11 refuses packages published in the last 24 h and
  only runs build scripts listed under `allowBuilds` in `pnpm-workspace.yaml`.

## Checks

```bash
pnpm lint && pnpm typecheck && pnpm build
```

## Good to know

- Finished runs are saved by the engine and survive a restart. A run still in progress when the
  engine stops is listed as failed.
- Schedules run inside the engine, so the engine must be running at the scheduled time.
- The engine holds live runs in one process: run one engine per dashboard.
- `/api/*` (other than `/api/engine/*`) is a second server-side proxy to the platform API
  (`packages/api`, `API_INTERNAL_URL`). No dashboard page depends on it yet.

## License

MIT. See [LICENSE](../../LICENSE).
