# Implementation plan

This is the build order for turning [REQUIREMENTS.md](REQUIREMENTS.md) into a working runtime. It's the "how and in what order" counterpart to the spec's "what" — internal code layout, milestone sequencing, and engineering tradeoffs belong here, not in REQUIREMENTS.md.

Milestones are ordered by dependency, not by section number: config has to exist before anything can read it, storage has to exist before the engine can persist a run, and so on. Each milestone lists the requirement IDs it must satisfy so progress stays traceable back to the spec.

Phase 1 (core runtime) is the only phase planned in detail below. Phases 2–4 are sequenced at the end but not broken into milestones until P1 ships — see [TODO.md](TODO.md).

Every milestone below is broken into a checked task list in TODO.md under the same name. This file is the rationale (why this order, what "done" means); TODO.md is the actionable checklist. Keep both in sync when scope shifts.

**Cross-cutting rule (DEL-02):** `schemas/agent.schema.json`, `schemas/agent-overlay.schema.json`, and `openapi.json` are always generated from source, never hand-edited. Bake a regeneration+diff check into CI as each artifact is introduced — `agent.schema.json` in Milestone 1, `agent-overlay.schema.json` in Milestone 6, `openapi.json` in Milestone 5 — rather than retrofitting it once all three exist.

**Cross-cutting rule (NFR-*):** most of §6's non-functional requirements (NFR-00 – NFR-10) are verified together as one benchmark/chaos run against the finished image (Milestone 8's exit check), not milestone-by-milestone. Three are worth checking the moment their milestone lands instead of waiting: NFR-05 (deterministic config resolution) at Milestone 1, NFR-06 (OpenAI SDK compatibility range) at Milestone 5, and NFR-08 (zero-downtime reload) at Milestone 6.

**Cross-cutting rule (STACK-01):** dependency manifests and locks live at the repo root — `requirements.txt` (direct compatible ranges) + `requirements.txt`/`requirements-dev.txt` union locked into `requirements.lock` / `requirements-dev.lock` (exact versions + hashes, universal so one lock serves the linux/amd64 and linux/arm64 image builds, resolved for Python 3.12 to match the runtime). Regenerate with `scripts/compile-lock.sh`; CI runs `scripts/verify-lock.sh` (hash-pinned dry-run) on every change. A library upgrade that changes a documented API shape or lifecycle is a requirements-impacting change (STACK-01) and must go through the same review as a REQUIREMENTS.md change, not land as an automatic dependency bump.

## Milestone 0 — Project bootstrap

- Repository skeleton, dependency manifests, and lockfile tooling (STACK-01, STACK-02).
- Minimal Dockerfile that builds and runs an empty FastAPI app with a single Uvicorn worker (CNT-01, CNT-04, CNT-05, CNT-06, CNT-08).
- CI skeleton: lint, type-check, lockfile hash verification.

**Exit check:** `docker build` produces a running (but functionally empty) container.

## Milestone 1 — Configuration engine

- Pydantic v2 schema for the full Agent Definition, including phase-gated sections with disabled defaults (SCH-01 – SCH-09, BASE-01).
- Tier 1–7 resolver: file discovery, deep-merge, list replacement, null-reset, relaxed env binding, CLI flags (CFG-01 – CFG-11a).
- Validation pipeline: schema + cross-field + capability gating, in the order CFG-15 specifies (CFG-12 – CFG-14, CAP-01, CAP-02).
- `--validate`, `--dump-config`, `--version`, `--help` CLI surface.
- Operational-mode selection (standalone vs. Kubernetes-watcher intent) — tier 8 itself lands in Milestone 6 (MODE-01 – MODE-04).

**Exit check:** given any combination of bundled/mounted/env/CLI config, the resolver produces a deterministic, validated `AgentConfig` or a clear aggregate error — with no engine, storage, or API code written yet.

## Milestone 2 — Storage and sessions

- Session/run/idempotency data model and the common backend contract (SES-01 – SES-03, SES-06 – SES-08).
- `memory` backend first (simplest; unblocks engine work in Milestone 3 without needing Redis/Postgres running).
- `file` backend (atomic write/fsync/replace, symlink rejection).
- `redis` and `postgres` backends, including the session-fencing mechanism (SES-04, SES-05).
- ADK session-service adapter so ADK and the runtime share one revisioned history (SES-09).

**Exit check:** the shared contract test suite (REQUIREMENTS.md §18) passes identically against all four backends; Redis/PostgreSQL additionally pass the fencing/multi-replica proof.

## Milestone 3 — Engine execution

- ADK `LlmAgent` construction per configured provider, including the LiteLLM bridge and Vertex AI path (STACK-01, LLM-01 – LLM-03, ENG-01).
- `AgentRunner` façade over `Runner.run_async` with the internal `AgentEvent` union (ENG-02).
- Admission pipeline in the exact order ENG-03 specifies — auth and rate limiting can be stubbed until Milestones 4–5 land, but the ordering contract should be enforced from the start so it isn't retrofitted later.
- Context-window pruning, run state machine, transactional persistence, iteration/token/output limits, tool-call-ID deduplication (ENG-04 – ENG-10).

**Exit check:** a run executes end-to-end against a mock model connector with exactly-one-terminal-outcome semantics, correct cancellation/timeout behavior, and no duplicated tool side effects — verified by the state-machine/property tests implied by ACC-01.

## Milestone 4 — MCP tool integration

- `McpToolset` wiring for stdio, Streamable HTTP, and legacy SSE transports (SCH-04, MCP-01, MCP-05).
- Bounded parsing before buffering/decoding, tool-count/name/schema limits (MCP-08). **Phased per REQUIREMENTS.md 2.5:** the `maxTransportMessageBytes` pre-parse cap is enforced on Streamable HTTP and legacy SSE via the `httpx_client_factory`/`http_client` seam (verified in the M0 STACK-02 spike); the stdio pre-parse cap is deferred until a google-adk release supports the mcp 2.x `Transport` seam — stdio connect/reconnect/limits are still proven per §18, minus the byte cap.
- Tool filtering, collision-safe renaming, redacted/truncated results (MCP-03, MCP-04).
- stdio sandboxing (`shell=False`, minimal inherited env) and call-outcome/cancellation semantics (MCP-06, MCP-07).

**Exit check:** connect/reconnect, every documented bound, and tool-name collision handling are proven against the official MCP SDK for each transport — not a hand-written fake alone (per §18).

## Milestone 5 — API surface and security

- Health/metadata endpoints (API-01 – API-04).
- OpenAI-compatible chat: request validation, state rules, idempotency, non-streaming and streaming responses, error mapping (API-05 – API-08a, API-12 – API-20). **STACK-02 finding (M0):** uvicorn 0.52.1's `h11_max_incomplete_event_size` bounds the incomplete request-line+headers buffer pre-allocation, but over-limit requests get 400/abort (never 414/431) and there is no header-count cap. `uvicorn.Config(http=...)` accepts a custom protocol class — M5 ships a small h11 subclass that enforces request-line/header-count caps and maps `error_status_hint` to 414/431; httptools is not used for the bounded path.
- Session management endpoints (API-09).
- Auth modes — none, API key, JWT/JWKS — wired into the admission pipeline from Milestone 3 (SEC-01, SEC-03, SEC-08).
- Recursive secret redaction, CORS, egress restriction, proxy handling, audit events, response hardening (SEC-02, SEC-05, SEC-06, SEC-09 – SEC-11).

**Exit check:** golden fixtures for every route/error code pass, including the published min/max OpenAI SDK compatibility range (NFR-06); security controls hold under adversarial input (§18).

## Milestone 6 — Kubernetes watcher and reload

- ConfigMap watch (initial list, resourceVersion watch, 410 handling, resync) (K8S-01 – K8S-03).
- Reload categorization and transactional apply with rollback (REL-01 – REL-06).
- RBAC and Deployment/Service manifests (K8S-08).

**Exit check:** every schema field's reload category behaves correctly, including no-op detection and rollback on a failed component rebuild.

## Milestone 7 — Observability

- Structured logging with request/run/principal correlation (OBS-01 – OBS-03).
- OpenTelemetry traces and metrics, fully skippable when disabled (OBS-04 – OBS-06).

**Exit check:** required correlation fields are present across concurrent requests; disabling OTel removes its import/runtime cost.

## Milestone 8 — Container hardening and release packaging

- Non-root arbitrary-UID support, read-only rootfs, graceful shutdown, HEALTHCHECK (CNT-02, CNT-03, CNT-07, CNT-10, CNT-11).
- Multi-arch build, SBOM generation, vulnerability scanning, provenance/signing (CNT-12, CNT-13).
- `docker-compose.yaml` wiring the runtime, Redis, Postgres, and one sample MCP server (CNT-09).
- Deployment/configuration documentation covering every supported auth/storage option (DEL-01).

**Exit check:** both architectures build and pass the full acceptance suite in REQUIREMENTS.md §18; release evidence (image digest, commit, test results) is captured per TRC-01/TRC-02.

## After P1 ships

- **P2 — Multi-agent** (§13): sub-agent hierarchies, ADK transfer routing, shared budget/cancellation, ACP REST surface (API-16).
- **P3 — Human-in-the-loop** (§14): durable approval checkpoints, decision race handling, restart reconciliation.
- **P4 — RAG** (§15): document ingestion, chunking, retrieval-scoped context injection.

Each phase gets its own milestone breakdown in this file once P1's acceptance criteria pass — see [TODO.md](TODO.md) for what's tracked in the meantime.

---

## Phase 2 — Multi-agent and ACP (P2, §13 + API-16)

P1's §18 acceptance criteria pass; P2 is independently releasable per PHASE-01.
The API-16 acceptance annex (frozen in REQUIREMENTS.md §13.1) is the normative
contract for the ACP surface; nothing below contradicts it.

**Cross-cutting:** the `agents` section stays fail-closed (CAP-01) until the P2
acceptance suite passes; the capability flip (`multiAgent`/`acp` true in
`/health`) is the LAST commit of the phase (CAP-02). `agents` is classified as
component-rebuild in REL-02 (already present).

## Milestone P2-1 — Schema and capability gating (MA-01)

- Full `agents[]` field contract in the Pydantic schema: DNS-label `name`
  (unique, distinct from root), `systemInstruction` (required, non-empty),
  `description` ≤ 2 000 code points, optional `llm` block (deep-merged over
  the root's), `toolServers: list[str]` defaulting to every configured MCP
  server.
- Cross-field validation: flat one level only, no nested/cyclic definitions,
  every `toolServers` reference exists; root name distinctness.
- Schema/overlay regeneration + CI zero-diff (DEL-02).

**Exit check:** an `agents` definition with any violation is rejected with a
stable aggregate error; a valid one parses; the generated `agent.schema.json`
carries the new fields.

## Milestone P2-2 — Construction and routing (MA-02)

- `build_agent_component`: with a non-empty `agents` list, the root becomes an
  ADK coordinator carrying `sub_agents` in configured order; routing via ADK's
  native transfer informed by name/description. Empty list keeps P1 behavior
  and public fixtures byte-identical.
- All agents share the run's principal, session adapter, cancellation,
  deadline, iteration counter, request/session budget, and Applied Config
  generation (one `AgentRunner`/limits object per run).

**Exit check:** a routed run with two sub-agents executes a transfer and
returns; a single-agent config yields the P1 fixture output unchanged.

## Milestone P2-3 — Tool isolation (MA-03)

- Sub-agent toolsets are built from `toolServers`-named servers only, AFTER the
  shared MCP filter/collision mapping (final names). The coordinator's hidden
  tools are not visible to sub-agents; no direct cross-agent calls except via
  transfer; transfer does not change principal or reset budgets.

**Exit check:** a sub-agent with `toolServers: ["a"]` sees only server a's
(final, renamed) tools; a transfer target cannot see the coordinator's tools.

## Milestone P2-4 — Transfer events and audit (MA-04)

- `agent_transfer` events (`{"type":"agent_transfer","from","to"}`) in
  event/debug streams only; text mode stays text-only.
- Transfers stored in the run audit, never as user-visible session messages
  (session replay must not feed transfers back to the model).
- Transfer to an unknown/unavailable agent fails the run with
  `provider_error`; no silent fallback.

**Exit check:** event-stream tests observe the transfer event shape; replay
fixtures show no transfer in the session history; unknown-target runs end
with `provider_error`.

## Milestone P2-5 — ACP REST surface (API-16 + frozen annex §13.1)

- `GET /acp/agents` manifest and `POST /acp/runs` (SSE streaming + non-
  streaming) per the frozen annex schemas; auth/session/idempotency/error
  behavior per the annex. No 501 stubs.
- `server.protocols.acp` gates route registration; disabled = ordinary 404
  (API-00).

**Exit check:** annex golden fixtures pass over the real HTTP surface
(non-streaming + streaming), including the error table.

## Milestone P2-6 — Reload and acceptance (MA-05)

- `agents` as component-rebuild with transactional apply/rollback and
  in-flight run safety (the P1 reload machinery already rebuilds the runner;
  verify sub-agent configs rebuild cleanly).
- Full MA-05 acceptance suite: deterministic construction, routing fixtures,
  tool isolation, shared limits/cancellation, transfer events, session
  replay, reload with in-flight runs, and the single-agent regression suite.
- Capability flip (CAP-02): `multiAgent` and `acp` true in `/health` only
  after the suite passes; P1's fail-closed tests re-baselined (P1's
  forbidden cases become P2's accepted cases).

**Exit check:** the full P1 regression set + the P2 suite pass on the host and
in the image on both architectures; docs/TODO/CHANGELOG updated; release
evidence recorded per TRC-02.

---

## Phase 3 — Human-in-the-loop approvals (P3, §14)

P2's §18 acceptance criteria pass; P3 is independently releasable per
PHASE-01. The capability flip (`approval` true in `/health`) is the LAST
commit of the phase (CAP-02); `approval` is classified as component-rebuild
in REL-02.

## Milestone P3-1 — Schema and gating (HITL-01)

- Full `approval` field contract in the Pydantic schema: `enabled`, `tools`
  (patterns over final tool names), `timeoutSeconds`, `onTimeout`
  (deny|allow). Fail-closed cross-field rules: approval requires an
  auth mode other than `none` and a redis/postgres storage type; explicit
  `onTimeout: allow` is accepted only with a boot audit entry. Capability
  gating stays fail-closed (`approval` false in `/health`) until the suite
  passes.

**Exit check:** schema + validation tests pass; `approval.enabled` under
`none` auth or memory/file storage is rejected with a stable error.

## Milestone P3-2 — Durable checkpoints (HITL-02)

- `ApprovalRecord` on all four backends: the public surface is the args
  hash + redacted preview; the protected checkpoint (exact args, tool-call
  ID, session/run/principal) is never returned by any API. Memory/file/
  redis (Lua CAS + global index)/postgres (`agent_approvals` table)
  implementations share one contract suite.

**Exit check:** the shared approval contract tests pass on all four
backends (memory + file + the recorded redis/postgres substitutes per the
ACC-01 deviation).

## Milestone P3-3 — Engine gate + decision races (HITL-04)

- The engine pauses BEFORE a matched tool executes: `RunState
  .AWAITING_APPROVAL`, `ApprovalRequired` event, checkpoint committed
  durably. `resume_approval` is a CAS decide (first wins): approve executes
  the tool from the checkpoint via a minimal ADK `ToolContext` reusing the
  ORIGINAL tool-call ID, injects the function response into the session,
  and continues the conversation to a terminal event; deny/timeout return
  structured outcomes and the tool never runs. The gate skips calls with a
  resolved approval (resumed replays do not double-gate or duplicate side
  effects).

**Exit check:** approve-and-continue, deny, race-loss, and repeat-decision
tests pass; no path executes the tool twice for one call ID.

## Milestone P3-4 — Client surface (HITL-03)

- Chat is stateful-only while approval is enabled (400
  `approval_session_required`); non-streaming pauses detach with 202
  `run.pending_approval` (the sole API-08a exception); SSE emits
  `approval_required` then `[DONE]`. `POST /v1/approvals/{id}` (repeat
  decision -> stored outcome, conflicting -> 409, expired -> 410),
  `GET /v1/approvals?session_id=` (pending-only, public metadata),
  `GET/DELETE /v1/runs/{id}` (owner-scoped state + idempotent cancellation
  that cancels the pending approval).

**Exit check:** the endpoint suite passes over the real HTTP surface,
including the error table.

## Milestone P3-5 — Restart reconciler (HITL-05)

- Startup + periodic reconcile: expired pendings follow the onTimeout
  policy (deny finishes the run denied; allow resumes only after the same
  stale/cancellation checks); decided-while-down approvals resume exactly
  once (the deterministic resume run guards re-entry); pendings from a
  retired config generation terminate `stale_approval` and the tool MUST
  NOT execute. Reconciliation failures never block boot.

**Exit check:** stale/deny/allow/decided-while-down tests pass; a second
reconcile is a no-op.

## Milestone P3-6 — Acceptance (CAP-02)

- Capability flip: `approval` true in `/health`, phase `P3`; P1's
  fail-closed tests re-baselined. Docs/TODO/CHANGELOG updated; traceability
  regenerated (HITL-01..05); image acceptance on both architectures.

**Exit check:** the full regression set + the P3 suite pass on the host and
in the image on both architectures; release evidence recorded per TRC-02.

---

## Phase 4 — RAG / long-term memory (P4, §15)

P3's §18 acceptance criteria pass; P4 is independently releasable per
PHASE-01. The capability flip (`rag` true in `/health`, phase `P4`) is the
LAST commit of the phase (CAP-02); the store/embedding connectors follow
the recorded ACC-01 deviation (acceptance runs the memory substitute; the
real chroma/pgvector/gemini/openai drivers are import-guarded shells whose
real-instance proofs are deferred like the redis/postgres ones).

## Milestone P4-1 — Schema and gating (RAG-01)

- `rag` field contract: `required`, `store {type chroma|pgvector,
  connectionStringEnv/File (SEC-04), collection DNS-1123, options
  passthrough}`, `embedding {provider gemini|openai, model,
  apiKeyEnv/File}`, `topK 1..100`, `minScore 0..1`, `chunkChars`,
  `chunkOverlapChars < chunkChars`, `maxDocumentBytes` (default 10 MiB).
  Capability fail-closed until the suite passes.

**Exit check:** schema + constraint tests pass; `rag.enabled` under a P3
build is rejected with a stable error.

## Milestone P4-2 — Retrieval engine (RAG-02)

- Chunk keys by agent/principal/doc/chunk/embedding model/content hash;
  principal-scoped retrieval; ≤topK chunks before the root LLM call,
  sorted descending score then stable chunk id, minScore filter; one
  delimited context message after the system instruction, explicitly
  labeled untrusted knowledge. MemoryRagStore substitute + import-guarded
  chroma/pgvector/gemini/openai shells (ACC-01 deviation).

**Exit check:** chunking/tenancy/ranking/context-injection tests pass; the
model receives the labeled context in the runner integration test.

## Milestone P4-3 — Ingestion API (RAG-03)

- `POST /v1/documents` (id syntax, text bounded by maxDocumentBytes,
  metadata ≤ 64 KiB scalar-only, Idempotency-Key replay, 201 with
  id/chunk count/content hash), `GET /v1/documents/{id}` (metadata/count/
  hash only — never the stored text), `DELETE /v1/documents/{id}` (204
  idempotent). Atomic upsert: embedding failure leaves the previous
  version intact. Registered only when rag is enabled.

**Exit check:** the endpoint suite passes over the real HTTP surface,
including the error table and the failure-never-silent case.

## Milestone P4-4 — Availability (RAG-04)

- Optional: unavailable store logs ONE redacted error, emits
  `rag_degraded` only in events/debug streams, answers without context,
  readiness stays 200. Required: readyz 503 and the run fails
  `rag_unavailable`. Ingestion never degrades silently.

**Exit check:** required/optional failure-recovery tests pass at the
runner and the HTTP surface.

## Milestone P4-5 — Lifecycle/security (RAG-05)

- Any rag identity change (store/embedding/chunk fields) is a component
  rebuild (REL-02) — no silent re-embed of old documents; delete removes
  every scoped chunk; SEC-04 Env/File secrets; SEC-02 passthrough
  redaction; document content excluded from logs/traces; backups/
  retention are deployment responsibilities (documented).

**Exit check:** rebuild classification + redaction tests pass.

## Phase 5 — Extensions (P5, 2026-08 scope decision)

User decision 2026-08-05: implement the three deferred-scope items —
Prometheus /metrics, a WebSocket API, and a Kubernetes CRD/operator.
Each lands as its own milestone with its own acceptance criteria and a
commit; the phase flips CAP-02 reporting to P5 once all three ship.

## Milestone P5-1 — Prometheus /metrics (OBS-05)

- In-process registry (counters/gauges/histograms, text exposition
  0.0.4, per-metric label-set cap 128) on the Observability facade;
  instruments recorded by the runner (admit/terminal/tokens/tool calls/
  active runs), the chat route + rate-limit middleware (denials,
  output-queue cancellations), and the reload manager (outcomes).
- Config: `observability.prometheus.{enabled,path}` (default
  "/metrics"); cross-field validation rejects built-in-route collisions.
- REQUIREMENTS: OBS-05 rewritten (route is now in scope), §1.4 updated;
  schemas + traceability regenerated; deployment.md documents scraping.

**Exit check:** registry + route tests pass; a chat run is visible on
`/metrics`; rate-limit denials recorded; disabled build has no route;
gates green; commit.

## Milestone P5-2 — WebSocket API (WS-01)

- `server.protocols.websocket` (phase-gated like acp); `/v1/ws` upgrade
  route with the same auth as the REST surface (token via query or
  Authorization header).
- Bidirectional protocol: client `run.start`/`run.cancel`/
  `approval.decide`/`ping`; server push `run.delta`/`run.done`/
  `run.cancelled`/`approval.required`/`error`/`pong`. One active run per
  connection; events identical to the SSE vocabulary.
- REQUIREMENTS annex WS-01 (protocol + limits); schemas + traceability
  regenerated; deployment.md documents the endpoint.

**Exit check:** WS tests pass (start/cancel/approve/deny round-trips
over the mock model); auth enforced; gates green; commit.

## Milestone P5-4 — Cost accounting (COST-01)

- `costs` config (enabled + USD-per-1M defaults and per-model overrides;
  duplicate/negative validation); runner computes per-request cost when
  enabled; recorded in the run outcome + committed usage; surfaced as
  `usage.costUsd`; OBS-05 `agentbase_cost_usd_total{model}` counter.
- REQUIREMENTS: §1.4 deferral removed, API-14 amended, COST-01/02;
  schemas + traceability regenerated; deployment.md.

**Exit check:** cost unit + surface + metric + validation tests pass;
disabled builds show zero cost fields; gates green; commit.


## Milestone P5-3 — Kubernetes CRD / operator (K8S-01)

- CRD `agentconfigs.agentstrata.io` (validation schema embedded from the
  generated config schema); a Python operator watches AgentConfig CRs,
  reconciles the named ConfigMap the existing watcher consumes +
  Deployment/Service, writes status (observedGeneration, ready,
  appliedResourceVersion), and cleans up via ownerReferences.
- Tests against a fake k8s client (same pattern as the watcher tests);
  manifests + deployment.md operator section.

**Exit check:** CRD manifest validates; reconcile tests pass
(create/update/delete + status); gates green; commit.


## Milestone P4-6 — Acceptance (CAP-02)

- Capability flip: `rag` true in `/health`, phase `P4`; earlier
  fail-closed tests re-baselined. Docs/TODO/CHANGELOG updated;
  traceability regenerated (RAG-01..06); image acceptance on both
  architectures.

**Exit check:** the full regression set + the P4 suite pass on the host and
in the image on both architectures; release evidence recorded per TRC-02.
