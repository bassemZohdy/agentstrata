"""Runtime boot (REQUIREMENTS.md CFG-15 order) and entrypoint.

Boot order: parse bootstrap flags -> tiers 1-7 -> merge/reset -> schema and
cross-field validation -> capability validation -> establish fail-closed auth
state (SEC-03) -> construct components (engine agent per generation) -> bind
the server -> start dependency reconcilers. No listening socket opens before
configuration/capability/API-key validation completes.
"""

from __future__ import annotations

import logging
import os
import sys
from typing import Any

from .config import mode as mode_mod
from .config.cli import EX_CONFIG, EX_OK
from .config.resolver import ConfigError, resolve
from .config.validate import validate_resolution
from .engine.agent import AppliedConfig, build_agent_component
from .engine.mcp.manager import ServerManager
from .engine.runner import AgentRunner
from .storage.adk_adapter import AdkSessionService
from .storage.contract import BackendUnavailableError, StorageSettings
from .storage.file_backend import FileBackend
from .storage.memory import MemoryBackend
from .storage.postgres_backend import PostgresBackend
from .storage.redis_backend import RedisBackend

logger = logging.getLogger(__name__)


def build_storage(config: Any):
    """Construct the storage backend from the validated config (M2)."""
    storage = config.storage
    settings = StorageSettings.from_config(config)
    if storage.type.value == "memory":
        return MemoryBackend(settings)
    if storage.type.value == "file":
        return FileBackend(storage.path, settings)
    if storage.type.value == "redis":
        import redis.asyncio as redis

        client = redis.Redis.from_url(_connection_string(storage, dict(os.environ)))
        from typing import cast

        from .storage.redis_backend import RedisClient

        return RedisBackend(cast(RedisClient, client), settings)
    if storage.type.value == "postgres":
        return PostgresBackend(_psycopg_db(storage), settings)
    raise ConfigError(f"unsupported storage type {storage.type.value}")


def _connection_string(storage: Any, env: dict[str, str]) -> str:
    if storage.connectionStringFile:
        try:
            with open(storage.connectionStringFile, encoding="utf-8") as fh:
                value = fh.read().rstrip("\r\n")
            if value:
                return value
        except OSError:
            pass
    if storage.connectionStringEnv:
        return env.get(storage.connectionStringEnv, "")
    raise ConfigError("redis/postgres storage requires a connection string")


def _psycopg_db(storage: Any):
    import psycopg
    from psycopg.rows import dict_row

    dsn = _connection_string(storage, dict(os.environ))
    return _PsycopgDb(psycopg.AsyncConnection.connect(dsn), dict_row)


class _PsycopgDb:
    """Async psycopg adapter implementing the DbClient protocol."""

    def __init__(self, conn_factory, row_factory=None) -> None:
        self._factory = conn_factory
        self._row_factory = row_factory
        self._conn = None

    async def _ensure(self):
        if self._conn is None:
            self._conn = await self._factory
        return self._conn

    async def execute(self, sql, params=()):
        conn = await self._ensure()
        await conn.execute(sql, list(params) if params else None)

    async def query(self, sql, params=()):
        conn = await self._ensure()
        async with conn.cursor(row_factory=self._row_factory) as cur:
            await cur.execute(sql, list(params) if params else None)
            return await cur.fetchall()

    def transaction(self):
        return _PsycopgTxn(self)

    async def try_advisory_lock(self, key: int) -> bool:
        await self._ensure()
        rows = await self.query("SELECT pg_try_advisory_lock(%s) AS ok", (key,))
        return bool(rows and rows[0]["ok"])

    async def release_advisory_lock(self, key: int) -> None:
        await self.execute("SELECT pg_advisory_unlock(%s)", (key,))


class _PsycopgTxn:
    def __init__(self, db) -> None:
        self._db = db

    async def __aenter__(self):
        conn = await self._db._ensure()
        await conn.execute("BEGIN")
        return None

    async def __aexit__(self, exc_type, exc, tb):
        conn = await self._db._ensure()
        if exc_type is None:
            await conn.execute("COMMIT")
        else:
            await conn.execute("ROLLBACK")


def build_components(config: Any, backend: Any, generation: int = 1) -> dict[str, Any]:
    """ENG-01: one immutable agent component per generation + the runner."""
    applied = AppliedConfig.from_config(config, generation)
    component = build_agent_component(config, generation)
    service = AdkSessionService(backend)
    from google.adk.runners import Runner as AdkRunner

    adk_runner = AdkRunner(agent=component.agent, app_name=config.name, session_service=service)
    runner = AgentRunner(applied, adk_runner, backend, app_name=config.name)
    mcp = ServerManager(applied)
    mcp.configure(config.tools.mcpServers)
    return {
        "applied": applied,
        "agent": component,
        "runner": runner,
        "mcp": mcp,
        "backend": backend,
        "session_service": service,
    }


def run(argv: list[str] | None = None) -> int:
    """CLI/boot entrypoint used by ``python -m app.main``."""

    # Non-action boot: resolve + validate + select mode + bind server.
    argv = list(argv) if argv is not None else sys.argv[1:]
    bundled = os.environ.get("AGENT_BUNDLED_DIR") or "/app/config"

    try:
        resolution = resolve(argv=argv, bundled_dir=bundled)
        result = validate_resolution(resolution)
    except ConfigError as exc:
        print(f"configuration error: {exc}", file=sys.stderr)
        return EX_CONFIG
    if not result.ok:
        for issue in result.issues:
            print(f"{issue.path}: {issue.code}: {issue.message}", file=sys.stderr)
        return EX_CONFIG

    assert result.config is not None
    config = result.config
    env = dict(os.environ)

    # CFG-15: capability validation already done; MODE selection.
    try:
        selected_mode, mode_warnings = mode_mod.select_mode(config, env)
    except ConfigError as exc:
        print(f"configuration error: {exc}", file=sys.stderr)
        return EX_CONFIG
    for warning in mode_warnings:
        print(f"warning: {warning}", file=sys.stderr)

    from .security.audit import audit, validate_egress_targets

    # SEC-05: egress allowlist validation before bind.
    for problem in validate_egress_targets(config):
        print(f"configuration error: {problem}", file=sys.stderr)
        return EX_CONFIG

    # SEC-01: auth disabled + non-loopback bind -> high-severity audit warning.
    if config.server.auth.mode.value == "none":
        bind_host = config.server.host
        if bind_host not in ("127.0.0.1", "localhost", "::1"):
            audit(
                "auth_warn_none_bind",
                severity="high",
                host=bind_host,
                note="auth disabled on a non-loopback bind",
            )

    # SEC-03: fail-closed auth state before bind.
    if config.server.auth.mode.value == "apiKey":
        key = _resolve_api_key(config, env)
        if not key:
            print(
                "configuration error: apiKey auth requires a readable API key (SEC-03, exit 78)",
                file=sys.stderr,
            )
            return EX_CONFIG

    try:
        backend = build_storage(config)
        import asyncio

        asyncio.run(backend.initialize())
        components = build_components(config, backend)
    except (BackendUnavailableError, ConfigError) as exc:
        print(f"configuration error: {exc}", file=sys.stderr)
        return EX_CONFIG

    from .protocol.app import create_app
    from .protocol.http_limits import BoundedH11Protocol

    # Watcher mode (MODE-01/MODE-02): start the tier-8 reconciler.
    if selected_mode == mode_mod.WATCHER:
        from .watcher.reload import ReloadManager
        from .watcher.watcher import ConfigMapWatcher, RealKubeClient

        reload_manager = ReloadManager(
            build_components, config, components, bundled_dir="/app/config"
        )
        watcher = ConfigMapWatcher(
            RealKubeClient(),
            config.k8s.namespace,
            config.k8s.name,
            config.k8s.required,
            config.k8s.resyncSeconds,
            reload_manager.apply_tier8,
        )
        components["watcher"] = watcher
        components["reload_manager"] = reload_manager

    app = create_app(config, components, mode=selected_mode)
    import uvicorn

    uvicorn.run(
        app,
        host=config.server.host,
        port=config.server.port,
        workers=1,
        # API-20: bounded h11 parser with 431 mapping + header-count cap.
        http=BoundedH11Protocol,
        h11_max_incomplete_event_size=config.server.maxHeaderBytes,
    )
    return EX_OK


def _resolve_api_key(config: Any, env: dict[str, str]) -> str | None:
    if config.server.auth.apiKeyFile:
        try:
            with open(config.server.auth.apiKeyFile, encoding="utf-8") as fh:
                value = fh.read().rstrip("\r\n")
            if value:
                return value
        except OSError:
            pass
    if config.server.auth.apiKeyEnv:
        return env.get(config.server.auth.apiKeyEnv) or None
    return None


if __name__ == "__main__":
    sys.exit(run())
