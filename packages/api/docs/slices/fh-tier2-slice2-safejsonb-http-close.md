# FH-Tier-2 — Slice 2 CLOSED (FH-Comparison-SafeJSONB-HTTP)

## 0. Role & discipline (carry verbatim)
Claude = Master Tech Architect for AI SimTest (FastAPI + SQLAlchemy 2.x async +
asyncpg + Neon PG16 + Clerk + Next.js, monorepo). Operator = Bishnu (sole dev,
Singapore). Working dir `packages/api`; root venv `.venv` (`../../.venv` from
`packages/api`). Non-negotiables: review-before-implement + plan versioning;
empirical-first (probes/terminal output BEFORE any patch); per-commit gates;
sacred-test discipline (additive-only; named tests never renamed); terse
list-heavy comms; fail-loud Python patches (`assert old in content` + count==1);
heredoc commits with gates filled AFTER they pass; explicit `git add` per file
(never `-a`); close ONLY on the exact phrase `Proceed with FH-Tier2 S<N> close`.
Run suites in a shell where `.t45_env` is NOT sourced AND the root `.venv` IS active.

## 1. What Slice 2 delivered
POST /v1/comparisons previously dropped unknown `config` keys silently (incl.
secret-shaped); `COMPARISON_CREATE_REJECTED_SECRET_LEAK` (logger.py:105) was
defined but never emitted ("Turn 2 only defines the constants"). Slice 2 makes a
secret-shaped `config` reject at the HTTP boundary with that audit action.
- Design B: router-level `check_no_secrets(body.config)` + safe-emit + typed
  APIError. Chosen over a global RequestValidationError handler (avoids global-422
  / OpenAPI-envelope blast radius; ctx is resolved at the route handler;
  comparison-scoped).
- Reject-only: clean config accepted at the boundary, NOT threaded to persistence.
- Comparison-only: asset/run/conversation siblings remain dead (deferred).

## 2. Commits (pre-squash)
- B.1 930e0cf — additive `ErrorCodes.SECRET_LEAK_DETECTED` + `SecretLeakRejected`
  (APIError, 422 via non-deprecated `HTTP_422_UNPROCESSABLE_CONTENT`) + unit test.
- B.2 7a8321f — `CreateComparisonRequest.config` (plain dict) + router
  boundary reject/emit/raise + 4 additive router tests.

## 3. Gate EXIT state = Slice 3 entry floors
| Gate | Value |
|---|---|
| make test-db | 173 passed / 2 skipped |
| pytest -k app_factory | 49 |
| tests/auth/ | 88 |
| tests/audit/ | 68 |
| tests/test_comparisons_router.py | 15 (was 11; +4 additive this slice) |
| sacred src/audit/logger.py blob | d9741eff… (unchanged vs S1) |
| sacred src/audit/context.py blob | 23f41bdd… (unchanged vs S1) |

## 4. Sacred-surface attestation
- src/audit/logger.py — byte-identical (.pyc recompiled = F-25 churn, not source).
- src/audit/context.py — byte-identical.
- src/comparisons/service.py `create_comparison` signature — unchanged (reject-only).
- All 11 pre-existing comparisons-router tests preserved; +4 additive.

## 5. New symbols / verified facts (Slice 3 awareness)
- ErrorCodes.SECRET_LEAK_DETECTED = "secret_leak_detected"; SecretLeakRejected(APIError,422).
- CreateComparisonRequest.config: dict[str, Any] (plain; secret check is in the router).
- Reject pattern: check_no_secrets(body.config) -> aemit_tenant_event_safe(
  to_tenant_audit_event_lenient(ctx, COMPARISON_CREATE_REJECTED_SECRET_LEAK,
  resource_type="comparison", resource_id="comparison:create",
  metadata={"field_path":..., "reason":"secret_leak_detected"})) -> raise SecretLeakRejected.
- to_tenant_audit_event_lenient passes resource_id THROUGH literally (synth-UUID
  applies only to ctx.tenant_id/workspace_id). api_error_handler renders exc.details
  at response["error"]["details"] and uses exc.http_status as the status code.

## 6. Backlog (carried/filed)
- NEW: comparison config persistence threading (config accepted, ignored downstream).
- NEW: generic *_REJECTED_SECRET_LEAK rollout (asset/run/conversation still dead).
- F-25 tracked .pyc hygiene (4 dirty .pyc seen this slice; never staged).
- B.2b Neon-gated E2E audit-row assertion; FH-S8.5 atomic audit+business write;
  secret-scan pattern tightening; FH-Runbook-5a/10; FH-Middleware-Order;
  FH-Env-Mutation; Future-1..5; F-20.
- FH-D3-HTTP-Layer = the last FH-Tier-2 slice before Week 6 UI.

## 7. Roadmap position
~1-2 weeks behind original 16-week plan (FH detour). FH-D3-HTTP-Layer remains;
then Week 6 UI (Next.js) ~ mid-June 2026; SaaS MVP mid-to-late July 2026.
