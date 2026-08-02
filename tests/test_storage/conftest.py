"""Storage backend fixtures for the shared contract suite (§18 ACC-01).

Per the approved ACC-01 deviation (2026-08-02), the shared suite runs against
the memory backend (real) and the file backend (real filesystem), with
redis/postgres exercised via in-memory substitutes; the real-instance and
fencing/multi-replica proofs are recorded as deferred.
"""

from __future__ import annotations

import pytest

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


@pytest.fixture()
def redis_backend(settings):
    return RedisBackend(FakeRedis(), settings)


@pytest.fixture()
def postgres_backend(settings):
    return PostgresBackend(SqliteDb(), settings)


@pytest.fixture(params=["memory", "file", "redis", "postgres"])
def backend(request, memory_backend, file_backend, redis_backend, postgres_backend):
    return {
        "memory": memory_backend,
        "file": file_backend,
        "redis": redis_backend,
        "postgres": postgres_backend,
    }[request.param]
