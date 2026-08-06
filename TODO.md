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
- **R-31** ⚠ **Silent partial commit** — the R-10 reconnect retry fires
  inside transactions, so a mid-transaction connection drop autocommits
  the retried statement, loses the earlier ones, and reports SUCCESS to
  the caller (breaks the ENG-06 atomic commit).

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
- **R-26** Session routes now return 500 on a storage-driver outage
  (regression introduced by the R-19 fix).
- **R-27** JWKS stale-key cutoff only fires at refresh instants, so most
  requests past the cutoff still verify against stale keys (gap in the
  R-07 fix).
- **R-28** Redis driver-boundary wrap is over-broad: script/programming
  errors are reported as dependency outages (asymmetric with the psycopg
  wrap added in the same commit).
- **R-29** `observability.prometheus.*` classifies as live-snapshot but
  cannot take effect (route bound at boot); rate-limit exempt set still
  reads the stale boot config (residual gap in R-02).
- **R-30** ⚠ **Highest severity so far** — a storage blip during
  idempotency admission permanently leaks run slots on chat + ACP, so the
  replica 503s forever after `maxConcurrentRequests` failures (regression
  from the R-08 reordering).

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
| 2026-08-06 | *(idle)* | not run | HEAD unchanged at `a705e22`; only uncommitted mid-edit work across 24 files. Idle interval — no review, no edits. |
| 2026-08-06 | `dd278f4` | 548 pass, ruff + mypy clean (uncommitted work excluded) | 4 commits: R-01, R-04, R-05, R-12, R-15, R-18, R-19 all landed and independently verified (R-01/R-15/R-18 re-confirmed with the original repros). Every tick accurate. Opened **R-26** (R-19's narrowed `except` turns redis/postgres outages into 500s — verified) and one R-12 follow-up (drain snapshots `run_registry` once, so a late-registering run loses its grace). |
| 2026-08-06 | `1fb0772` | 548 pass; ruff check, **ruff format (app+tests)**, mypy all clean; tree clean | R-06 and R-03 landed; all ticks accurate. R-03 re-verified under real concurrency (6 concurrent runs → `active_runs` back to 0, 6 durations). The iteration-2 formatting gap was resolved before commit. No regression. One R-03 loose end opened (the new degraded flag is discarded; branch still uses the sentinel string). |
| 2026-08-06 | *(idle ×49)* | not run | HEAD held at `1fb0772` for ~12 h. Idle intervals — no review, no edits. Activity resumed at the end of the run. |
| 2026-08-06 | `9946770` | 565 pass, ruff + mypy clean | R-09 (streaming body cap) and R-13 (WS rate limit + UTF-8 byte cap) landed and verified. R-07 partially correct: the cadence refresh, `_jwks_failed` removal and the JWK-`alg` pinning are all right, but the stale-key cutoff leaks — opened **R-27** (measured: 77% of probes past the cutoff still verify against stale keys). Note R-26's ticks describe work that is still **uncommitted** at this HEAD, so it is not yet verified. |
| 2026-08-06 | `fed1185` | HEAD tree 565 pass, ruff + mypy clean (verified on an extracted copy — the main working tree has 4 failures from uncommitted R-02 work) | R-14 verified across all three surfaces (chat/ACP/WS now emit identical `prompt/completion/total_tokens` + `costUsd`). **R-26 confirmed fixed** with the original repro (both routes → 503 `storage_unavailable`); ticks accurate. Opened **R-28** — the redis boundary wrap catches bare `Exception`, so a Lua script bug reports as an outage, while the psycopg wrap in the same commit correctly re-raises code bugs. Note: `32ea6c1` is a broken commit — its own tests fail without the boundary files that landed in `fed1185` (self-disclosed). |
| 2026-08-06 | `90c7891` | 572 pass; ruff check, ruff format (117 files), mypy all clean; **working tree clean** | Two high-intelligence items landed. **R-02 confirmed fixed** with the original repro (`/config` now reflects an `applied_live` reload — was the founding finding of this backlog). R-11 verified: `maxTools` truncation propagates to the attached set, and the dead-session probe is covered by 4 genuinely-running stdio integration tests (not skipped). Opened **R-29** — a residual R-02 gap: `observability.prometheus.path` reports `applied_live` but the route stays bound at the boot path (verified 404/200), and `app.py:171` still reads the captured boot config. |
| 2026-08-06 | `938f689` | 578 pass; ruff check, ruff format, mypy all clean; tree clean | R-08 landed. All three contract sub-fixes verified: completed records still replay (checked the ordering), in-flight duplicates → 409 `idempotency_in_progress`, partial streams release the record. **But opened R-30 (highest severity of the run):** the same commit moved `create_idempotency` after `slots.try_acquire()` and outside the releasing `try/finally`, so a storage blip permanently leaks a run slot — measured `in_flight` stuck at 2 of 2 with the replica 503ing after the dependency recovered. Affects chat + ACP. |
| 2026-08-06 | `a70465d` | 586 pass; ruff + mypy clean (rag files uncommitted/excluded) | R-10 landed: connection **factory** replaces the one-shot coroutine, `_ensure` checks `closed`, `_run` retries once, pool deferred as a documented STACK-01 limit — all three original sub-findings addressed. **But opened R-31 (high):** the retry is not transaction-aware, so a drop inside one of the 7 ENG-06 `transaction()` blocks autocommits the retried statement, loses the earlier ones, and `__aexit__` COMMITs on a fresh connection — the caller is told SUCCESS. Simulated end-to-end. R-10's tests only cover standalone statements. |

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

- [x] Introduce a mutable config holder in `components` (e.g.
      `components["config"]`) that the reload path swaps atomically.
      Done: `create_app` seeds `components["config"]`; both reload
      categories (live + rebuild) swap it.
- [x] Convert route closures to read the current config per request
      (`chat.py` streaming mode / overrides gating / `maxRequestBytes` /
      `llm.model` / `approval.enabled`; `health.py` `/config` +
      `exposeSystemInstruction`; `sessions.register_models`;
      `websocket.py` `maxMessageBytes` / queue sizing).  Done: each
      handler re-binds `config = components["config"]` per request
      (chat, health readyz/health/config, sessions /v1/models, WS
      `_session` incl. the receive loop).
- [x] Make `_applied_dump` render the current generation's config (it
      currently ignores its `components` argument entirely).  Done: the
      live holder wins over the captured boot config.
- [x] Also propagate on `component_rebuild` — the swap replaces
      `components` but not the captured `config`.  Done:
      `replacements["config"] = new_config` in the rebuild swap.
- [x] Tests: one per live-snapshot leaf, asserting observable effect
      after `apply_tier8` without a restart.  Done: `TestLiveSnapshotLeaves`
      — exposeSystemInstruction (`/config`, the original R-02 repro),
      overrides gating (temperature override flips live), maxRequestBytes
      (413 threshold moves), llm.model rebuild (`/v1/models`), WS
      maxMessageBytes (direct holder swap — `server.protocols` is
      restart-pinned so apply_tier8 cannot flip it).

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

Independently re-verified (5th review): 6 concurrent runs on one shared
runner leave `agentbase_active_runs` at exactly 0 with 6 durations
recorded.

- [ ] *Loose end (5th review):* the new degraded flag is discarded by its
      only caller. `runner.py:167` binds `_rag_degraded` and never reads
      it; the branch still switches on the sentinel string
      (`rag_context == "degraded"`, set at `runner.py:408`). The flag is
      exactly what the refactor added, so switch the branch to it and drop
      the sentinel. Not a live bug — but the signature
      `tuple[str | None, bool]` permits `(context, True)` and
      `_rag_context` passes that through unchanged (`return context,
      degraded`), so a future "degraded store returned partial hits" path
      would silently emit no `RagDegraded` event and, under
      `rag.required`, would not fail the run (RAG-04).

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

- [x] Add the HITL-01 stateful-request guard to `POST /acp/runs`.  Done:
      approval enabled without `session_id` → 400
      `approval_session_required`, before any model work.
- [x] Return the annex-shaped pending-approval response (the HITL-03
      202 equivalent) instead of 500.  Done: the non-streaming path now
      uses the `paused` event from `_collect_non_streaming` and returns
      202 `run.pending_approval` with the durable approval record.
- [x] Tests: approval-gated ACP run, streaming and non-streaming.  Done:
      `test_acp_stateless_rejected_when_approval_enabled`,
      `test_acp_non_streaming_returns_202_with_approval` (durable record
      asserted), `test_acp_streaming_emits_approval_required_then_done`.

## P1 — robustness and security

### R-07 JWKS `refreshSeconds` is never honored (SEC-08)

`_JwtAuth` stores `refresh_seconds` (`auth.py:122`) and never reads it;
the JWKS is fetched once and re-fetched only after a verification
failure. A revoked or rotated-in-place key stays trusted indefinitely.
`self._jwks_failed` (`auth.py:126,168,172`) is written and never read.

- [x] Refresh the JWKS on a `refreshSeconds` cadence with a stale-key
      cutoff (the module docstring already promises both).  Done: keys are
      refreshed when `refresh_seconds` have passed since the last
      successful fetch (at most once per interval — a down IdP is not
      hammered per request); past 3× the interval without a successful
      refresh, auth fails closed 503 `auth_unavailable`.
- [x] Remove `_jwks_failed` or wire it into the fail-closed decision.
      Done: removed — the empty-dict check + fetch timestamps carry the
      fail-closed state.
- [x] Consider pinning `PyJWK(..., algorithm=...)` to the JWK's own `alg`
      rather than the attacker-supplied header (`_verify_jwt:212`).
      Done: `PyJWK(jwk_data, algorithm=jwk_data.get("alg"))` — key
      construction no longer follows the attacker-controlled header alg.
- [x] Tests: key removed from JWKS stops verifying after the refresh
      interval without needing a failed verification first.  Done:
      `TestJwtJwksRefresh` (cadence rotation, in-cutoff resilience,
      fail-closed cutoff, once-per-interval gate, initial unreachable).

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

- [x] Define and implement the in-flight-duplicate contract (409, or
      wait-and-replay) consistently across chat / ACP / documents.  Done:
      409 `idempotency_in_progress` (the REQUIREMENTS API-06a/A-5
      contract) on chat, ACP, and documents — a racing duplicate never
      runs a second time.  Codes added to the API-15 mapping.  (Note:
      the RFC 8785 hash-conflict distinction — same key, different body
      → `idempotency_conflict` — remains unimplemented; documented for a
      follow-up.)
- [x] Do not finalize an idempotency record for a cancelled or partial
      stream; release it instead.  Done: `_stream` finalizes only when
      the producer's end marker was reached; any other exit (disconnect
      poll, slow consumer, generator close) expires the record in the
      finally block — a retry never replays a truncated answer.
- [x] Acquire the run slot before admitting the idempotency key.  Done:
      chat + ACP admit the key after the slot acquire (a 503 overloaded
      rejection leaves no pending record); documents admits after
      validation and releases on ingest failure.
- [x] Tests: concurrent same-key requests, disconnect-then-retry.  Done:
      `test_in_flight_duplicate_409` (chat), ACP shares the chat check,
      documents `test_idempotency_in_flight_409` +
      `test_validation_failure_leaves_no_pending_record`,
      `test_partial_stream_never_finalized_as_completed` (route-level),
      `TestIdempotencyRelease` (unit: close releases, drain finalizes).

### R-09 Request body is fully buffered before the size check (API-20, NFR-03)

`_read_body` (`chat.py:260-272`) calls `await request.body()` — reading
the entire payload into memory — and only then compares against
`server.maxRequestBytes`. `h11_max_incomplete_event_size` bounds headers,
not the body, so an oversized POST is absorbed in full before the 413.

- [x] Enforce the cap while streaming the body (abort past the limit).
      Done: `_read_body` streams `request.stream()` and raises 413 as soon
      as the cap is crossed — the receive channel is not drained past the
      limit.
- [x] Apply the same bound to `documents.py` (`await request.json()`,
      currently unbounded by anything but the parser).  Done: the
      documents route streams with the same cap + 413.
- [x] Tests: an oversized body is rejected without full buffering.  Done:
      `test_oversized_body_413` (chat), `test_oversized_document_body_413`
      (documents), `test_read_body_aborts_at_cap_before_buffering_completes`
      (fake receive channel proves the mid-stream abort).

### R-10 Postgres backend cannot reconnect and has no pool (SES-01)

`_psycopg_db` (`main.py:70-75`) builds `_PsycopgDb` around a **coroutine
object**, awaited once in `_ensure`. After `close()` sets `self._conn =
None`, the next `_ensure` awaits the already-consumed coroutine and
raises `RuntimeError: cannot reuse already awaited coroutine`. There is
also no reconnect on a dropped connection and no pool — one connection
serves every concurrent request.

- [x] Store a connection *factory* (callable), not a coroutine.  Done:
      `_PsycopgDb` now holds a callable factory (main.py + conftest pass
      `lambda: psycopg.AsyncConnection.connect(dsn)`); `_connect` also
      tolerates a coroutine for un-migrated callers — close-then-use no
      longer awaits a consumed coroutine (was `RuntimeError`).
- [x] Reconnect on a dropped/closed connection.  Done: `_ensure` checks
      `conn.closed`; `_run` retries once on `psycopg.OperationalError`
      with a fresh connection (execute/query/txn entry all routed through
      it); an exhausted retry still surfaces as
      `BackendUnavailableError` (R-26).
- [x] Use a connection pool (`psycopg_pool`) sized from config, or
      document the single-connection serialization as a known limit.
      Done: single-connection serialization documented as a known limit
      in the adapter docstring (a pool needs `psycopg_pool` — a STACK-01
      dependency decision, deferred).
- [x] Tests: close-then-use, and a killed connection recovering.  Done:
      `test_psycopg_factory_reconnects_after_close`,
      `test_psycopg_dropped_connection_retries_once`,
      `test_psycopg_retry_exhausted_still_wraps_outage`.

### R-11 MCP reconciler never detects a dead-but-connected server; `maxTools` unenforced (MCP-01, MCP-03)

- `_reconcile_loop` (`manager.py:296-303`) only calls `_connect` when
  `not handle.connected`. Nothing ever moves a `CONNECTED` handle back to
  `DISCONNECTED` except an explicit close, so a session that dies
  in-flight is never re-established and `/readyz` keeps reporting ready.
- The loop also wakes every `backoff_seconds` (1 s when healthy) purely
  to re-check a flag.
- `ServerHandle.max_tools` (`manager.py:63`) is populated from
  `server.maxTools` (`manager.py:172`) and never enforced anywhere.

- [x] Add a liveness probe (or catch transport errors at call time) that
      transitions a dead handle to `DISCONNECTED`.  Done: `_probe` checks
      the session manager's live sessions and calls `list_resources()`;
      the reconcile loop probes CONNECTED handles on a cadence
      (`_LIVENESS_PROBE_SECONDS`, 30 s) and flips dead ones back to
      DISCONNECTED with backoff + detach, so readiness reflects the loss
      and the next tick reconnects.
- [x] Sleep on an event rather than polling a flag when connected.  Done:
      the connected branch sleeps the liveness cadence instead of the 1 s
      flag poll (the flag is only re-checked when a probe is due).
- [x] Enforce `maxTools` at connect (truncate + warn, or fail the server).
      Done: the filtered tool set is truncated to `maxTools` with a
      warning (an over-limit server still connects with its first N
      tools); the cap propagates to final names + attached tools.
- [x] Tests: a dropped session reconnects; a server exceeding `maxTools`
      is capped.  Done: `test_dead_session_detected_and_reconnected`
      (real stdio server, session killed → probe fails → DISCONNECTED →
      re-established) and `test_max_tools_capped_at_connect` (spike server
      now exposes echo + count; maxTools=1 caps the attached set).

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
- [ ] *Follow-up (4th review):* `_drain_after_grace` snapshots
      `run_registry` **once** at drain start. A run that passes the
      `is_draining()` check just before the flag flips, then registers its
      task after the snapshot, is not waited on — when the snapshotted runs
      finish early the drain proceeds and `_cancel_inflight_runs` cancels
      the newcomer before its grace expires (CNT-07 says in-flight runs
      keep their deadline up to `shutdownGraceSeconds`). The window is the
      several awaits in `chat.py` between the draining check and
      `run_registry.add(...)`. Re-snapshot in a loop until the registry is
      stable or the grace expires.

### R-13 WebSocket surface bypasses rate limiting; message cap counts characters (WS-01, API-20)

`rate_limit_middleware` is an HTTP middleware, so `/v1/ws` is never rate
limited — `run.start` can be issued in a loop on one connection.
`_receive_loop` (`websocket.py:234`) compares `len(raw)` (code points)
against `server.maxMessageBytes`, so a multi-byte payload can be several
times the configured byte cap.

- [x] Apply the limiter to WS connects and/or to `run.start` messages.
      Done: `run.start` is gated by the shared limiter keyed by principal
      (the HTTP middleware never sees WS frames); denials emit a
      `rate_limited` error + the OBS-05 denial counter.
- [x] Compare `len(raw.encode("utf-8"))` against `maxMessageBytes`.  Done.
- [x] Decide whether `websocket.accept()` should precede the auth failure
      close (`websocket.py:85-89`) and document the choice.  Decided:
      KEEP accept-then-close(1008) — WS-01 normatively pins the 1008
      close for failed auth (a pre-accept 403 would be a REQUIREMENTS
      amendment), and close codes are the only client-visible failure
      signal on an upgraded socket.  Comment documents the choice.
- [x] Tests: WS rate limiting; a multi-byte oversize message is rejected.
      Done: `test_run_start_rate_limited`,
      `test_oversize_multibyte_message_closes` (400 emoji ≈ 1600 bytes
      under the code-point limit → 1009).

## P2 — consistency and observability

### R-14 Usage/cost shape differs across the three run surfaces (COST-01, API-07/08)

- `chat.py` `_normalize_usage` emits `prompt_tokens` /
  `completion_tokens` / `total_tokens` / `costUsd`.
- `acp.py:211-215` hand-rolls the same three token fields and **drops
  `costUsd`** entirely.
- `websocket.py:424` forwards the raw internal dict
  (`input_tokens`/`output_tokens`/`cost_usd`).

The P5-4 finish line normalized chat only.

- [x] Route all three surfaces through one shared normalizer.  Done:
      `_normalize_usage()` (chat) is now used by ACP non-streaming
      (`_acp_completion_body`) and the WS `run.done` payload — the
      hand-rolled ACP copy that dropped `costUsd` is gone and WS no
      longer forwards the raw internal dict.
- [x] Decide the ACP/WS cost field name and record it in
      `docs/decisions.md`.  Done: `costUsd` (camelCase, the COST-01
      extension) on all three surfaces; recorded in `docs/decisions.md`.
- [x] ACP streaming passes no `include_usage` (`acp.py:141-152`) so it
      can never emit a usage chunk — confirm against the annex.
      Confirmed: A-4 says the streaming vocabulary has an "optional
      usage chunk" and the ACP request contract has no `stream_options`
      field — omitting it is annex-consistent (adding the field would be
      a versioned annex change).  Recorded in `docs/decisions.md`.
- [x] Tests: cost enabled/disabled × chat/ACP/WS.  Done:
      `TestCrossSurfaceUsage` (ACP + WS, enabled/disabled each).

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

- [x] Filter `search` by `embedding_model` in all three stores.  Done:
      memory (the stored chunk now carries the model and search filters
      by it), chroma (`{"model": ...}` in the where clause — the metadata
      already carried it), pgvector (`AND embedding_model = %s`).
- [x] Make the dimension mismatch an explicit error.  Done: `_cosine`
      uses `zip(strict=True)` — a mismatch raises ValueError instead of
      silently truncating (wrong scores against stale vectors).
- [x] Align `min_score`/`top_k` ordering across stores and document it.
      Done: the memory store now ranks → truncates to top_k → THEN
      applies min_score, mirroring chroma's n_results/pgvector's LIMIT
      (documented in the store code).
- [x] `RagRetriever.ingest` embeds every chunk in one call — add a batch
      size bound for large documents.  Done: `_INGEST_EMBED_BATCH = 32`.
- [x] Tests: model change isolates old chunks; identical result counts
      across stores for the same fixture.  Done: `TestEmbeddingModelIsolation`
      — model change isolates old chunks, min_score-after-truncation
      ordering, dimension-mismatch ValueError.  (Cross-store identical
      counts need chroma/pgvector instances — covered by the real-backend
      matrix; the ordering contract is now store-identical by code.)

### R-17 Prometheus cardinality guard is global, not per metric (OBS-05)

`_CardinalityGuard` (`metrics.py:49-70`) keeps **one** `_seen` set shared
by every metric name, so the 128-label-set cap is consumed across all 13
instruments combined and one high-cardinality metric silently starves the
others — contradicting the module docstring ("each metric caps its
distinct label sets"). `render()` also emits `# TYPE` but never `# HELP`,
so the descriptions passed to every instrument are discarded.

- [x] Key the guard by `(metric, labels)` or hold one guard per metric.
      Done: `_CardinalityGuard._seen` is now keyed by metric name — one
      high-cardinality metric cannot starve the others.
- [x] Emit `# HELP` lines from the instrument descriptions.  Done: the
      registry gains `register(name, description)`; the dual instruments
      thread their description at construction (observability.counter/
      gauge/histogram), and render emits `# HELP` before `# TYPE`.
- [x] Tests: one metric exhausting its cap does not affect another;
      exposition includes HELP.  Done: `TestCardinalityAndHelp` — per-
      metric cap isolation, HELP in the exposition, and HELP flowing from
      instrument construction (the production path).

### R-18 `204 No Content` responses carry a JSON body

`sessions.py:87` returns `JSONResponse(status_code=204, content=None)`
and `documents.py:180` returns `content={}`.

Verified: `DELETE /v1/sessions/{id}` → 204 with
`content-type: application/json` and a 4-byte body `null`, which RFC 9110
forbids and strict proxies/HTTP-2 clients reject.

- [x] Return `Response(status_code=204)` on both routes.  Done:
      `sessions.py` DELETE and `documents.py` DELETE now return a bare
      `Response(status_code=204)` — no body, no content-type (RFC 9110).
- [x] Tests: 204 responses have an empty body and no content-type.
      Done: the existing create/get/delete session test asserts
      `content == b""` and no content-type; documents DELETE shares the
      same code path.

### R-19 Session route error mapping collapses distinct failures (API-15, ENG-10)

`sessions.py:38` maps every exception from `create_session` to
`storage_unavailable`, so a `CapacityError` (`storage.maxSessions`)
surfaces as a 5xx outage rather than a capacity signal;
`sessions.py:76` maps every `delete_session` exception to `session_busy`.
`GET /v1/sessions/{id}` also skips the `validate_session_id` check that
POST applies.

- [x] Map `CapacityError`, `InvalidSessionId`, `SessionBusy`, and
      `BackendUnavailableError` to their distinct public codes.  Done:
      `_session_error` maps CapacityError → 503 `storage_capacity`,
      InvalidSessionId → 400, SessionBusy → 409 `session_busy`, everything
      else → 503 `storage_unavailable`; wired per-endpoint (create:
      capacity/invalid/unavailable; delete: busy/unavailable).
- [x] Validate the session id on GET/DELETE for consistency.  Done:
      both routes now apply `validate_session_id` (400 `invalid_session_id`).
- [x] Tests: one per mapped exception.  Done: capacity → 503, busy → 409,
      invalid id on GET/DELETE → 400 (stub-backend tests).

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

- [x] Decide per item: wire it up or delete it and amend the docstrings
      and REQUIREMENTS traceability accordingly.  Done (each item):
      `begin_iteration` wired into the run loop (the per-call deadline
      check); `cap_output_tokens` DELETED — the current google-adk
      RunConfig cannot carry per-call max_output_tokens (extra_forbidden),
      so ENG-08's budget stays enforced at the accounting boundary
      (observe_usage / can_start_another_call); the unwired RunConfig
      temperature/max_output_tokens kwargs removed (they raised
      ValidationError and degraded every overridden run to provider_error
      — a latent bug, now fixed); ToolLedger's durable persist plumbing
      and the unused fail/record_for/executing_ids/reconcile_executing
      deleted (no tool-record store exists; ENG-05's sweep reconciles
      crashed runs; completed tool activity stays in the run audit per
      ENG-06); RunResult deleted — `usage_estimated` lives on the usage
      object as API-14's `usage.estimated: true`.
- [x] If ENG-08/ENG-09 stand as written, implement the pre-call token cap,
      the durable tool-call records, and surface `usage_estimated`.
      Done: `usage.estimated` now surfaces — `_convert` calls
      `observe_usage` even for empty metadata (the estimate flag was never
      set before), the runner's usage objects carry `estimated: true`,
      and `_normalize_usage` passes it through on all three surfaces.
      The pre-call provider cap and durable tool records are documented
      as not expressible with the current google-adk / storage layer
      (docstrings amended; the audit + sweep cover the crash paths).

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

### R-26 Session routes return 500 on a storage-driver outage (regression from R-19)

*Intelligence: medium — localized correctness.*

The R-19 fix (`20c0e7b`) replaced `except Exception` in
`app/protocol/routes/sessions.py` with narrow tuples:
`create_session` catches `(BackendUnavailableError, CapacityError,
InvalidSessionId)` and `delete_session` catches
`(BackendUnavailableError, SessionBusy)`.

The mapping itself is right, but the redis and postgres backends do
**not** wrap driver failures in a `StorageError` — `create_session`
calls `self._eval(...)` / `self._client.get(...)` bare, so a Redis or
Postgres outage raises `redis.exceptions.ConnectionError` /
`psycopg.OperationalError`. Those are no longer caught and now fall
through to the global handler.

Verified with a backend whose `create_session`/`delete_session` raise a
non-`StorageError` exception:

| Route | Before `20c0e7b` | After |
| --- | --- | --- |
| `POST /v1/sessions` | 503 `storage_unavailable` | **500 `internal_error`** |
| `DELETE /v1/sessions/{id}` | 503 `storage_unavailable` | **500 `internal_error`** |

This inverts the retry signal for the exact case the handler existed
for: 503 is retryable and matches the `/readyz` outage story, 500 tells
the client the server is broken. API-15/ENG-10 map dependency outages to
`storage_unavailable`.

- [x] Catch `StorageError` (the common base) plus a broad fallback, or
      restore `except Exception` with the mapper deciding — keep R-19's
      distinct codes for the typed cases and default the rest to
      `storage_unavailable`.  Done: both session routes use
      `except Exception` through `_session_error` — typed cases keep
      their codes, everything else maps to 503 `storage_unavailable`.
- [x] Preferred root fix: wrap driver exceptions at the backend boundary
      so redis/postgres raise `BackendUnavailableError` like the memory
      and file backends already do. That also fixes every other route
      that touches these backends, not just sessions.  Done: redis
      `_eval` wraps driver errors; `_PsycopgDb.execute/query` and
      `_PsycopgTxn.__aenter__` wrap `psycopg.OperationalError`
      (`_raise_driver_unavailable`).
- [ ] Note `StorageUnavailable` (`contract.py:57`) is declared but never
      raised anywhere — decide whether it is the intended wrapper type or
      dead code (see R-20).  *Carried into R-20's per-item decision
      (same implement-vs-delete review).*
- [x] Tests: a backend raising a non-`StorageError` yields 503 on both
      routes.  Done: `test_driver_outage_maps_to_503` (both routes) +
      redis/postgres boundary-wrap tests in the storage contract suite.

### R-27 JWKS stale-key cutoff leaks: most post-cutoff requests still verify (gap in R-07, SEC-08)

*Intelligence: medium — security.*

In `_JwtAuth.authenticate` (`app/protocol/auth.py`) the stale-key cutoff
is evaluated **inside** the `if due and not attempted:` branch. Once a
refresh attempt is recorded, `attempted` stays true for the rest of the
interval, so the whole branch — including the cutoff test — is skipped
and verification proceeds against the stale JWKS.

Net effect: the cutoff only fires at the instants a refresh is attempted
(about once per `refreshSeconds`), not continuously. R-07's tick claims
"past 3× the interval without a successful refresh, auth fails closed" —
that is materially overstated.

Measured with `refreshSeconds=300` (cutoff 900 s), IdP down from t=0,
probing once a minute for 30 minutes **past** the cutoff:

| Probe | Result |
| --- | --- |
| t=900 s | fails closed ✔ |
| t=901 / 1000 / 1100 s | **accepted against stale keys** ✘ |
| t=1200 s | fails closed ✔ |
| 31 probes past cutoff | **24 accepted (77%)** |

`TestJwtJwksRefresh`'s fail-closed case passes because it probes exactly
at an attempt boundary — which is why the suite is green.

- [ ] Evaluate the cutoff on **every** request, independent of whether a
      refresh was attempted in the current window: if
      `now - _jwks_fetched_at >= refresh_seconds * _STALE_CUTOFF_MULTIPLIER`,
      fail closed before verifying. Keep the once-per-interval gate for the
      *fetch attempt* only — that part is right and should not change.
- [ ] Strengthen the test to probe **between** attempt boundaries (e.g.
      cutoff + 0.5 × `refreshSeconds`), not just on them.
- [ ] Re-word the R-07 tick once fixed — as written it overstates the
      guarantee.

### R-28 Redis driver-boundary wrap is over-broad (asymmetric with psycopg, from the R-26 fix)

*Intelligence: medium — localized correctness.*

`fed1185` added driver-boundary wrapping on both backends, but the two
sides differ in breadth:

- `app/main.py::_raise_driver_unavailable` — **correct**: wraps only
  `psycopg.OperationalError`; everything else re-raises unchanged, with
  the docstring calling this out ("Non-connection errors (code bugs) keep
  propagating as-is").
- `app/storage/redis_backend.py::_eval` — catches bare `Exception` and
  wraps **everything** as `BackendUnavailableError`.

Verified:

| Raised | Result |
| --- | --- |
| psycopg `OperationalError` | `BackendUnavailableError` ✔ |
| psycopg `ProgrammingError` | propagates ✔ |
| redis `ResponseError` (Lua compile error) | **`BackendUnavailableError`** ✘ |

The redis backend drives several Lua scripts (session create, admit_run,
the R-04 sweep, fence lease ops). A bug in any of them — or a `DataError`
from a malformed argument — now surfaces as 503 `storage_unavailable`:
clients are told to retry a deterministic failure that can never
succeed, and operators see an outage signal instead of an error. The
correct pattern is already in the same commit, on the psycopg side.

- [ ] Narrow the `_eval` catch to redis's connection/timeout family
      (`redis.exceptions.ConnectionError`, `TimeoutError`,
      `BusyLoadingError`), re-raising `ResponseError`/`DataError` and the
      rest — mirroring `_raise_driver_unavailable`.
- [ ] Consider one shared helper so the two boundaries cannot drift again.
- [ ] Tests: a script/response error propagates rather than mapping to
      503; a connection error still maps.

### R-29 `observability.prometheus.*` reloads report success but never take effect (residual R-02 gap, REL-02)

*Intelligence: medium — localized correctness.*

R-02 converted the route handlers it enumerated to read
`components["config"]` per request. Two readers were missed, both with
the same symptom R-02 existed to remove — a leaf that classifies as
live-snapshot, reports `applied_live`, and silently does nothing.

**1. The Prometheus route is bound at boot.**
`observability.prometheus.path` / `.enabled` appear in neither
`RESTART_REQUIRED_PREFIXES` nor the rebuild set, so `classify_change`
returns `live_snapshot` — but `create_app` registers the exposition route
once, at the boot path.

Verified with a single-leaf overlay (`path: /metrics` → `/newmetrics`):

| | Result |
| --- | --- |
| reload outcome | `applied_live`, changed `['observability.prometheus.path']` |
| live holder | updated to `/newmetrics` ✔ |
| `GET /newmetrics` | **404** ✘ |
| `GET /metrics` | **200** — still serving the old path ✘ |

A scrape config moved to the new path gets 404s while the runtime
reports the reload applied. `.enabled` is worse: flipping it on live
cannot register the route at all.

**2. `app/protocol/app.py:171` still reads the captured boot config.**
The rate-limiter's exempt set is built from
`config.observability.prometheus.path` (the closure variable, not the
holder), so after any path change the limiter exempts the **old** path —
the scrape endpoint R-02's sibling fix meant to protect.

- [ ] Decide: either add `observability.prometheus` to
      `RESTART_REQUIRED_PREFIXES` (simplest, and honest — a bound route
      cannot move live), or make the exposition path dynamically routable.
- [ ] Point `app.py:171` at `components["config"]` regardless of which
      way (1) goes.
- [ ] Audit for any other closure readers R-02 left behind — the two
      found here were both outside the enumerated route handlers.
- [ ] Extend `TestLiveSnapshotLeaves` to assert that every leaf
      `classify_change` calls `live_snapshot` has an observable effect, so
      a future addition cannot regress silently.

### R-30 ⚠ Storage blip during idempotency admission permanently leaks run slots (regression from R-08, NFR-03)

*Intelligence: medium — localized correctness. **Severity: highest of
this review run** — a transient dependency failure permanently disables
the replica.*

R-08 moved `create_idempotency` to **after** `slots.try_acquire()` (a
correct fix for "a 503 leaves a pending record"). But the call now sits
between the acquire and the `try/finally` that releases the slot, so if
it raises, the slot is never released. `RunSlotGate._in_flight` only ever
decrements in `release()`, so the loss is permanent.

This is newly reachable *because of* R-26: redis/postgres driver outages
now raise `BackendUnavailableError` out of the storage boundary, exactly
where this call sits.

Verified with `maxConcurrentRequests: 2` and `create_idempotency` raising
`BackendUnavailableError`:

| Step | Result |
| --- | --- |
| request 1 (storage down) | 500 — `in_flight` 0 → **1** |
| request 2 (storage down) | 500 — `in_flight` 1 → **2** |
| storage **recovers**, healthy request | **503 `overloaded`** ✘ |
| final state | `in_flight = 2 of limit 2`, permanently |

After `maxConcurrentRequests` such failures the replica refuses **all**
traffic until restarted, even though the dependency has recovered. A
brief Redis blip is enough. `/readyz` would report ready (storage is
healthy again) while every request 503s.

Affects `chat.py` and `acp.py` — both run-admitting surfaces have the
identical shape. `documents.py` acquires no slot and is unaffected.

- [ ] Wrap everything between the acquire and the existing `try/finally`
      so any failure releases the slot — or simply move the acquire to the
      top of that block.
- [ ] Apply to both `chat.py` and `acp.py`.
- [ ] Map the admission failure to 503 `storage_unavailable` rather than
      letting it surface as 500 `internal_error` (same reasoning as R-26).
- [ ] Test: N admission failures followed by a healthy request must still
      be admitted; assert `run_slots._in_flight` returns to 0.
- [ ] Consider making `RunSlotGate` acquisition a context manager so the
      pairing cannot be broken by a future reorder — this is the second
      inc/dec pairing bug in the run path (cf. R-03's gauge).

### R-31 ⚠ Reconnect retry inside a transaction silently partial-commits and reports success (regression from R-10, ENG-06/SES-01)

*Intelligence: high — connection lifecycle / transaction contract.*
*Severity: silent data loss with a false success signal.*

R-10's `_PsycopgDb._run` retries a dropped statement on a fresh
connection. `_connect` sets `autocommit=True`, and the retry is **not
transaction-aware** — but `app/storage/postgres_backend.py` wraps seven
call sites in `async with self._db.transaction():` (mutate_session,
admit_run, decide_approval, …), and every statement inside those blocks
goes through `execute`/`query` → `_run`.

`_PsycopgTxn.__aexit__` compounds it: it calls `self._db._ensure()`,
which happily hands back a **new** connection, then issues `COMMIT` on
it — a no-op, since that connection never ran `BEGIN`.

Simulated drop on the second statement of a two-statement transaction:

```
conn1: autocommit=False
conn1: 'BEGIN'
conn1: 'UPDATE sessions SET ...'          <- inside txn
conn1: 'INSERT INTO runs ...'  -> CONNECTION DROPPED
conn2: autocommit=True
conn2: 'INSERT INTO runs ...'  (autocommit=True)   <- COMMITTED STANDALONE
conn2: 'COMMIT'                (no txn open — no-op)
caller saw: SUCCESS (transaction reported committed)
```

So the first statement's effect is **lost**, the second is **durably
committed on its own**, and the caller is told the transaction
committed. For `mutate_session` that is ENG-06's "one revision-checked
transaction commits pruning + the complete turn + usage" broken into a
partial write — with the revision check (optimistic concurrency) also
defeated, since the caller proceeds as if it held the revision.

R-10's own tests pass because they exercise **standalone** statements
only; nothing covers a drop inside a transaction, which is why the suite
is green.

- [ ] Make the retry transaction-aware: track in-transaction state on
      `_PsycopgDb` and **do not retry** while inside one — fail the whole
      transaction with `BackendUnavailableError` and let the caller's
      revision check / higher-level retry handle it. Retrying standalone
      statements stays correct and valuable.
- [ ] `__aexit__` must not silently `_ensure()` a fresh connection. If the
      original connection is gone, raise rather than issue COMMIT/ROLLBACK
      on a connection that never began the transaction.
- [ ] Tests: a drop mid-transaction must surface an error, must not
      autocommit the retried statement, and must never report success.
      Add this to the real-Postgres contract matrix, not just fakes.
- [ ] Re-check the same hazard on the redis backend's Lua scripts (each
      script is atomic server-side, so `_eval` retry is likely safe — but
      confirm rather than assume).

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
