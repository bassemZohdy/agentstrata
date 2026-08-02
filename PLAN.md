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
- Bounded parsing before buffering/decoding, tool-count/name/schema limits (MCP-08).
- Tool filtering, collision-safe renaming, redacted/truncated results (MCP-03, MCP-04).
- stdio sandboxing (`shell=False`, minimal inherited env) and call-outcome/cancellation semantics (MCP-06, MCP-07).

**Exit check:** connect/reconnect, every documented bound, and tool-name collision handling are proven against the official MCP SDK for each transport — not a hand-written fake alone (per §18).

## Milestone 5 — API surface and security

- Health/metadata endpoints (API-01 – API-04).
- OpenAI-compatible chat: request validation, state rules, idempotency, non-streaming and streaming responses, error mapping (API-05 – API-08a, API-12 – API-20).
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
