# FH-Tier-2 — Slice 3 CLOSED (FH-D3-HTTP-Layer / Workspace Selector)

## 0. Role & discipline (carry verbatim)
Claude = Master Tech Architect for AI SimTest (FastAPI + SQLAlchemy 2.x async +
asyncpg + Neon PG16 + Clerk + Next.js, monorepo). Operator = Bishnu (sole dev,
Singapore). Working dir `packages/api`; root venv `.venv`. Non-negotiables:
review-before-implement + plan versioning; empirical-first (probes/output BEFORE
patch); per-commit gates; sacred-test additive-only (named tests never renamed);
terse list-heavy comms; fail-loud Python patches (`assert old in content` +
count==1); heredoc commits with gates filled AFTER they pass; explicit `git add`
per file; close ONLY on exact phrase `Proceed with FH-Tier2 S<N> close`. Run
suites with `.t45_env` NOT sourced AND root `.venv` active.

## 1. What Slice 3 delivered
Provider-agnostic HTTP workspace selector — the last FH-Tier-2 item gating the
Week 6 UI (turn4_5 evidence D3 / FH-D3-HTTP-Layer). Middleware reads an
`X-Workspace-Id` request header after `provider.verify` and injects it into
`claims.workspace_id` (`model_copy`) BEFORE bootstrap, so the EXISTING
explicit-workspace-claim validation (workspace-belongs-to-tenant + membership)
and repo-layer enforcement (`workspace_filter` + `CrossWorkspaceForbidden`)
apply. Header absent -> unchanged default-workspace behavior. Canonical header
overrides any provider-set value (e.g. dev `X-Dev-Workspace-Id`). No new error
path: invalid workspace -> 404 (WorkspaceNotFound), cross-tenant -> 403
(CrossTenantForbidden) via existing middleware handlers.

## 2. Commits
- Pre-step (already on main): F-25 1120828 — untrack 29 .pyc + ignore __pycache__.
- B.1 40c60d3 — middleware X-Workspace-Id selector + 2 tests (one commit = whole slice).

## 3. Gate EXIT state
| Gate | Value |
|---|---|
| make test-db | 173 passed / 2 skipped |
| pytest -k app_factory | 49 |
| tests/auth/ | 90 (was 88; +2 selector tests) |
| tests/audit/ | 68 |
| tests/test_comparisons_router.py | 15 |
| sacred logger.py blob | d9741eff... (unchanged) |
| sacred context.py blob | 23f41bdd... (unchanged) |

## 4. Sacred-surface attestation
- src/audit/logger.py / context.py — byte-identical.
- src/auth/middleware.py — additive 12-line block only (between verify and bootstrap).
- All 88 pre-existing tests/auth preserved; +2 additive in test_tenant_context_middleware.py.

## 5. New symbols / verified facts
- HTTP header `X-Workspace-Id` (provider-agnostic). Injection: middleware
  `claims = claims.model_copy(update={"workspace_id": header.strip()})`.
- VerifiedClaims is frozen (model_copy is the mutation path); workspace_id: str|None.
- bootstrap honors claims.workspace_id (ensure_tenant_and_workspace) + validates
  via workspace_repo.get_by_id (WorkspaceNotFound->404, CrossTenantForbidden->403,
  both already mapped in middleware bootstrap try/except).
- Positive non-default selection proven transitively (test_bootstrap_hardening
  honors claims.workspace_id) + the header->claims wiring (this slice).

## 6. Backlog (carried)
- DONE this session: F-25 .pyc hygiene.
- FH-Clerk-Audience-Validation (prod gate); FH-Asset-Secret-Leak-HTTP (mount /v1/assets);
  comparison config persistence threading; generic *_REJECTED_SECRET_LEAK rollout;
  FH-Middleware-Order; FH-Env-Mutation; F-20; FH-S8.5 atomic audit+write; B.2b Neon
  E2E; secret-scan pattern tightening; Future-1..5.
- Pre-existing FH (sprint-plan §13): typed per-asset content schemas; asset registry
  version history; run_events table; audit partition rotation; idempotency-key sweeper;
  service-account AuthProvider; audit retention pruning; local-users migration;
  tenant-level endpoints (workspace CRUD / member invite / role change); workspace RLS.
- NONE of the above gate Week 6 UI (prod-gate or hygiene only).

## 7. Roadmap position
FH-D3 was the LAST UI-gating FH-Tier-2 slice -> **Week 6 Next.js UI is now
unblocked** (~mid-June 2026; SaaS MVP mid-to-late July). Remaining FH-Tier-2 items
are prod-gating/hygiene and can interleave with or follow Week 6.
