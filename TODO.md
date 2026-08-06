# Backlog — remaining work

The backlog from the 2026-08-05 deep review is **closed**.  All phases
P1–P5 are implemented and passing the host-based test suite; the P5-4
per-request cost-accounting finish line (usage-shape consistency, tests,
docs, hygiene) landed on 2026-08-06 and is recorded in
[CHANGELOG.md](CHANGELOG.md).  Resolved engineering decisions live in
[docs/decisions.md](docs/decisions.md).  Requirement IDs trace back to
[REQUIREMENTS.md](REQUIREMENTS.md); build order and rationale are in
[PLAN.md](PLAN.md).

Two sections follow:

1. **2026-08-06 review backlog** (R-01 … R-23) — defects and gaps found
   in a full-project review. Baseline at review time: 527 host tests
   pass, `ruff check` clean, `mypy` clean on 75 source files, so every
   item is something the green suite does not currently catch.
2. **Planned scope** (E1, E2) — requested feature work: environment
   variables as a first-class minimal-configuration surface, and full
   LLM provider/model coverage.

## Intelligence classification

Tasks are grouped by how much design, concurrency, and cross-module
reasoning they require.  The current agent is working on the **high-
intelligence** items; medium/low items are left for follow-up agents.

### High intelligence — architectural / concurrency / contract design

- **R-02** Live-snapshot reloads never reach HTTP routes (config holder +
  closure redesign across all routes).
- **R-03** Per-run state on process-wide singletons (concurrency model for
  runner metrics + RAG degraded signal).
- **R-08** Idempotency concurrent duplicates and partial streams (contract
  design: wait vs 409, cleanup rules).
- **R-10** Postgres reconnect and pool (connection lifecycle architecture).
- **R-11** MCP reconciler dead-session detection + `maxTools` enforcement
  (transport-state machine design).
- **R-16** RAG search embedding-model filtering and store-result ordering
  (store abstraction alignment).
- **R-17** Prometheus cardinality guard per metric (instrument model).
- **R-20** Declared-but-unwired engine contracts (decision: implement vs
  delete + spec traceability).

### Medium intelligence — localized correctness / security fixes

- **R-01** Middleware order fix.
- **R-04** Storage sweep scheduling.
- **R-05** MCP manager start after component rebuild.
- **R-06** ACP approval-paused response.
- **R-07** JWKS refresh cadence.
- **R-09** Streaming request-body size cap.
- **R-12** Shutdown task leaks / early drain.
- **R-13** WebSocket rate limiting + byte cap.
- **R-14** Usage/cost shape normalizer across chat/ACP/WS.
- **R-15** Reload audit generation fix.
- **R-18** 204 responses with empty body.
- **R-19** Session route error mapping.

### Low intelligence — mechanical cleanup / docs / CI

- **R-21** Dead/unreachable/stale code removal.
- **R-22** Operator loop backoff + logger namespace.
- **R-23** Documentation and CI hygiene.
- **R-24** README/TODO backlog-status drift.
- **R-25** Operator backoff tests flake on a zero-margin lower bound.

## Review log

A monitoring agent re-reviews this repo every 15 minutes and appends
findings here. Each entry records the commit reviewed so the next pass
knows where it left off.

The loop **stops on its own** once a pass reviews genuinely new work and
finds nothing to add or correct — that is the signal the backlog has
caught up with the code. An interval where nothing changed is not a
clean pass and does not end the loop.

| Reviewed | HEAD | Suite | Findings |
| --- | --- | --- | --- |
| 2026-08-06 | `9408092` | 527 pass, ruff + mypy clean | Commit was docs-only (CHANGELOG/PLAN/README); no R-item landed. Opened **R-24**. |
| 2026-08-06 | `a705e22` | 532 pass, ruff + mypy clean; committed tree passes all CI steps incl. the two new ones | R-21 (8 of 11), R-22 (all), R-23 (all) landed and independently verified against the code; the 3 open R-21 items are correctly annotated as blocked on in-flight R-02/R-03. No regression. Opened **R-25** (new backoff tests have zero timing margin). |

## Completed work (pointer)

P1 core runtime, P2 multi-agent/ACP, P3 approvals, P4 RAG, and P5-1/P5-2/
P5-3/P5-4 (Prometheus metrics, WebSocket API, Kubernetes CRD/operator,
per-request cost accounting) are complete and recorded in
[CHANGELOG.md](CHANGELOG.md) — including the 2026-08-05 review items
(cost-accounting consistency/spec/test gaps, code-quality/hygiene,
spec-compliance gaps, documentation debt) under the "P5-4 finish line"
entry, and the TRC-01 ACP-annex traceability fix.

---

# 2026-08-06 review backlog

## P0 — correctness and contract violations

### R-01 Middleware order drops `X-Request-Id` and hardening headers on auth failures (API-00, SEC-10, SEC-11)

Starlette runs the **last-registered** middleware outermost. In
`app/protocol/app.py` the registration order is rate-limit (131),
hardening (166), request-id (175), auth (213) — so `auth_middleware`
runs *before* `request_id_middleware` and *outside* `hardening_middleware`.
An auth failure short-circuits both inner layers.

Verified: a 401 from `/v1/chat/completions` in `apiKey` mode returns no
`X-Request-Id` header, no `X-Content-Type-Options`, and `request_id: ""`
in the body; the same request when authorized returns both headers.

- [x] Re-register middleware so request-id and hardening are outermost
      (register them last) and auth/rate-limit inner.  Done: hardened +
      request-id middleware blocks moved after auth in `create_app`;
      registration order is now rate-limit → auth → hardening →
      request-id (outermost), so 401/403/429 responses carry
      `X-Request-Id` + every hardening header.
- [x] Confirm `request.state.client_ip` is still populated before the
      rate limiter keys on it (`FixedWindowLimiter.key_for_request`).
      Confirmed: request-id (which sets client_ip when
      `trustedProxyCidrs` is configured) now runs outermost; the limiter
      also degrades to `request.client.host`/`unknown` when absent.
- [x] Fix the SEC-10 `audit("auth_failure", …)` call so it records the
      real request id instead of `""`.  Fixed by the reorder itself:
      auth now runs inside request-id, so `request.state.request_id` is
      populated when the audit fires.
- [x] Tests: 401/403/429 responses carry `X-Request-Id` + every
      `HARDENING_HEADERS` entry and a non-empty body `request_id`.
      Done: `TestMiddlewareOrder` in test_api.py (401 auth failure +
      audit-record request_id match, 429 rate-limit denial).

### R-02 Live-snapshot reloads never reach the HTTP routes (REL-02)

`create_app` and every `register(app, config, components)` capture the
**boot** `AgentConfig` in a closure. The live-snapshot path in
`app/watcher/reload.py:196-212` re-applies only `run_slots` and
`rate_limiter`; the other ~25 leaves in `LIVE_SNAPSHOT_PATHS` are read
from the stale closure and have no effect until process restart.

Verified: after an `applied_live` reload setting
`server.exposeSystemInstruction: true`, `/health` reports
`configGeneration: 2` while `/config` still hides the system instruction.

Affected leaves include `engine.streaming`, `engine.overrides`,
`engine.maxOutputBytes`/`timeoutSeconds`/`maxIterations`,
`server.maxRequestBytes`, `server.maxMessageBytes`,
`server.streamQueueEvents`, `server.slowConsumerSeconds`,
`server.exposeSystemInstruction`, `server.shutdownGraceSeconds`,
`observability.includeToolArguments`.

- [ ] Introduce a mutable config holder in `components` (e.g.
      `components["config"]`) that the reload path swaps atomically.
- [ ] Convert route closures to read the current config per request
      (`chat.py` streaming mode / overrides gating / `maxRequestBytes` /
      `llm.model` / `approval.enabled`; `health.py` `/config` +
      `exposeSystemInstruction`; `sessions.register_models`;
      `websocket.py` `maxMessageBytes` / queue sizing).
- [ ] Make `_applied_dump` render the current generation's config (it
      currently ignores its `components` argument entirely).
- [ ] Also propagate on `component_rebuild` — the swap replaces
      `components` but not the captured `config`.
- [ ] Tests: one per live-snapshot leaf, asserting observable effect
      after `apply_tier8` without a restart.

### R-03 Per-run state stored on process-wide singletons (ENG-02, OBS-05, RAG-04)

`AgentRunner` and `RagRetriever` are one instance per generation, shared
by all concurrent requests, but both hold **per-run** mutable state:

- `AgentRunner._run_started` (`runner.py:105,137,873,909`) — concurrent
  runs overwrite each other's start time, so `agentbase_run_duration_seconds`
  is wrong under load and the `active_runs` gauge dec in `execute`'s
  `finally` is guarded by another run's value.
- `RagRetriever.degraded` (`rag.py:334`, set/reset at `runner.py:363` and
  `rag.py:349`) — request A's reset can mask request B's real degradation,
  and a `rag.required` run can fail or pass on the wrong signal (RAG-04).

- [x] Move `_run_started` into the per-run local scope (it is already
      threaded through `_execute_inner`; pass it to the commit helpers).
- [x] Make the active-runs gauge inc/dec strictly paired in one scope.
- [x] Replace `RagRetriever.degraded` with a per-call return value
      (`retrieve()` returns `(context, degraded)`).
- [x] Tests: concurrent runs assert independent durations and independent
      degraded signals.

**Done** — `AgentRunner` now passes `run_started` through a per-run holder
and manages `active_runs` inc/dec in `execute()`; `RagRetriever.retrieve`
returns `(context, degraded)` so the singleton has no per-call mutable
state.  Added `TestConcurrency` in `tests/test_engine/test_run.py` and
`TestRetrieverConcurrency` in `tests/test_engine/test_rag.py`; full suite
passes.

### R-04 Storage sweep is implemented but never scheduled (SES-05, ENG-05)

`StorageBackend.sweep()` and `expire_idempotency()` exist on the contract
and in all four backends, but nothing in `app/` ever calls them. TTL
expiry, `maxSessions`/`maxRunsPerSession` trimming, and nonterminal-run
reconciliation therefore never run in a live process — despite
`chat.py:431` documenting that "any lingering nonterminal run is
reconciled by the storage sweep".

- [x] Start a periodic sweep task in `_lifespan` (interval from config)
      alongside the approval reconciler.  Done: `storage.sweepIntervalSeconds`
      (default 60 s) added to the config model; `_lifespan` runs an initial
      sweep and then a loop, each failure logged non-fatally.
- [x] Cancel it in `ShutdownManager.close_components` before the storage
      flush.  Done (with R-12): `reconcile_task`/`sweep_task`/`watcher_task`
      are cancelled first in the close order.
- [x] Emit the sweep counters to the OBS-05 metric set.  Done:
      `agentbase_storage_sweeps_total{kind}` counter (sessions/runs/
      idempotency/interrupted).
- [x] Tests: a run left nonterminal is reconciled to `run_interrupted`;
      an expired session/idempotency record is removed.  Done:
      `test_sweep_reconciles_stale_nonterminal_run` (all four backends —
      stale nonterminal → failed/run_interrupted, fresh nonterminal left
      alone) + `test_startup_sweep_reconciles_stale_run_and_reports_metric`
      (lifespan startup sweep + counter).  Reconciliation is
      staleness-gated (nonterminal for ≥ runTtl) so an in-flight run is
      never raced; expired-session/idempotency removal was already covered
      by `test_sweep_*` in the contract suite.

### R-05 MCP manager is never started after a component rebuild (MCP-01, MCP-02, REL-03)

`ServerManager.start()` is called only from `_lifespan`
(`app/protocol/app.py:81-82`). `build_components` constructs a **new**
`ServerManager`, calls `configure()` on it, and the reload path swaps it
into `components` — with no reconciler tasks. After any
`component_rebuild` reload the MCP servers never connect, so required
servers keep `/readyz` at 503 permanently and tools disappear from the
agents.

- [x] Start the replacement manager inside the rebuild path (before the
      pointer swap, so a failed start rolls back to last-known-good).
      Done: `apply_tier8` starts the replacement `ServerManager` inside
      the rebuild try-block before `_health_check`.
- [x] Add the started-state check to `_health_check` in `reload.py`.
      Done: a replacement MCP manager with `_started == False` fails the
      rebuild health check.
- [x] Tests: a `tools.mcpServers` change reconnects and re-attaches tools
      without a restart.  Done: `test_rebuild_starts_replacement_mcp_manager`
      (a rebuild swaps in a fresh, started manager).

### R-06 Approval-paused runs return HTTP 500 on the ACP surface (API-16 annex A-5, HITL-01/03)

`acp.py:158` discards the `_paused` value from `_collect_non_streaming`.
When the approval gate pauses a run there is no `Done` event, so the next
line raises `internal_error` (500). The ACP route also omits the HITL-01
guard that `chat.py:111` applies (approval enabled ⇒ `session_id`
required), so the failure is reachable with the documented config.

- [ ] Add the HITL-01 stateful-request guard to `POST /acp/runs`.
- [ ] Return the annex-shaped pending-approval response (the HITL-03
      202 equivalent) instead of 500.
- [ ] Tests: approval-gated ACP run, streaming and non-streaming.

## P1 — robustness and security

### R-07 JWKS `refreshSeconds` is never honored (SEC-08)

`_JwtAuth` stores `refresh_seconds` (`auth.py:122`) and never reads it;
the JWKS is fetched once and re-fetched only after a verification
failure. A revoked or rotated-in-place key stays trusted indefinitely.
`self._jwks_failed` (`auth.py:126,168,172`) is written and never read.

- [ ] Refresh the JWKS on a `refreshSeconds` cadence with a stale-key
      cutoff (the module docstring already promises both).
- [ ] Remove `_jwks_failed` or wire it into the fail-closed decision.
- [ ] Consider pinning `PyJWK(..., algorithm=...)` to the JWK's own `alg`
      rather than the attacker-supplied header (`_verify_jwt:212`).
- [ ] Tests: key removed from JWKS stops verifying after the refresh
      interval without needing a failed verification first.

### R-08 Idempotency does not protect concurrent duplicates, and caches partial streams (API-06a)

- `chat.py:120-173` replays only when `status == "completed"`; a second
  request with the same key arriving while the first is in flight
  executes a second full run. `documents.py:91-100` documents a "racing
  duplicate waits on the same outcome" — no code implements the wait.
- `chat.py:685-702` writes the idempotency record after a mid-stream
  cancel with the partial `assistant_text`, `finish_reason: "stop"` and
  empty usage, marked `completed`. A retry with the same key then
  returns a truncated answer as a successful result.
- A `503 overloaded` rejection (`chat.py:178`) leaves the record
  `pending` until TTL because it is created before the slot acquire.

- [ ] Define and implement the in-flight-duplicate contract (409, or
      wait-and-replay) consistently across chat / ACP / documents.
- [ ] Do not finalize an idempotency record for a cancelled or partial
      stream; release it instead.
- [ ] Acquire the run slot before admitting the idempotency key.
- [ ] Tests: concurrent same-key requests, disconnect-then-retry.

### R-09 Request body is fully buffered before the size check (API-20, NFR-03)

`_read_body` (`chat.py:260-272`) calls `await request.body()` — reading
the entire payload into memory — and only then compares against
`server.maxRequestBytes`. `h11_max_incomplete_event_size` bounds headers,
not the body, so an oversized POST is absorbed in full before the 413.

- [ ] Enforce the cap while streaming the body (abort past the limit).
- [ ] Apply the same bound to `documents.py:76` (`await request.json()`,
      currently unbounded by anything but the parser).
- [ ] Tests: an oversized body is rejected without full buffering.

### R-10 Postgres backend cannot reconnect and has no pool (SES-01)

`_psycopg_db` (`main.py:70-75`) builds `_PsycopgDb` around a **coroutine
object**, awaited once in `_ensure`. After `close()` sets `self._conn =
None`, the next `_ensure` awaits the already-consumed coroutine and
raises `RuntimeError: cannot reuse already awaited coroutine`. There is
also no reconnect on a dropped connection and no pool — one connection
serves every concurrent request.

- [ ] Store a connection *factory* (callable), not a coroutine.
- [ ] Reconnect on a dropped/closed connection.
- [ ] Use a connection pool (`psycopg_pool`) sized from config, or
      document the single-connection serialization as a known limit.
- [ ] Tests: close-then-use, and a killed connection recovering.

### R-11 MCP reconciler never detects a dead-but-connected server; `maxTools` unenforced (MCP-01, MCP-03)

- `_reconcile_loop` (`manager.py:296-303`) only calls `_connect` when
  `not handle.connected`. Nothing ever moves a `CONNECTED` handle back to
  `DISCONNECTED` except an explicit close, so a session that dies
  in-flight is never re-established and `/readyz` keeps reporting ready.
- The loop also wakes every `backoff_seconds` (1 s when healthy) purely
  to re-check a flag.
- `ServerHandle.max_tools` (`manager.py:63`) is populated from
  `server.maxTools` (`manager.py:172`) and never enforced anywhere.

- [ ] Add a liveness probe (or catch transport errors at call time) that
      transitions a dead handle to `DISCONNECTED`.
- [ ] Sleep on an event rather than polling a flag when connected.
- [ ] Enforce `maxTools` at connect (truncate + warn, or fail the server).
- [ ] Tests: a dropped session reconnects; a server exceeding `maxTools`
      is capped.

### R-12 Shutdown leaks background tasks and always waits the full grace (CNT-07)

`_lifespan` creates `watcher_task` and `reconcile_task`
(`app.py:79,102`); `close_components` cancels neither. The reconcile loop
keeps calling `reconcile_pending()` against a backend that is being
closed. `_drain_after_grace` (`lifecycle.py:109`) also sleeps the whole
`shutdownGraceSeconds` even when every in-flight run finished in the
first second, needlessly extending pod termination.

- [x] Cancel `reconcile_task` and `watcher_task` first in the close order.
      Done: `close_components` cancels `reconcile_task`, `sweep_task`, and
      `watcher_task` (awaits each cancellation) before the watcher stop.
- [x] Wake the drain timer early once `run_registry` is empty.  Done:
      `_drain_after_grace` waits on the in-flight run tasks themselves
      (bounded by the grace timeout) instead of a blind sleep — an
      early-finishing fleet shortens pod termination.
- [x] Tests: no pending tasks after shutdown; early drain shortens it.
      Done: `test_background_tasks_cancelled_before_components_close`
      (all three tasks cancelled in order) +
      `test_early_drain_when_runs_finish_before_grace` (a 30 s grace
      completes in < 2 s when the run finishes in 50 ms).

### R-13 WebSocket surface bypasses rate limiting; message cap counts characters (WS-01, API-20)

`rate_limit_middleware` is an HTTP middleware, so `/v1/ws` is never rate
limited — `run.start` can be issued in a loop on one connection.
`_receive_loop` (`websocket.py:234`) compares `len(raw)` (code points)
against `server.maxMessageBytes`, so a multi-byte payload can be several
times the configured byte cap.

- [ ] Apply the limiter to WS connects and/or to `run.start` messages.
- [ ] Compare `len(raw.encode("utf-8"))` against `maxMessageBytes`.
- [ ] Decide whether `websocket.accept()` should precede the auth failure
      close (`websocket.py:85-89`) and document the choice.
- [ ] Tests: WS rate limiting; a multi-byte oversize message is rejected.

## P2 — consistency and observability

### R-14 Usage/cost shape differs across the three run surfaces (COST-01, API-07/08)

- `chat.py` `_normalize_usage` emits `prompt_tokens` /
  `completion_tokens` / `total_tokens` / `costUsd`.
- `acp.py:211-215` hand-rolls the same three token fields and **drops
  `costUsd`** entirely.
- `websocket.py:424` forwards the raw internal dict
  (`input_tokens`/`output_tokens`/`cost_usd`).

The P5-4 finish line normalized chat only.

- [ ] Route all three surfaces through one shared normalizer.
- [ ] Decide the ACP/WS cost field name and record it in
      `docs/decisions.md`.
- [ ] ACP streaming passes no `include_usage` (`acp.py:141-152`) so it
      can never emit a usage chunk — confirm against the annex.
- [ ] Tests: cost enabled/disabled × chat/ACP/WS.

### R-15 Reload audit reports the wrong generations (REL-06)

`_audit` (`reload.py:246-266`) is called *after* `self._generation` is
incremented, then logs `old_generation=self._generation` and
`new_generation=self._generation + 1`.

Verified: a 1 → 2 live reload logs
`old_generation=2 new_generation=3`.

- [x] Pass the pre-increment generation into `_audit` explicitly.  Done:
      `_audit(..., old_generation=...)` — the applied_live and
      applied_rebuild call sites pass `self._generation - 1` (they have
      already incremented); non-applied outcomes fall back to the current
      generation (unchanged).
- [x] Tests: assert the logged pair for live, rebuild, and rejected.
      Done: `test_audit_logs_true_generation_pair` — 1→2 logged as
      `old_generation=1 new_generation=2` for both applied paths and
      `1→1` for a rejected overlay.

### R-16 RAG search ignores the embedding model and filters inconsistently (RAG-02)

`chunk_key` and the pgvector primary key both include `embedding_model`,
so chunks from different models coexist — but **no** store filters
`search` by it (`rag.py:215-242`, `rag_connectors.py:299`, `:473`). After
an `rag.embedding.model` change, queries are scored against stale vectors
from the old model, and `_cosine` (`rag.py:150`) uses
`zip(..., strict=False)`, silently truncating on a dimension mismatch
instead of erroring.

Ordering also differs by store: `MemoryRagStore` applies `min_score`
*before* `[:top_k]`, while chroma and pgvector apply it *after* their
`n_results`/`LIMIT` — so the ACC-01 memory substitute does not model the
real backends' result counts.

- [ ] Filter `search` by `embedding_model` in all three stores.
- [ ] Make the dimension mismatch an explicit error.
- [ ] Align `min_score`/`top_k` ordering across stores and document it.
- [ ] `RagRetriever.ingest` embeds every chunk in one call — add a batch
      size bound for large documents.
- [ ] Tests: model change isolates old chunks; identical result counts
      across stores for the same fixture.

### R-17 Prometheus cardinality guard is global, not per metric (OBS-05)

`_CardinalityGuard` (`metrics.py:49-70`) keeps **one** `_seen` set shared
by every metric name, so the 128-label-set cap is consumed across all 13
instruments combined and one high-cardinality metric silently starves the
others — contradicting the module docstring ("each metric caps its
distinct label sets"). `render()` also emits `# TYPE` but never `# HELP`,
so the descriptions passed to every instrument are discarded.

- [ ] Key the guard by `(metric, labels)` or hold one guard per metric.
- [ ] Emit `# HELP` lines from the instrument descriptions.
- [ ] Tests: one metric exhausting its cap does not affect another;
      exposition includes HELP.

### R-18 `204 No Content` responses carry a JSON body

`sessions.py:87` returns `JSONResponse(status_code=204, content=None)`
and `documents.py:180` returns `content={}`.

Verified: `DELETE /v1/sessions/{id}` → 204 with
`content-type: application/json` and a 4-byte body `null`, which RFC 9110
forbids and strict proxies/HTTP-2 clients reject.

- [ ] Return `Response(status_code=204)` on both routes.
- [ ] Tests: 204 responses have an empty body and no content-type.

### R-19 Session route error mapping collapses distinct failures (API-15, ENG-10)

`sessions.py:38` maps every exception from `create_session` to
`storage_unavailable`, so a `CapacityError` (`storage.maxSessions`)
surfaces as a 5xx outage rather than a capacity signal;
`sessions.py:76` maps every `delete_session` exception to `session_busy`.
`GET /v1/sessions/{id}` also skips the `validate_session_id` check that
POST applies.

- [ ] Map `CapacityError`, `InvalidSessionId`, `SessionBusy`, and
      `BackendUnavailableError` to their distinct public codes.
- [ ] Validate the session id on GET/DELETE for consistency.
- [ ] Tests: one per mapped exception.

## P3 — dead code, hygiene, and documentation

### R-20 Declared-but-unwired engine contracts (ENG-08, ENG-09)

- `RunLimiter.cap_output_tokens` (`limits.py:102`) — never called. The
  ENG-08 pre-call budget cap is not enforced; the budget is only detected
  *after* an overshoot in `observe_usage`.
- `RunLimiter.begin_iteration` (`limits.py:70`) — never called.
- `ToolLedger`'s `persist` callback, `fail`, `record_for`,
  `executing_ids`, `reconcile_executing` — never used. The runner builds
  `ToolLedger()` with no persist (`runner.py:125`), so the ENG-09
  "persisted as `executing` before invocation" durable record does not
  exist in production, and `outcome_unknown` can never be reached across
  a restart.
- `RunResult` (`runner.py:72-83`) is never constructed; its
  `usage_estimated` field is the only carrier of the ENG-08
  "missing usage is estimated + labeled" contract, which therefore never
  reaches any API response.

- [ ] Decide per item: wire it up or delete it and amend the docstrings
      and REQUIREMENTS traceability accordingly.
- [ ] If ENG-08/ENG-09 stand as written, implement the pre-call token cap,
      the durable tool-call records, and surface `usage_estimated`.

### R-21 Dead, unreachable, and stale code

- [x] `manager.py:216` — unreachable `return self._handles.get(name)`
      after `return None`.  Removed.
- [x] `manager.py:356` — unreachable `return params` after
      `return wrap_stdio_params(params)`.  Removed.
- [x] `manager.py:28` and `:40` — duplicate `logger = getLogger(__name__)`.
      Second occurrence removed.
- [x] `main.py:257` and `:303` — duplicate
      `from .observability.otel import Observability` in one function.
      Second occurrence removed.
- [ ] `runner.py:804` — error message still reads "Approval is a Phase 3
      capability" although P3 approvals shipped; it is emitted for
      `requested_tool_confirmations`.  *Pending: `runner.py` is owned by
      the in-flight R-03 change; apply once R-03 lands.*
- [ ] `runner.py:807-821` — `_finalize` takes `state` and `request` and
      uses neither.  *Pending: same reason (runner.py owned by R-03).*
- [ ] `health.py:123` — `_applied_dump` ignores its `components` argument
      (see R-02).  *Pending: part of R-02's own checklist (high
      intelligence).*
- [x] `redact.py:25` — `__import__("re").compile(...)`; use a normal
      import.  Done: `import re` at module top.
- [x] `acp.py:134` — `__import__("asyncio").current_task()`; use a normal
      import.  Done: `import asyncio` at module top.
- [x] `main.py:56-67` — `_connection_string` silently swallows `OSError`
      on an unreadable connection-string file; log before falling back.
      Done: `logger.warning` with the exception before the env fallback.
- [x] Untracked leftover `operator/` directory (holds only
      `__pycache__`; the real package is `k8s_operator/`).  Deleted.

### R-22 Operator loop and logger namespace (K8S-01)

`run_operator` (`k8s_operator/loop.py:31-43`) has no backoff: if both the
list and the watch fail immediately, the outer `while True` spins against
the API server. The logger is `agentstrata.operator` (`loop.py:18`) while
the rest of the runtime uses `agentbase.*` / `app.*`, so operator logs
escape any `agentbase`-scoped log configuration.

- [x] Add exponential backoff with jitter to the re-list path.  Done:
      `_backoff()` (base 0.25 s, cap 30 s, ±0.25 s jitter); consecutive
      list/watch failures back off, any successful phase resets.
- [x] Align the logger name with the `agentbase.*` namespace.  Done:
      `logging.getLogger("agentbase.operator")`.
- [x] Tests: repeated watch failure does not hot-loop.  Done:
      `test_watch_failure_backs_off` + `test_list_failure_backs_off`
      (bounded re-list count in a fixed window).

### R-23 Documentation and CI hygiene

- [x] `docs/traceability.md:26` — API-08a still says "TODO M5
      backpressure"; the slow-consumer/queue backpressure shipped.
      Done: MAPPINGS row now names the bounded-queue/backpressure
      implementation + streaming tests; matrix regenerated.
- [x] `docs/traceability.md:60,65` — CNT-02 and CNT-07 still say
      "TODO M8"; reconcile against the P5 release evidence.  Done:
      CNT-02 → buildx manifest in docs/release.md; CNT-07 →
      `app/lifecycle.py` ShutdownManager/ManagedServer.
- [x] `.github/workflows/ci.yml` — the `test` job is titled
      "Tests (placeholder)" but runs the full 527-test suite.  Done:
      renamed to "Tests".
- [x] CI lints `app scripts` but not `tests` (mypy already checks tests);
      add `ruff check tests` or record the exclusion.  Done: added
      `Ruff lint (tests)` + `Ruff format check (tests)` steps to the
      `test` job.
- [x] Naming: the CRD API group is `agentstrata.io` (fixed by
      REQUIREMENTS.md K8S-11) while the product is "Agentbase". Decide
      pre-1.0 whether to rename the group and record it in
      `docs/decisions.md`.  Done: decided to keep `agentstrata.io`
      pre-1.0 (repo identity, invisible to runtime users, cheap to
      rename before 1.0); recorded in `docs/decisions.md` with a
      revisit-if-the-product-name-changes note.

### R-24 README and TODO describe a closed backlog that is no longer closed

*Intelligence: low — documentation.*

Commit `9408092` ("docs: strip process-housekeeping detail") rewrote the
documentation set on the premise that the backlog was closed. That was
true at the time; it no longer is, now that R-01…R-23 and the E1/E2
epics are open.

- [ ] `README.md:128` — the doc-set table calls TODO.md "Deferred scope
      and pointers to completed work (backlog is closed)".
- [ ] `README.md:110` — "Remaining work is tracked in TODO.md" now needs
      to account for the open review backlog, not just the release gates.
- [ ] Re-check `CHANGELOG.md:371` ("TODO.md tracks only the remaining
      work") for the same premise.
- [ ] Keep the TODO.md preamble's scope narrow — the *2026-08-05* backlog
      is closed; the 2026-08-06 one is not.

### R-25 The R-22 backoff tests sit exactly on their assertion's lower bound

*Intelligence: low — test reliability / CI.*

`test_watch_failure_backs_off` and `test_list_failure_backs_off`
(`tests/test_operator/test_operator.py`, added in `a705e22`) let the
operator run for 1.6 s of wall clock and assert
`3 <= kube.list_calls <= 8`.

Measured: `list_calls` is **exactly 3** on every run, for both tests
(4 trials each, both failure modes). The expected value *is* the lower
bound, so the tests have zero margin — any scheduling delay on a loaded
CI runner pushes the third re-list past the stop and yields 2, failing
the build.

The docstring's premise is a miscount: it says the window "admits ~4-5
re-lists", but the backoff sleeps are 0.25 / 0.5 / 1.0 s, so re-lists
land at t ≈ 0, 0.25, 0.75 and the fourth at ≈1.75 s — after the 1.6 s
stop. The author chose the bound believing there was 1-2 calls of
headroom that does not exist.

This does not affect production code — the R-22 backoff itself is
correct and the assertion's *upper* bound is what guards the hot-loop
regression.

- [ ] Drop the lower bound (or set it to 1): it guards nothing — the
      hot-loop regression is caught by the upper bound alone.
- [ ] Preferred: make the test deterministic by injecting the sleep/clock
      into `_backoff` instead of racing wall time, which also removes
      ~3.2 s from the suite.
- [ ] Fix the docstring's "~4-5 re-lists" arithmetic either way.

---

# Planned scope — minimal configuration + full model coverage

Requested 2026-08-06. Two epics. **Both are requirements-impacting**:
they change documented surfaces (CFG-07/CFG-08 env binding, LLM-01
provider set), so each needs a REQUIREMENTS.md amendment and a
`docs/decisions.md` entry *before* implementation, per the project's own
change policy. Neither is a pure dependency bump.

## Baseline — what already exists (do not rebuild)

Read this first; roughly half of "config via env" is already shipped.

- **Tier 5 env binding works today.** `app/config/resolver.py` derives
  `AGENT_*` variable names from the Pydantic schema
  (`_env_alias`/`_build_binding_index`, `models.py:iter_schema_fields`),
  binds them relaxed/schema-aware, reserves
  `AGENT_PROFILE`/`AGENT_CONFIG_DIR`/`AGENT_APPLICATION_JSON`/`AGENT_BUNDLED_DIR`,
  warns on unmatched variables with a nearest-path hint, and raises
  `AmbiguousEnvError` when one variable matches two schema paths.
- **Zero-file boot already works.** Tier 1 `config/agent.yaml` supplies
  `name`, `engine.systemInstruction`, and a gemini binding, so
  `docker run -e GEMINI_API_KEY=… agentbase` runs with no mounted file.
- **Multi-provider already exists via LiteLLM.** `Provider` =
  `gemini | openai | anthropic | ollama | litellm`;
  `connectors.py:_llm_model_string` prefixes openai/anthropic/ollama and
  passes `litellm` through verbatim, so arbitrary LiteLLM model strings
  are *already* reachable through the `litellm` escape hatch.
- **`llm.extra`** is a CFG-13 passthrough map into the LiteLLM kwargs.

The gaps below are what is genuinely missing.

## E1 — Minimal configuration: environment variables as a first-class surface

Goal: a working agent from environment variables alone, with discoverable
names and sane defaults — no YAML authoring required.

### E1-1 Publish the env-var catalog (currently undocumented)

The variable names are derivable only by reading the resolver. No doc,
README table, or CLI command lists them; `AGENT_LLM_MODEL` appears
nowhere in `docs/` or `README.md`.

- [ ] Add a `--print-env` (or `--dump-env`) CLI action to
      `app/config/cli.py` that emits every bindable path with its
      `AGENT_*` name, type, default, and whether it is secret.
- [ ] Generate `docs/env-reference.md` from the schema via a script in
      `scripts/` (mirroring `scripts/gen-schemas.py`).
- [ ] Add a CI zero-diff gate for the generated reference, exactly like
      the existing "Schema zero-diff (SCH-02, DEL-02)" step.
- [ ] Redact secret-bearing entries per SEC-02 (`is_sensitive_key`).

### E1-2 Guarantee and document the minimum viable env set

- [ ] Audit every schema leaf for a usable default; list the ones with
      no default (today `llm.model` and `name` are the load-bearing ones,
      supplied by tier 1 rather than by field defaults).
- [ ] Decide whether schema defaults should stand alone without tier 1,
      so `AGENT_BUNDLED_DIR` pointing nowhere still boots.
- [ ] Document the true minimum per provider (e.g. gemini: one API key;
      openai: key + model) in README Quick start.
- [ ] Test: boot with **only** env vars and no config file at all.

### E1-3 Collection config is not env-bindable

`iter_schema_fields` marks list-of-model and passthrough keys
non-bindable, so `tools.mcpServers`, `agents`, and `costs.models` cannot
be set by discrete env vars — only wholesale through
`AGENT_APPLICATION_JSON`.

- [ ] Choose one: (a) an indexed convention
      (`AGENT_TOOLS_MCPSERVERS_0_NAME=…`), (b) a compact per-entry DSL
      (`AGENT_MCP_SERVERS="fs=stdio:npx -y @mcp/fs"`), or (c) keep JSON
      as the only path.
- [ ] If (c), make the unmatched-variable warning name
      `AGENT_APPLICATION_JSON` explicitly so the dead end is signposted.
- [ ] Preserve CFG-07 ambiguity detection under whichever scheme wins.
- [ ] Tests: multi-server MCP + multi-agent config from env alone.

### E1-4 Short aliases for the high-traffic knobs

`AGENT_ENGINE_SYSTEM_INSTRUCTION` and `AGENT_LLM_MODEL` are long; a
minimal-config story wants `AGENT_MODEL`, `AGENT_INSTRUCTION`,
`AGENT_API_KEY`, `AGENT_PROVIDER`.

- [ ] Define a small, closed alias table (not a heuristic) mapping short
      names to schema paths.
- [ ] Aliases must lose to the fully-qualified name and must participate
      in `AmbiguousEnvError`.
- [ ] Record the alias set in `docs/decisions.md` — it is a frozen
      surface once published.
- [ ] Tests: alias binds, alias+canonical conflict, alias precedence.

### E1-5 Provider key auto-detection (opt-in)

Today `llm.apiKeyEnv` must name the variable. A minimal setup would infer
`OPENAI_API_KEY`/`ANTHROPIC_API_KEY`/`GEMINI_API_KEY` from the provider.

- [ ] Per-provider default `apiKeyEnv` when unset (deterministic table,
      no scanning of the environment).
- [ ] Keep SEC-03 fail-closed: an inferred-but-absent key still fails
      validation at boot with a clear message.
- [ ] Emit an OBS-03 startup line naming the *variable* used, never the
      value.
- [ ] Tests: inference per provider; explicit config always wins.

### E1-6 Binding diagnostics

- [ ] Extend `--dump-config` provenance (already tracks tier + source) to
      a human-readable "what bound from where" report for env tiers.
- [ ] Promote unmatched-`AGENT_*` warnings to a boot summary line rather
      than scattered warnings.
- [ ] Tests: provenance shows tier 5/6/7 attribution correctly.

### E1-7 Documentation

- [ ] README: a "zero-file agent" quick start next to the existing
      8-tier table.
- [ ] `docs/deployment.md`: env-only Kubernetes/Compose examples.
- [ ] Update `docs/traceability.md` for CFG-07/CFG-08 once the surface
      changes.

## E2 — Full LLM model and integration coverage

Goal: every mainstream provider is first-class and validated, not just
reachable through the `litellm` verbatim escape hatch.

**Sequencing note:** E2-1 through E2-4 are one unit — adding providers
without their credential contracts and cross-field validation produces
configs that pass validation and fail at first token.

### E2-1 Expand the first-class provider set (LLM-01)

- [ ] Extend `Provider` (`config/models.py:31`) with the providers to
      support first-class. Candidates: `azure`, `bedrock`, `vertex-ai`,
      `groq`, `mistral`, `cohere`, `deepseek`, `xai`, `together`,
      `fireworks`, `openrouter`, `huggingface`, `vllm`, `watsonx`.
- [ ] Extend `connectors.py:_llm_model_string` with each provider's
      LiteLLM prefix (the current map covers three).
- [ ] Keep `litellm` as the verbatim escape hatch — it is the
      compatibility guarantee for anything not enumerated.
- [ ] Decide the enum's stability policy: it is a published schema enum,
      so additions are backward compatible but removals are not.

### E2-2 Per-provider credential contracts (SEC-04, LLM-02)

`Llm` today offers exactly one credential pair (`apiKeyEnv`/`apiKeyFile`).
Azure needs endpoint + API version + key; Bedrock needs AWS region and
either static keys, a profile, or instance role.

- [ ] Model multi-field credentials without breaking the single-key
      shape (a per-provider sub-block, or reuse `llm.extra` with
      validation).
- [ ] Route every new secret through `SecretResolver` so SEC-04
      file-over-env and rotation semantics hold.
- [ ] Verify SEC-02 redaction covers the new key names
      (`is_sensitive_key` is suffix-based — check `awsSecretAccessKey`,
      `azureApiKey`).
- [ ] Note: Bedrock via LiteLLM pulls in `boto3` — a STACK-01 manifest +
      lock change, subject to the CNT-12 vulnerability gate.

### E2-3 A generic OpenAI-compatible provider

vLLM, LM Studio, OpenRouter, Together and most self-hosted servers are
OpenAI-compatible; one provider covers them all.

- [ ] Add `openai-compatible` requiring `baseUrl` (validated, CFG-14).
- [ ] Confirm the existing `baseUrl` → `base_url` kwarg path
      (`connectors.py:146`) is correct for LiteLLM in this mode.
- [ ] Tests: a local mock server (`scripts/mock_openai_server.py` already
      exists) exercises the path with no network.

### E2-4 Per-provider cross-field validation (CFG-14, CAP-01)

Only `ollama` currently has a documented required-`baseUrl` rule.

- [ ] Table-driven required/forbidden fields per provider.
- [ ] Fail at boot (exit 78) with the offending path, never at runtime.
- [ ] Gate genuinely unproven providers behind CAP-01 until a test
      exists, rather than shipping them silently.
- [ ] Tests: one invalid-config case per provider.

### E2-5 Model capability registry

`llm.contextWindowTokens` (used by ENG-04 history trimming,
`engine/context.py:52`) must be set by hand per model, and nothing
records whether a model supports tool calling, streaming, or vision.

- [ ] Ship a per-model capability table (context window, tools,
      streaming, vision, structured output) with config override.
- [ ] Default `contextWindowTokens` from it so ENG-04 trimming works
      without manual tuning.
- [ ] Reject configs requesting unsupported capabilities (e.g. MCP tools
      on a model with no tool calling) at boot.
- [ ] Decide the refresh policy — a stale table is worse than none.

### E2-6 Default pricing catalog (COST-01)

`costs.models` is an empty list by default, so every deployment must
hand-enter prices or accept the flat defaults.

- [ ] Ship a default price catalog keyed by provider/model.
- [ ] Add a refresh script under `scripts/` and document the provenance
      and update cadence.
- [ ] Keep explicit config overriding the catalog.
- [ ] Tests: catalog hit, catalog miss falling back to defaults, override.

### E2-7 Per-sub-agent cost pricing (closes deferred MA-02)

- [ ] Price each sub-agent against its own `llm.model` instead of the
      root model (`runner.py:_cost_usd` reads `self._applied.llm_model`).
- [ ] Land the P2 cost tests this was deferred behind.
- [ ] Remove the limitation note from `docs/deployment.md`.

### E2-8 Model fallback chains — decide in or out

- [ ] Decide whether an ordered fallback list belongs in scope. It
      interacts with LLM-03 (`RetryableLlm` never replays after a delta)
      and with COST-01 attribution, so it is a requirements change, not a
      config addition.
- [ ] If in scope: define fallback triggers, cost attribution across
      models, and the `x_agent_status` surfaced to the client.

### E2-9 Embedding provider parity (RAG-01)

`RagEmbeddingProvider` is `gemini | openai` only
(`config/models.py:343`), so RAG cannot follow the expanded LLM matrix.

- [ ] Extend the embedding provider set to match E2-1 where LiteLLM
      supports embeddings.
- [ ] Validate embedding dimension against the store's configured
      dimension at boot — this also interlocks with **R-16** (search does
      not filter by `embedding_model`); do R-16 first.

### E2-10 Verification and documentation

- [ ] A provider-matrix test that asserts the LiteLLM model string and
      kwargs per provider with a mocked bridge — no network, no keys.
- [ ] Extend `tests/test_config/test_validation.py` for the new
      cross-field rules.
- [ ] `docs/decisions.md`: the provider set, the enum stability policy,
      and the fallback decision.
- [ ] Update `REQUIREMENTS.md` LLM-01/LLM-02 and regenerate
      `docs/traceability.md`.
- [ ] Regenerate `requirements.lock` via `scripts/compile-lock.sh` if any
      provider needs a new dependency; re-run the CNT-12 scan gate.

## Deferred scope (carried forward)

- **Multi-agent per-sub-agent cost pricing** (P2, MA-02) — cost is priced
  against the root `llm.model`; sub-agent `llm.model` overrides are not
  priced per model until P2 cost tests land.  Documented in
  `docs/deployment.md` (Known limitations).
- **NFR-00 image-based release gates** — benchmark/chaos, zero-downtime
  reload proof, and multi-architecture acceptance run against the built
  image at release time (see the checklist in `docs/release.md`).
