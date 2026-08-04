# Backlog — remaining work

Phase 1 (core runtime) milestones 0–8 are implemented; all code work is done.
The completed work is recorded milestone-by-milestone in
[CHANGELOG.md](CHANGELOG.md). This file now tracks only what is **not yet
done**: the image-based M8 exit checks, issues & improvements found by a full
code review, the unstarted later phases (P2–P4), resolved decisions (for the
record), the one open human-call decision, and explicitly deferred scope.

Requirement IDs in parentheses trace each task back to
[REQUIREMENTS.md](REQUIREMENTS.md); the build order is
[PLAN.md](PLAN.md). "Later phases," "Deferred scope," and "Open decisions" at
the bottom are not part of the P1 critical path.

---

## P1 — remaining items

### Milestone 8 — Container hardening and release packaging (image-based exit checks)

- [ ] **NFR-08 — zero-downtime reload verification.** Verify that a valid
      live/rebuild config update causes zero failed admitted requests and no
      listener restart. **Status:** pending the full acceptance/chaos run (M8
      exit check). *Code in place:* transactional reload with rollback
      (`app/watcher/reload.py`).
- [ ] **NFR-00 — full §6 benchmark/chaos suite.** Run against the built image
      and record the report: startup latency (NFR-01), request overhead
      (NFR-02), concurrency under load (NFR-03), idle footprint (NFR-04),
      bounded resources under a slow/disconnected client (NFR-07),
      dependency-recovery races (NFR-09), and cross-platform portability
      (NFR-10). **Status:** the container plumbing is proven end-to-end
      (`scripts/mock_openai_server.py` + `llm.provider: openai` + `baseUrl` +
      `host.docker.internal`; non-stream + SSE chat verified against the built
      image); the full image-driven NFR harness is the M8 exit check. *Code in
      place:* API-08a stream backpressure, CNT-07 graceful shutdown, NFR-03
      run cap (503 `overloaded`), API-20 rate limiter.
- [x] **ACC-01 — full §18 acceptance suite on both architectures.** Must pass
      on `linux/amd64` and `linux/arm64` before P1 is called done.
      **Status: PASS on both architectures (334/334 each, current code).** The
      image-based harness (`scripts/run-image-acceptance.sh`, `Dockerfile.test`)
      runs the full pytest suite inside the shipped image per platform
      (imports verified against `/app/app`, not the host checkout); evidence in
      `docs/acceptance-{amd64,arm64}.{log,json}` (both `status: pass`, with
      image ID / commit / lock hash / `staleness_check: pass`). Blockers
      resolved: (a) MSYS path-mangling via `MSYS_NO_PATHCONV=1` + `cygpath -m`;
      (b) the arm64 stdio MCP connect failure — google-adk 2.6.1 wraps a bare
      `StdioServerParameters` with a hardcoded `timeout=5` that fires
      mid-handshake under QEMU; `wrap_stdio_params` now passes ADK's own
      `StdioConnectionParams(timeout=30)`; (c) the MCP stdio readiness test
      window is env-tunable for slow emulated platforms
      (`AGENT_TEST_MCP_CONNECT_SECONDS`). The suite now includes the new
      cap/rate-limit/connector/reload regression tests. Storage runs per the
      recorded deviation. **Committed with the M8 gate commit.**

---

## Recently completed (recorded in [CHANGELOG.md](CHANGELOG.md))

- [x] **API-08a — Stream failure/disconnect/backpressure.** Bounded output
      queue (`server.streamQueueEvents`) + producer/consumer; producer `put`
      times out after `server.slowConsumerSeconds` of a full queue; consumer
      polls client-disconnect at ≤1 s; either trigger requests run cancellation
      within 1 s and emits one `x_agent_event` error chunk then `[DONE]`.
      `app/protocol/routes/chat.py::_stream`; tests in
      `tests/test_protocol/test_streaming.py`.
- [x] **CNT-07 — Graceful shutdown.** `app/lifecycle.py::ShutdownManager` +
      `ManagedServer` uvicorn wrapper: first SIGTERM/SIGINT drains (`/readyz`
      503, new chat 503, in-flight runs keep their deadline, `healthz` live);
      at grace expiry it **cancels in-flight runs** (run registry, terminal
      states persist BEFORE storage closes — CNT-07 gap closed), then closes
      watcher→MCP→storage→OTel and stops the listener (exit 0 only if all
      flush/close succeeds, else 1); second signal hard-exits 1; manifests set
      `terminationGracePeriodSeconds: 35`. Tests in
      `tests/test_protocol/test_shutdown.py`.

---

## Issues & improvements (from a full code review)

Found by a read-only review pass over the whole project. Prioritized; each item
is file-referenced and actionable. Critical/High items should land before the
P1 release; the rest can land before/after the M8 image-based exit checks.

### Critical/High — fix before release

- [x] **runner.py `execute()` yields after `GeneratorExit`** — FIXED. The
      `except BaseException` was split into `except GeneratorExit` (commits a
      terminal state via `state.fail()`, persists under `suppress(BaseException)`,
      then re-raises **without yielding**) and `except Exception` (the only path
      that may yield). The `CancelledError` path now goes through
      `_mark_cancelled` and also persists under `suppress(BaseException)` so
      teardown can't be broken by a commit failure. Bonus hardening in the same
      diff: `_token_count` defensively coerces provider usage metadata (TRUST-01),
      and `_usage_dict` now uses it. (`app/engine/runner.py`)
- [x] **`_stream` emits SSE from `finally`** — FIXED. The `finally` block is
      now teardown-only (cancel producer + `aclose` + release the run slot +
      unregister from the run registry) with NO yields; the final
      finish/usage/`[DONE]` chunks are emitted after the block on the normal
      completion path only, so a Starlette generator close (client disconnect)
      can no longer raise `RuntimeError` from a yield inside `finally`.
      (`app/protocol/routes/chat.py::_stream`)
- [x] **CNT-07: in-flight runs are not cancelled at grace expiry** — FIXED.
      `create_app` seeds `components["run_registry"] = set()` (`app.py:72`); the
      chat route adds the current task on admit and discards it on every exit
      path (non-streaming `chat.py:183`, streaming `chat.py:479`). `_drain_after_grace`
      now calls `_cancel_inflight_runs()` (`lifecycle.py:111`) **before**
      `close_components()`, so the runner's `CancelledError` path persists
      terminal states/usage while storage is still open. Test:
      `test_grace_expiry_cancels_inflight_runs_before_close` asserts the order
      `[run_cancelled, watcher, mcp, storage, otel]`. (Also closes the CNT-07
      gap previously noted under "Recently completed → CNT-07".)
- [x] **`maxConcurrentRequests` cap: streaming leak FIXED; live-reload gap
      recorded as a limitation.** Implemented: `RunSlotGate` in
      `app/protocol/app.py` (counter+lock, atomic `try_acquire`, never blocks),
      created in `create_app` as `components["run_slots"]`; the chat route
      acquires before any model work and answers 503 `overloaded` at the cap
      (API-15/NFR-03); tests in `tests/test_protocol/test_limits.py`.
      **Streaming-slot leak: FIXED** — `_stream`'s `finally` now calls
      `slots.release()` + registry unregister; two regression tests
      (completion + mid-stream disconnect) prove the slot frees.
      **Recorded limitation — live-reload no-op on both fields:**
      `server.maxConcurrentRequests` / `server.rateLimit` are classified
      `live_snapshot`, but the gate/limiter are built once in `create_app` and
      the live-snapshot apply branch only bumps generation/hash — a live change
      to the cap or limiter takes effect on the next component-rebuild or
      restart. This matches the pre-existing live-snapshot behavior for every
      route-level setting (routes hold the boot config object). A systemic fix
      (routes re-resolving the live config per request) is deferred; the
      NFR-08 proof uses rebuild-category changes, which DO reach the live
      surface now.

### Medium — correctness & robustness

- [x] **JWKS: per-call httpx client (cache lock FIXED)** — the cache race is
      fixed: `_refresh_jwks` now serializes through `_jwks_lock` (`auth.py:154`),
      guarding the concurrent refresh/stampede. The client stays per-call:
      refresh frequency is low (boot + failed verification), so the connection
      churn is bounded; a lifespan-managed shared client was judged not worth
      the lifecycle wiring. (`app/protocol/auth.py`)
- [x] **MCP `release()` double-release closes the shared toolset** — FIXED.
      `_release` guards `ref_count <= 0` (warns and returns — a spurious
      release can no longer destroy the shared toolset), and shutdown closes
      toolsets via an explicit `_close_toolset` regardless of refcount.
      (`app/engine/mcp/manager.py`)
- [x] **Reload swap may not take effect on the chat surface** — FIXED, and a
      deeper production bug found: (1) `ReloadManager` invoked
      `build_components(config, generation)` against main.py's
      `build_components(config, backend, generation=1)` — the generation was
      silently bound as the backend, so every production rebuild built a broken
      runner and failed `_health_check` → permanent `rebuild_failed` rollback.
      main.py now passes `functools.partial(build_components, backend=backend)`
      so rebuilds reuse the shared backend (sessions survive) with the correct
      generation. (2) The chat route captured `runner = components["runner"]`
      at register time, so even a successful swap never reached the live
      surface; the route now resolves `components["runner"]` per request.
      Regression test:
      `tests/test_protocol/test_limits.py::test_rebuild_swap_reaches_chat_route`.
      (`app/main.py`, `app/watcher/reload.py`, `app/protocol/routes/chat.py`)
- [ ] **Redis `KEYS` in Lua** — deferred (storage design change; the hashed
      keyspace is bounded by the configured session/run/idempotency caps;
      revisit with a concrete scale requirement). (`app/storage/redis_backend.py`)
- [ ] **Postgres `mutate_session` read-then-CAS TOCTOU** — deferred (move the
      merge into SQL or a serializable txn; the CAS still bounds the window;
      revisit with the real-instance matrix). (`app/storage/postgres_backend.py`)
- [ ] **Pre-admission cancellation loses the run record** — deferred: a cancel
      before `_admit` returns has no run record yet; `_commit_failure` already
      suppresses `SessionNotFound`, and the storage sweep reconciles
      nonterminal runs. Residual: an orphaned session until TTL (bounded).
      (`app/engine/runner.py:114,175`)
- [ ] **Non-atomic admission** — deferred: session creation + run-record
      creation are two steps; a `create_run` failure orphans the session until
      TTL. Wrapping as one transaction across backends is a storage-contract
      change; revisit with the real-backend matrix. (`app/engine/runner.py::_admit`)
- [x] **Conflicting-credential check is not constant-time** — FIXED. The
      `bearer != x_api_key` pre-check now uses `_constant_time_eq`
      (`hmac.compare_digest` over UTF-8 bytes), so the conflicting-credential
      branch no longer leaks via a non-constant-time compare. (`app/protocol/auth.py:91`)
- [x] **`AdkSessionService` unbounded in-memory dicts** — FIXED. The unused
      `_user_state` dict is removed (the runtime never persists user-scoped
      state — `set_user_state` is unimplemented, so `get_user_state` returns
      `{}`). `_revisions` is retained but documented as bounded by live sessions
      (create adds, delete pops; backend TTL sweeps age out the rest) — one int
      per session. (`app/storage/adk_adapter.py`)

### Test coverage gaps

- [x] **Cancellation stress tests** — COVERED. The highest-risk path now has
      direct tests: `tests/test_engine/test_connectors.py::test_cancel_mid_run_persists_terminal_state`
      cancels an in-flight `execute()`, then `aclose()`s the generator, and
      asserts a terminal run record was persisted with no `RuntimeError`/orphan
      (exercises the runner's `CancelledError` + `GeneratorExit` paths).
      Reinforced by `test_streaming.py::test_disconnect_requests_cancellation_within_1s`,
      `test_limits.py::test_streaming_disconnect_releases_slot`, and
      `test_shutdown.py::test_grace_expiry_cancels_inflight_runs_before_close`.
- [ ] **Real-backend CI matrix (ACC-01 deviation)** — run the shared contract
      suite against real Redis 7 + Postgres 16 containers (Lua scripts,
      advisory locks, CAS) instead of `FakeRedis`/`SqliteDb` substitutes.

### Improvements (non-bug)

- [ ] **Structured shutdown audit** — the `shutdown_draining`/`shutdown_complete`
      audit events exist (exit code + close_ok); a single summary log line with
      duration + per-component failure detail is still open.
      (`app/lifecycle.py`)
- [x] **Narrow `except BaseException` → `except Exception`** — DONE as part of
      the GeneratorExit split (CancelledError / GeneratorExit / Exception
      handlers; the GeneratorExit handler persists without yielding).
      (`app/engine/runner.py`)
- [ ] **Warn on unknown audit events** instead of silently remapping to
      `audit_unknown`. (`app/security/audit.py:29`)

### M8 image-based gate findings (fixed)

The image-based NFR/acceptance probes surfaced production bugs the host suite
could not reach (tests inject fakes that bypass the production connector and
reload wiring):

- [x] **`RetryableLlm` never exposed `.model`** — ADK's request builder reads
      `agent.model.model` at request time; the wrapper's pydantic field was
      declared but never initialized, so every production run failed with
      `AttributeError`. Fixed: `super().__init__(model=inner.model)`; regression
      tests in `tests/test_engine/test_connectors.py`.
      (`app/engine/connectors.py`)
- [x] **`SecretResolver()` snapshotted an empty env** — `build_llm` creates a
      bare resolver, which never read `os.environ`, so `apiKeyEnv` refs never
      resolved and every provider call went out without credentials. Fixed:
      default the snapshot to `os.environ`; regression test in
      `tests/test_engine/test_connectors.py`. (`app/engine/connectors.py`)
- [x] **Reload rebuild wiring (backend/generation binding + stale runner)** —
      see the *Reload swap* item above; NFR-08's rebuild semantics depend on it.
- [x] **Test false-green** — `tests/test_protocol/test_limits.py` used
      `google.genai.types.LlmResponse` (does not exist; the ADK class is
      `google.adk.models.llm_response.LlmResponse`), so runs failed with
      `internal_error` while status-200/`[DONE]` assertions still passed;
      fixed the import and strengthened assertions to check content.

---

## Later phases (not started)

Each gets its own milestone breakdown in PLAN.md once P1's acceptance criteria
(§18) pass.

- [ ] **P2 — Multi-agent** (§13): sub-agent hierarchies, ADK transfer routing,
      tool isolation, ACP REST surface (API-16).
- [ ] **P3 — Human-in-the-loop** (§14): durable approval checkpoints,
      decision-race handling, restart reconciliation.
- [ ] **P4 — RAG / long-term memory** (§15): document ingestion, chunking,
      retrieval-scoped context injection.

---

## Decisions made (resolved, for the record)

- [x] **NFR-06 OpenAI SDK compatibility range** (recorded 2026-08-03). Tested
      range: `openai` Python SDK `>=1.0,<3` (locked 2.52.0, transitive via
      litellm). The OpenAI-compatible surface is a strict subset
      (`chat.completions.create` non-streaming + streaming, `models.list`); the
      golden-fixture suite exercises it through the real SDK client against the
      mock engine.
- [x] **CNT-12 vulnerability severity policy** (recorded 2026-08-03). A release
      is blocked when the image's vulnerability scan reports any CRITICAL or
      HIGH severity CVE in the runtime image with no available fix; MEDIUM/LOW
      are tracked but non-blocking.
- [x] **MCP-08 stdio pre-parse byte cap (STACK-02 seam)** (decided 2026-08-02).
      Defer the stdio `maxTransportMessageBytes` pre-parse cap; enforce the cap
      on Streamable HTTP + legacy SSE now. Reason: google-adk 2.6.1 pins
      `mcp>=1.24,<2`, and mcp 1.29.0's `stdio_client` has no bounded-read
      injection point; mcp 2.x has the `Transport` seam but is incompatible
      with ADK 2.6.1. Recorded in REQUIREMENTS.md MCP-08 (v2.5). Revisit when a
      google-adk release supports mcp 2.x.
- [x] **mcp SDK version range.** Pinned `mcp>=1.24,<2` (locked 1.29.0) per
      google-adk 2.6.1's declared range — mcp 2.0.0 breaks `McpToolset` imports.
- [x] **ACC-01 storage proof deviation** (approved by user decision 2026-08-02;
      recorded here per the M2 exit check). The §18 ACC-01 storage criteria run
      against the memory backend for real and against in-memory substitutes for
      the external backends: `file` via a temp-dir real backend, `redis` via
      `app/storage/fakes.py::FakeRedis` (same Lua script paths), `postgres` via
      `app/storage/fakes.py::SqliteDb` (same SQL/migration paths). The
      real-instance proof (live Redis/Postgres servers) and the
      fencing/multi-replica concurrency proof are **deferred**; the fencing
      logic itself (redis Lua CAS lease, postgres advisory locks) is unit-tested
      through the substitutes. Revisit when a real multi-replica deployment
      needs proof.

## Open decisions (need a human call, not an engineering call)

- [ ] **Product name / trademark / domain / package-registry clearance.**
      **Agentbase** is the chosen name (repo-wide rename applied); it still
      needs clearing (USPTO/EUIPO trademark in classes 9/42, `agentbase` domain,
      PyPI/Docker Hub/GitHub namespace) before a public release. Until cleared,
      the name should be treated as provisional.

## Deferred scope — revisit only if a concrete need shows up

Cut in the v2.2 scope pass. Don't reopen speculatively; reopen when an actual
caller or deployment needs one.

- [ ] **WebSocket API.** Revisit if a client needs bidirectional push (e.g.,
      server-initiated cancellation notices, multiplexed tool-approval UI) that
      SSE can't express.
- [ ] **Kubernetes CRD / operator.** Revisit once the product name/API-group
      (above) is settled and there's a real need for `kubectl get agentconfigs`,
      CRD-native status, or admission-webhook validation.
- [ ] **Prometheus `/metrics` endpoint and per-request dollar-cost accounting.**
      Explicitly deferred (REQUIREMENTS.md §1.4) — OTel metrics cover the
      interim need.
