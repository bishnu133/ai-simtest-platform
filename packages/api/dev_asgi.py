"""Local development ASGI entry point (Week-6 UI dev-boot).

Operator scaffolding — NOT a src/ change, NOT imported by tests. Mirrors
t45_asgi.py but targets LOCAL Postgres so the web UI has a real API to
talk to. Refuses any non-local DATABASE_URL.
"""
from __future__ import annotations

import os
from urllib.parse import urlparse

from src.app_factory import create_app
from src.config import AppSettings

_db = os.environ.get("DATABASE_URL", "")
_host = urlparse(_db).hostname
if _host not in ("localhost", "127.0.0.1"):
    raise SystemExit(
        f"dev_asgi: refusing to boot against non-local DATABASE_URL (host={_host!r}). "
        'Export the local URL first: '
        'export DATABASE_URL="postgresql+asyncpg://localhost:5432/ai_simtest_dev"'
    )

app = create_app(
    settings=AppSettings(
        app_env="development",
        auth_enabled=True,
        auth_provider="dev",
        database_url=_db,
        use_postgres_runs=True,
        use_postgres_comparisons=True,
        use_postgres_idempotency=True,
        use_postgres_conversation_summaries=True,
    )
)
