# Backlog — remaining work

Phase 1 (core runtime) milestones 0–8 are implemented; all code work is done.
The completed work is recorded milestone-by-milestone in
[CHANGELOG.md](CHANGELOG.md). This file now tracks only what is **not yet
done**: the image-based M8 exit checks, the unstarted later phases (P2–P4),
resolved decisions (for the record), the one open human-call decision, and
explicitly deferred scope.

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
      (NFR-10). **Status:** feasibility probes recorded in
      `docs/nfr-report.json`; the image-based chaos run is the M8 exit check.
      *Code in place:* API-08a stream backpressure, CNT-07 graceful shutdown.
- [ ] **ACC-01 — full §18 acceptance suite on both architectures.** Must pass
      on `linux/amd64` and `linux/arm64` before P1 is called done.
      **Status:** 324 tests pass on the host; the image-based acceptance run
      (storage per the recorded deviation: memory + file now, redis/postgres
      via in-memory substitutes; real-instance + fencing proof deferred) is the
      M8 exit check.

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
      at grace expiry cancels runs and closes watcher→MCP→storage→OTel then
      stops the listener (exit 0 only if all flush/close succeeds, else 1);
      second signal hard-exits 1; manifests set `terminationGracePeriodSeconds:
      35`. Tests in `tests/test_protocol/test_shutdown.py`.

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
