# Backlog

Actionable checklist for implementing [REQUIREMENTS.md](REQUIREMENTS.md) in the order set by [PLAN.md](PLAN.md). Each milestone section below mirrors a milestone in PLAN.md; check items off as they land, and add new ones there if scope shifts — don't let the two documents drift.

Requirement IDs in parentheses are what each task traces back to (REQUIREMENTS.md). "Later phases," "Deferred scope," and "Open decisions" at the bottom are not part of the Phase 1 critical path.

---

## Milestone 0 — Project bootstrap

**Exit check: `docker build` produces a running (but functionally empty) container — PASSED (2026-08-02, image `agentstrata:m0`): serves on 8080, exactly one Uvicorn worker, exec-form PID 1, no reload.**

- [x] Repository skeleton
  - [x] Directory layout for config/engine/storage/protocol/security/watcher concerns (internal layout is free-form, DEL-01 — just keep these independently testable)
  - [x] `.gitignore`, base `README`/license placeholders (README existed; added `.gitignore`, `.gitattributes`, LICENSE placeholder — license/trademark remains an open decision)
- [x] Dependency management (STACK-01)
  - [x] `requirements.txt` with direct compatible ranges: `fastapi`, `uvicorn[standard]`, `pydantic` v2, `google-adk`, `litellm`, `mcp`, `PyYAML`, `kubernetes`, `redis`, `psycopg[binary]`, `PyJWT[crypto]`
  - [x] `requirements-dev.txt` for test/schema/lint tooling (`pytest`, `ruff`, `mypy`, `types-PyYAML`)
  - [x] Lock tooling producing `requirements.lock` with exact versions + hashes — `uv pip compile` via `scripts/compile-lock.sh`; `requirements.lock` (prod) + `requirements-dev.lock` (dev union), both universal + hash-pinned for Python 3.12; `scripts/verify-lock.sh` verifies hashes (CI + Docker builder); resolve/update/review flow documented in PLAN.md (STACK-01 cross-cutting rule)
- [x] **STACK-02 feasibility spike — do this before Milestones 1–4, not after:**
  - [x] Confirm the locked ADK version exposes the documented session/event lifecycle through a public/stable seam — **GO** (google-adk 2.6.1): `LlmAgent`, `Runner.run_async` (yields typed `Event`), `BaseSessionService` (public abstract contract, injectable into `Runner`), `BaseLlm.generate_content_async` extension hook. Verified end-to-end by `scripts/spike_adk_lifecycle.py` (run → event stream → session history persisted).
  - [x] Confirm `McpToolset` connection/cancellation lifecycle is usable without private-internal monkey-patching — **GO on mcp 1.29.0** (`McpToolset(connection_params=...)` → `get_tools()` → `close()`; verified by `scripts/spike_mcp_lifecycle.py` against a real stdio server); **BROKEN on mcp 2.0.0** — `google.adk.tools.McpToolset` import fails: ADK 2.6.1 imports `mcp.shared.session.ProgressFnT`, removed in mcp 2.0 (ADK declares `mcp>=1.24,<2`; 2.6.1 is the latest ADK release). **Dependency fix required: pin `mcp>=1.24,<2` (lock → 1.29.0).**
  - [x] Confirm a bounded-read seam exists for enforcing `maxTransportMessageBytes` (MCP-08) on every transport — **GO for HTTP/SSE** (`httpx_client_factory`/`http_client` injection is an ADK-blessed public seam on `SseConnectionParams`/`StreamableHTTPConnectionParams` and `mcp.client.streamable_http`); **NO seam for stdio** in mcp 1.29.0 (`stdio_client` reads `async for line` with an unbounded accumulate-until-newline buffer; no byte-source injection point). mcp 2.0.0 *does* define a documented `Transport` protocol seam, but is incompatible with ADK 2.6.1. **→ decision needed (STACK-02: dependency choice / trust boundary / phase scope MUST change).**
  - [x] Confirm Uvicorn exposes the parser bounds API-20 requires (request-line/header/header-count limits pre-allocation) — **PARTIAL** on uvicorn 0.52.1: `h11_max_incomplete_event_size` bounds the incomplete request-line+headers buffer before full allocation (public Config/CLI seam), but (a) complete single-segment requests bypass the cap, (b) there is no header-count limit, (c) over-limit responses are `400` or connection aborts — `error_status_hint` (431) is ignored; 414 is never produced. httptools path (default) exposes nothing configurable. `uvicorn.Config(http=...)` accepts a custom protocol *class* — the documented seam for an M5 custom h11 subclass that maps 431/414 — plan for it in Milestone 5.
  - [x] If any seam is missing: revise the dependency choice, trust boundary, or phase scope before continuing — **two findings recorded above: (1) mcp pin <2 applied (ADK's declared range — locked at 1.29.0), (2) stdio byte-bound seam absent — user decision (2026-08-02): defer stdio pre-parse cap, enforce HTTP/SSE now; REQUIREMENTS.md MCP-08 amended with phase note (v2.5)** (see Open decisions)
- [x] Minimal Dockerfile (CNT-01, CNT-04, CNT-05, CNT-06)
  - [x] Multi-stage build: digest-pinned `python:3.12-slim` builder installing into a venv, then a matching runtime stage
  - [x] `ENTRYPOINT ["python","-m","app.main"]` (exec form)
  - [x] `VOLUME /etc/agent`, `EXPOSE 8080`
  - [x] `ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1`
  - [x] `.dockerignore` covering the build context boundary
- [x] Exactly one Uvicorn worker; confirm reload/debug mode is disabled in the production entrypoint (CNT-08)
- [x] CI skeleton: lint, type-check, lockfile hash verification, placeholder test job — `.github/workflows/ci.yml` (SHA-pinned actions, read-only permissions, zizmor clean); all jobs verified locally from a clean checkout

---

## Milestone 1 — Configuration engine

- [x] Pydantic schema (SCH-01 – SCH-09, BASE-01)
  - [x] Base `model_config` (camelCase alias generator, `extra="forbid"`, `strict=True`, `populate_by_name=True`) (SCH-01)
  - [x] Top-level fields: `$schema`, `schemaVersion`, `name`, `description`, `profile`
  - [x] `engine.*`: `systemInstruction`, `temperature`, `topP`, `maxTokens`, `maxOutputBytes`, `timeoutSeconds`, `maxIterations`, `historyMaxMessages`/`historyMaxBytes`, `streaming`, `overrides.*`, `tokenBudget.*`
  - [x] `llm.*`: `provider`, `model`, `apiKey` secret ref pair, `baseUrl`, `contextWindowTokens`, `vertex.*`, `extra` passthrough
  - [x] `tools.mcpServers[]` and its `SecretHeaderRef` sub-model (SCH-04)
  - [x] `storage.*` (SCH-05)
  - [x] `server.*`: protocols, CORS, auth (apiKey/JWT), rate limit, transport byte/count limits (SCH-06)
  - [x] `k8s.*`: `enabled`, `required`, `namespace`, `name`, `resyncSeconds` — ConfigMap-only, no `source` field (SCH-07)
  - [x] `observability.*` (SCH-08)
  - [x] Phase-gated stub sections with disabled defaults: `agents[]`, `approval`, `rag` (SCH-09)
  - [x] Bundled base config `config/agent.yaml` (repo) → `/app/config/agent.yaml` (image) matching schema defaults (BASE-01)
  - [x] JSON Schema generation → `schemas/agent.schema.json` (draft 2020-12, `$id` v1), CI zero-diff step wired (SCH-02, DEL-02)
- [x] Tier 1–7 resolver (CFG-01 – CFG-11a)
  - [x] Tier 1/2: bundled base + profile file loading
  - [x] Tier 3/4: mounted base/profile file discovery in exact candidate order, first-match-only, warn on ignored siblings (CFG-03b)
  - [x] Tier 5: relaxed env binding — schema-aware alias matching, ambiguity detection, near-match security-sensitive warning (CFG-07, CFG-08)
  - [x] Tier 6: `AGENT_APPLICATION_JSON` inline JSON (fatal on invalid JSON)
  - [x] Tier 7: `--<dotted.path>=<value>` CLI flags, last-occurrence-wins with warning (CFG-10)
  - [x] Deep-merge engine: recursive mapping merge, wholesale list replacement, null-reset semantics (CFG-04 – CFG-06)
  - [x] Provenance tracking per leaf, including defaulted/reset values
  - [x] Source parsing safety: UTF-8, 1 MiB cap, single mapping root, duplicate-key rejection, immutable byte-snapshot reads (CFG-03a, CFG-03b)
  - [x] `--validate` (CFG-10a), `--dump-config` with canonical masked YAML + winning-source comments (CFG-11), `--version`/`--help` (CFG-11a) — exit codes 0/64/78 verified
  - [x] Verify NFR-05 as this lands: identical resolver inputs (including permuted env-var enumeration order) produce byte-identical `--dump-config` output — `tests/test_config/test_dump.py::TestNfr05` (60 env permutations, argv-order permutations, repeated identical inputs)
- [x] Validation pipeline (CFG-12 – CFG-15, CAP-01, CAP-02)
  - [x] `AgentConfig.model_validate()` + alias-only external shape walk (CFG-13)
  - [x] Cross-field validation checklist (CFG-14): storage↔connection-string, MCP transport↔command/url, auth-mode↔credentials, vertex↔provider, MCP name uniqueness/size caps, byte-limit orderings, `k8s.required`↔`k8s.enabled`, JWT claim/CIDR checks
  - [x] Deterministic aggregate error reporting sorted by path + code, secrets omitted
  - [x] Capability fail-closed gating: reject non-empty `agents`, `acp`/`approval`/`rag` enabled in a P1 build (CAP-01)
  - [x] `GET /health` capability reporting wired to build-time flags (CAP-02) — `app/config/capabilities.py` (endpoint lands in M5)
  - [x] Enforce the exact boot order (CFG-15)
- [x] Operational mode selection (MODE-01 – MODE-04)
  - [x] Detect `k8s.enabled` + `KUBERNETES_SERVICE_HOST`
  - [x] `k8s.required` fail-closed (exit 78) vs. warn-and-run-standalone behavior (MODE-03)

---

## Milestone 2 — Storage and sessions

- [x] Common data model & backend contract (SES-01 – SES-03)
  - [x] Session/run/idempotency record shapes with internal `schema_version`
  - [x] Storage backend interface (create/get/update/delete session, run records, idempotency records, locking) — `app/storage/contract.py` incl. fencing + StorageSettings bounds
- [x] `memory` backend
  - [x] In-process maps + locks
  - [x] Boot warning: data lost on restart, not shared across replicas
- [x] `file` backend
  - [x] Path layout `{path}/{agent_name}/{principal_digest}/{session_id}.json` with safe fixed-format components
  - [x] Atomic write: exclusive temp file → fsync contents → same-filesystem replace → fsync parent directory (Windows: directory fsync skipped — os.replace still atomic)
  - [x] Symlink-traversal rejection
  - [x] Readiness probing: create/write/fsync/rename/delete
- [x] `redis` backend
  - [x] Key layout with shared hash tags for Cluster compatibility — `agentstrata:{principal_digest}:{kind}:{agent}:{sid}[:{rid|key}]`, all session-scoped keys share the principal hash tag
  - [x] Atomic Lua/transaction revision mutations — CREATE_SESSION/MUTATE_SESSION (revision CAS)/CREATE_RUN (capacity eviction)/CREATE_IDEM with python twins in `app/storage/fakes.py::FakeRedis`
  - [x] Fencing lease: token-valued lease + monotonic fencing number, renew/release by token match (SES-05) — ACQUIRE/RENEW/RELEASE_FENCE Lua; lease extends session TTL; fencing counter persisted in the store
- [x] `postgres` backend
  - [x] `agent_sessions` + companion run/idempotency tables, transactional versioned migrations — `agent_schema` version table, JSONB data, revision + fencing_number columns
  - [x] Session-scoped advisory lock fencing on a dedicated connection (SES-05) — `try_advisory_lock` on the DbClient for the run lifetime, persisted fencing-number increment, token-matched renew/release
- [x] Retention & bounds (SES-06, SES-07)
  - [x] TTL sweep: memory/file every 10 min + redis run-TTL sweep (SWEEP_RUNS); session/idempotency TTLs atomic-with-mutation in redis (age-based index cleanup, cutoff = now - sessionTtl); postgres sweep purges expired sessions (skipping leased) + expired idempotency + terminal runs older than runTtl
  - [x] Sweep skips sessions with a live run/lease, rechecks revision before delete — fence check in memory/file sweeps; redis lease extends session TTL
  - [x] Enforce `maxSessions` / `maxRunsPerSession` / `maxIdempotencyRecordsPerSession` atomically — index ZCARD in create, run eviction of oldest terminal (fail capacity when cannot free), idem capacity fails new keys
- [x] Delete & shutdown flush (SES-08) — cascade delete (session+runs+idempotency+fence, busy on nonterminal run) implemented and tested on all four backends; per-backend `close()` flushes/closes (runtime shutdown wiring lands with the main loop in M5)
- [x] ADK session-service adapter — one revisioned transaction path shared with ADK events (SES-09) — `app/storage/adk_adapter.py::AdkSessionService` implements ADK `BaseSessionService` over the runtime backend; `append_event` persists each event via the backend revision CAS (no independent ADK history); proven end-to-end with a real `LlmAgent` run (`tests/test_storage/test_adk_adapter.py`)
- [x] Shared backend contract test suite, run against all four backends; extra fencing/multi-replica proof for Redis and PostgreSQL only (§18 ACC-01) — **per user decision: runs against memory + real file now; redis/postgres join via in-memory substitutes; real-instance + fencing proof deferred (TODO Decisions made)** — 108 contract tests passing on all four backends (memory + file + redis(FakeRedis) + postgres(SqliteDb))

---

## Milestone 3 — Engine execution

- [x] Model connector construction (LLM-01 – LLM-03)
  - [x] Gemini native + Vertex AI (ADC) path — `app/engine/connectors.py::build_llm` (client_kwargs api_key / vertexai+project+location)
  - [x] LiteLLM bridge model-string mapping: `openai/{model}`, `anthropic/{model}`, `ollama_chat/{model}` (+`api_base`), `litellm` verbatim
  - [x] Retry policy: ≤2 retries on transport/429/5xx, 1s→2s backoff + jitter, honor `Retry-After`, never replay after a delta/tool call — `RetryableLlm`
  - [x] Credential health state machine: `unavailable`/`unknown`/`available`, file-backed re-resolve per request vs. env-backed process-start snapshot — `CredentialHealth` + `SecretResolver`
- [x] ADK `LlmAgent` construction, one immutable root-agent per Applied Config generation (ENG-01) — `app/engine/agent.py`
- [x] `AgentRunner` façade over `Runner.run_async` + internal `AgentEvent` union (ENG-02) — `app/engine/runner.py`
- [x] Admission pipeline in exact order (ENG-03) — auth/rate-limit may be stubbed until Milestone 5, but enforce the 8-step ordering now — `_admit` (session resolve/create, budget eligibility, run record; steps 1-4 enforced by the M5 adapter)
- [x] Context bounds & pruning: history-message/byte limits, context-window trimming, uncommitted-until-success (ENG-04) — `app/engine/context.py` (pruning committed only by the ENG-06 transaction)
- [x] Run state machine (ENG-05) — `app/engine/events.py::RunStateMachine`
  - [x] `created → running → succeeded|failed|cancelled` (+ `cancelling`)
  - [x] Compare-and-swap terminal transition, exactly one winner under timeout/disconnect/shutdown races — CAS persisted via storage; `RunStateMachine._to_terminal`
  - [x] Restart reconciliation: `run_interrupted`, `tool_outcome_unknown` for orphaned `executing` tool records — `reconcile_after_restart` + `ToolLedger.reconcile_executing`
- [x] Transactional persistence: admit without appending history, commit pruning+turn+usage only on success (ENG-06) — `_commit_success`/`_commit_failure` + `truncate_session_events` revert on failure (verified: no history on failed run)
- [x] Iteration/output/token limits (ENG-07, ENG-08) — `app/engine/limits.py::RunLimiter`
  - [x] Iteration exhaustion → `finish_reason: "length"`, `x_agent_status: "iteration_limit"` — verified by test
  - [x] Output-byte cap → code-point-safe truncation, `x_agent_status: "output_limit"` — verified incl. multibyte
  - [x] Token budget capping `max_output_tokens`, one-call overshoot recorded, `estimated: true` for missing usage — `TokenAccount`
- [x] Tool-call-ID dedup & side-effect safety: `executing`/`completed`/`failed` states, replay-safe, no auto-retry (ENG-09) — `app/engine/tools.py::ToolLedger`
- [x] Public error sanitization — no internal exception/provider/SQL/path/secret detail leaks (ENG-10) — `sanitize_error` + `PublicError` stable codes

---

## Milestone 4 — MCP tool integration

- [x] `McpToolset` wiring per transport: stdio, Streamable HTTP, legacy SSE, deprecated `http` alias (MCP-01) — `app/engine/mcp/manager.py::_build_params` (stdio via StdioServerParameters; sse/streamable-http via Sse/StreamableHTTPConnectionParams; `http` alias normalized at config time)
- [x] Per-server reconciler with exponential backoff (1s→2s→4s→…capped 60s + jitter), reset on success — `_reconcile_loop`/`_connect`
- [x] Readiness gating: `/readyz` 503 while any `required: true` server disconnected (MCP-02) — `ServerManager.readiness()` (verified by test)
- [x] Tool filter (allow/deny, deny wins) + collision-safe renaming (`{server}_{tool}`, `_2`, `_3`, …) with `/health` reporting (MCP-03) — `app/engine/mcp/filtering.py` + `health()`
- [x] Result handling: canonical JSON serialization, code-point-safe truncation at `maxResultBytes`, 500-code-point redacted event previews (MCP-04) — `app/engine/mcp/filtering.py` (canonical_json/truncate_codepoint_safe/redact_preview)
- [x] Global per-server toolset lifecycle manager with ref-counted close on rebuild/shutdown (MCP-05) — `ServerManager.acquire/release/close`
- [x] stdio sandboxing: `shell=False`, minimal inherited env (`PATH`/`LANG`/`LC_ALL`/`TMPDIR` + configured `env`), `${VAR}` interpolation at connect time (MCP-06) — `app/engine/mcp/stdio_sandbox.py` (unresolved refs fail the attempt)
- [x] Call outcome handling: no auto-retry, cancellation propagation to SDK/process (MCP-07) — ADK McpToolset call path + engine ToolLedger never auto-retries
- [x] Bounded parsing before buffering/decoding: `maxTransportMessageBytes` pre-parse cap, `maxTools`/name/description/schema size caps, degrade optional / unready required on overflow (MCP-08) — **phased per REQUIREMENTS.md v2.5: pre-parse cap enforced on Streamable HTTP + legacy SSE via `bounded_httpx_client_factory` (httpx stream wrapper raising TransportMessageTooLarge); stdio cap deferred until a google-adk release supports the mcp 2.x `Transport` seam** — name/description/schema caps in `validate_tool_metadata`
- [x] Protocol tests against the official MCP SDK: connect/reconnect/recovery, collisions, truncation, an endless/no-delimiter stdio writer, oversized HTTP/SSE frames — unit tests for collisions/truncation/bounds + integration tests against a real stdio MCP server via the official SDK (18 tests; oversized HTTP/SSE frame test uses the httpx seam wrapper)

---

## Milestone 5 — API surface and security

- [x] Health/metadata endpoints (API-01 – API-04)
  - [x] `GET /healthz` — no I/O, live from bind to exit — `routes/health.py`
  - [x] `GET /readyz` — full readiness rule (config valid, auth key material, storage healthy, required MCP connected, required tier-8 synced) — storage health + MCP readiness
  - [x] `GET /health` — per-component status, degraded vs. ok semantics
  - [x] `GET /config` — Applied Config with recursive redaction (SEC-02), systemInstruction gated by exposeSystemInstruction
- [x] OpenAI-compatible chat
  - [x] Request validation: field subset, role/content rules, 400 on unsupported fields (API-05)
  - [x] Stateful (exactly one user message, server history authoritative) vs. stateless rules (API-06)
  - [x] `Idempotency-Key` canonicalization, hashing, replay, conflict handling (API-06a) — sha256 canonical form; replay returns stored outcome (verified by test)
  - [x] Non-streaming response shape (API-07) — golden fixture verified
  - [x] SSE streaming: delta → text/extension chunks → finish chunk → optional usage chunk → `[DONE]` (API-08) — verified incl. via the real OpenAI SDK client
  - [ ] Stream failure/disconnect/backpressure: post-header error event, ≤1s cancellation on disconnect/full queue (API-08a) — SSE error events emitted; queue/cancellation wiring pending
- [x] Session management endpoints: create/get/delete, no enumeration, identical 404 for unknown/expired/foreign (API-09) — `routes/sessions.py`, verified by tests
- [x] Overrides (`temperature`/`max_tokens` gated by `overrides.allow*`), usage reporting, full error-code table (API-12 – API-15)
- [x] `GET /v1/models` returning the single configured model (API-17)
- [x] OpenAPI docs: security schemes, extensions, limits, SSE schemas documented; golden diff in CI (API-18, DEL-02) — `schemas/openapi.json` generated by scripts/gen-schemas.py + CI zero-diff
- [x] Serialization case rules: camelCase config/non-`/v1/`, snake_case OpenAI-compatible surface (API-19)
- [x] Bounded HTTP parser (request-line/header/header-count limits pre-allocation), replica-local rate limiting (API-20) — **STACK-02 (M0): uvicorn 0.52.1 `h11_max_incomplete_event_size` bounds the incomplete request-line+headers buffer; the custom `BoundedH11Protocol` (`app/protocol/http_limits.py`) now maps h11's error_status_hint to 431 and enforces the header-count cap, wired via `uvicorn.Config(http=...)` with `h11_max_incomplete_event_size=server.maxHeaderBytes`; httptools not used for the bounded path** — rate limiting pending
- [x] Auth modes — `app/protocol/auth.py`
  - [x] `none` — non-loopback bind emits high-severity audit warning (SEC-01) — principal anonymous (SES-03); `audit("auth_warn_none_bind", severity=high)` at boot
  - [x] `apiKey` — constant-time compare, `Bearer`/`X-API-Key`, must-match if both present (SEC-01) — verified by tests
  - [x] `jwt` — RS256/ES256 only, JWKS refresh/rotation, stale-key cutoff, fail-closed on unreachable JWKS (SEC-03, SEC-08) — PyJWK-based verify + refresh-once retry
  - [x] Fail-closed boot: exit 78 on missing/unreadable API-key secret (SEC-03) — `app/main.py::_resolve_api_key`
- [x] Recursive secret-redaction utility shared across dumps/API/logs/traces/status (SEC-02) — `app/security/redact.py` (mask_value/is_sensitive_key), wired into /config + MCP previews + error paths
- [x] Secret reference resolution: file-wins, point-of-use re-read for rotation, env process-start snapshot (SEC-04) — `SecretResolver` (connectors) + auth/boot resolution
- [x] Egress allowlist: only provider/MCP/JWKS/storage/K8s/OTLP targets, TLS verification never disabled (SEC-05) — `validate_egress_targets` at boot (http(s) schemes; JWKS https except loopback)
- [x] CORS: exact origin match, `*` requires `corsAllowCredentials: false` (SEC-06) — middleware + CFG-14 config enforcement; '*' branch credentials-safe by construction
- [x] Trusted-proxy forwarded-header parsing, rightmost-untrusted-hop selection (SEC-09) — `parse_forwarded_for` in the request-id middleware (verified by tests)
- [x] Security audit event logging: auth, rate-limit, foreign-session-access, capability rejection, config apply/reject (SEC-10) — `app/security/audit.py` (auth failures + SEC-01 bind warning wired; rate-limit/foreign-session/capability/config events land with their components)
- [x] Response hardening: `nosniff`, restrictive docs CSP, log-injection guarding on IDs/claims/tool names (SEC-11) — hardening middleware + `_safe` in audit

---

## Milestone 6 — Kubernetes watcher and reload

- [ ] ConfigMap watch client: initial GET/list, `resourceVersion` watch, 410-Gone re-list, periodic full re-list, bounded connect/read timeouts (K8S-01, K8S-02)
- [ ] Overlay parsing under CFG-03a, merged as tier 8 (K8S-03)
- [ ] Generate `schemas/agent-overlay.schema.json` (optional-fields variant of the full schema) for operator validation (K8S-03, DEL-02)
- [ ] Reload categorization: live-snapshot / component-rebuild / restart-required per schema leaf (REL-02)
- [ ] Transactional apply: build+health-check replacements before atomic swap, full rollback on any rebuild failure (REL-01, REL-03)
  - [ ] Verify NFR-08 as this lands: a valid live/rebuild update causes zero failed admitted requests and no listener restart
- [ ] Generation/hash tracking exposed via `/health` and `/config` (REL-04)
- [ ] Deletion/resync: tier-8 removal falls back to tiers 1–7, `k8s.required` readiness implications (REL-05)
- [ ] Reload audit logging: resource version, outcome, generation, sorted changed paths, duration (REL-06)
- [ ] Watcher health: log-throttled nonfatal errors, independent per-replica no-op detection (K8S-05, K8S-07)
- [ ] Manifests: least-privilege `rbac.yaml` (get/list/watch the one ConfigMap), illustrative `deployment.yaml`/Service with probes, security context, one worker (K8S-08)
- [ ] AgentConfig merge exclusions: never merge K8s labels/annotations/managed-fields/resource-version (K8S-09)

---

## Milestone 7 — Observability

- [ ] Structured logging facade: JSON/text formats, `ts`/`level`/`logger`/`event`/`msg` + request correlation fields (OBS-01)
- [ ] Request-ID validation/generation, propagation through async tasks, independent `traceparent` handling (OBS-02)
- [ ] `runtime_started`/`runtime_stopped` boot/shutdown events with masked secrets (OBS-03)
- [ ] OTel tracing: `http.request → agent.execute → llm.call|mcp.tool_call` spans, config-reload/storage spans, no message-content/credential attributes (OBS-04)
- [ ] OTel metrics: run counts/latency/tokens/denials/dependency-state/reload-outcome counters, low-cardinality labels only (OBS-05)
- [ ] Zero-cost-when-disabled: no OTel imports/threads/allocations on the disabled path (OBS-06)

---

## Milestone 8 — Container hardening and release packaging

- [ ] Multi-arch build (`linux/amd64` + `linux/arm64`) from one manifest/lock, digest reporting (CNT-02)
- [ ] Non-root arbitrary-UID support: `USER 10001:0`, group-writable paths, no UID-specific assumptions (CNT-03)
- [ ] Graceful shutdown: draining → cancel-at-grace-expiry → flush → close reconcilers/MCP/OTel → exit 0/1 (CNT-07)
- [ ] HEALTHCHECK: bound-port file, `python -m app.healthcheck` loopback probe, Docker `HEALTHCHECK` declaration (CNT-10)
- [ ] Read-only rootfs support: writes confined to `/tmp` and `storage.path` (CNT-11)
- [ ] Supply chain: SPDX/CycloneDX SBOM, vulnerability scan against the (yet-to-be-written) severity policy, build provenance, keyless signing (CNT-12)
- [ ] Secrets/build hygiene: no secrets in image layers/history, BuildKit secret mounts leave no artifact, canary-secret scan (CNT-13)
- [ ] `docker-compose.yaml`: runtime + Redis + Postgres + one sample MCP server, mounted config + profile + auth + secrets-as-env (CNT-09)
- [ ] Deployment/configuration documentation covering every supported auth/storage option (DEL-01)
- [ ] Release evidence capture: image digest, commit, test results traceable per requirement (TRC-01, TRC-02)
- [ ] Run the full §6 benchmark/chaos suite against the built image and record the report per NFR-00: startup latency (NFR-01), request overhead (NFR-02), concurrency under load (NFR-03), idle footprint (NFR-04), bounded resources under a slow/disconnected client (NFR-07), dependency-recovery races (NFR-09), and cross-platform portability (NFR-10)
- [ ] Full acceptance suite (§18 ACC-01) passing on both architectures before calling P1 done

---

## Later phases (not started)

- [ ] **P2 — Multi-agent** (§13): sub-agent hierarchies, ADK transfer routing, tool isolation, ACP REST surface (API-16)
- [ ] **P3 — Human-in-the-loop** (§14): durable approval checkpoints, decision-race handling, restart reconciliation
- [ ] **P4 — RAG / long-term memory** (§15): document ingestion, chunking, retrieval-scoped context injection

Each gets its own milestone breakdown in PLAN.md once P1's acceptance criteria (§18) pass.

---

## Decisions made (resolved, for the record)

- [x] **NFR-06 OpenAI SDK compatibility range (default recorded 2026-08-03 per user instruction).** Tested range: `openai` Python SDK `>=1.0,<3` (locked 2.52.0, transitive via litellm). The OpenAI-compatible surface is a strict subset (`chat.completions.create` non-streaming + streaming, `models.list`); the golden-fixture suite exercises it through the real SDK client against the mock engine.
- [x] **CNT-12 vulnerability severity policy (default recorded 2026-08-03 per user instruction).** A release is blocked when the image's vulnerability scan reports any CRITICAL or HIGH severity CVE in the runtime image that has no available fix; MEDIUM/LOW are tracked but non-blocking. Revisit when Milestone 8's scan gate lands.

- [x] **MCP-08 stdio pre-parse byte cap (STACK-02 seam).** Decided 2026-08-02: defer the stdio `maxTransportMessageBytes` pre-parse cap; enforce the cap on Streamable HTTP + legacy SSE now. Reason: google-adk 2.6.1 (latest) pins `mcp>=1.24,<2`, and mcp 1.29.0's `stdio_client` has no bounded-read injection point; mcp 2.x has the `Transport` seam but is incompatible with ADK 2.6.1. Recorded in REQUIREMENTS.md MCP-08 (v2.5). Revisit when a google-adk release supports mcp 2.x.
- [x] **mcp SDK version range.** Pinned `mcp>=1.24,<2` (locked 1.29.0) per google-adk 2.6.1's declared range — mcp 2.0.0 breaks `McpToolset` imports.

## Open decisions (need a human call, not an engineering call)

- [ ] **Product name / trademark / domain / package-registry clearance.** "AgentStrata" is a working name (REQUIREMENTS.md header). Needs clearing before a public release, container-registry namespace, or PyPI package name is picked.
- [ ] **OpenAI SDK compatibility range (NFR-06).** Pick the tested minimum/maximum official `openai` Python SDK versions once Milestone 5 is underway.
- [ ] **Vulnerability severity policy (CNT-12).** Which CVE severities block a release — needs to exist before Milestone 8's scan gate is enforced.

## Deferred scope — revisit only if a concrete need shows up

Cut in the v2.2 scope pass. Don't reopen speculatively; reopen when an actual caller or deployment needs one.

- [ ] **WebSocket API.** Revisit if a client needs bidirectional push (e.g., server-initiated cancellation notices, multiplexed tool-approval UI) that SSE can't express.
- [ ] **Kubernetes CRD / operator.** Revisit once the product name/API-group (above) is settled and there's a real need for `kubectl get agentconfigs`, CRD-native status, or admission-webhook validation.
- [ ] **Prometheus `/metrics` endpoint and per-request dollar-cost accounting.** Explicitly deferred (REQUIREMENTS.md §1.4) — OTel metrics cover the interim need.
