# Deployment and configuration

This document covers running Agentbase everywhere and every supported
configuration option. The authoritative contract is
[REQUIREMENTS.md](../REQUIREMENTS.md); this is the operator-facing summary.

## Configuration

Configuration is a single Agent Definition merged from 8 precedence tiers
(REQUIREMENTS.md CFG-01); the highest tier that supplies a leaf wins.

| Tier | Source |
| --- | --- |
| 1 | Bundled base `/app/config/agent.yaml` (image) |
| 2 | Bundled profile `/app/config/agent-{profile}.yaml` |
| 3 | Mounted base: `{configDir}/agent.yaml` → `.yml` → `.json` → `config.yaml` |
| 4 | Mounted profile: `{configDir}/agent-{profile}.{yaml,yml,json}` |
| 5 | `AGENT_*` environment variables (schema-aware relaxed binding) |
| 6 | `AGENT_APPLICATION_JSON` inline JSON |
| 7 | `--<dotted.path>=<value>` CLI flags |
| 8 | Kubernetes ConfigMap overlay (watcher mode) |

`configDir` defaults to `/etc/agent` (override `AGENT_CONFIG_DIR` or
`--config-dir`). Validate a candidate before applying:

```bash
AGENT_BUNDLED_DIR=config .venv/bin/python -m app.config.cli --validate
AGENT_BUNDLED_DIR=config .venv/bin/python -m app.config.cli --dump-config
```

The JSON Schema for documents lives in `schemas/agent.schema.json`; the
ConfigMap overlay variant in `schemas/agent-overlay.schema.json` (all fields
optional) — both generated, never hand-edited.

## Running locally

```bash
uv venv --python 3.12 .venv && uv pip install -r requirements-dev.lock
AGENT_BUNDLED_DIR=config .venv/bin/python -m app.main
# health: curl localhost:8080/healthz | /readyz | /health
```

## Authentication (all modes)

- **none** — development/controlled-network only; binding to a non-loopback
  address emits a high-severity startup audit warning (SEC-01).
- **apiKey** — `server.auth.mode: apiKey` plus `apiKeyEnv`/`apiKeyFile`.
  Accepted via `Authorization: Bearer <key>` or `X-API-Key` (both present
  must match). Boot fails closed (exit 78) when the key is unreadable.
- **jwt** — `server.auth.mode: jwt` with `jwt.issuer`, `jwt.jwksUrl`,
  `jwt.principalClaim` (default `sub`). RS256/ES256 only; JWKS refreshed
  every `refreshSeconds`; fail-closed when unreachable.

Secret references (`*Env`/`*File` pairs) are file-wins, re-read at point of
use for rotation; env values are the process-start snapshot (SEC-04).

## Storage (all four backends)

| Type | When | Replicas |
| --- | --- | --- |
| `memory` | dev / single replica | 1 only (data lost on restart) |
| `file` | single-process durable | 1 only (`storage.path`) |
| `redis` | multi-replica shared sessions | ≥1 (`connectionStringEnv/File`) |
| `postgres` | multi-replica shared sessions | ≥1 (`connectionStringEnv/File`) |

`maxSessions`/`maxRunsPerSession`/`maxIdempotencyRecordsPerSession` bound
growth; TTLs (`sessionTtlSeconds`, `runTtlSeconds`, `idempotencyTtlSeconds`)
are swept every 10 minutes. Multi-replica deployments MUST use Redis or
Postgres (session fencing, SES-05).

## Multi-agent and ACP (P2, §13 + API-16)

`agents[]` (optional, flat, one level) declares sub-agents; the root agent
becomes an ADK coordinator that transfers to them by name/description:

```yaml
agents:
  - name: worker            # DNS-label, distinct from the root
    systemInstruction: "…"  # required, non-empty
    description: "…"        # optional, <= 2000 code points
    llm:                    # optional; deep-merged over the root llm
      model: "…"
    toolServers: ["alpha"]  # optional; defaults to EVERY MCP server
```

- Empty/absent `agents[]` keeps the P1 single-agent behavior.
- Tool isolation (MA-03): a sub-agent sees only its `toolServers` tools
  (after MCP filter + collision-safe renaming); the coordinator has no
  hidden tools; transfer grants no new principal/budget.
- Transfers surface as `agent_transfer` events in events/debug streams and
  in the run audit; streaming-mode gating follows API-13.
- The ACP surface (API-16, annex §13.1) is enabled with
  `server.protocols.acp: true`: `GET /acp/agents` lists the coordinator
  and sub-agents (name/description/tools), `POST /acp/runs` runs an agent
  by name (non-streaming and SSE; the annex error table applies).
- Changing `agents` is a component rebuild (REL-02): in-flight runs
  finish on the old generation; new runs use the new one.



Human-in-the-loop tool approval gates matched tools before any side effect.
While `approval.enabled` is true:

- chat requests MUST be stateful (`session_id`); stateless requests get
  400 `approval_session_required`;
- non-streaming runs pause with 202 `run.pending_approval`; SSE streams
  emit `approval_required` then `[DONE]` (the run DETACHES — it is not
  cancelled);
- decide with `POST /v1/approvals/{id}` (`{"decision": "approve"|"deny"}`;
  repeat → stored outcome, conflict → 409, expired → 410); pending items
  via `GET /v1/approvals?session_id=`; run state/cancellation via
  `GET/DELETE /v1/runs/{id}`.

Configuration (`approval`): `tools` are `server/rawTool` or `server/*`
patterns matched BEFORE public renaming; `timeoutSeconds` bounds the
decision window; `onTimeout: deny` (default) finishes the run denied,
`onTimeout: allow` is accepted only with an explicit boot audit and still
runs the same stale/cancellation checks. Approval requires `auth.mode`
other than `none` and a `redis` or `postgres` storage type (fail-closed).
A config reload terminates pending approvals `stale_approval` and the tool
never executes; the startup reconciler resumes/finishes records left by a
previous process (HITL-05).

## RAG / long-term memory (P4, §15)

`rag.enabled` turns on principal-scoped retrieval: before each root-agent
call the runtime retrieves ≤ `topK` chunks for the latest user message and
inserts ONE delimited context message (explicitly labeled untrusted
knowledge) after the system instruction.

- **Store** (`rag.store`): `chroma` or `pgvector` with
  `connectionStringEnv/File` (SEC-04) and a DNS-1123 `collection`. The
  ACC-01 deviation applies: the acceptance proofs and offline runs use the
  in-memory substitute, constructed directly by tests/offline dev; when
  the configured driver is not installed the runtime FAILS CLOSED with a
  ConfigError at boot (no silent degradation). Changing any
  store/embedding/chunk
  identity field is a component rebuild — old documents are NEVER silently
  re-embedded; migrate explicitly (RAG-05).
- **Embedding** (`rag.embedding`): `gemini` or `openai` with
  `apiKeyEnv/File`; `model` selects the embedding model.
- **Tuning**: `topK` (1..100), `minScore` (0..1), `chunkChars`,
  `chunkOverlapChars` (must be < chunkChars), `maxDocumentBytes` (default
  10 MiB).
- **Documents API** (owner-scoped): `POST /v1/documents` (201 with id /
  chunk count / content hash; `Idempotency-Key` supported), `GET
  /v1/documents/{id}` (metadata/count/hash only — never the stored text),
  `DELETE /v1/documents/{id}` (204, idempotent).
- **Availability** (`rag.required`): optional — an unavailable store logs
  ONE redacted error, emits `rag_degraded` in events/debug streams only,
  and answers without context (readiness stays 200); required — `/readyz`
  is 503 and chat runs fail `rag_unavailable`. Ingestion NEVER degrades
  silently.
- Document content is excluded from logs and traces (RAG-05); backups and
  retention are the deployment's responsibility (the runtime stores
  documents until deleted).



```bash
docker build -t agentbase:latest .
docker run --rm -p 8080:8080 -e GEMINI_API_KEY=... agentbase:latest
docker compose up -d --build   # full stack: runtime + Redis + Postgres + MCP sample
```

The image runs non-root (UID 10001, group 0 — OpenShift-compatible) with a
read-only rootfs (writes confined to `/tmp` and `storage.path`), one Uvicorn
worker, exec-form `ENTRYPOINT`, and a loopback HEALTHCHECK. Config is
mounted at `/etc/agent/agent.yaml` (VOLUME). See `manifests/` for
Kubernetes RBAC + Deployment/Service with probes and security context.

## Kubernetes (watcher mode)

Set `k8s.enabled: true` (+ optionally `k8s.required: true`); the runtime
detects `KUBERNETES_SERVICE_HOST` and watches the ConfigMap named by
`k8s.name` in `k8s.namespace`, reading key `agent.yaml` as a partial
tier-8 overlay. Apply `manifests/rbac.yaml` (least-privilege get/list/watch)
then `manifests/deployment.yaml`. Reloads are categorized
(live-snapshot / component-rebuild / restart-required); invalid or
restart-required updates are rejected with the last-known-good retained.
Replicas reconcile independently — per-pod `/health` reports the local
generation/hash (REL-04/K8S-07).

## Observability

Logs are structured JSON (or text) to stderr with `ts/level/logger/event/msg`
plus request/run correlation. OpenTelemetry is opt-in via
`observability.otel.enabled` with standard `OTEL_EXPORTER_OTLP_*` env vars;
when disabled, no OTel code is imported (zero-cost). A Prometheus
`/metrics` endpoint (OBS-05) is available via
`observability.prometheus.enabled` (default false) at
`observability.prometheus.path` (default `/metrics`) in the text
exposition format 0.0.4 — counters for admitted/completed/failed runs,
model/tool calls, tokens, denials, reloads, and output-queue
cancellations, an active-runs gauge, and run-latency histograms, all
low-cardinality. The scrape path is exempt from the replica-local rate
limiter; the registry is process-local and shared across live reloads.
Scrape `/metrics` from the same listener (the endpoint is served by the
app, no sidecar or extra port).

## Known limitations (operator documentation required)

- `maxTransportMessageBytes` is enforced on HTTP/SSE MCP transports; the
  stdio pre-parse cap is deferred until a google-adk release supports the
  mcp 2.x Transport seam (REQUIREMENTS.md v2.5).
- A token budget may be exceeded by a single call's reported input usage
  (ENG-08); no later call starts after an overshoot.
- A crashed stateless request cannot be resumed; a client retry is a new run
  that may cause a new side effect (ENG-09).
- With `auth.mode: none`, client-chosen sessions are mutually accessible
  (SES-03).
