# AI SimTest API — Week 5 developer commands
# Usage: make <target>

# .PHONY: install test test-verbose test-coverage clean lint run
.PHONY: install test test-verbose test-coverage test-db clean lint run \
        db-migrate db-seed-dev

install:
	pip install -r requirements.txt

test:
	PYTHONPATH=. python -m pytest tests/ --ignore=tests/test_storage_r2.py -q

test-verbose:
	PYTHONPATH=. python -m pytest tests/ --ignore=tests/test_storage_r2.py -v

test-db:
	@echo "=== DB tests (requires Postgres 16 binaries) ==="
	PYTHONPATH=. python -m pytest tests/db/ -v

db-migrate:
	@echo "=== Running Alembic upgrade head ==="
	@echo "    Requires DATABASE_URL to be set (postgresql+asyncpg://...)"
	python -m alembic upgrade head

db-seed-dev:
	@echo "=== Seeding dev tenant + workspace + dual membership ==="
	@echo "    Requires DATABASE_URL to be set. Idempotent — safe to re-run."
	python scripts/seed_dev_tenant.py

test-suite:
	@echo "=== Storage adapter base ==="
	@PYTHONPATH=. python -m pytest tests/test_storage_adapter_base.py -v
	@echo "=== Local filesystem adapter ==="
	@PYTHONPATH=. python -m pytest tests/test_storage_local.py -v
	@echo "=== R2 adapter (moto) ==="
	@PYTHONPATH=. python -m pytest tests/test_storage_r2.py -v
	@echo "=== Payload router ==="
	@PYTHONPATH=. python -m pytest tests/test_payload_router.py -v
	@echo "=== Asset router HTTP API ==="
	@PYTHONPATH=. python -m pytest tests/test_assets_router.py -v
	@echo "=== Large payload routing ==="
	@PYTHONPATH=. python -m pytest tests/test_assets_large_payload.py -v
	@echo "=== Conversation service ==="
	@PYTHONPATH=. python -m pytest tests/test_conversations_service.py -v

test-coverage:
	PYTHONPATH=. python -m pytest tests/ --cov=src --cov-report=term-missing

clean:
	find . -type d -name __pycache__ -exec rm -rf {} +
	find . -type d -name .pytest_cache -exec rm -rf {} +
	find . -type f -name '*.pyc' -delete

run:
	@echo "FastAPI dev server will be added in Week 6 when the full app is wired."
	@echo "For now, the router is mounted via tests; production main.py is S2 Week 8 work."
	@echo "=== Dev API (local Postgres only) — http://127.0.0.1:8000 ==="
	PYTHONPATH=. uvicorn dev_asgi:app --reload --host 127.0.0.1 --port 8000
