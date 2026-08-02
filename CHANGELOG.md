# Changelog

This project has no released code yet — everything below documents the evolution of [REQUIREMENTS.md](REQUIREMENTS.md), the authoritative specification. Once implementation begins, this file will track runtime releases (tied to image digests, per REQUIREMENTS.md TRC-02) the same way.

## [Unreleased]

Nothing shipped yet. See [PLAN.md](PLAN.md) for the current build order and [TODO.md](TODO.md) for the backlog.

### Added

- **Milestone 0 bootstrap (complete):** repository skeleton — `app/` package layout for the config/engine/storage/protocol/security/watcher concerns (DEL-01, free-form but independently testable; `app.main`/`app.healthcheck` fixed paths reserved), `.gitignore`, `.gitattributes`, and a `schemas/` area for generated artifacts (DEL-02).
- **Dependency manifests (STACK-01):** `requirements.txt` (direct compatible ranges) + `requirements-dev.txt`, locked with `uv pip compile` into `requirements.lock` / `requirements-dev.lock` (exact versions + hashes, universal, Python 3.12); `scripts/compile-lock.sh` / `scripts/verify-lock.sh`; review flow documented in PLAN.md.
- **STACK-02 feasibility spike (M0):** ADK session/event lifecycle — GO (public seams, e2e smoke); McpToolset lifecycle — GO on mcp 1.29.0, broken on mcp 2.0.0; MCP-08 bounded-read seam — GO for HTTP/SSE (httpx injection), no stdio seam; uvicorn 0.52.1 API-20 — partial (414/431 + header-count cap planned via custom protocol class at M5). Resulted in REQUIREMENTS.md v2.5 phase note (stdio pre-parse cap deferred per user decision) and the `mcp>=1.24,<2` pin.
- **Minimal Dockerfile (CNT-01/04/05/06/08):** digest-pinned `python:3.12-slim`, multi-stage, hash-verified venv install, exec-form `ENTRYPOINT`, one worker, no reload; `.dockerignore`. Exit check passed: running container serving on 8080.
- **CI skeleton:** lint/type-check/lockfile-hash/placeholder-test jobs (SHA-pinned actions, read-only permissions, zizmor clean).
- **LICENSE placeholder only** — no license/trademark decision (working name "AgentStrata" pending clearance; see TODO.md "Open decisions").
- [README.md](README.md), [PLAN.md](PLAN.md), [TODO.md](TODO.md), and this changelog, establishing the project's documentation set alongside REQUIREMENTS.md.
- PLAN.md's 9 dependency-ordered P1 milestones (bootstrap → config → storage → engine → MCP → API/security → K8s reload → observability → container/release), each tied to the requirement IDs it satisfies.
- TODO.md's full task-and-subtask backlog for every PLAN.md milestone (175+ checklist items), plus open decisions and deferred-scope tracking.

## Requirements history

### 2.4 — Contradiction fix

- DEL-01 claimed internal code organization was entirely free, but CNT-04/CNT-10 fix the entrypoint/healthcheck module path (`app.main`/`app.healthcheck`) so Docker has a concrete command to invoke. DEL-01 now names that one narrow exception instead of contradicting it.

### 2.3 — Consistency pass

- Removed a stale reference to the deleted baseline-approval gate (GATE-01) in the phase-delivery rule.
- De-duplicated the `docker-compose.yaml` deliverable description against its detailed requirement (CNT-09).
- Added the OpenAI SDK compatibility matrix to the required deliverables list.
- Clarified the container image-size measurement rule (must cover the image as shipped, not a stripped-down variant).

### 2.2 — Scope pass

- Dropped the WebSocket API from scope. Agent calling conventions in practice are overwhelmingly request/response or SSE-streamed HTTP; a second stateful transport is deferred until a concrete caller needs it.
- Dropped the Kubernetes Custom Resource (CRD) path. Config reload now only watches a plain ConfigMap — same reload mechanics, no API-group/RBAC/status-subresource surface to build and no dependency on trademark/domain clearance.
- Storage stays configurable across all four backends (memory, file, Redis, PostgreSQL); simplified the *testing* requirement to one shared contract suite for all backends, with an additional session-fencing proof required only for the two that support multi-replica deployment (Redis, PostgreSQL).
- Replaced the internal Python file-tree, pytest-tooling test plan, and release-governance apparatus (old §§17–19, GATE-01, STACK-02) with outcome-based deliverables, acceptance criteria, and a lightweight traceability requirement. The document now states what the runtime must do, not how it is built or tested.

### 2.1 — Editorial pass

- Consolidated the revision history and trimmed repeated/filler phrasing.
- Removed two genuine cross-section duplications (a health-status fact restated in two requirements; two self-referential "this resolves the previous conflict" comments about the document's own drafting history).
- Added a phase-organized section index for navigation.
- No behavioral changes.

### 2.0 — Independent requirements review

- Introduced fail-closed phase capabilities (a build must refuse config it can't honor, not warn and continue).
- Made configuration parsing fully deterministic (canonical dumps, aggregate sorted errors, no OS-enumeration-order dependence).
- Bounded every transport, stream, and session (byte limits, connection caps, queue limits) to prevent unbounded memory growth.
- Defined atomic multi-replica session semantics (fencing tokens for Redis/PostgreSQL).
- Specified an explicit run lifecycle and state machine, JWT/proxy hardening, transactional config reload with rollback, and measurable release gates.

### 1.1 — Review pass

- Added non-functional requirements (§6), concurrency and session-serialization limits.
- Added `GET /v1/models`, `--validate` CLI flag, JWT algorithm allowlist, TTL sweeps for memory/file storage backends, container hardening (HEALTHCHECK, read-only rootfs, `.dockerignore`, SBOM), and `engine.topP`.

### 1.0 — Initial consolidated baseline

- First complete draft of the specification.
