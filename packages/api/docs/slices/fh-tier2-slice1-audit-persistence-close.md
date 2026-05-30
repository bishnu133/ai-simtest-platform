# FH-Tier-2 Slice 1 — Audit Persistence Wiring (T45-8) — CLOSE

**Slice:** FH-Tier-2 Slice 1 — Audit Persistence Wiring
**Tracking item:** T45-8 (Turn 4.5 Row 6 / D1 PARTIAL trigger)
**Branch:** `fh-tier2-slice1-audit-persistence` (base `6b96507`, tag `fh-tier1-slice7.8-close`)
**Closed:** 2026-05-30
**Tag:** `fh-tier2-slice1-close` (squash commit on `main`)
**Master Tech Architect:** Claude · **Operator:** Bishnu Prasad Panda

## Objective
Wire `audit_events` to persist to Postgres over a real HTTP request, closing the
Turn 4.5 Row 6 / D1 PARTIAL gap (audit channel absent on the rejection path; every
Neon snapshot showed `audit_events = 0`).

## Root cause — config gap, not a code defect
The composition root already contained
`audit_logger.bind_repository(PostgresAuditEventRepository())` in `src/app_factory.py`,
gated behind the `use_postgres_audit_events` setting (default `False`). The operator
scaffolding (`.t45_env`, untracked) never set `USE_POSTGRES_AUDIT_EVENTS`, so the
logger stayed on its in-memory default. Fix = activate the switch + harden, not new wiring.

## Work delivered
- **B.0 — Neon HTTP proof (no commit).** With the switch on, a real Clerk-authenticated
  run over HTTP produced (last-15-min window): `auth.accepted = 2` (tenant),
  `auth.rejected = 1` (pretenant), **0** suppressed `aemit_*_safe` warnings — confirming
  request -> middleware -> bound repo -> Neon row.
- **B.1 (`356f3a2`) — staging/prod fail-loud guard + atomic fixtures.**
  `_enforce_production_guardrails` raises `FatalConfigurationError` if
  `use_postgres_audit_events` is off in staging/production. +4 coupling/guard tests
  (`test_staging_production_requires_audit_persistence`, 4-case);
  `test_app_factory_guardrails.py` updated with audit kwarg + singleton pin. (3 files, +64.)
- **B.2a (`7a993a1`) — composition-root HTTP regression pin.** New
  `tests/db/test_audit_logger_postgres.py::test_create_app_routes_http_rejected_request_to_bound_audit_repository`:
  a no-`Authorization` request through a `create_app`-composed app reaches the
  composition-root-bound spy via `append_pretenant_event` with `auth.rejected`. Closes
  the gap between the bind tests (no HTTP) and the middleware tests (no `create_app`).
  (1 file, +79.)
- **B.3 — Turn 4.5 evidence-doc banner (project-knowledge only; doc lives outside repo).**
  Additive "T45-8 RESOLVED" banner superseding the historical NOT-WIRED status,
  citing B.0/B.1/B.2a.

## Gate matrix (frozen gates: entry -> exit)
| Gate | Entry | Exit |
|---|---|---|
| `make test-db` | 168 / 2 skip | 173 / 2 skip |
| `pytest -k app_factory` | 45 | 49 |
| `tests/auth/` | 88 | 88 |
| `tests/audit/` | 68 | 68 |
| destructive + auth + audit (aggregate) | — | 159 |
| sacred diff `logger.py`+`context.py` vs `6b96507` | empty | empty |

## Sacred surfaces
`src/audit/logger.py` and `src/audit/context.py` byte-identical vs base `6b96507`
(empty diff). No named test renamed/removed; all changes additive.

## Security
- **SEC-0 — credential rotation.** A live Neon credential was exposed during early B.0
  harness setup; rotated and `.t45_env` updated before any harness run. `.t45_env`
  never tracked.
- **Secret-scan note.** The initial secret scan matched benign `postgresql://` scheme
  literals only. No `npg_`, `sk_`, `pk_`, or credentialed DB URL was present. Future
  scans should use a tighter pattern: `npg_|sk_live|sk_test|pk_live|pk_test|://[^/@\s]+:[^/@\s]+@`.

## Backlog carried (not in this slice)
- **FH-Comparison-SafeJSONB-HTTP** (FH-Tier-2 S2) — `config` silently strips unknown
  keys; `COMPARISON_CREATE_REJECTED_SECRET_LEAK` goes live when SafeJSONB lands.
- **B.2b** — Neon-gated E2E audit-row assertion (deferred; tenant-accepted persistence
  covered by B.0 + repo tests + middleware tests).
- **FH-S8.5** — atomic audit + business write (session plumbing / repo-layer emission).
- **F-25** — tracked `.pyc` hygiene (recompiled at runtime; not in commits).
- **Secret-scan pattern tightening** (process item, per note above).
- Prior FH backlog (FH-Runbook-5a/10, FH-Middleware-Order, FH-Env-Mutation,
  Future-1..5, F-20) unchanged.

## Verification trail
Phase A (empirical diagnosis -> verdict H1 config-gap) -> B.0 (Neon proof) -> B.1 -> B.2a
-> B.3 -> Phase C skipped (no drift) -> Phase D (read-only matrix, all green) -> Phase E (close).
