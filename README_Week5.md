# AI SimTest API — S2 Week 5

**Asset Registry + Hybrid Storage** (PostgreSQL metadata + Cloudflare R2 payloads)

This package contains the Week 5 deliverable for the AI SimTest SaaS transformation. It builds on the S1 Foundation (auth, tenant isolation, audit, base asset registry) and adds the full HTTP API surface for asset management plus the hybrid storage layer.

---

## What's in this package

### Source code (14 files, ~2,712 lines)

```
packages/api/
├── pyproject.toml              # Python package metadata & dependencies
├── requirements.txt            # Pip-friendly dependency list
├── pytest.ini                  # Test runner config (asyncio mode)
├── Makefile                    # Common dev commands
├── .env.example                # All Week 5 environment variables
├── README.md                   # This file
│
├── src/
│   ├── common/
│   │   └── models.py           # Foundation types: ActorRef, AssetType, AssetStatus,
│   │                           #                    AssetRef, TenantContext, CursorPage
│   │
│   ├── audit/
│   │   └── logger.py           # Append-only audit logger + canonical action constants
│   │
│   ├── storage/                # NEW — hybrid storage layer
│   │   ├── models.py           # ObjectRef, StoredObject, SignedUrl, exceptions
│   │   ├── base.py             # StorageAdapter ABC + tenant-scoped key validator
│   │   ├── local.py            # LocalFilesystemAdapter (dev/test)
│   │   ├── r2.py               # CloudflareR2Adapter (S3-compatible via boto3)
│   │   ├── factory.py          # Env-driven adapter selection (singleton)
│   │   └── payload_router.py   # PG-vs-object routing policy engine
│   │
│   ├── assets/
│   │   ├── models.py           # Domain models (AssetRecord, request types)
│   │   ├── service.py          # Asset Registry service — full lifecycle
│   │   ├── schemas.py          # NEW — HTTP-layer Pydantic response schemas
│   │   └── router.py           # NEW — 11 FastAPI routes for asset management
│   │
│   └── conversations/          # NEW — hybrid conversation storage
│       ├── models.py           # Turn, ConversationTranscript, ConversationSummary
│       └── service.py          # Store/fetch with integrity verification + GDPR delete
│
└── tests/                      # 7 suites, 45 tests
    ├── conftest.py             # Shared fixtures: tenants, actors, local adapter
    ├── test_storage_adapter_base.py     #  7 tests
    ├── test_storage_local.py            #  8 tests
    ├── test_storage_r2.py               #  7 tests (uses moto mock)
    ├── test_payload_router.py           #  5 tests
    ├── test_assets_router.py            # 10 tests (uses FastAPI TestClient)
    ├── test_assets_large_payload.py     #  4 tests (object-tier routing)
    └── test_conversations_service.py    #  4 tests (PG summary + R2 transcript)
```

---

## Setup

### 1. Prerequisites

- **Python 3.11 or higher** (`python --version`)
- **pip 23+** (`pip --version`)
- Optional: **virtualenv** or **venv** (recommended for isolation)
- No database, no Redis, no Docker required for Week 5 — everything runs in-process or against the local filesystem.

### 2. Extract the package

```bash
unzip ai-simtest-week5.zip
cd ai-simtest-platform/packages/api
```

### 3. Create a virtual environment (recommended)

```bash
python -m venv .venv
source .venv/bin/activate          # Linux / macOS
# OR
.venv\Scripts\activate             # Windows PowerShell
```

### 4. Install dependencies

Either via `pip + requirements.txt` (simplest):

```bash
pip install -r requirements.txt
```

Or via the package metadata (cleaner):

```bash
pip install -e ".[dev]"
```

This installs:

| Package | Purpose |
|---|---|
| `fastapi` | HTTP framework for the asset registry API |
| `pydantic` | Domain models, request/response validation |
| `httpx` | Used by FastAPI's `TestClient` |
| `boto3` + `botocore` | S3-compatible client for Cloudflare R2 |
| `python-multipart` | FastAPI multipart form support (for future upload endpoints) |
| `pytest` + `pytest-asyncio` | Test runner (`asyncio_mode=auto`) |
| `moto[s3]` | In-process mock S3 backend — zero network calls during tests |

### 5. Configure environment (optional for tests)

```bash
cp .env.example .env
```

Tests don't need any environment variables — they use a per-test `tmp_path` Local adapter and an in-process moto-mocked S3. The `.env` file matters only when you wire up real R2 credentials for end-to-end work.

---

## Running the tests

### Quick run (everything, quiet output)

```bash
make test
```

or directly:

```bash
PYTHONPATH=. python -m pytest tests/ -q
```

**Expected output:**

```
.............................................                            [100%]
45 passed in ~8s
```

### Verbose run (see every test name)

```bash
make test-verbose
```

or:

```bash
PYTHONPATH=. python -m pytest tests/ -v
```

### Suite-by-suite (see each module's results separately)

```bash
make test-suite
```

This prints headers between each of the 7 suites and runs them in order, useful when reviewing the deliverable.

### Single suite

```bash
PYTHONPATH=. python -m pytest tests/test_storage_local.py -v
PYTHONPATH=. python -m pytest tests/test_assets_router.py -v
PYTHONPATH=. python -m pytest tests/test_storage_r2.py -v
```

### Single test

```bash
PYTHONPATH=. python -m pytest tests/test_assets_router.py::TestAssetRouter::test_cannot_update_approved_asset -v
```

### With coverage report

```bash
pip install pytest-cov
make test-coverage
```

---

## Test inventory (45 tests)

### Suite 1 — `test_storage_adapter_base.py` (7 tests)

Tests the abstract contract and the tenant-isolation guard that every adapter inherits.

- `test_compute_sha256_deterministic` — same input → same hash
- `test_compute_sha256_differs_for_different_input`
- `test_tenant_key_validator_rejects_empty_key`
- `test_tenant_key_validator_rejects_unprefixed_key` — prevents cross-tenant key leaks
- `test_tenant_key_validator_rejects_path_traversal` — blocks `../` segments
- `test_tenant_key_validator_accepts_valid_key`
- `test_exception_hierarchy` — all storage errors derive from `StorageError`

### Suite 2 — `test_storage_local.py` (8 tests)

Tests the filesystem-backed adapter used in dev and tests.

- `test_put_and_get_round_trip` — write then read returns identical bytes + matching hash
- `test_put_rejects_cross_tenant_key` — adapter blocks cross-tenant writes
- `test_get_missing_raises_object_not_found` — typed exception
- `test_delete_returns_true_when_exists`
- `test_delete_returns_false_when_missing`
- `test_list_keys_returns_tenant_scoped_only`
- `test_sign_url_generates_file_url_with_hmac` — local adapter generates `file://` URLs with HMAC tokens so test code exercises the same code path as R2
- `test_atomic_write_leaves_no_tmp_files_on_success` — verifies tmp+rename atomic write pattern

### Suite 3 — `test_storage_r2.py` (7 tests)

Tests the Cloudflare R2 adapter against `moto` (in-process mocked S3 backend, zero network).

- `test_put_and_get_round_trip`
- `test_get_missing_key_raises_object_not_found`
- `test_exists_returns_false_for_missing`
- `test_delete_returns_false_when_missing`
- `test_tenant_isolation_enforced_on_put`
- `test_list_keys_returns_prefix_matches`
- `test_sign_url_generates_https_url` — boto3 presigned URL generation

### Suite 4 — `test_payload_router.py` (5 tests)

Tests the policy engine that decides whether content goes inline (PG) or to object storage (R2).

- `test_small_payload_goes_inline` — under threshold → INLINE
- `test_large_payload_goes_to_object` — over threshold → OBJECT
- `test_dataset_always_goes_to_object_regardless_of_size` — datasets always offloaded
- `test_bot_profile_forced_inline_even_above_threshold` — small config types stay inline
- `test_force_tier_overrides_everything` — explicit override beats all rules

### Suite 5 — `test_assets_router.py` (10 tests)

Tests the FastAPI HTTP endpoints with the full request/response cycle via `TestClient`.

- `test_create_asset_returns_201` — POST /v1/assets
- `test_create_duplicate_slug_returns_409` — slug uniqueness enforced
- `test_get_asset_by_id` — GET /v1/assets/{id}
- `test_get_missing_asset_returns_404`
- `test_list_assets_with_type_filter` — GET /v1/assets?asset_type=…
- `test_update_draft_changes_content_and_hash` — PATCH /v1/assets/{id}
- `test_cannot_update_approved_asset` — approved assets are immutable (HTTP 409)
- `test_create_new_version_after_approval` — POST /v1/assets/{id}/versions
- `test_list_versions_returns_all` — GET /v1/assets/{id}/versions
- `test_clone_asset_creates_new_asset_with_lineage` — POST /v1/assets/{id}/clone

### Suite 6 — `test_assets_large_payload.py` (4 tests)

Tests the hybrid storage flow end-to-end through the asset service.

- `test_dataset_asset_routes_to_object_storage` — DATASET type → R2/Local adapter
- `test_large_judge_pack_routes_to_object_tier` — over threshold → object tier
- `test_load_content_verifies_hash` — fetched content's hash matches stored hash
- `test_cross_tenant_asset_fetch_is_blocked` — tenant A's asset invisible to tenant B

### Suite 7 — `test_conversations_service.py` (4 tests)

Tests the conversation storage pattern: summary in PG (in-memory for tests), transcript in R2.

- `test_store_and_fetch_summary` — round trip through hybrid storage
- `test_get_transcript_round_trip_with_integrity_check` — R2 fetch with hash verification
- `test_delete_run_conversations_removes_all` — GDPR right-to-erasure
- `test_cross_tenant_isolation_on_conversation_summary` — tenant boundaries enforced

---

## Architecture summary

### The hybrid storage decision

Every payload that lands in the system is routed by `PayloadRouter`:

| Asset type | Storage tier | Why |
|---|---|---|
| `judge_pack` | Inline (PG) when small, Object (R2) when large | Small judge configs read frequently |
| `policy_pack` | Inline / Object based on size | Same |
| `scenario_pack` | Inline / Object based on size | Same |
| `workflow_pack` | Inline / Object based on size | Same |
| `dataset` | **Always Object** | Datasets are always large, rarely fetched |
| `bot_profile` | **Always Inline** | Tiny configs, hot-path access |
| `scorecard` | **Always Inline** | Tiny configs, hot-path access |
| Conversation summary | Always Inline | List views need fast access |
| Conversation transcript | Always Object | Large, rarely accessed after run |

The threshold for size-based routing is `PAYLOAD_R2_THRESHOLD_BYTES` (default 64 KB).

### The tenant isolation triple guard

1. **Database layer** (S1) — PostgreSQL Row-Level Security policies on every table
2. **Service layer** (S1 + W5) — every method takes a `TenantContext` and only operates within that context
3. **Storage layer** (W5) — every storage key MUST start with the tenant_id, validated by `_validate_tenant_scoped_key()` in `StorageAdapter` base class

A bug in any one layer cannot leak data across tenants because the other two layers will block the operation. This is tested explicitly in `test_cross_tenant_asset_fetch_is_blocked` and `test_put_rejects_cross_tenant_key`.

### The integrity chain

1. On `put()`, the adapter computes SHA-256 of the bytes and stores it in the `ObjectRef`
2. The asset service stores that hash in the `AssetRecord.content_hash` field
3. On `get()`, the adapter recomputes SHA-256 of the fetched bytes
4. The service compares the recomputed hash to the stored hash; mismatches raise `AssetIntegrityError` (HTTP 500 with `error_code: asset_integrity_failure`)

This catches silent corruption, R2 bugs, or tampering. Tested in `test_load_content_verifies_hash` and `test_get_transcript_round_trip_with_integrity_check`.

---

## API endpoints (Week 5)

All routes are tenant-scoped via `TenantContext` dependency. In tests, the dependency is overridden directly; in production, S1 middleware resolves it from a JWT.

| Method | Path | Purpose |
|---|---|---|
| GET    | `/v1/assets` | List assets (filters: type, status, slug, tags; cursor pagination) |
| POST   | `/v1/assets` | Create draft asset (v1) |
| GET    | `/v1/assets/{id}` | Fetch asset (latest or `?version=N`) |
| PATCH  | `/v1/assets/{id}` | Update draft (409 if approved) |
| GET    | `/v1/assets/{id}/versions` | Version history |
| POST   | `/v1/assets/{id}/versions` | Create new draft version |
| POST   | `/v1/assets/{id}/approve` | Draft → Approved |
| POST   | `/v1/assets/{id}/deprecate` | Approved → Deprecated |
| POST   | `/v1/assets/{id}/clone` | Clone into new asset (lineage tracked) |
| GET    | `/v1/assets/{id}/download-url` | Generate signed URL for object-tier payloads |

### Error envelope

Every 4xx and 5xx response carries a typed envelope:

```json
{
  "detail": {
    "error_code": "asset_immutable",
    "message": "Cannot update asset abc-123 in status approved...",
    "correlation_id": "corr-a-1",
    "details": {}
  }
}
```

Stable `error_code` values: `asset_not_found`, `asset_slug_conflict`, `asset_immutable`, `asset_invalid_state_transition`, `asset_integrity_failure`, `tenant_isolation_violation`, `asset_bad_request`, `asset_inline_storage`.

---

## Configuration reference

| Variable | Default | Purpose |
|---|---|---|
| `STORAGE_BACKEND` | `local` | Adapter to use: `local` or `r2` |
| `LOCAL_STORAGE_ROOT` | `/tmp/simtest-storage` | Filesystem root for Local adapter |
| `LOCAL_STORAGE_BUCKET` | `simtest-local` | Bucket label for Local adapter |
| `R2_BUCKET` | (required for r2) | Cloudflare R2 bucket name |
| `R2_ACCOUNT_ID` | (required for r2 unless endpoint set) | Cloudflare account ID |
| `R2_ACCESS_KEY_ID` | (required for r2) | R2 API token access key |
| `R2_SECRET_ACCESS_KEY` | (required for r2) | R2 API token secret |
| `R2_ENDPOINT_URL` | derived from account ID | Override for the R2 endpoint URL |
| `R2_REGION` | `auto` | R2 region (always `auto` for Cloudflare) |
| `PAYLOAD_R2_THRESHOLD_BYTES` | `65536` | Size threshold for PG → R2 routing |
| `SIGNED_URL_TTL_SECONDS` | `900` | Default signed URL lifetime |

---

## Troubleshooting

### `ModuleNotFoundError: No module named 'src'`

You forgot `PYTHONPATH=.` Run from the `packages/api` directory:

```bash
cd packages/api
PYTHONPATH=. python -m pytest tests/
```

Or use the Makefile target which sets it for you: `make test`.

### `ImportError: cannot import name 'mock_aws' from 'moto'`

You have an old version of moto. Upgrade:

```bash
pip install --upgrade 'moto[s3]>=5.0'
```

(moto 4.x used `mock_s3`; moto 5.x uses `mock_aws`.)

### `botocore.exceptions.NoCredentialsError`

You're hitting real AWS instead of moto. The R2 tests use `mock_aws()` as a context manager — make sure you're running them via pytest (not as a standalone script).

### Tests pass locally but I want to verify against real R2

Set up a free Cloudflare R2 account (10 GB free tier), create a bucket and API token, then:

```bash
export STORAGE_BACKEND=r2
export R2_BUCKET=your-bucket
export R2_ACCOUNT_ID=your-account-id
export R2_ACCESS_KEY_ID=your-key
export R2_SECRET_ACCESS_KEY=your-secret
```

Then write a small smoke test that uses `get_storage_adapter()` from `src.storage.factory` instead of the `local_adapter` fixture.

### `pytest` warns about `pytest-asyncio` configuration

Make sure `pytest.ini` is in the `packages/api` directory. It sets `asyncio_mode=auto` so async tests don't need `@pytest.mark.asyncio`.

---

## Verification checklist

After installation, you should be able to:

- [ ] Run `make test` and see `45 passed`
- [ ] Run `make test-suite` and see all 7 suites pass individually
- [ ] Run `make test-verbose` and see every test name with a green dot
- [ ] Read any source file under `src/` without errors
- [ ] Import the asset router: `python -c "from src.assets.router import router; print(len(router.routes), 'routes')"` should print at least 10

---

## What's next — Week 6

S2 Week 6 builds the first real Next.js UI on top of the Week 5 API:

- Interactive results dashboard (React components, replacing static HTML reports)
- Conversation drill-down viewer (consumes the Week 5 transcript API)
- Failure clustering visualization
- Coverage metrics charts
- Run comparison (select 2 runs → diff view)
- **Target: 30+ tests** (cumulative S2: 75+, total platform: 1,928 engine + 163 S1 + 75+ S2 = 2,166+)

---

## Credits

- **Design philosophy:** "Wrap, Don't Rewrite" — preserve the Python evaluation engine, build the SaaS layer around it
- **Approved design:** AI SimTest SaaS Design Plan v2 (incorporates all must-fix and strongly-recommended items from Review Doc V1 and V2)
- **Built by:** Bishnu Prasad Panda (lead) + Claude (architecture & implementation)
