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

- [x] **NFR-00 — full §6 benchmark/chaos suite.** `docs/nfr-report.json`
      records the complete image-based run (harness `scripts/image-nfr.py`, 1
      CPU / 512 MiB, mock model via the real LiteLLM bridge). **6 gates pass:**
      NFR-01 startup (p95 2.99 s ≤ 5 s, 20 fresh starts), NFR-03 concurrency
      (100 held streaming runs, 145 events/run ≥ 1/s, 101st → 503 `overloaded`,
      peak 304 MB < 512 MiB), NFR-04 footprint (115 MB ≤ 300 MB, 5 samples),
      NFR-07 boundedness (repeated slow/disconnect rounds plateau — last-round
      growth ≤ 8 MiB after a 154 MB first-round warm-up; peak 271 MB),
      NFR-09 dependency recovery (required MCP server down-at-start holds
      readyz 503 and recovers within the reconciler's bounded retry; Redis
      kill → 503 → recovery; file-backed secret rotation recovers on the next
      request), NFR-10 arm64 portability (boots + both chat modes; QEMU
      emulation makes cold boots ~35 s — the 5 s startup gate is amd64-scoped
      per NFR-00). **NFR-02 FAILS as measured** (p95 147 ms vs < 50 ms): the
      spec's gate assumes an in-process deterministic mock result, while the
      harness measures end-to-end through the real LiteLLM bridge + a
      localhost mock; the recorded breakdown shows raw server overhead
      (healthz/models) is ~2 ms and a single chat round trip ~13 ms, with an
      ADK per-run scheduling tail under concurrency 10. Recorded honestly in
      the report with the environment notes; closing options are (a) an
      in-process mock connector in the image for this one gate, (b) a
      server-only measurement, or (c) a threshold revision — all spec/
      harness decisions for the release gate, not code bugs.

> ACC-01 (§18 acceptance, 339/339 on both architectures, current code) and
> NFR-08 (zero-downtime reload: live 1→2, rebuild 2→3, 0 failed requests, no
> restart) are **done** — see [CHANGELOG.md](CHANGELOG.md) and
> `docs/acceptance-{amd64,arm64}.{log,json}` / `docs/nfr-report.json`.

- [x] **NFR-08 — zero-downtime reload verification.** PASS against the
      running image: a live-snapshot update (observability.logLevel) and a
      component-rebuild update (engine.systemInstruction) both advanced the
      config generation (1→2, 2→3) with **0 failed admitted requests, 0
      readyz 503s, and no listener restart** (PID and StartedAt stable),
      exercised through the real tier-8 path (a controlled K8s ConfigMap API
      + the runtime's real watcher/kubeconfig client). Recorded in
      `docs/nfr-report.json` (nfr08_reload). Enabled by five production
      fixes the probe surfaced: engine streaming_mode, the watcher never
      being started, the MCP reconcilers never being started, the rebuild
      swap wiping manager-owned singletons, and the reload-builder backend
      binding.

### Milestone 8 — Supply chain (CNT-12/13) evidence

- [x] **SBOM** — CycloneDX (`docs/supplychain/sbom-agentbase-amd64.cdx.json`,
      3 097 components) + SPDX (`sbom-agentbase-amd64.spdx.json`, 184
      packages).
- [x] **Vulnerability scan** — trivy CRITICAL/HIGH: **23 OS-level findings in
      the pinned base image with NO available fix yet** (debian trixie;
      `python:3.12-slim` @ the current latest digest). Per the recorded CNT-12
      policy this is **release-blocking until Debian ships fixes** — re-run
      the scan and bump the base digest when they land. The scan's 2 fixable
      python findings (pip's vendored msgpack/setuptools) were eliminated by
      removing pip from the runtime image (CNT-01/12) — re-scan shows 0
      fixable python findings. Details in `docs/supplychain/README.md`.
- [x] **Build provenance** — buildx `--provenance=true` OCI layout with an
      in-toto SLSA v1 attestation (blob `sha256:961fbf3d…`; buildkit
      slsa-definitions buildType, subject digest, resolved dependencies).
- [x] **Keyless signing** — `.github/workflows/release.yml` (cosign keyless
      via GitHub OIDC on `v*` tag push, signing the image digest + SBOM/
      provenance attestations). Keyless signing cannot run from a local
      machine (no OIDC identity) — the CI workflow is the signing step of
      record; local evidence is the SBOM/provenance/canary artifacts.
- [x] **Canary-secret scan (CNT-13)** — `scripts/canary-scan.py` passed:
      no forbidden paths or canary content in layers, no secret patterns in
      history, and a `.dockerignore`-excluded canary file never reaches a
      built image.

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

- [x] **P2 — Multi-agent** (§13 + API-16): **DONE** — milestone
      breakdown in PLAN.md (§13.1 ACP annex frozen in REQUIREMENTS.md);
      task breakdown below.
- [x] **P3 — Human-in-the-loop** (§14): **DONE** — milestone breakdown in
      PLAN.md; task breakdown below.
- [ ] **P4 — RAG / long-term memory** (§15): document ingestion, chunking,
      retrieval-scoped context injection.

### P3 — Human-in-the-loop (HITL-01..05, §14)

- [x] **P3-1 Schema + gating (HITL-01):** `approval` field contract
      (enabled/tools/timeoutSeconds/onTimeout deny|allow); fail-closed
      cross-field rules (approval requires auth + redis/postgres storage);
      boot audit for explicit `onTimeout: allow`.
- [x] **P3-2 Durable checkpoints (HITL-02):** `ApprovalRecord` on all four
      backends (memory/file/redis Lua CAS + global index/postgres table);
      public surface = args hash + redacted preview; protected checkpoint
      holds the exact resume arguments; 24+ shared contract tests.
- [x] **P3-3 Decision races (HITL-04):** CAS decide first-wins; approve
      executes the tool from the checkpoint reusing the ORIGINAL tool-call
      ID, injects the function response, and continues the conversation;
      deny/timeout return structured outcomes; the gate skips calls with a
      resolved approval (no double gating, no duplicated side effects).
- [x] **P3-4 Client surface (HITL-03):** stateful-only chat while enabled
      (400 `approval_session_required`); non-streaming pause -> 202
      `run.pending_approval` (the sole API-08a exception); SSE emits
      `approval_required` then `[DONE]`; `POST /v1/approvals/{id}` (repeat
      -> stored outcome, conflict -> 409, expired -> 410); `GET
      /v1/approvals?session_id=` pending-only public metadata; `GET/DELETE
      /v1/runs/{id}` owner-scoped state + idempotent cancellation.
- [x] **P3-5 Restart reconciler (HITL-05):** startup + periodic reconcile;
      stale approvals (config generation changed) terminate `stale_approval`
      and never execute the tool; timeout follows onTimeout policy with the
      same stale/cancellation checks; decided-while-down approvals resume
      exactly once (deterministic resume run guard).

### P2 — Multi-agent and ACP (MA-01..05 + API-16, annex §13.1)

- [x] **P2-1 Schema + gating (MA-01):** `agents[]` field contract (DNS-label
      unique `name` distinct from root, required non-empty `systemInstruction`,
      `description` ≤ 2 000 code points, optional `llm` deep-merged over
      root, `toolServers` defaulting to every MCP server); flat one level,
      nested/cyclic rejected, every tool-server reference exists; schema
      artifacts regenerated with CI zero-diff.
- [x] **P2-2 Construction + routing (MA-02):** root becomes an ADK coordinator
      with `sub_agents` in configured order; ADK native transfer routing by
      name/description; empty list retains P1 behavior and public fixtures;
      shared principal/session/cancellation/deadline/iteration/budget/
      generation.
- [x] **P2-3 Tool isolation (MA-03):** sub-agents get only
      `toolServers`-named toolsets AFTER MCP filter/collision mapping (final
      names); no coordinator hidden tools; no direct cross-agent calls except
      transfer; transfer grants no new principal/budget.
- [x] **P2-4 Transfer events + audit (MA-04):** `agent_transfer` events in
      event/debug streams only; transfers in the run audit, never user-visible
      session messages; unknown/unavailable target fails the run with
      `provider_error`, no silent fallback.
- [x] **P2-5 ACP surface (API-16, annex §13.1):** `GET /acp/agents` +
      `POST /acp/runs` per the frozen annex (auth, session, idempotency,
      error schemas); ACP-disabled = ordinary 404; no 501 stubs.
- [x] **P2-6 Reload (MA-05):** `agents` component-rebuild with transactional
      apply/rollback + in-flight run safety.
- [x] **P2-7 Acceptance + capability flip (MA-05, CAP-02):** deterministic
      construction, routing fixtures, tool isolation, shared
      limits/cancellation, transfer events, session replay, reload with
      in-flight runs, single-agent regression suite; `multiAgent`/`acp` true
      in `/health` only after the suite passes; P1 fail-closed tests
      re-baselined; docs updated.

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
