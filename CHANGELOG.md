# Changelog

This project has no released code yet — everything below documents the evolution of [REQUIREMENTS.md](REQUIREMENTS.md), the authoritative specification. Once implementation begins, this file will track runtime releases (tied to image digests, per REQUIREMENTS.md TRC-02) the same way.

## [Unreleased]

Nothing shipped yet. See [PLAN.md](PLAN.md) for the current build order and [TODO.md](TODO.md) for the backlog.

### Added
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
