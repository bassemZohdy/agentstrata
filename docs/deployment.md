# Deployment and configuration

This document covers running AgentStrata everywhere and every supported
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

## Docker

```bash
docker build -t agentstrata:latest .
docker run --rm -p 8080:8080 -e GEMINI_API_KEY=... agentstrata:latest
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
when disabled, no OTel code is imported (zero-cost). No Prometheus `/metrics`
endpoint is in scope (OTel metrics cover the interim need).

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
