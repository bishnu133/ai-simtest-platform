# Slice 7.7 Close — F-18 Synth-UUID Lenient Helper Rollout

**Status:** CLOSED
**Branch:** `fh-tier1-slice7-7` (to be squash-merged to `main` and tagged `fh-tier1-slice7.7-close`)
**Branch tip:** `45fff34`
**Squash base:** `2c98a20` (Slice 7.6 close, tag `fh-tier1-slice7.6-close`)
**Sacred-surface anchor:** `94a027f` (Slice 7.5 close)
**Plan version:** v0.2.1 (signed off; executed without re-negotiation)
**Close date:** May 26, 2026
**Working dir:** `~/Documents/Initiative/ai-simtest-platform/packages/api`

---

## 1. Slice summary

FH-Tier-1 Slice 7.7 closes the F-18 UUID-parse regression introduced by Slice 8's async migration. The regression surfaced when `to_tenant_audit_event` strictly validated `ctx.tenant_id` and `ctx.workspace_id` as UUID instances, but test fixtures supplied string sentinels (`"t_a"`, `"w_main"`), producing `ValueError: badly formed hexadecimal UUID string` across 28 tests in 5 files.

**Resolution: Path A′** — an additive lenient sibling helper `to_tenant_audit_event_lenient` appended to `src/audit/_compat.py`. It deterministically synthesizes a UUID5 from non-UUID inputs (with WARNING log) and is called from the 15 Slice 8 service-layer sites. The strict `to_tenant_audit_event` body remains byte-identical at all auth/bootstrap/middleware sites, preserving sentinel-context audit semantics from Slice 7.5.

F-20 (M6 emit `resource_type` fix), originally piggy-backed as Phase D, was **deferred to `fh-tier1-slice7.8`** when D.0 probes revealed wider scope: a 2-file change with a sweep across 5 pretenant emit sites and an additive extension to `to_pretenant_audit_event`.

---

## 2. Plan locks (carried from charter, unchanged)

1. Path A′ final — additive lenient sibling helper. Path C (fixture rewrite) rejected.
2. Strict `to_tenant_audit_event` body byte-identical vs `94a027f`. Regression-pinned by `test_strict_to_tenant_audit_event_body_unchanged`.
3. Lenient helper handles BOTH `ctx.tenant_id` AND `ctx.workspace_id` via UUID5 synthesis.
4. Private `_TEST_SENTINEL_NAMESPACE = UUID("00000000-0000-0000-0000-000000000001")` (do not export).
5. WARNING log level on synth path.
6. Log MUST NOT contain audit metadata payload (only field_name, raw sentinel, synth UUID, action).
7. Auth/bootstrap/middleware paths stay on strict helper.
8. Only the 15 Slice 8 service-layer sites migrate to lenient.
9. F-20 piggy-back DEFERRED → `fh-tier1-slice7.8`.
10. No `src/audit/logger.py` change.
11. No `src/audit/context.py` change.
12. No migrations.

---

## 3. Commit ledger

| Commit | Phase | Scope | Tests impacted |
|---|---|---|---|
| `eae09ef` | Phase B | Add lenient sibling helper + 12 unit tests | — (helper + regression-pin) |
| `28fb7d7` | C.1 | Migrate `src/runs/service.py` (1 site) | 1 green |
| `2763c94` | C.2 | Migrate `src/comparisons/service.py` (2 sites; commit amended once to backfill P/F/E values) | 18 green |
| `0a9f90d` | C.3 | Migrate `src/conversations/router.py` (2 sites) + `service.py` (4 sites); atomic 2-file Python script | 10 + 5 transitive (signed_url fixture unblocked) |
| `b328e3e` | C.4 | Migrate `src/assets/service.py` (6 sites; single 12/16 indent group) | 13 green |
| `45fff34` | C.5 | Update `test_signed_url_audit_event_payload` assertion to expect synth UUID (F-22 closed) | 1 green |

Total: 6 commits ahead of `origin/main` at `2c98a20` (Slice 7.6 close).

---

## 4. Migration ledger — 15/15 Slice 8 service-layer sites COMPLETE

| File | Sites | Phase |
|---|---|---|
| `src/runs/service.py` | 1 | C.1 |
| `src/comparisons/service.py` | 2 | C.2 |
| `src/conversations/router.py` | 2 | C.3 |
| `src/conversations/service.py` | 4 | C.3 |
| `src/assets/service.py` | 6 | C.4 |
| **Total** | **15** | |

**Strict-helper sites preserved (NOT migrated — sacred per plan lock §7):**
- `src/auth/bootstrap.py` — lines 278, 438, 451 (3 `to_tenant_audit_event` calls)
- `src/auth/middleware.py` — line 515 (1 `to_tenant_audit_event` call)
- `src/auth/middleware.py` — lines 324, 360 (2 `to_tenant_audit_event_from_exc` calls — different helper, out of scope)

---

## 5. Frozen gate matrix (entry → close)

| Gate | Entry (Slice 7.6 close) | Close (this slice) | Delta |
|---|---|---|---|
| `pytest tests/audit/` | 46 | **58** | +12 (Phase B unit tests) |
| `pytest tests/auth/` | 88 | **88** | unchanged |
| `make test-db` | 168 passed / 2 skipped | **168 passed / 2 skipped** | unchanged |
| `pytest -k app_factory` | 45 | **45** | unchanged |
| `git diff 94a027f -- src/audit/logger.py` | 0 lines | **0 lines** | unchanged (sacred surface) |

---

## 6. A.6 file ledger (entry → close)

| File | Entry P/F/E | Close P/F/E | Status | Closed by |
|---|---|---|---|---|
| `test_comparison_lifecycle_contract.py` | 2/5/0 (floor ≤5) | 7/0/0 | TARGET | C.2 |
| `test_comparisons_router.py` | 5/6/0 (floor ≤6) | 11/0/0 | TARGET | C.2 |
| `test_conversations_router.py` | 0/0/10 (floor ≤10) | 10/0/0 | TARGET | C.3 |
| `test_runs_repository.py` | 4/1/0 (floor ≤1) | 5/0/0 | TARGET | C.1 |
| `test_signed_url_and_transcript_auth.py` | 0/0/6 (floor ≤6) | 6/0/0 | TARGET | C.3 (5 transitive) + C.5 (1 residual) |

All 5 A.6 files at TARGET at close.

---

## 7. Auxiliary test movement (asset suite)

| File | Entry P/F/E | Close P/F/E |
|---|---|---|
| `test_assets_router.py` | 1/9/0 | 10/0/0 |
| `test_assets_large_payload.py` | 0/4/0 | 4/0/0 |

---

## 8. Cumulative test movement

**~41 tests flipped red→green across the slice.**

| Phase | Tests flipped |
|---|---|
| C.1 (`runs/service.py`) | 1 |
| C.2 (`comparisons/service.py`) | 18 |
| C.3 (`conversations/router.py` + `service.py`) | 10 + 5 transitive (signed_url fixture unblocked) |
| C.4 (`assets/service.py`) | 13 |
| C.5 (`test_signed_url_audit_event_payload` residual) | 1 |
| **Total** | **~41** (plus 12 new unit tests in Phase B for helper coverage) |

---

## 9. Sacred surface verification at close

| Surface | Rule | Status at close |
|---|---|---|
| `src/audit/logger.py` | FROZEN byte-identical vs `94a027f` | `git diff 94a027f -- src/audit/logger.py` = 0 lines ✓ |
| `src/audit/_compat.py` strict `to_tenant_audit_event` body | byte-identical | Regression-pin test PASSED at branch tip ✓ |
| `src/audit/_compat.py` lenient helper | additive (Phase B) | Present + 12 unit tests cover it ✓ |
| `src/audit/context.py` | unchanged | `git diff <anchor> -- src/audit/context.py` = 0 ✓ |
| `src/auth/bootstrap.py` | unchanged (3 strict sites preserved) | grep `_lenient` = empty ✓ |
| `src/auth/middleware.py` | unchanged (F-20 deferred — file untouched in slice 7.7) | ✓ |
| 5 Slice 8 service files | mutable (pure helper-name swap) | per-site diff: import + helper name only ✓ |
| Sacred test function names in 5 A.6 files | preserved | no renames; only `test_signed_url_audit_event_payload` body updated per charter §13 ✓ |
| `migrations/` | fix-forward only | no migrations ✓ |

---

## 10. F-18 outcome — CLOSED ✓

UUID-parse regression eliminated. All 15 Slice 8 service-layer sites now use `to_tenant_audit_event_lenient`. All 5 A.6 floor files at TARGET. Asset suite fully green.

**Design rationale recap:** synth-UUID over raise-or-skip because (a) tests assert on event existence with specific resource_id; (b) skipping bad UUIDs would produce empty event lists and tests would fail; (c) synth + WARNING log keeps tests passing while making any production occurrence operationally visible.

**Why workspace_id is also coerced:** the strict helper parses both `ctx.tenant_id` and `ctx.workspace_id`. Test fixtures use `"t_a"` for tenant AND `"w_main"` for workspace. Either would crash strict; both are now leniently synthesized via the same UUID5 namespace.

---

## 11. F-22 outcome — CLOSED ✓

`test_signed_url_audit_event_payload` assertion updated at C.5:
```python
# Was:  assert events[0].tenant_id == "t_a"
# Now:  assert events[0].tenant_id == str(uuid5(_TEST_SENTINEL_NAMESPACE, "t_a"))
```
Added `from uuid import UUID, uuid5` (stdlib) and a locally re-declared `_TEST_SENTINEL_NAMESPACE = UUID("00000000-0000-0000-0000-000000000001")` constant to mirror the locked private constant in `src/audit/_compat.py` (charter §13). RHS uses `str(...)` to match `audit_logger.query_all_events()`'s flattened/stringly-typed result shape.

---

## 12. F-20 outcome — DEFERRED → `fh-tier1-slice7.8`

D.0 probes revealed the original §11 charter premise was inaccurate:
- M6 emit at `src/auth/middleware.py:424` uses `aemit_pretenant_event_safe` + `to_pretenant_audit_event` (a PRETENANT event, not Tenant).
- `resource_type` / `resource_id` are currently inside the metadata dict (lines 432–433), causing `event.resource_type` to default to `"auth"` and `event.resource_id` to `"session"` per `PretenantAuditEvent` field defaults.
- Fix requires additive extension of `to_pretenant_audit_event` in `src/audit/_compat.py` (sacred-ish, CARVED-OUT ADDITIVE-ONLY) plus a sweep of M1/M2/M5/M6/M7 emit sites — all share the same shape of bug.
- 6+ test files reference `AUTH_TENANT_STATE_INVALID` (`test_middleware_authz_hardening.py`, `test_destructive_failure_paths.py`, `test_logger_async.py`, `test_context.py`, `test_postgres_audit_event_repository.py`, `test_migration_0009_safety.py`); full impact unknown without per-test probing.
- Scope exceeded §11 defer trigger (>1 source file change + likely multi-test impact + sacred-file extension).

Re-scoped F-20 owns the full pretenant emit sweep, scheduled for `fh-tier1-slice7.8` ahead of Week 6 UI work.

---

## 13. F-23 outcome — LOGGED (no urgency)

Architectural observation surfaced during C.5 diagnosis: `audit_logger.query_all_events()` returns flattened/stringly-typed records (top-level `tenant_id` as `str`), distinct from `TenantAuditEvent`'s nested `context: AuditContext` with `tenant_id: UUID`. The query layer is implicitly normalizing/projecting events from the in-memory store, presumably for backward compatibility with the pre-Slice-8 sync `write()` API shape. Candidate for future normalization pass; not blocking any active work.

---

## 14. F-24 outcome — NEW, DEFERRED → `fh-tier1-slice7.8`

`to_pretenant_audit_event` docstring (`src/audit/_compat.py:77`) claims:
> "PretenantAuditEvent has no tenant_id/workspace_id/resource_type/resource_id fields. Callers that want to preserve resource_type and resource_id semantics should fold them into the metadata dict."

This is stale — `PretenantAuditEvent` (`src/audit/context.py:267`) does have `resource_type: str = PRETENANT_DEFAULT_RESOURCE_TYPE` and `resource_id: str = PRETENANT_DEFAULT_RESOURCE_ID` as top-level fields. F-24 fix: update the docstring as part of the F-20 sweep (both touch `to_pretenant_audit_event`).

---

## 15. Backlog state at close

### 15.1 Full backlog state

| ID | State | Target slice / notes |
|---|---|---|
| **F-18** | CLOSED | This slice (7.7) |
| **F-22** | CLOSED | This slice (7.7 C.5) |
| **F-20** (re-scoped) | OPEN | `fh-tier1-slice7.8` — full pretenant emit sweep (M1/M2/M5/M6/M7) |
| **F-23** | OPEN (no urgency) | Backlog — query-API vs domain-event surface drift |
| **F-24** (NEW) | OPEN | `fh-tier1-slice7.8` — stale `to_pretenant_audit_event` docstring (co-located with F-20) |
| F-17 | DEFERRED | Cleanup `conftest_Backup.py` / `conftest_newBackup.py` (no urgency; not on pytest collection path) |
| F-21 | Backlog reference only | If Path C is ever revisited, 12 inline `"t_a"/"t_b"` literals across 5 test files = migration scope |
| Future-1..5, FH-Middleware-Order, FH-Env-Mutation, FH-Runbook-5a/10, FH-S8.5 | Unchanged from Slice 7.6 close | — |

### 15.2 Deferred but not forgotten

The following items were surfaced or re-scoped during Slice 7.7 and have committed follow-up:

| ID | Item | Follow-up |
|---|---|---|
| **F-20** | Pretenant `resource_type` / `resource_id` promotion (sweep M1/M2/M5/M6/M7 emit sites + additive `to_pretenant_audit_event` extension) | `fh-tier1-slice7.8` |
| **F-24** | Stale `to_pretenant_audit_event` docstring | `fh-tier1-slice7.8` (co-located with F-20) |
| **F-23** | `query_all_events` record-shape normalization (audit query API vs domain-event drift) | Lower-priority backlog (no slice assigned yet) |

---

## 16. Working-discipline lessons (new this slice)

- **Charter premises about specific code locations can be stale by the time a slice executes.** F-20's "M6 emit OMITS `resource_type`" assumption was outdated — the actual code had the kwargs in metadata. The §11 defer trigger handled this correctly; the lesson is to always run D.0 probes before drafting a piggy-back patch, even when the item looks like a "1-line additive kwarg" on paper.
- **Assertion-error formatting (single-quoted both sides) is a strong empirical signal of stringly-typed fields**, but confirm directly via class field inspection before designing the assertion shape. The field-decl probe at C.5 turned a likely-correct guess into a known-correct fix.
- **Query-API vs domain-event drift is a real risk** in audit subsystems where the in-memory query layer and the domain dataclass diverge (F-23).
- **Heredoc commit messages with real numbers** (no `<FILL_AFTER_GATE>` placeholders) — carried forward from Slice 7.6 C.2 amend lesson, followed cleanly across all 6 commits in this slice.
- **Multi-file commits read all files first, write at the end** — applied in C.3 (atomic 2-file Python script) and reused as the proven pattern for single-file C.4 with similar paranoia (count-aware replaces + post-edit sanity assertions).

---

## 17. Next slice queue

1. **`fh-tier1-slice7.8`** — F-20 (re-scoped) + F-24 (stale docstring). Sweep M1/M2/M5/M6/M7 pretenant emit sites. Additive extension to `to_pretenant_audit_event`. **Before Week 6 UI.**
   - **Must begin with Phase A discovery and Plan v0.1 before any code,** because F-20 touches sacred-ish `src/audit/_compat.py` and multiple pretenant emit sites.
2. **Week 6 UI** — Next.js dashboard work resumes after Slice 7.8.
3. **Week 7–8** — Celery + Quality Loop.
4. Turn 4.5 criteria #2–6 (Clerk JWT e2e on Neon, real-row creation, audit on Neon, cross-tenant RLS rejection) — unchanged from Slice 7.6 carry-forward.

---

## 18. Sign-off

| Role | Sign-off | Date |
|---|---|---|
| Plan author | Bishnu | May 26, 2026 |
| Master Tech Architect | Claude | May 26, 2026 |
