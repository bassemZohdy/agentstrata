# Backlog — remaining work

All four phases (P1 core runtime, P2 multi-agent/ACP, P3 approvals, P4 RAG)
are implemented and accepted; the completed work is recorded
milestone-by-milestone in [CHANGELOG.md](CHANGELOG.md). This file tracks only
what is **not yet done**: the deferred improvements found by the code review,
resolved decisions (for the record), the one open human-call decision, and
explicitly deferred scope.

Requirement IDs in parentheses trace each task back to
[REQUIREMENTS.md](REQUIREMENTS.md); the build order is
[PLAN.md](PLAN.md). "Deferred scope" and "Open decisions" at the bottom are
not part of any phase's critical path.

---

## Issues & improvements (from a full code review)

Found by a read-only review pass over the whole project. The critical/high items
and most medium items are already fixed (see CHANGELOG.md); what remains is
deferred (low priority or needs a concrete scale/deployment trigger).

### Medium — correctness & robustness

- [x] **Redis `KEYS` in Lua** — DONE: every blocking `KEYS` scan is gone
      from the Lua scripts. Runs and idempotency records now live in
      per-session ZSET indexes (`agentbase:{tag}:runidx:{agent}:{sid}` /
      `...:idemidx:...`; member = full key, score = timestamp), so
      capacity, terminal-prune, delete-cascade, and list are exact
      ZRANGE/ZCARD ops; the retention sweep enumerates the indexes with a
      non-blocking SCAN (`redis.replicate_commands()` makes the
      DEL/ZREM/ZADD writes legal after the random SCAN). Verified
      137/137 on the real Redis 7 matrix. (`app/storage/redis_backend.py`,
      `app/storage/fakes.py`)
- [x] **Postgres `mutate_session` read-then-CAS TOCTOU** — DONE: the
      read-merge-CAS is now a bounded retry (up to 3 attempts) that
      re-reads the fresh revision and re-applies the delta only when the
      CAS itself lost the race (a genuinely stale caller baseline still
      raises immediately); the race is exercised by
      `TestPostgresCasRetry::test_cas_race_retries_and_commits` against
      the substitute AND the real matrix. (`app/storage/postgres_backend.py`)
- [x] **Pre-admission cancellation loses the run record** — DONE via atomic
      admission: the session and the run record now appear as ONE storage
      step (`admit_run`), so a cancel mid-admission either finds the run
      record (terminal `cancelled` commit) or leaves nothing at all — the
      orphaned-session-until-TTL residual is closed. `_commit_failure`
      still suppresses `SessionNotFound` for the pre-record window.
      (`app/engine/runner.py::_admit`)
- [x] **Non-atomic admission** — DONE: the storage contract gained
      `StorageBackend.admit_run` (ensure-session + create-run in one atomic
      step, returning `(session_id, admit_revision)`) on all four backends:
      redis via ONE Lua script (`ADMIT_RUN`), postgres via one transaction,
      memory/file via a single lock hold. The runner's `_admit` uses it;
      24 shared contract tests (`TestAdmitRun`) pass on the substitutes AND
      the real Redis 7 + Postgres 16 matrix. (`app/storage/contract.py`,
      `app/storage/{redis_backend,postgres_backend,memory,file_backend}.py`,
      `app/engine/runner.py`, `tests/test_storage/test_contract.py`)
- [x] **Live-reload no-op on `server.maxConcurrentRequests` /
      `server.rateLimit`** — DONE: the live-snapshot apply branch now
      re-applies both to the shared objects — `RunSlotGate.set_limit` and
      `FixedWindowLimiter.set_requests_per_minute` (the limiter is stored in
      `components["rate_limiter"]` so the reload can reach it) — so a live
      change takes effect immediately, no rebuild or restart needed.
      (`app/protocol/app.py`, `app/watcher/reload.py`,
      `tests/test_engine/test_rag.py::TestAuditAndReloadCleanup`)

### Test coverage gaps

- [x] **Real-backend CI matrix (ACC-01 deviation)** — DONE: the
      `storage-contract-real` CI job runs the shared contract suite against
      real Redis 7 + Postgres 16 services; the fixtures switch on
      `AGENT_TEST_REAL_REDIS_URL` / `AGENT_TEST_REAL_POSTGRES_DSN` (with
      per-test isolation + connection cleanup). Verified locally:
      **137/137 pass against fresh real services**. The matrix surfaced and
      fixed real production bugs: the redis `eval` call shape (list-numkeys
      is a DataError on redis-py), bytes Lua returns (prefix checks missed),
      the broken `idem:`/`run:` KEYS patterns, wall-clock vs `updated_at`-
      anchored `PEXPIREAT` expiry semantics, fence expiry persistence +
      session-reentrant advisory locks, psycopg JSONB dict rows vs
      `json.loads`, and the uncommitted-implicit-transaction poisoning.
      (`tests/test_storage/conftest.py`, `.github/workflows/ci.yml`)

### Improvements (non-bug)

- [x] **Structured shutdown audit** — DONE: `close_components` returns
      `(ok, failed_component_labels)` and `_drain_after_grace` logs ONE
      `shutdown_summary` line with exit code, duration_ms, and the failed
      components. (`app/lifecycle.py`,
      `tests/test_protocol/test_shutdown.py::TestShutdownSummaryAudit`)
- [x] **Warn on unknown audit events** — DONE: `audit()` logs a
      `audit_unknown_event` warning with the offending name + fields and
      still emits the remapped `audit_unknown` record (nothing is lost).
      (`app/security/audit.py`, `tests/test_engine/test_rag.py::TestAuditAndReloadCleanup`)

---

## Completed phases

P2 (multi-agent + ACP), P3 (approvals), and P4 (RAG) are implemented,
accepted in-image on both architectures, and recorded in
[CHANGELOG.md](CHANGELOG.md) — including the capability flips
(`multiAgent`/`acp`/`approval`/`rag` true in `/health`, phase P4) and the
ACC-01 storage-deviation proofs.

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

- [x] **Product-name clearance RESEARCH (recorded 2026-08-05).** Every
      checkable registry was probed; the provisional name is encumbered
      across the board:
      - PyPI `agentbase` — taken (unrelated "OmniAgents Framework" package);
        PyPI `agent-strata` — free; npm `agentbase` — free.
      - Docker Hub `agentbase` namespace — taken (unrelated `abi-image-v2`).
      - GitHub `agentbase` login — taken (existing user "AgentBase").
      - Domains: `agentbase.com` registered since ~2005 (AgentBase UK, a
        sales-agent register, acquired by Sales Agents Ltd 2026-01);
        `agentbase.io` (AgentBase LLC, staffing); `agentbase.sh` — a
        serverless AI-agent platform (SF, founded 2025) IN THE SAME SPACE.
      - Trademarks: no exact AGENTBASE registration surfaced in USPTO/
        Justia/EUIPO, but the mark is actively used in commerce — including
        Demandbase's "Agentbase" AI-agent product line (PRNewswire 2025-05)
        — so classes 9/42 carry high confusion/opposition risk.
      **Verdict: keep "Agentbase" only as a provisional internal name; a
      rename before any public release is strongly advised.** The GitHub
      repo is already `agentstrata`, and `agent-strata` is clear on PyPI.
- [ ] **Product name DECISION (human call, informed by the clearance above):**
      keep the provisional "Agentbase" and accept the collision risk, or
      rename to "AgentStrata" / `agent-strata` (registry-clear) before any
      public release, trademark filing, or registry publication.

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
