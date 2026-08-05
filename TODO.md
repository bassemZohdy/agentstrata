# Backlog — remaining work

All phases (P1 core runtime, P2 multi-agent/ACP, P3 approvals, P4 RAG,
P5 extensions — Prometheus /metrics, WebSocket API, Kubernetes CRD/
operator) are implemented and accepted; every completed item — including
the review round (redis `KEYS` elimination, atomic admission, live-reload
caps, the real Redis 7 + Postgres 16 matrix, shutdown audit, unknown-event
warning, product-name clearance) — is recorded in [CHANGELOG.md](CHANGELOG.md).
This file tracks only resolved decisions (for the record).

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

## Deferred scope — RESOLVED (P5, 2026-08-05)

All items cut in the v2.2 scope pass are implemented by user decision on
2026-08-05 — P5-1 Prometheus /metrics, P5-2 WebSocket API, P5-3
Kubernetes CRD/operator, P5-4 per-request cost-in-dollars accounting
(COST-01) — see CHANGELOG.md. There are no remaining open items.

- [x] **WebSocket API — DONE (P5-2, WS-01/02):** `/v1/ws` with the same
      auth as REST, one active run per connection, run.start/cancel +
      approval.decide + ping/pong, SSE-vocabulary push; 9 tests. Recorded
      in CHANGELOG.md.
- [x] **Kubernetes CRD / operator — DONE (P5-3, K8S-11/12):** the
      `agentconfigs.agentstrata.io` CRD (schema generated from the config
      model) + the `k8s_operator` reconciler (ConfigMap/Deployment/Service/
      status, fail-closed on invalid spec or missing image annotation);
      10 tests. Recorded in CHANGELOG.md.
- [x] **Prometheus `/metrics` endpoint — DONE (P5-1, OBS-05):**
      `observability.prometheus.{enabled,path}` + in-process registry +
      route + runner/route/reload recording; 8 tests. The per-request
      cost-in-dollars accounting half remains deferred (token counts are
      reported per API-14). Recorded in CHANGELOG.md.
