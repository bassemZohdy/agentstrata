"""Storage backend fixtures for the shared contract suite (§18 ACC-01).

Per the approved ACC-01 deviation (2026-08-02), the shared suite runs against
the memory backend (real) and the file backend (real filesystem), with
redis/postgres exercised via in-memory substitutes; the real-instance and
fencing/multi-replica proofs are recorded as deferred.
"""

from __future__ import annotations

import pytest

from app.storage.contract import StorageSettings
from app.storage.file_backend import FileBackend
from app.storage.memory import MemoryBackend


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


@pytest.fixture(params=["memory", "file"])
def backend(request, memory_backend, file_backend):
    if request.param == "memory":
        return memory_backend
    return file_backend
