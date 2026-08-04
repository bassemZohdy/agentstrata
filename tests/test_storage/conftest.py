"""Storage backend fixtures for the shared contract suite (§18 ACC-01).

Per the approved ACC-01 deviation (2026-08-02), the shared suite runs against
the memory backend (real) and the file backend (real filesystem), with
redis/postgres exercised via in-memory substitutes; the real-instance and
fencing/multi-replica proofs are recorded as deferred.
"""

from __future__ import annotations

import asyncio
import os
import sys

import pytest

# psycopg async requires a selector event loop; Windows defaults to Proactor
# (host-only concern — CI runs Linux where the selector loop is the default).
if sys.platform == "win32" and os.environ.get("AGENT_TEST_REAL_POSTGRES_DSN"):
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from app.storage.contract import StorageSettings
from app.storage.fakes import FakeRedis, SqliteDb
from app.storage.file_backend import FileBackend
from app.storage.memory import MemoryBackend
from app.storage.postgres_backend import PostgresBackend
from app.storage.redis_backend import RedisBackend


@pytest.fixture()
def settings() -> StorageSettings:
    # small bounds so capacity/TTL tests are cheap
    return StorageSettings(
        session_ttl_seconds=3600,
        run_ttl_seconds=600,
        idempotency_ttl_seconds=300,
        max_sessions=50,
        max_runs_per_session=5,
        max_idempotency_records_per_session=5,
    )


@pytest.fixture()
def memory_backend(settings):
    backend = MemoryBackend(settings)
    return backend


@pytest.fixture()
def file_backend(tmp_path, settings):
    backend = FileBackend(str(tmp_path), settings)
    return backend


def _real_redis_backend(settings):
    """Real Redis 7 client when AGENT_TEST_REAL_REDIS_URL is set (the CI
    real-backend matrix); otherwise the ACC-01 substitute."""
    import os
    from typing import cast

    import redis.asyncio as redis

    from app.storage.redis_backend import RedisClient

    url = os.environ.get("AGENT_TEST_REAL_REDIS_URL")
    if not url:
        return None
    client = redis.Redis.from_url(url)
    return RedisBackend(cast(RedisClient, client), settings)


def _real_postgres_backend(settings):
    """Real Postgres 16 when AGENT_TEST_REAL_POSTGRES_DSN is set (the CI
    real-backend matrix); otherwise the ACC-01 substitute."""
    dsn = os.environ.get("AGENT_TEST_REAL_POSTGRES_DSN")
    if not dsn:
        return None
    import psycopg
    from psycopg.rows import dict_row

    from app.main import _PsycopgDb

    return PostgresBackend(_PsycopgDb(psycopg.AsyncConnection.connect(dsn), dict_row), settings)


@pytest.fixture()
async def redis_backend(settings):
    real = _real_redis_backend(settings)
    backend = real if real is not None else RedisBackend(FakeRedis(), settings)
    yield backend
    if real is not None:
        import redis.asyncio as redis_mod

        client = real._client
        if isinstance(client, redis_mod.Redis):
            await client.aclose()


@pytest.fixture()
async def postgres_backend(settings):
    real = _real_postgres_backend(settings)
    backend = real if real is not None else PostgresBackend(SqliteDb(), settings)
    yield backend
    await backend.close()


@pytest.fixture(params=["memory", "file", "redis", "postgres"])
def backend(request, memory_backend, file_backend, redis_backend, postgres_backend):
    return {
        "memory": memory_backend,
        "file": file_backend,
        "redis": redis_backend,
        "postgres": postgres_backend,
    }[request.param]


@pytest.fixture(autouse=True)
async def _real_backend_isolation(redis_backend, postgres_backend):
    """The shared contract suite assumes fresh state per test (the
    substitutes are per-test instances). In real-backend mode the services
    persist, so flush both before each test."""
    if not os.environ.get("AGENT_TEST_REAL_REDIS_URL") and not os.environ.get(
        "AGENT_TEST_REAL_POSTGRES_DSN"
    ):
        yield
        return
    import redis.asyncio as redis_mod

    client = redis_backend._client
    if isinstance(client, redis_mod.Redis):
        await client.flushdb()
    db = getattr(postgres_backend, "_db", None)
    if db is not None and not isinstance(db, SqliteDb):
        tables = ("agent_sessions", "agent_runs", "agent_idempotency", "agent_approvals")
        from contextlib import suppress

        for table in tables:
            with suppress(Exception):  # noqa: BLE001 - table may not exist yet
                await db.execute(f"TRUNCATE TABLE {table} CASCADE")
    yield
