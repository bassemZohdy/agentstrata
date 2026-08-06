# Agentbase

*Working name — pending trademark, domain, and package-registry clearance.*

A declarative, production-grade AI agent runtime delivered as **one multi-stage Docker image**. You describe the agent — instructions, model binding, tools, storage, protocols, and operational policy — entirely through external configuration. No application code, no rebuilding the image.

Agentbase is the runtime, not an agent builder: the same Agent Definition runs unmodified locally, in Docker, on Kubernetes/OpenShift, or on a managed container platform.

## Quick start

```bash
# Build the image
docker build -t agentbase:latest .

# Run with a Gemini key (the bundled default agent). Health: /healthz, /readyz, /health
docker run --rm -p 8080:8080 -e GEMINI_API_KEY=... agentbase:latest
```

Try the OpenAI-compatible API:

```bash
curl http://localhost:8080/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model":"agent","messages":[{"role":"user","content":"hello"}]}'
```

Or bring up the full stack — runtime plus Redis, PostgreSQL, and a sample MCP server — wired with mounted config, profile, auth, and secrets-as-env:

```bash
docker compose up -d --build    # runtime: http://localhost:8080
```

## How you configure it

Everything is one Agent Definition, merged from **8 precedence tiers** (highest tier that supplies a leaf wins):

| Tier | Source |
| --- | --- |
| 1 | Bundled base `/app/config/agent.yaml` (in the image) |
| 2 | Bundled profile `/app/config/agent-{profile}.yaml` |
| 3 | Mounted base: `{configDir}/agent.yaml` → `.yml` → `.json` → `config.yaml` |
| 4 | Mounted profile: `{configDir}/agent-{profile}.{yaml,yml,json}` |
| 5 | `AGENT_*` environment variables (schema-aware relaxed binding) |
| 6 | `AGENT_APPLICATION_JSON` inline JSON |
| 7 | `--<dotted.path>=<value>` CLI flags |
| 8 | Kubernetes ConfigMap overlay (watcher mode) |

`configDir` defaults to `/etc/agent`. A minimal mounted definition (the file `docker-compose.yaml` mounts) is all you need:

```yaml
schemaVersion: 1
name: "agent"
engine:
  systemInstruction: "You are a helpful assistant. You have tools available; use them when asked."
llm:
  provider: "gemini"
  model: "gemini-2.5-flash"
  apiKeyEnv: "GEMINI_API_KEY"
storage:
  type: "memory"          # memory | file | redis | postgres
server:
  auth:
    mode: "apiKey"        # none | apiKey | jwt
    apiKeyEnv: "AGENT_API_KEY"
tools:
  mcpServers:
    - name: "echo"
      transport: "stdio"
      command: "python"
      args: ["/srv/server.py"]
```

The JSON Schema for documents is in `schemas/agent.schema.json` (ConfigMap overlay variant in `schemas/agent-overlay.schema.json`). Validate a candidate before applying:

```bash
python -m app.config.cli --validate
python -m app.config.cli --dump-config
```

Every option and backend is covered in [docs/deployment.md](docs/deployment.md).

## What's in the image

- **OpenAI-compatible API** — `POST /v1/chat/completions` (streaming and non-streaming), `GET /v1/models`, plus session-management endpoints. Works with existing OpenAI SDKs and chat UIs out of the box.
- **Multi-provider LLM** — Gemini (native or Vertex AI), OpenAI, Anthropic, Ollama, or any LiteLLM-supported provider, selected entirely by config.
- **MCP tools** — connects to configured MCP servers (stdio, Streamable HTTP, legacy SSE) via the official MCP SDK; per-server tool filtering, renaming, and bounded/redacted results.
- **Configurable storage** — sessions, runs, and idempotency records in memory, local file, Redis, or PostgreSQL. Redis/PostgreSQL support multi-replica deployments via session fencing.
- **Configurable auth** — none, static API key, or JWT/JWKS, selected per deployment.
- **Kubernetes-native reload** — watches a ConfigMap for live config changes, with three reload categories (live snapshot, component rebuild, restart-required) and last-known-good rollback on a bad update.
- **Observability** — structured JSON/text logs with request/run correlation, OpenTelemetry traces and metrics (opt-in, zero-cost when disabled), a `/health` endpoint reporting per-component status, and a Prometheus `/metrics` scrape endpoint.
- **WebSocket API** — `/v1/ws` for streaming runs, mid-stream cancellation, and approval decisions (`server.protocols.websocket`).
- **Kubernetes operator (optional)** — an in-cluster operator reconciles `AgentConfig` custom resources into running runtimes (`agentconfigs.agentstrata.io`), including live CR → ConfigMap → runtime reloads.
- **Cost accounting** — per-request USD pricing from a `costs` config table (`usage.costUsd`, run-outcome `cost_usd`, `agentbase_cost_usd_total{model}` metric); disabled by default with zero surface change.
- **Hardened container** — non-root, arbitrary-UID (OpenShift-compatible), read-only rootfs, multi-arch (`amd64`/`arm64`), graceful shutdown, SBOM + vulnerability scanning.

## Deploy it

```bash
# Kubernetes watcher mode: apply least-privilege RBAC, then the Deployment/Service
kubectl apply -f manifests/rbac.yaml
kubectl apply -f manifests/deployment.yaml
```

Set `k8s.enabled: true` and the runtime watches the ConfigMap named by `k8s.name` in `k8s.namespace`, reading key `agent.yaml` as a partial tier-8 overlay. Replicas reconcile independently — per-pod `/health` reports the local generation/hash. Full deployment, auth, storage, and observability guidance: [docs/deployment.md](docs/deployment.md).

Alternatively, the optional in-cluster operator turns AgentConfig custom resources into runtimes (`kubectl apply -f k8s_operator/crd/agentconfigs.agentstrata.io.yaml` plus the RBAC and operator manifests — see [docs/deployment.md](docs/deployment.md)).

## Status

The runtime is feature-complete through phase 5: P1 core, P2 multi-agent/ACP, P3 approvals, P4 RAG, and P5 (metrics, WebSocket, Kubernetes CRD/operator, cost accounting) are implemented and passing the host-based test suite. Remaining work is tracked in [TODO.md](TODO.md); the image-based release gates (benchmark/chaos, zero-downtime reload proof, multi-architecture acceptance) run against the built image per [PLAN.md](PLAN.md).

| Phase | Adds |
| --- | --- |
| **P1 — Core runtime** | Config engine, sessions/storage, OpenAI-compatible API, MCP tools, observability |
| **P2 — Multi-agent** | Root-agent + sub-agent hierarchies, agent-to-agent REST (ACP) |
| **P3 — Human-in-the-loop** | Tool-call approval workflow with durable checkpoints |
| **P4 — RAG / long-term memory** | Document ingestion and retrieval-augmented context |
| **P5 — Extensions** | P5-1 Prometheus `/metrics`, P5-2 WebSocket API (`/v1/ws`), P5-3 Kubernetes CRD/operator, P5-4 per-request cost accounting (`costs.enabled`, `usage.costUsd`) |

## Documentation

| File | Purpose |
| --- | --- |
| [docs/deployment.md](docs/deployment.md) | Operator guide: every configuration option, backend, auth mode, and deployment target. |
| [docs/decisions.md](docs/decisions.md) | Decision log: resolved engineering and product decisions (requirement-ID traceable). |
| [REQUIREMENTS.md](REQUIREMENTS.md) | The authoritative specification. Every rule has a stable ID (e.g. `CFG-03`, `API-07`) that code and tests trace back to. |
| [PLAN.md](PLAN.md) | Build order and milestones. |
| [TODO.md](TODO.md) | Deferred scope and pointers to completed work (backlog is closed). |
| [CHANGELOG.md](CHANGELOG.md) | What changed and why, release by release. |
