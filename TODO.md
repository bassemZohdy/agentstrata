# Backlog — remaining work

The backlog from the 2026-08-05 deep review is **closed**.  All phases
P1–P5 are implemented and passing the host-based test suite; the P5-4
per-request cost-accounting finish line (usage-shape consistency, tests,
docs, hygiene) landed on 2026-08-06 and is recorded in
[CHANGELOG.md](CHANGELOG.md).  Requirement IDs in parentheses trace back
to [REQUIREMENTS.md](REQUIREMENTS.md); build order and rationale are in
[PLAN.md](PLAN.md).

## Completed work (pointer)

P1 core runtime, P2 multi-agent/ACP, P3 approvals, P4 RAG, and P5-1/P5-2/
P5-3/P5-4 (Prometheus metrics, WebSocket API, Kubernetes CRD/operator,
per-request cost accounting) are complete and recorded in
[CHANGELOG.md](CHANGELOG.md).  The 2026-08-05 review items (cost-accounting
consistency/spec/test gaps, code-quality/hygiene, spec-compliance gaps,
documentation debt) are closed; see the "P5-4 finish line" entry in
CHANGELOG.md.

## Decisions made (resolved, for the record)

- [x] **NFR-06 OpenAI SDK compatibility range** — `openai` Python SDK
      `>=1.0,<3` (locked 2.52.0); the surface is a strict subset.
- [x] **CNT-12 vulnerability severity policy** — CRITICAL/HIGH with no
      available fix block release; MEDIUM/LOW are tracked.
- [x] **MCP-08 stdio pre-parse byte cap** — deferred until `google-adk`
      supports `mcp` 2.x; HTTP/SSE cap enforced now.
- [x] **mcp SDK version range** — pinned `mcp>=1.24,<2` (locked 1.29.0).
- [x] **ACC-01 storage proof deviation** — memory substitute for unit
      tests; real Redis/Postgres proofs deferred to the release
      acceptance run.
- [x] **Product name** — "Agentbase"/"AgentStrata" stays for open-source,
      non-commercial use; `agent-strata` is the registry-clear fallback if
      the project ever turns commercial.
- [x] **google.adk MCPTool deprecation** — the warning fires inside
      google-adk's own `mcp_toolset.py` (2.6.1 and 2.6.2 still construct
      the deprecated class); this codebase already uses the modern
      `McpToolset`, so no migration applies until an upstream release
      replaces it (STACK-01: a google-adk bump is a
      requirements-impacting decision).

## Deferred scope

- **Multi-agent per-sub-agent cost pricing** (P2, MA-02) — cost is priced
  against the root `llm.model`; sub-agent `llm.model` overrides are not
  priced per model until P2 cost tests land.  Documented in
  `docs/deployment.md` (Known limitations).
- **NFR-00 image-based release gates** — benchmark/chaos, zero-downtime
  reload proof, and multi-architecture acceptance run against the built
  image at release time (see the checklist in `docs/release.md`).
