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
from contextlib import suppress
from typing import Any, NoReturn

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
        except OSError as exc:
            # SEC-04: never fail closed on an unreadable file silently — log
            # and fall through to the env var (or the config error below).
            logger.warning(
                "connectionStringFile unreadable (%s): falling back to connectionStringEnv",
                exc,
            )
    if storage.connectionStringEnv:
        return env.get(storage.connectionStringEnv, "")
    raise ConfigError("redis/postgres storage requires a connection string")


def _psycopg_db(storage: Any):
    import psycopg
    from psycopg.rows import dict_row

    dsn = _connection_string(storage, dict(os.environ))
    # R-10: pass a FACTORY (callable returning a connect coroutine), not a
    # one-shot coroutine object — the adapter must be able to reconnect
    # after close() or a dropped connection.
    return _PsycopgDb(lambda: psycopg.AsyncConnection.connect(dsn), dict_row)


class _PsycopgDb:
    """Async psycopg adapter implementing the DbClient protocol.

    R-10: the adapter holds a connection FACTORY (a callable returning a
    connect coroutine), so every acquisition — including after ``close()``
    or a dropped connection — creates a fresh connection.  A connection
    lost mid-operation is detected and re-established with one retry.
    One connection serves concurrent requests (serialized by the DB); a
    pool (psycopg_pool) is a documented STACK-01 follow-up.
    """

    def __init__(self, conn_factory, row_factory=None) -> None:
        self._factory = conn_factory
        self._row_factory = row_factory
        self._conn = None
        # R-31: nonzero while a _PsycopgTxn is open — the reconnect retry
        # is DISABLED inside a transaction (a dropped statement there must
        # fail the whole transaction, never re-run on a fresh autocommit
        # connection outside it).
        self._txn_depth = 0

    async def _connect(self):
        """Create a fresh connection from the factory (a callable, or a
        coroutine for callers not yet migrated to the factory form)."""
        coro: Any = self._factory() if callable(self._factory) else self._factory
        conn = await coro
        # Simple ops autocommit (matching the SqliteDb substitute); the
        # explicit transaction() wrapper toggles autocommit off around
        # BEGIN/COMMIT. Without this, implicit transactions dangle on
        # the connection and a single failed statement aborts them,
        # poisoning every later statement (InFailedSqlTransaction).
        await conn.set_autocommit(True)
        return conn

    async def _ensure(self):
        if self._conn is None or self._conn.closed:
            self._conn = await self._connect()
        return self._conn

    async def _run(self, op):
        """Run ``op(conn)`` with one reconnect retry when the connection
        was dropped mid-operation (R-10); driver outages still surface as
        BackendUnavailableError after the retry (R-26).

        R-31: inside a transaction the retry is DISABLED — re-running a
        dropped statement on a fresh autocommit connection would
        partial-commit outside the transaction and report false success.
        """
        import psycopg

        retry = self._txn_depth == 0
        try:
            return await op(await self._ensure())
        except Exception as exc:  # noqa: BLE001 — driver boundary
            if not isinstance(exc, psycopg.OperationalError) or not retry:
                _raise_driver_unavailable(exc)
            # R-10: dropped/closed connection — reset and retry once.
            if self._conn is not None:
                with suppress(Exception):
                    await self._conn.close()
                self._conn = None
            try:
                return await op(await self._ensure())
            except Exception as exc2:  # noqa: BLE001 — driver boundary
                _raise_driver_unavailable(exc2)

    async def close(self) -> None:
        """Release the connection (the real-backend matrix opens one per
        test; without this, postgres max_connections is exhausted)."""
        if self._conn is not None:
            await self._conn.close()
            self._conn = None

    async def execute(self, sql, params=()):
        async def _op(conn):
            await conn.execute(sql, list(params) if params else None)

        await self._run(_op)

    async def query(self, sql, params=()):
        async def _op(conn):
            async with conn.cursor(row_factory=self._row_factory) as cur:
                await cur.execute(sql, list(params) if params else None)
                return await cur.fetchall()

        return await self._run(_op)

    def transaction(self):
        return _PsycopgTxn(self)

    async def try_advisory_lock(self, key: int) -> bool:
        await self._ensure()
        rows = await self.query("SELECT pg_try_advisory_lock(%s) AS ok", (key,))
        return bool(rows and rows[0]["ok"])

    async def release_advisory_lock(self, key: int) -> None:
        await self.execute("SELECT pg_advisory_unlock(%s)", (key,))


def _raise_driver_unavailable(exc: Exception) -> NoReturn:
    """R-26: translate psycopg DRIVER outages to BackendUnavailableError at
    the boundary, so routes map them to 503 ``storage_unavailable`` instead
    of leaking a raw psycopg error as a 500.  Non-connection errors (code
    bugs) keep propagating as-is."""
    import psycopg

    if isinstance(exc, psycopg.OperationalError):
        raise BackendUnavailableError(f"postgres driver error: {exc}") from exc
    raise exc


class _PsycopgTxn:
    """BEGIN/COMMIT/ROLLBACK wrapper (R-31: holds the ORIGINAL connection;
    if it is gone at close time, the transaction outcome is unknown and an
    outage is reported instead of committing nothing on a fresh
    connection)."""

    def __init__(self, db) -> None:
        self._db = db
        self._conn: Any = None

    async def __aenter__(self):
        self._db._txn_depth += 1
        try:

            async def _begin(_unused: Any = None) -> Any:
                conn = await self._db._ensure()
                self._conn = conn
                await conn.set_autocommit(False)
                await conn.execute("BEGIN")
                return conn

            # _run wraps driver outages as BackendUnavailableError (R-26);
            # inside a transaction (depth > 0) it does not retry (R-31).
            await self._db._run(_begin)
        except BaseException:
            self._db._txn_depth -= 1
            raise
        return None

    async def __aexit__(self, exc_type, exc, tb):
        self._db._txn_depth -= 1
        conn: Any = self._conn
        try:
            if conn is None or conn.closed:
                # R-31: the transaction connection is gone — the outcome is
                # unknown.  NEVER commit on a fresh connection (a no-op that
                # would report false success and lose the partial writes).
                import psycopg

                raise psycopg.OperationalError("transaction connection lost")
            if exc_type is None:
                await conn.execute("COMMIT")
            else:
                await conn.execute("ROLLBACK")
        except Exception as exc2:  # noqa: BLE001 — driver boundary
            _raise_driver_unavailable(exc2)
        finally:
            self._conn = None
            if conn is not None:
                with suppress(Exception):
                    await conn.set_autocommit(True)


def build_components(
    config: Any, backend: Any, generation: int = 1, observability: Any = None
) -> dict[str, Any]:
    """ENG-01: one immutable agent component per generation + the runner.

    ``observability`` (created once per process) is reused across rebuilds so
    the Prometheus registry/OTel provider survive live reloads; the
    MetricBundle is (re)built per generation and injected into the runner.
    """
    metrics = None
    if observability is not None and getattr(observability, "prometheus_enabled", False):
        from .observability.metrics import MetricBundle

        metrics = MetricBundle(observability)
    applied = AppliedConfig.from_config(config, generation)
    component = build_agent_component(config, generation)
    service = AdkSessionService(backend)
    # NFR-00/NFR-02: the release performance gates run with the
    # deterministic in-process mock AgentRunner (REQUIREMENTS.md §6); the
    # hook is inert unless AGENT_MOCK_MODEL is set — normal deployments
    # always use the real ADK runner below.
    if os.environ.get("AGENT_MOCK_MODEL"):
        from .engine.mock_runner import MockAgentRunner

        runner: Any = MockAgentRunner(backend, app_name=config.name)
        mcp = ServerManager(applied, tool_targets=list(component.tool_targets))
        mcp.configure(config.tools.mcpServers)
        return {
            "applied": applied,
            "agent": component,
            "runner": runner,
            "mcp": mcp,
            "backend": backend,
            "session_service": service,
            "rag": None,
            "metrics": metrics,
        }
    from google.adk.runners import Runner as AdkRunner

    adk_runner = AdkRunner(agent=component.agent, app_name=config.name, session_service=service)
    # MA-03: agents receive only their toolServers' tools (root: all servers).
    mcp = ServerManager(applied, tool_targets=list(component.tool_targets))
    # RAG-02: the retriever exists only when rag is enabled; the memory
    # substitute is the ACC-01 deviation for acceptance proofs.
    rag = None
    if config.rag.enabled:
        from .engine.rag import RagRetriever, build_embedding, build_store

        rag = RagRetriever(
            config=config.rag,
            store=build_store(config.rag),
            embedding=build_embedding(config.rag),
        )
    runner = AgentRunner(
        applied,
        adk_runner,
        backend,
        app_name=config.name,
        mcp=mcp,  # HITL-02: the approval gate resolves raw tool names via the manager
        rag=rag,  # RAG-02: principal-scoped retrieval before the root call
        metrics=metrics,  # OBS-05: prometheus/OTel instruments
    )
    mcp.configure(config.tools.mcpServers)
    return {
        "applied": applied,
        "agent": component,
        "runner": runner,
        "mcp": mcp,
        "backend": backend,
        "session_service": service,
        "rag": rag,  # None unless rag.enabled (RAG-03 ingestion surface)
        "metrics": metrics,
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

    # LLM-04 (E1-5): opt-in credential-variable inference is fail-closed —
    # an inferred-but-absent variable is a boot error naming the variable.
    from .config.validate import auto_api_key_error

    api_key_error = auto_api_key_error(config, env)
    if api_key_error is not None:
        print(f"configuration error: {api_key_error}", file=sys.stderr)
        return EX_CONFIG

    # CFG-15: capability validation already done; MODE selection.
    try:
        selected_mode, mode_warnings = mode_mod.select_mode(config, env)
    except ConfigError as exc:
        print(f"configuration error: {exc}", file=sys.stderr)
        return EX_CONFIG
    for warning in mode_warnings:
        print(f"warning: {warning}", file=sys.stderr)

    # OBS-01: structured logging (json/text per config) before component logs.
    from .observability.logging import configure_logging

    configure_logging(config.observability)
    from .observability.otel import Observability
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

    # HITL-01: onTimeout "allow" is accepted only when explicitly configured
    # and emits a high-severity startup audit warning (default is deny).
    if config.approval.enabled and config.approval.onTimeout.value == "allow":
        audit(
            "approval_timeout_allow",
            severity="high",
            timeout_seconds=config.approval.timeoutSeconds,
            note="approval onTimeout=allow: timed-out requests are auto-approved",
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
        # OBS-05: one observability per process — the registry/OTel provider
        # are reused across component rebuilds (live reload keeps metrics).
        observability = Observability(config)
        components = build_components(config, backend, observability=observability)
        components["observability"] = observability
    except (BackendUnavailableError, ConfigError) as exc:
        print(f"configuration error: {exc}", file=sys.stderr)
        return EX_CONFIG

    # OBS-03: runtime_started event (masked secrets).
    from .observability.lifecycle import runtime_started

    runtime_started(config, components, selected_mode)

    from .protocol.app import create_app
    from .protocol.http_limits import BoundedH11Protocol

    # Watcher mode (MODE-01/MODE-02): start the tier-8 reconciler.
    if selected_mode == mode_mod.WATCHER:
        from .watcher.reload import ReloadManager
        from .watcher.watcher import ConfigMapWatcher, RealKubeClient

        # Rebuilds reuse the SAME backend (sessions must survive reloads) and
        # the reload call is (config, generation) — bind backend explicitly so
        # the signature collision (build_components(config, backend,
        # generation=1)) cannot silently bind the generation as the backend.
        reload_builder = lambda cfg, gen: build_components(  # noqa: E731
            cfg, backend, gen, observability=observability
        )
        reload_manager = ReloadManager(
            reload_builder, config, components, bundled_dir="/app/config"
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

    server_config = uvicorn.Config(
        app,
        host=config.server.host,
        port=config.server.port,
        workers=1,
        # API-20: bounded h11 parser with 431 mapping + header-count cap.
        http=BoundedH11Protocol,
        h11_max_incomplete_event_size=config.server.maxHeaderBytes,
    )
    # CNT-07: graceful shutdown — first signal drains (readyz/chat 503), the
    # grace timer then flushes storage and closes reconcilers/MCP/OTel before
    # the listener stops; a second signal hard-exits 1.
    from .lifecycle import ManagedServer, ShutdownManager

    shutdown_mgr = ShutdownManager(components, config.server.shutdownGraceSeconds)
    components["shutdown"] = shutdown_mgr
    server = ManagedServer(server_config, shutdown_mgr)
    shutdown_mgr.server = server._server
    server.run()
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
