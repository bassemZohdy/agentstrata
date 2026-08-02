# AgentStrata

*Working name — pending trademark, domain, and package-registry clearance.*

A declarative, production-grade AI agent runtime delivered as one multi-stage Docker image. You supply the agent's instructions, model binding, tools, storage, protocols, and operational policy entirely through external configuration — no application code, no rebuilding the image.

The runtime, not an agent builder: the same Agent Definition runs unmodified locally, in Docker, on Kubernetes/OpenShift, or on a managed container platform.

## Status

**Requirements-defined, pre-implementation.** [REQUIREMENTS.md](REQUIREMENTS.md) is the authoritative specification; no runtime code exists yet. See [PLAN.md](PLAN.md) for the build order and [TODO.md](TODO.md) for what's still open.

## What it does (Phase 1 / core runtime)

- **Configuration-driven** — one YAML/JSON Agent Definition, layered over 8 precedence tiers (bundled defaults → mounted files → env vars → CLI flags → Kubernetes overlay), deep-merged and deterministically validated.
- **OpenAI-compatible API** — `POST /v1/chat/completions` (streaming and non-streaming), `GET /v1/models`, plus session management endpoints. Works with existing OpenAI SDKs and chat UIs out of the box.
- **Multi-provider LLM** — Gemini (native or Vertex AI), OpenAI, Anthropic, Ollama, or any LiteLLM-supported provider, selected entirely by config.
- **MCP tools** — connects to configured MCP servers (stdio, Streamable HTTP, legacy SSE) via the official MCP SDK; per-server tool filtering, renaming, and bounded/redacted results.
- **Configurable storage** — sessions, runs, and idempotency records persisted to memory, local file, Redis, or PostgreSQL, chosen per deployment. Redis/PostgreSQL support multi-replica deployments via session fencing.
- **Configurable auth** — none, static API key, or JWT/JWKS, selected per deployment.
- **Kubernetes-native reload** — watches a ConfigMap for live config changes, with three reload categories (live snapshot, component rebuild, restart-required) and last-known-good rollback on a bad update.
- **Observability** — structured JSON/text logs with request/run correlation, OpenTelemetry traces and metrics (opt-in, zero-cost when disabled), and a `/health` endpoint reporting per-component status.
- **Hardened container** — non-root, arbitrary-UID (OpenShift-compatible), read-only rootfs, multi-arch (`amd64`/`arm64`), graceful shutdown, SBOM + vulnerability scanning.

Deliberately **not** in scope for now: a GUI, model hosting/fine-tuning, acting as an MCP server, a WebSocket API, and a Kubernetes CRD/operator (a plain ConfigMap covers the same reload job). See REQUIREMENTS.md §1.4 for the full list and reasoning.

## Roadmap

| Phase | Adds |
|---|---|
| **P1 — Core runtime** | Everything above |
| **P2 — Multi-agent** | Root-agent + sub-agent hierarchies, agent-to-agent REST (ACP) |
| **P3 — Human-in-the-loop** | Tool-call approval workflow with durable checkpoints |
| **P4 — RAG / long-term memory** | Document ingestion and retrieval-augmented context |

Each phase is independently releasable and gated by its own acceptance criteria (REQUIREMENTS.md §18).

## Documentation

| File | Purpose |
|---|---|
| [REQUIREMENTS.md](REQUIREMENTS.md) | The single source of truth for what the runtime must do. Every rule has a stable ID (e.g. `CFG-03`, `API-07`) that code and tests trace back to. |
| [PLAN.md](PLAN.md) | Build order and milestones for turning the requirements into a working runtime. |
| [TODO.md](TODO.md) | The actionable checklist: every task and subtask for each PLAN.md milestone, plus open decisions, deferred-scope items, and unstarted phase work. |
| [CHANGELOG.md](CHANGELOG.md) | History of what changed and why, release by release. |

## Contributing

This repository has no code yet — the immediate work is implementing P1 against REQUIREMENTS.md. Read [PLAN.md](PLAN.md) for the build order and start checking off Milestone 0 in [TODO.md](TODO.md).
