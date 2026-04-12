# AI SimTest API — Week 5 developer commands
# Usage: make <target>

.PHONY: install test test-verbose test-coverage clean lint run

install:
	pip install -r requirements.txt

test:
	PYTHONPATH=. python -m pytest tests/ -q

test-verbose:
	PYTHONPATH=. python -m pytest tests/ -v

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
