# Backlog — remaining work

Phase 1 (core runtime) milestones 0–8 are implemented; all code work is done.
The completed work is recorded milestone-by-milestone in
[CHANGELOG.md](CHANGELOG.md). This file now tracks only what is **not yet
done**: the image-based NFR-00 exit check, deferred improvements found by the
code review, the unstarted later phases (P2–P4), resolved decisions (for the
record), the one open human-call decision, and explicitly deferred scope.

Requirement IDs in parentheses trace each task back to
[REQUIREMENTS.md](REQUIREMENTS.md); the build order is
[PLAN.md](PLAN.md). "Later phases," "Deferred scope," and "Open decisions" at
the bottom are not part of the P1 critical path.

---

## P1 — remaining items

### Milestone 8 — Container hardening and release packaging (image-based exit checks)

- [ ] **NFR-00 — full §6 benchmark/chaos suite.** Run against the built image
      and record the report: startup latency (NFR-01), request overhead
      (NFR-02), concurrency under load (NFR-03), idle footprint (NFR-04),
      bounded resources under a slow/disconnected client (NFR-07),
      dependency-recovery races (NFR-09), and cross-platform portability
      (NFR-10). **Status: harness RUN, NFR-02 failing.** `docs/nfr-report.json`
      is now a real image-based run (commit `cd5c9ff`, image `agentbase:amd64`,
      1 CPU / 512 MiB), but it stopped after the first gate: **NFR-02 (runtime
      overhead) FAILED — p95 151.47 ms vs the < 50 ms target** (1000
      non-streaming reqs @ conc 10, after 100 warm-up, mock model). The suite
      did not proceed to NFR-03/04/07/09/10. **Investigation (narrowed):** the
      mock's non-streaming path returns instantly (no sleep), so the 151 ms is
      NOT model hold — it is server overhead + the LiteLLM HTTP round-trip to
      the out-of-process mock. **This is a harness/spec mismatch:** NFR-02
      (REQUIREMENTS.md §6) mandates an ***in-process* deterministic mock**
      measured "from request receipt through validation/session work to
      serialization," but `image-nfr.py` drives the runtime's LiteLLM bridge to
      an **out-of-process** mock over `host.docker.internal`, so the measurement
      conflates server overhead with LiteLLM-client construction + the
      Windows↔container network hop. **To close NFR-00:** (1) decide how NFR-02
      is to be measured fairly against the built image — either inject an
      in-process mock model into the image for this one gate (matching the
      spec), or measure server-only overhead by subtracting a baseline
      direct-to-mock round-trip, or revise the threshold for the out-of-process
      variant (a spec change). (2) Re-run the full suite (`--platform amd64` +
      `arm64`) and record an all-green `docs/nfr-report.json`. Note:
      `scripts/image-nfr.py` is untracked — commit it with the NFR evidence.

> ACC-01 (§18 acceptance, 336/336 on both architectures) and NFR-08
> (zero-downtime reload) are **done** — see [CHANGELOG.md](CHANGELOG.md).

---

## Issues & improvements (from a full code review)

Found by a read-only review pass over the whole project. The critical/high items
and most medium items are already fixed (see CHANGELOG.md); what remains is
deferred (low priority or needs a concrete scale/deployment trigger).

### Medium — correctness & robustness (deferred)

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
- [ ] **Live-reload no-op on `server.maxConcurrentRequests` /
      `server.rateLimit`** — these fields are classified `live_snapshot`, but
      the `RunSlotGate` / `FixedWindowLimiter` are built once at boot and the
      live-snapshot apply branch only bumps generation/hash. A live change to
      the cap or limiter takes effect on the next component-rebuild or restart.
      Matches the pre-existing live-snapshot behavior for every route-level
      setting (routes hold the boot config object). A systemic fix (routes
      re-resolving the live config per request) is deferred; the NFR-08 proof
      uses rebuild-category changes, which DO reach the live surface.
      (`app/protocol/app.py`, `app/watcher/reload.py`)

### Test coverage gaps

- [ ] **Real-backend CI matrix (ACC-01 deviation)** — run the shared contract
      suite against real Redis 7 + Postgres 16 containers (Lua scripts,
      advisory locks, CAS) instead of `FakeRedis`/`SqliteDb` substitutes.

### Improvements (non-bug)

- [ ] **Structured shutdown audit** — the `shutdown_draining`/`shutdown_complete`
      audit events exist (exit code + close_ok); a single summary log line with
      duration + per-component failure detail is still open.
      (`app/lifecycle.py`)
- [ ] **Warn on unknown audit events** instead of silently remapping to
      `audit_unknown`. (`app/security/audit.py:29`)

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
