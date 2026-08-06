# Backlog — remaining work

The backlog from the 2026-08-05 deep review is **closed**.  All phases
P1–P5 are implemented and passing the host-based test suite; the P5-4
per-request cost-accounting finish line (usage-shape consistency, tests,
docs, hygiene) landed on 2026-08-06 and is recorded in
[CHANGELOG.md](CHANGELOG.md).  Resolved engineering decisions live in
[docs/decisions.md](docs/decisions.md).  The only remaining work is the
deferred scope below.  Requirement IDs trace back to
[REQUIREMENTS.md](REQUIREMENTS.md); build order and rationale are in
[PLAN.md](PLAN.md).

## Completed work (pointer)

P1 core runtime, P2 multi-agent/ACP, P3 approvals, P4 RAG, and P5-1/P5-2/
P5-3/P5-4 (Prometheus metrics, WebSocket API, Kubernetes CRD/operator,
per-request cost accounting) are complete and recorded in
[CHANGELOG.md](CHANGELOG.md) — including the 2026-08-05 review items
(cost-accounting consistency/spec/test gaps, code-quality/hygiene,
spec-compliance gaps, documentation debt) under the "P5-4 finish line"
entry, and the TRC-01 ACP-annex traceability fix.

## Deferred scope

- **Multi-agent per-sub-agent cost pricing** (P2, MA-02) — cost is priced
  against the root `llm.model`; sub-agent `llm.model` overrides are not
  priced per model until P2 cost tests land.  Documented in
  `docs/deployment.md` (Known limitations).
- **NFR-00 image-based release gates** — benchmark/chaos, zero-downtime
  reload proof, and multi-architecture acceptance run against the built
  image at release time (see the checklist in `docs/release.md`).
