# FH-Tier-1 Slice 7.8 — Close Document

**Slice:** fh-tier1-slice7.8
**Date:** 2026-05-29
**Base commit:** `3e9795a` (tag `fh-tier1-slice7.7-close`, origin/main)
**Sacred-surface anchor:** `94a027f` (Slice 7.5 close)
**Squash-merge title:** `fh-tier1-slice7.8: F-20 helper rollout + F-24 docstring fix + F-26 query_all_events fix (L1 exception)`

---

## Charter

Scope: close three linked audit-shape defects.

- **F-20** — async-emit shape drift: M6's `auth.tenant_state_invalid` pretenant
  event surfaced an empty `resource_type`. Root cause spanned the helper, the
  read path, and the caller — required an end-to-end chain, not a point fix.
- **F-24** — stale `to_pretenant_audit_event` docstring (claimed
  PretenantAuditEvent lacked top-level resource fields).
- **F-26** — `query_all_events` was coercing PretenantAuditEvent's top-level
  `resource_type`/`resource_id` to empty strings in its pretenant for-loop,
  blocking F-20's end-to-end chain. This defect lives **inside the L1-locked
  surface** (`src/audit/logger.py`).

**Path A scope-widening rationale:** F-26 was discovered to sit inside the
sacred L1 surface. Rather than route around it (which would leave F-20's
read-path broken), Plan v0.2.1 approved a narrowly-scoped L1 exception to
repair the defect in place. See L14 record below.

---

## Commits (8, oldest → newest by plan; branch order C.1→C.5→C.2→C.3→C.4)

| # | SHA | What |
|---|-----|------|
| 1 | `27e57a6` | B.1: extend `to_pretenant_audit_event` with `resource_type`/`resource_id` kwargs (F-20) + fix stale docstring (F-24) |
| 2 | `a441414` | B.2: add unit tests for `to_pretenant_audit_event` resource_type/resource_id kwargs (+7) |
| 3 | `3d8ee9f` | B.3: fix `query_all_events` pretenant resource_type/resource_id preservation (F-26) + regression tests (+3; L1 exception active) |
| 4 | `ed9d137` | C.1: migrate M6 emit site to kwargs — **closes the F-20 fingerprint** (2/1/0 → 3/0/0) |
| 5 | `1280f2a` | C.5: migrate M7 emit site (`tenant_bootstrap_failed`) |
| 6 | `ba096a4` | C.2: migrate M1 emit site (`missing_bearer_token`) |
| 7 | `f0f41a8` | C.3: migrate M2 emit site (`provider.verify` failed) |
| 8 | `d94057a` | C.4: migrate M5 emit site (`workspace_not_found`) — **sweep complete** |

Note: C.5 executed out of planned order per developer request; independent
floor-preservation refactors, harmless — squash-merge collapses commit order.

---

## Gate baseline (slice tip `d94057a`, Phase D live re-run)

| Gate | Value | Floor |
|---|---|---|
| `test_destructive_failure_paths.py` | 3/0/0 | 3/0/0 (F-20 fingerprint passing) |
| `tests/auth/` | 88/0/0 | 88 |
| `tests/audit/` | 68/0/0 | 68 |
| `make test-db` | 168/0/0 (+2 skipped) | 168/2sk |
| `pytest -k app_factory` | 45/0/0 | 45 |
| Strict helper regression-pin | PASS | L2 |
| `git diff 94a027f -- src/audit/logger.py \| wc -l` | 30 | L1 exception (30/12) |
| `git diff 94a027f -- src/audit/context.py \| wc -l` | 0 | L4 byte-identical |
| Slice 7.7 A.6 combined | 39/0/0 | 39 (7+11+10+5+6) |

---

## Sacred surfaces

- `src/audit/logger.py` — **narrow L1 exception ACTIVE** (F-26 fix only):
  - Changed: `query_all_events` pretenant for-loop body — `resource_type=""` →
    `pretenant_event.resource_type`; same for `resource_id`.
  - Removed: two inline comments ("not a PretenantAuditEvent field").
  - Corrected: stale docstring paragraph at L294-298.
  - Forbidden surfaces byte-identical vs `94a027f`: `AuditEvent` dataclass,
    `write()`, `query()`, `clear()`, `clear_all()`, all `aemit_*` methods,
    tenant transformation block, post-loop filters, module-level singleton.
  - Diff gate: raw `git diff 94a027f -- src/audit/logger.py | wc -l` = 30.
- `src/audit/context.py` — byte-identical vs `94a027f` (L4; no exception).
- `src/audit/_compat.py` — extended at B.1 (resource_type/resource_id kwargs on
  `to_pretenant_audit_event`); `to_tenant_audit_event` strict body unchanged
  (regression-pin holds).
- `src/auth/middleware.py` — all 5 pretenant emit sites (M1 L204, M2 L235,
  M5 L394, M6 L424, M7 L462) thread `resource_type`/`resource_id` as direct
  kwargs to `to_pretenant_audit_event(...)`; metadata no longer carries them.

---

## L14 — L1 Sacred-Surface Exception Record

This slice carries a narrowly-scoped exception to L1 (`src/audit/logger.py`
byte-identical vs Slice 7.5 anchor `94a027f`):

- Approved at Plan v0.2.1 (Path A scope widening) to admit F-26 — a defect
  inside the locked surface itself (`query_all_events` was coercing
  PretenantAuditEvent's top-level resource_type/resource_id to empty strings,
  blocking F-20's end-to-end chain).
- Lines touched (B.3 commit `3d8ee9f`):
  * `query_all_events` pretenant for-loop body: `resource_type=""` ->
    `pretenant_event.resource_type`; `resource_id=""` ->
    `pretenant_event.resource_id`
  * Two inline comments ("not a PretenantAuditEvent field") removed
  * Stale docstring paragraph at L294-298 corrected
- Diff scope: 30 raw lines / 12 changed lines (raw `git diff 94a027f --
  src/audit/logger.py | wc -l`).
- Forbidden surfaces remain byte-identical: `AuditEvent` dataclass, `write()`,
  `query()`, `clear()`, `clear_all()`, all `aemit_*` methods, tenant
  transformation block, post-loop filters, module-level singleton.

This exception does not weaken the general sacred-surface rule; it only
records a line-scoped repair where the defect is inside the locked surface
itself. Future slices that touch `src/audit/logger.py` must either reset the
anchor to a post-7.8 commit (and lock the new baseline) or carry a similarly
scoped, plan-approved exception.

---

## F-20 chain analysis

F-20 closed end-to-end via three coordinated commits:

1. **B.1 (helper)** — `to_pretenant_audit_event` gained `resource_type`/
   `resource_id` kwargs surfacing them as top-level `AuditEvent` fields.
2. **B.3 (read-path)** — `query_all_events` stopped coercing those fields to
   `""` (this was F-26, the in-surface blocker).
3. **C.1 (M6 caller)** — M6 emit site threaded the new kwargs; the F-20
   fingerprint test `test_destructive_failure_paths.py` moved 2/1/0 → 3/0/0.

C.2/C.3/C.4/C.5 are floor-preservation **consistency sweep** commits bringing
the remaining four pretenant sites (M1/M2/M5/M7) into the top-level-fields
convention. No test asserts on their resource fields, so no behavioral change
is observable — alignment only.

---

## Backlog at slice close

**Closed this slice:** F-20, F-24, F-26.

**Carried forward:** F-15 (ValueError→500 broader coverage), F-17 (conftest
backups cleanup), F-19 (gate expansion), F-23, F-25 (.pyc noise),
FH-Runbook-5a, FH-Runbook-10, FH-Middleware-Order, FH-Env-Mutation, Future-1..5,
FH-S8.5. `fh-tier1-slice7-6` local branch left untouched.

---

## Handoff notes

- Slice tip is clean; next slice starts from a fresh `main` after squash.
- **L1 exception is closed at this slice's commit.** Any future change to
  `src/audit/logger.py` requires either an anchor reset to a post-7.8 commit
  (locking the new baseline) or a fresh plan-approved scoped exception.
- L4 (`src/audit/context.py`) has no exception — must remain byte-identical.
- Platform: Week 6 UI (Next.js dashboard) unblocked once FH-Tier-1 closes;
  UUID4 IDs + WriteContext actor in place. Then Week 7-8 Celery + Quality Loop,
  Turn 4.5 criteria #2-6.
