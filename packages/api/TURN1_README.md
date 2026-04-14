# AI SimTest — Week 6a Turn 1 Delivery

**Status:** ✅ Complete · 13/13 Turn 1 tests green · 51/51 cumulative tests green · Zero Week 5 regressions

This is the foundation slice of Week 6a, delivered per **Plan v1.2.2**. Turns 2 and 3 build on top of these primitives.

---

## What's in this delivery

### New source files

| File | Purpose |
|---|---|
| `packages/api/src/main.py` | FastAPI app factory with `CorrelationIdMiddleware` (v1.2.2 §11.1). Mounts the typed `APIError` exception handler. Routers for conversations/results/comparisons are stubbed in comments — they get added in Turns 2 and 3. |
| `packages/api/src/api/__init__.py` | Package marker |
| `packages/api/src/api/errors.py` | Typed error envelope, 11 stable error codes (including `forbidden_role` and `cross_tenant_forbidden` as **distinct** codes per the v1.2.2 final review note), `APIError` exception hierarchy, FastAPI exception handler that renders the canonical envelope shape |
| `packages/api/src/api/deps.py` | Shared dependencies: `get_tenant_context` (fail-closed, raises 401 if missing), `get_actor_role`, `require_role(minimum)` factory enforcing the v1.2.2 §11.4 role hierarchy `viewer < member < admin < service_account`, no-op `check_entitlement(action)` injection point for the future EntitlementEngine |
| `packages/api/src/runs/__init__.py` | Public exports — `_update_status` is intentionally NOT in `__all__` |
| `packages/api/src/runs/models.py` | `RunRecord`, `RunStatus` enum (with `is_terminal` property used by cache TTL selection in Turn 2), `RunSummary` |
| `packages/api/src/runs/repository.py` | `RunRepository` Protocol + `InMemoryRunRepository`. Internal-only `_update_status` method, documented per v1.2.2 §M1 / Correction 1: single-underscore convention (not Python name mangling), guarded by a static test |
| `packages/api/src/runs/service.py` | `RunService` (read-side) + `RunStateTransition` (write-side). Implements the **ordered best-effort sequence with fail-fast semantics** per v1.2.2 Correction 2 — explicitly NOT atomic. Documents the consistency-warning path on audit failure. Exposes `set_dashboard_invalidator()` so Turn 2's `results.cache` can wire its invalidator at app startup without creating a circular import. |

### New test files (13 tests, all passing)

| File | Tests | Coverage |
|---|---|---|
| `tests/test_main_app_wiring.py` | 4 | OpenAPI generates · correlation ID auto-generation as UUIDv4 · correlation ID propagation from header · canonical error envelope shape with correlation ID echoed |
| `tests/test_api_deps.py` | 4 | Tenant context extraction · missing context returns 401 with `tenant_context_missing` code · `require_role` denies lower privilege with `forbidden_role` and structured details · `require_role` allows equal-or-higher privilege |
| `tests/test_runs_repository.py` | 5 | CRUD round-trip · 404 on missing run · cross-tenant access raises `CrossTenantForbidden` (not 404 — info-leak guard) · **public-surface guard**: asserts `_update_status` is NOT in `RunRepository.__dict__` and NOT in `runs.__all__`, but IS on the in-memory implementation as an internal helper · full transition sequence (queued → running → completed) verifying audit emission with correct action codes and cache invalidator firing per transition |

---

## Test results

```
$ PYTHONPATH=. python -m pytest tests/ --ignore=tests/test_storage_r2.py -q
...................................................                      [100%]
51 passed in 1.96s
```

- **Week 5 baseline:** 38 tests (excluding `test_storage_r2.py` which requires live R2 credentials)
- **Turn 1 new tests:** 13
- **Total:** 51/51 ✅
- **Regression on Week 5:** 0

---

## Design decisions honored from v1.2.2

| Plan section | How it's realized in Turn 1 |
|---|---|
| §11.1 Correlation IDs | `CorrelationIdMiddleware` reads `X-Correlation-Id`, generates UUIDv4 if absent, attaches to `request.state.correlation_id`, echoes on response, propagates into `TenantContext.correlation_id` if context already present |
| §11.2 Typed error envelope | `ErrorEnvelope`/`ErrorBody` Pydantic models, 11 stable codes in `ErrorCodes`, `api_error_handler` installed on the app |
| §11.4 Authorization matrix | `require_role(minimum)` dependency factory enforcing the 4-tier role hierarchy. Distinguishes `cross_tenant_forbidden` (tenant boundary) from `forbidden_role` (within-tenant role denial) per the v1.2.2 final review note |
| §M1 Run state invalidation | `RunStateTransition` is the only sanctioned mutation path. Repository's `_update_status` is internal-only by convention with a static test guard |
| Correction 1 (`_update_status` wording) | Implemented as documented: single-underscore internal-by-convention, NOT name-mangled. Test asserts absence from public protocol |
| Correction 2 (consistency semantics) | Step 1 fail-fast aborts the transition · Step 2 audit failure logs `CONSISTENCY_WARNING`, still invalidates cache, then re-raises · Step 3 cache failure logs `CACHE_WARNING` and swallows. Documented in module docstring |
| §11.8 Future-proofing | `check_entitlement(action)` no-op dependency exists; gating routes is mechanical when EntitlementEngine lands |
| Audit code mapping | `_STATUS_TO_AUDIT_ACTION` maps each `RunStatus` to the existing `AuditActions.RUN_*` constant. The 5 codes already existed in the audit module — no new constants needed |

---

## Known follow-ups for Turn 2

1. **Wire `results.cache.invalidate` into `runs.set_dashboard_invalidator()`** at app startup in `main.py`. The hook is already in place; Turn 2 just calls `set_dashboard_invalidator(dashboard_cache.invalidate)` from the app factory.
2. **Mount conversations + results routers** in `create_app()` (commented placeholders are already there).
3. **Conversations service additive methods**: `list_with_cursor` (opaque base64 cursor reusing the asset pattern) and `get_for_tenant_or_404` — both additive, no existing method modified.
4. **Results package**: models, repository protocol + in-memory impl, cache module with active vs terminal TTL split per §M4, retrieval-only service, 5-endpoint router.

## Files

Layout matches the existing `packages/api/` tree:

```
packages/api/
├── src/
│   ├── main.py                       NEW
│   ├── api/
│   │   ├── __init__.py               NEW
│   │   ├── deps.py                   NEW
│   │   └── errors.py                 NEW
│   └── runs/
│       ├── __init__.py               UPDATED (was empty)
│       ├── models.py                 NEW
│       ├── repository.py             NEW
│       └── service.py                NEW
└── tests/
    ├── test_main_app_wiring.py       NEW
    ├── test_api_deps.py              NEW
    └── test_runs_repository.py       NEW
```

**Zero modifications** to any existing Week 5 file. Conforms to the v1.2.2 wording: "all changes to existing files are additive and backward-compatible." (Turn 1 didn't even need additive changes — only `runs/__init__.py` was overwritten, and it was an empty file.)

---

## Cumulative progress against v1.2.2 §12 test matrix

| Suite | v1.2.2 target | Turn 1 status |
|---|---|---|
| `test_main_app_wiring.py` | 4 | ✅ 4/4 |
| `test_api_deps.py` | 4 | ✅ 4/4 |
| `test_runs_repository.py` | 5 | ✅ 5/5 |
| `test_results_router.py` | 11 | Turn 2 |
| `test_conversations_router.py` | 10 | Turn 2 |
| `test_comparisons_router.py` | 11 | Turn 3 |
| `test_comparison_lifecycle_contract.py` | 7 | Turn 3 |
| `test_signed_url_and_transcript_auth.py` | 6 | Turn 3 |
| `test_pagination_contracts.py` | 4 | Turn 3 (some assertions in Turn 2) |
| **Total** | **62** | **13 done, 49 to go** |

(v1.2.2 §12 totals to 62 with the +1 idempotency test in §11.3; Turn 1 delivers exactly the 13 it scoped.)
