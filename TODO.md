# Backlog — remaining work

All four phases (P1 core runtime, P2 multi-agent/ACP, P3 approvals, P4 RAG)
are implemented and accepted; the completed work — including every review
item (redis `KEYS` elimination, atomic admission, live-reload caps, the real
Redis 7 + Postgres 16 matrix, shutdown audit, unknown-event warning, product-
name clearance research + decision) — is recorded in
[CHANGELOG.md](CHANGELOG.md). This file tracks only what is **not yet done**:
resolved decisions (for the record) and explicitly deferred scope.

Requirement IDs in parentheses trace each task back to
[REQUIREMENTS.md](REQUIREMENTS.md); the build order is
[PLAN.md](PLAN.md). "Deferred scope" is not part of any phase's critical
path and must not be reopened speculatively.

---

## Completed work (pointer)

The full review round is resolved and the product-name decision is closed —
see CHANGELOG.md ("Review-item cleanup", "Deferred-review items completed",
"Product-name clearance research", "Product name decision RESOLVED"). The
key facts on record there:

- **Redis `KEYS` in Lua** — eliminated (per-session ZSET indexes +
  non-blocking SCAN sweep); verified 137/137 on real Redis 7.
- **Atomic admission** — `StorageBackend.admit_run` on all four backends
  (single Lua script / single txn / single lock hold); the pre-admission
  cancellation residual is closed; 24 shared `TestAdmitRun` tests pass on
  substitutes AND the real matrix (161/161).
- **Postgres CAS retry, live-reload cap/limiter re-application, structured
  shutdown summary, unknown-audit-event warning, real-backend CI matrix**
  — all DONE (details + test names in CHANGELOG).
- **Product name** — open-source, non-commercial (user decision 2026-08-05):
  no trademark/domain clearance required; name stays "Agentbase"/
  "AgentStrata". Clearance research + candidate check remain on record;
  `agent-strata` is the registry-clear fallback if the project ever turns
  commercial.

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

## Deferred scope — revisit only if a concrete need shows up

Cut in the v2.2 scope pass. Don't reopen speculatively; reopen when an actual
caller or deployment needs one.

- [ ] **WebSocket API.** IN PROGRESS (P5, user decision 2026-08-05): the
      `/v1/ws` surface ships in P5-2 (WS-01: auth, one active run per
      connection, run.start/cancel + approval.decide + ping, SSE-vocabulary
      push; WS-02 acceptance tests).
- [ ] **Kubernetes CRD / operator.** Revisit once the product name/API-group
      is settled (DONE 2026-08-05 — the name stays as-is) and there's a real
      need for `kubectl get agentconfigs`, CRD-native status, or
      admission-webhook validation.
- [ ] **Prometheus `/metrics` endpoint and per-request dollar-cost accounting.**
      IN PROGRESS (P5, user decision 2026-08-05): the endpoint ships in
      P5-1 (OBS-05 rewritten; `observability.prometheus.{enabled,path}`;
      in-process registry + route + runner/route/reload recording). The
      per-request cost-in-dollars accounting half stays deferred (token
      counts are reported per API-14).
