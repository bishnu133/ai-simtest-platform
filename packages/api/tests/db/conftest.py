"""Postgres test fixtures for the Postgres + Auth Wiring Sprint.

Strategy: spawn a real `postgres:16` instance as a subprocess in a
temporary data directory, apply the Alembic migration once per test
session, and hand out fresh tenant-scoped session factories per test.

Why a real Postgres and not SQLite / in-memory stub:
  - RLS policies are Postgres-specific. SQLite does not implement them.
  - Partial unique indexes with WHERE clauses are Postgres-specific.
  - The v0.5.1 §8.1 sprint-gating tests require a real IntegrityError
    raised by the actual partial unique index, not a mocked one.
  - `SET LOCAL app.current_tenant_id` requires a real Postgres session.

Environment: these fixtures rely on Postgres 16 binaries being present on
the host. On most Debian/Ubuntu images that's `/usr/lib/postgresql/16/bin/`.
If the binaries are missing, the fixtures skip all DB-requiring tests
cleanly with a clear message — they never silently pass or fall back to a
weaker implementation.

Root handling: Postgres refuses to run as the Unix root user. If the
current process is root, the fixture drops privileges to a non-root user
(`postgres` if present, else we create a throwaway user). This is
sandbox/CI-friendly: it works whether the host runs tests as root (common
in containers) or as an unprivileged user (common on dev machines).
"""
from __future__ import annotations

import os
import pwd
import shutil
import signal
import socket
import subprocess
import time
from pathlib import Path
from typing import AsyncIterator, Iterator

import pytest
import pytest_asyncio
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession


# ---------------------------------------------------------------------------
# Locate the Postgres binaries
# ---------------------------------------------------------------------------

_PG_BIN_CANDIDATES = [
    "/usr/lib/postgresql/16/bin",
    "/usr/lib/postgresql/15/bin",
    "/usr/local/pgsql/bin",
    "/opt/homebrew/opt/postgresql@16/bin",
    "/opt/homebrew/opt/postgresql@15/bin",
]


def _find_pg_bin() -> str | None:
    """Return the first directory containing `postgres` + `initdb` + `pg_ctl`."""
    for candidate in _PG_BIN_CANDIDATES:
        if (
            os.path.isfile(os.path.join(candidate, "postgres"))
            and os.path.isfile(os.path.join(candidate, "initdb"))
            and os.path.isfile(os.path.join(candidate, "pg_ctl"))
        ):
            return candidate
    # Also try PATH
    if shutil.which("postgres") and shutil.which("initdb") and shutil.which("pg_ctl"):
        return os.path.dirname(shutil.which("postgres"))
    return None


PG_BIN = _find_pg_bin()
PG_AVAILABLE = PG_BIN is not None


def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


# ---------------------------------------------------------------------------
# Session-scoped Postgres subprocess fixture
# ---------------------------------------------------------------------------


class _PostgresInstance:
    """A live Postgres 16 subprocess rooted in a temp datadir.

    Created once per test session. `url` is an asyncpg-style URL the app
    can connect to.

    If the current process is root (common in CI containers), we drop
    privileges to a non-root user because Postgres refuses to run as root.
    The datadir is chowned to that user before initdb/postgres start.
    """

    def __init__(self, datadir: Path, port: int) -> None:
        self.datadir = datadir
        self.port = port
        self.proc: subprocess.Popen | None = None
        self.log_file: Path = datadir / "server.log"
        self._drop_uid, self._drop_gid = self._resolve_drop_ids()

    @staticmethod
    def _resolve_drop_ids() -> tuple[int | None, int | None]:
        """Return (uid, gid) we should drop to, or (None, None) to stay put.

        Only drops if the current process is root. Prefers the `postgres`
        system user; falls back to `nobody`.
        """
        if os.geteuid() != 0:
            return (None, None)
        for username in ("postgres", "pgtest", "nobody"):
            try:
                entry = pwd.getpwnam(username)
                return (entry.pw_uid, entry.pw_gid)
            except KeyError:
                continue
        return (None, None)

    def _preexec_drop_privs(self):
        """preexec_fn for subprocess — drop privs after fork, before exec."""
        if self._drop_gid is not None:
            os.setgid(self._drop_gid)
        if self._drop_uid is not None:
            os.setuid(self._drop_uid)
        # New process group so we can signal the whole tree on stop()
        os.setpgrp()

    def _run_as_pg(self, argv: list[str], **kwargs) -> subprocess.CompletedProcess:
        """Run a one-shot Postgres utility with dropped privileges if needed."""
        return subprocess.run(
            argv,
            preexec_fn=(
                self._preexec_drop_privs if self._drop_uid is not None else None
            ),
            **kwargs,
        )

    @property
    def url(self) -> str:
        """asyncpg driver URL used by SQLAlchemy."""
        return (
            f"postgresql+asyncpg://postgres@127.0.0.1:{self.port}/postgres"
        )

    @property
    def sync_url(self) -> str:
        """psycopg2 driver URL used by Alembic (which needs sync for some ops)."""
        return f"postgresql://postgres@127.0.0.1:{self.port}/postgres"

    def start(self) -> None:
        assert PG_BIN is not None, "Postgres binaries not found"

        # If we're dropping privileges, the datadir must be owned by the
        # target user BEFORE initdb runs.
        if self._drop_uid is not None:
            os.chown(self.datadir, self._drop_uid, self._drop_gid)

        # initdb
        initdb = [
            os.path.join(PG_BIN, "initdb"),
            "-D",
            str(self.datadir),
            "-U",
            "postgres",
            "--auth=trust",
            "--encoding=UTF8",
            "--no-locale",
        ]
        result = self._run_as_pg(
            initdb,
            capture_output=True,
        )
        if result.returncode != 0:
            raise RuntimeError(
                f"initdb failed (rc={result.returncode}):\n"
                f"stdout: {result.stdout.decode(errors='replace')}\n"
                f"stderr: {result.stderr.decode(errors='replace')}"
            )

        # Write a minimal postgresql.conf override so unix_socket_directories
        # is /tmp (avoid /var/run permission issues in sandboxed envs) and
        # fsync is off for speed. Must be written as root if we chowned the
        # datadir, because the directory is now owned by the postgres user —
        # but root can still write into it. Use sudo-style append.
        conf = self.datadir / "postgresql.conf"
        existing = conf.read_text()
        conf.write_text(
            existing
            + "\n"
            + "\n".join(
                [
                    "listen_addresses = '127.0.0.1'",
                    f"port = {self.port}",
                    "unix_socket_directories = '/tmp'",
                    "fsync = off",
                    "synchronous_commit = off",
                    "full_page_writes = off",
                    "max_connections = 50",
                    "shared_buffers = 32MB",
                ]
            )
            + "\n"
        )
        # Ensure the conf file is readable/writable by the postgres user.
        if self._drop_uid is not None:
            os.chown(conf, self._drop_uid, self._drop_gid)

        # Start postgres in the foreground so we can kill it cleanly.
        # Log file is created by root, so chown it before postgres opens it.
        log_fd = open(self.log_file, "wb")
        if self._drop_uid is not None:
            os.chown(self.log_file, self._drop_uid, self._drop_gid)
        self.proc = subprocess.Popen(
            [os.path.join(PG_BIN, "postgres"), "-D", str(self.datadir)],
            stdout=log_fd,
            stderr=log_fd,
            preexec_fn=(
                self._preexec_drop_privs if self._drop_uid is not None else os.setpgrp
            ),
        )

        # Wait for it to accept connections.
        deadline = time.time() + 15.0
        while time.time() < deadline:
            try:
                with socket.create_connection(("127.0.0.1", self.port), timeout=0.5):
                    # Also check it responds to SQL — not just TCP accept.
                    # psql is fine to run as root; only the server refuses.
                    r = subprocess.run(
                        [
                            os.path.join(PG_BIN, "psql"),
                            "-h",
                            "127.0.0.1",
                            "-p",
                            str(self.port),
                            "-U",
                            "postgres",
                            "-d",
                            "postgres",
                            "-c",
                            "SELECT 1",
                        ],
                        capture_output=True,
                        timeout=2,
                    )
                    if r.returncode == 0:
                        return
            except (OSError, subprocess.TimeoutExpired):
                pass
            time.sleep(0.2)
        # If we got here, Postgres never came up. Include the log.
        log_tail = ""
        try:
            log_tail = self.log_file.read_text()[-2000:]
        except Exception:
            pass
        raise RuntimeError(
            f"Postgres failed to start within 15s on port {self.port}. "
            f"Log tail:\n{log_tail}"
        )

    def stop(self) -> None:
        if self.proc is not None:
            try:
                os.killpg(os.getpgid(self.proc.pid), signal.SIGTERM)
                self.proc.wait(timeout=10)
            except (ProcessLookupError, subprocess.TimeoutExpired):
                try:
                    os.killpg(os.getpgid(self.proc.pid), signal.SIGKILL)
                except ProcessLookupError:
                    pass
            self.proc = None


@pytest.fixture(scope="session")
def pg_instance() -> Iterator[_PostgresInstance]:
    """Session-scoped real Postgres 16 subprocess."""
    if not PG_AVAILABLE:
        pytest.skip(
            "Postgres 16 binaries not found. Install postgresql-16 or set "
            "PG_BIN to a valid bin directory. DB-requiring tests are skipped."
        )

    # Place the datadir directly under /tmp (world-traversable) so the
    # dropped-privileges postgres user can reach it. tmp_path_factory's
    # /tmp/pytest-of-root/... parent is 700-perm and root-owned, which
    # blocks traversal even if the leaf dir is chowned.
    import tempfile

    datadir = Path(tempfile.mkdtemp(prefix="ai-simtest-pgdata-", dir="/tmp"))
    port = _find_free_port()
    instance = _PostgresInstance(datadir=datadir, port=port)
    try:
        instance.start()
        yield instance
    finally:
        instance.stop()
        shutil.rmtree(datadir, ignore_errors=True)


@pytest.fixture(scope="session")
def pg_url(pg_instance: _PostgresInstance) -> str:
    """asyncpg URL for the session-scoped Postgres."""
    return pg_instance.url


@pytest.fixture(scope="session", autouse=True)
def _configure_engine(pg_instance: _PostgresInstance) -> Iterator[None]:
    """Point src.db.session at the container Postgres for the whole session.

    Also enables NullPool mode so per-test event loops don't collide with
    pooled connections from prior tests. Runs before any DB-requiring test
    imports `tenant_scoped_session`.
    """
    from src.db.session import configure_engine_from_url

    os.environ["AI_SIMTEST_DB_NULLPOOL"] = "1"
    configure_engine_from_url(pg_instance.url)
    yield
    os.environ.pop("AI_SIMTEST_DB_NULLPOOL", None)


# ---------------------------------------------------------------------------
# Migration application — once per session
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture(scope="session")
async def migrated_db(pg_instance: _PostgresInstance) -> AsyncIterator[str]:
    """Apply the Alembic migration to the container Postgres, session-scoped.

    Yields the `pg_url` string. After all DB tests in the session run, the
    Postgres instance is torn down, so no downgrade is needed here.

    We run alembic as a subprocess rather than via the Python API because
    alembic/env.py uses `asyncio.run(...)` internally, which cannot be called
    from inside pytest-asyncio's already-running event loop.
    """
    os.environ["DATABASE_URL"] = pg_instance.url
    os.environ["AI_SIMTEST_DATABASE_URL"] = pg_instance.url

    project_root = Path(__file__).resolve().parent.parent.parent

    # Run alembic in a subprocess so it owns its own event loop.
    result = subprocess.run(
        ["python", "-m", "alembic", "upgrade", "head"],
        cwd=str(project_root),
        env={
            **os.environ,
            "DATABASE_URL": pg_instance.url,
            "AI_SIMTEST_DATABASE_URL": pg_instance.url,
            "PYTHONPATH": str(project_root),
        },
        capture_output=True,
        text=True,
        timeout=60,
    )
    if result.returncode != 0:
        raise RuntimeError(
            "alembic upgrade head failed:\n"
            f"stdout:\n{result.stdout}\n"
            f"stderr:\n{result.stderr}"
        )

    yield pg_instance.url


# ---------------------------------------------------------------------------
# Per-test clean DB — TRUNCATE everything between tests
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def clean_db(migrated_db: str) -> AsyncIterator[str]:
    """Yield the migrated DB URL; truncate all data tables before each test.

    This keeps tests isolated without paying the cost of re-running the
    migration (~1.5s) between every test.

    Critical: we reset the shared engine at the START of each test so the
    test's asyncpg connections attach to the CURRENT event loop. Without
    this, a test that inherits a session factory built under an earlier
    test's (now-closed) loop would raise "Event loop is closed".
    """
    from src.db.session import get_sessionmaker, reset_engine

    await reset_engine()

    # Pre-test cleanup: TRUNCATE all 12 tables CASCADE to clear any leftover
    # rows from prior tests. Runs as admin (no RLS SET LOCAL) so all rows
    # are visible regardless of tenant.
    sm = get_sessionmaker()
    async with sm() as session:
        await session.execute(
            sa.text(
                "TRUNCATE TABLE "
                "audit_events, idempotency_keys, comparisons, dashboard_artifacts, "
                "conversation_summaries, runs, assets, api_keys, service_accounts, "
                "memberships, workspaces, tenants "
                "RESTART IDENTITY CASCADE"
            )
        )
        await session.commit()

    yield migrated_db


# ---------------------------------------------------------------------------
# Raw admin session — no tenant scoping, used by tests that INSERT seed data
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def admin_session(clean_db: str) -> AsyncIterator[AsyncSession]:
    """Yield a raw admin AsyncSession (no SET LOCAL)."""
    from src.db.session import raw_admin_session

    async with raw_admin_session() as session:
        yield session
