# Changelog

Phase 1 (core runtime) is implemented and passing its host-based test suite (336 tests). This file records what landed, milestone by milestone, against [REQUIREMENTS.md](REQUIREMENTS.md). The §18 ACC-01 acceptance and NFR-08 zero-downtime reload checks now pass inside the shipped image; the only remaining M8 exit check is NFR-00. See [TODO.md](TODO.md) for the few remaining items.

## [Unreleased]

### P5-2: WebSocket API (WS-01) — DONE

- `server.protocols.websocket` (default false) enables `/v1/ws` — the
  bidirectional surface SSE cannot express: client `run.start` /
  `run.cancel` / `approval.decide` / `ping` over one connection with the
  engine's SSE vocabulary pushed back (run.started/iteration/delta/
  tool_call/tool_result/transfer/rag_degraded/approval.required/error/
  done/cancelled/approval.decided/pong).
- Auth reuses the REST provider (Authorization/X-API-Key headers or
  `?token=` injected as a bearer for browser clients); failure closes
  with 1008 + an auth_failure audit event; oversize inbound messages
  close with 1009 (bounded by server.maxMessageBytes).
- One active run per connection; runs consume the replica-local run cap;
  the output queue honors streamQueueEvents/slowConsumerSeconds (wedged
  consumers cancel the run + record the OBS-05 queue-cancellation
  metric); client disconnect cancels the active run which commits a
  terminal state (CNT-07); approval.decide routes to the same
  resume_approval engine path as REST.
- REQUIREMENTS: WS-01/WS-02 added, the §1.4 WebSocket deferral removed;
  schemas + traceability regenerated; deployment.md documents the
  endpoint + proxy upgrade guidance.
- Tests: 9 WS tests (auth reject/accept, round trip, ping/pong, cancel,
  oversize close, sequential runs, unknown-approval error, disabled-by-
  default) — verified against a REAL uvicorn server + websockets client
  as well as the TestClient suite.



### P5-1: Prometheus /metrics endpoint (OBS-05) — DONE

- User decision 2026-08-05: implement all three deferred-scope items;
  P5-1 ships the metrics endpoint first.
- `app/observability/metrics.py`: in-process registry (counters/gauges/
  histograms with the OBS-05 latency buckets through 3600 s, text
  exposition 0.0.4, per-metric label-set cap 128 with a drop warning);
  `MetricBundle` holds the instrument set.
- Config `observability.prometheus.{enabled,path}` (default "/metrics");
  cross-field validation rejects collisions with built-in routes; the
  scrape path is exempt from the replica-local rate limiter.
- Recording: runner (admitted/completed{status}/failed{code}/active-runs
  gauge/run-duration/tokens/tool+llm calls), chat route (concurrency
  denials, slow-consumer output-queue cancellations), rate-limit
  middleware (rate_limit denials), reload manager (reload outcomes).
  The Observability facade records to the registry AND the OTel meter
  when both are enabled; the registry survives live reloads.
- REQUIREMENTS OBS-05 rewritten (route now in scope), §1.4 updated;
  schemas + traceability regenerated; deployment.md documents scraping.
- Tests: registry unit tests + end-to-end route tests
  (tests/test_protocol/test_metrics.py, 8 tests) + config validation.



### TODO.md cleanup round 3 (2026-08-05)

- The completed-task records in TODO.md were removed (they have been in
  CHANGELOG since their respective rounds): the review section is now a
  pointer, and the product-name research + decision are condensed to their
  on-record facts. TODO.md now contains only the five resolved decisions
  (for the record) and the three deferred-scope items (WebSocket API,
  Kubernetes CRD, Prometheus /metrics — each with its recorded "don't
  reopen speculatively" justification).



### Product name decision RESOLVED (2026-08-05)

- The open human-call item is closed: the project is open-source and
  non-commercial, so trademark/domain/registry clearance is not required
  and the name stays as-is ("Agentbase" / "AgentStrata"). The clearance
  research (2026-08-05, PyPI/Docker Hub/GitHub/npm/domains/USPTO-EUIPO
  surfaces + the ParamAgent/BaseAgent/Agenter/AgentImage candidate check)
  stays on record in TODO.md — if the project ever turns commercial, the
  registry-clear fallback is `agent-strata` (free on PyPI, npm, Docker
  Hub, and GitHub).



### Product-name clearance research (recorded 2026-08-05)

- The TODO.md clearance item's checkable part is DONE: every registry was
  probed and the provisional "Agentbase" name is encumbered everywhere:
  PyPI `agentbase` taken (unrelated OmniAgents package; `agent-strata` is
  free), Docker Hub namespace taken (`abi-image-v2`), GitHub login taken
  (user "AgentBase"), npm free, and `agentbase.com` (AgentBase UK, since
  ~2005), `agentbase.io` (AgentBase LLC), and `agentbase.sh` (a serverless
  AI-agent platform in the SAME product space) are all registered. No exact
  AGENTBASE USPTO/EUIPO registration surfaced, but the mark is in active
  commercial use — including Demandbase's "Agentbase" AI-agent product line
  (PRNewswire 2025-05) — so classes 9/42 carry high confusion/opposition
  risk. Verdict recorded in TODO.md: provisional-only; a rename before any
  public release is strongly advised (the repo is already `agentstrata` and
  `agent-strata` is clear on PyPI). The keep-vs-rename DECISION itself
  remains a human call.



### Deferred-review items completed (TODO.md -> CHANGELOG.md, round 2)

- **Redis `KEYS` in Lua (DONE):** every blocking `KEYS` scan is gone from
  the Lua scripts. Runs and idempotency records live in per-session ZSET
  indexes (`agentbase:{tag}:runidx:{agent}:{sid}` / `...:idemidx:...`,
  member = full key, score = epoch timestamp), so capacity checks, the
  terminal-run prune, the delete-session cascade, and `list_runs` are
  exact ZRANGE/ZCARD ops; the retention sweep enumerates the indexes with
  a non-blocking SCAN (`redis.replicate_commands()` makes the
  DEL/ZREM/ZADD writes legal after the random SCAN). `expire_idempotency`
  is now an atomic DEL+ZREM script. Verified 137/137 on the real Redis 7
  matrix (Lua actually executes there, so the SCAN-in-script path is
  proven).
- **Atomic admission (`admit_run`, DONE):** the storage contract gained
  `StorageBackend.admit_run` — ensure-session (minting when absent,
  enforcing maxSessions) + create-run (enforcing maxRunsPerSession) as
  ONE atomic step returning `(session_id, admit_revision)`; idempotent on
  `run_id`. Implementations: redis = single `ADMIT_RUN` Lua script,
  postgres = single transaction, memory/file = single lock hold. The
  runner's `_admit` now calls it (budget check moved before, so a
  budget-exceeded admission writes nothing). 24 shared `TestAdmitRun`
  contract tests pass on the substitutes AND the real Redis 7 + Postgres
  16 matrix (161/161).
- **Pre-admission cancellation residual (DONE via atomic admission):** a
  cancel before `_admit` returned previously left an orphaned session
  until TTL (the run record didn't exist yet). With `admit_run` the
  session and run record appear together: a mid-admission cancel either
  finds the run record (terminal `cancelled` commit) or leaves nothing.
  `_commit_failure` still suppresses `SessionNotFound` for the
  pre-record window.
- **TODO.md cleanup round 2:** the last three deferred review items are
  now DONE records; TODO.md's only remaining `[ ]` items are the
  product-name human decision and the explicitly-deferred scope
  (WebSocket / CRD / metrics).



### Review-item cleanup (TODO.md -> CHANGELOG.md)

- **Real-backend CI matrix (ACC-01 deviation, DONE):** the
  `storage-contract-real` CI job runs the shared storage contract suite
  against real Redis 7 + Postgres 16 services; the fixtures switch on
  `AGENT_TEST_REAL_REDIS_URL` / `AGENT_TEST_REAL_POSTGRES_DSN` (per-test
  isolation, connection cleanup, Windows selector-loop shim). Verified
  locally against fresh real services: **137/137 pass**. The matrix
  surfaced and fixed real production bugs:
  - redis `eval` call shape: the backend used the FakeRedis two-list
    signature, which is a `DataError` on redis-py (list `numkeys`);
    `_eval` now dispatches both shapes.
  - redis Lua returns are BYTES on real redis-py: the `rev:`/`capacity:`/
    `missing:`/`busy:` prefix checks and the SMEMBERS/KEYS entry parses
    now decode first.
  - broken Lua patterns: `idem:[^:]*$` / `run:[^:]*$` can't cross colons
    (the intended last-segment replace never matched), so capacity counts
    were always 0/1; replaced with `:[^:]*$`.
  - expiry semantics: the Lua used wall-clock `PEXPIRE` while the
    substitutes anchor at `updated_at`/`expires_at` + ttl; both
    CREATE_SESSION/MUTATE_SESSION and CREATE_IDEM now use `PEXPIREAT`
    anchored the same way (the shared suite backdates timestamps to
    simulate expiry).
  - fence persistence: `pg_try_advisory_lock` is session-reentrant, so
    the held-check now consults a persisted `fence_expires_at` column
    (idempotent `ADD COLUMN IF NOT EXISTS` migration; SQLite substitute
    translates it to a no-op via `PRAGMA table_info`); acquire/renew
    write the expiry, release clears it, and the expired-session purge
    skips live fences.
  - psycopg auto-parses JSONB columns into dicts: the record parsers
    (`SessionRecord`/`RunRecord`/`IdempotencyRecord`/`ApprovalRecord`) and
    the backend's row reads accept both strings and dicts;
    `_parse_iso` accepts datetime objects (TIMESTAMPTZ).
  - implicit-transaction poisoning: `_PsycopgDb` never committed simple
    operations, so one failed statement aborted the dangling transaction
    and every later statement failed; simple ops now autocommit and the
    explicit `transaction()` wrapper toggles autocommit around
    BEGIN/COMMIT.
- **Postgres `mutate_session` TOCTOU (DONE):** the read-merge-CAS is a
  bounded retry (up to 3 attempts) that re-reads the fresh revision and
  re-applies the delta only when the CAS itself lost the race; a stale
  caller baseline still raises immediately. Race test:
  `TestPostgresCasRetry`.
- **Live-reload cap/limiter re-application (DONE):** the live-snapshot
  branch now re-applies `server.maxConcurrentRequests` and
  `server.rateLimit.requestsPerMinute` to the shared
  `RunSlotGate`/`FixedWindowLimiter` objects (new `set_limit` /
  `set_requests_per_minute`), so those live changes take effect
  immediately.
- **Structured shutdown audit (DONE):** `close_components` returns
  `(ok, failed_labels)` and the drain path logs one `shutdown_summary`
  line with exit code, duration_ms, and the failed components.
- **Unknown audit events warn (DONE):** `audit()` logs an
  `audit_unknown_event` warning with the offending name + fields and
  still emits the remapped `audit_unknown` record.
- **TODO.md cleanup:** completed task records moved here; TODO.md now
  tracks only the genuinely-deferred review items (Redis `KEYS` in Lua,
  pre-admission cancellation residual, non-atomic admission), the product-
  name human decision, and the explicitly-deferred scope (WebSocket/CRD/
  metrics).



### P4 — RAG / long-term memory (RAG-01..06, §15, complete)

- **RAG-01/02 (complete):** `rag` field contract with all constraints;
  the retrieval engine — deterministic code-point chunking with overlap,
  content hashing, chunk keys by agent/principal/doc/chunk/model/hash,
  principal-scoped search (descending score, stable chunk-id ties,
  minScore filter), and one delimited context message labeled untrusted
  knowledge injected before the root LLM call. MemoryRagStore +
  DeterministicEmbedding are the ACC-01 substitutes; chroma/pgvector/
  gemini/openai connector shells are import-guarded (real-instance proofs
  deferred).
- **RAG-03 (complete):** owner-scoped documents API — POST (id syntax,
  `maxDocumentBytes` bound, ≤64 KiB scalar-only metadata,
  Idempotency-Key replay, 201 with chunk count + content hash), GET
  (metadata/count/hash only, never the stored text), DELETE 204
  idempotent; atomic upsert (embedding failure leaves the previous
  version intact); registered only when rag is enabled.
- **RAG-04 (complete):** optional — one redacted log, `rag_degraded` in
  events/debug streams only, answer without context, readiness 200;
  required — readyz 503 + the run fails `rag_unavailable`; ingestion
  never degrades silently.
- **RAG-05 (complete):** rag identity changes are component rebuilds
  (no silent re-embed); delete removes all scoped chunks; SEC-04 Env/File
  secrets; document content excluded from logs/traces; backups/retention
  are deployment responsibilities.
- **Capability flip (CAP-02):** phase `P4`, `rag` true in `/health`;
  earlier fail-closed tests re-baselined; traceability regenerated (164
  IDs); **450/450 acceptance inside the image on linux/amd64 AND
  linux/arm64** (`docs/acceptance-{amd64,arm64}.{log,json}`, staleness pass).



### P3 — human-in-the-loop approvals (HITL-01..05, §14, complete)

- **HITL-05 (complete):** the restart/config-change reconciler — startup
  pass + periodic loop (failures never block boot); `resume_approval` checks
  the config generation BEFORE the CAS decide (stale approvals terminate
  `stale_approval`, the tool MUST NOT execute); timeout follows the
  onTimeout policy with the same stale/cancellation checks; decided-while-
  down approvals resume exactly once via the deterministic `resume-{id}`
  run guard; `list_all_approvals` (agent-scoped) on all four backends.
- **Capability flip (CAP-02):** phase `P3`, `approval` true in `/health`;
  P1 fail-closed tests re-baselined; traceability regenerated (164 IDs);
  **417/417 acceptance inside the image on linux/amd64 AND linux/arm64**.


- **HITL-01/02 (complete):** `ApprovalConfig` (enabled/tools/timeoutSeconds/
  onTimeout deny|allow) with fail-closed cross-field rules (approval requires
  auth + redis/postgres storage; `onTimeout: allow` requires an explicit boot
  audit); durable `ApprovalRecord` on all four backends — public surface is
  the args hash + redacted preview, the protected checkpoint holds the exact
  resume arguments; memory/file/redis (Lua CAS + global index)/postgres
  (`agent_approvals` table) implementations with 24 shared contract tests.
- **HITL-03/04 (complete):** the engine pauses before a matched tool executes
  (`RunState.AWAITING_APPROVAL`, `ApprovalRequired` event, checkpoint commit);
  `resume_approval` is a CAS decide (first wins) that executes the approved
  tool from the checkpoint via a minimal ADK `ToolContext` reusing the
  ORIGINAL tool-call ID, injects the function response into the session, and
  continues the conversation to a terminal event — resume exactly once, no
  duplicated side effects, no double gating. Client surface: chat rejects
  stateless requests with `approval_session_required` (400) while enabled;
  non-streaming pauses detach with 202 `run.pending_approval`; SSE emits
  `approval_required` then `[DONE]` (the sole API-08a disconnect exception);
  `POST /v1/approvals/{id}` (approve resumes, repeat → stored outcome,
  conflict → 409, expired → 410), `GET /v1/approvals?session_id=`
  (pending-only, public metadata), `GET/DELETE /v1/runs/{id}` (owner-scoped
  state + idempotent cancellation that cancels the pending approval).



Phase 1 milestones 0–8 are complete; the §18 ACC-01 acceptance run now passes
inside the shipped image on both architectures (336/336 each) and the NFR-08
zero-downtime reload proof passes. The only remaining M8 exit check tracked in
[TODO.md](TODO.md) is the NFR-00 benchmark/chaos run.

### Added

- **Milestone 0 — Project bootstrap (complete):** repository skeleton — `app/` package layout for the config/engine/storage/protocol/security/watcher concerns (DEL-01, free-form but independently testable; `app.main`/`app.healthcheck` fixed paths reserved), `.gitignore`, `.gitattributes`, and a `schemas/` area for generated artifacts (DEL-02).
- **Dependency manifests (STACK-01):** `requirements.txt` (direct compatible ranges) + `requirements-dev.txt`, locked with `uv pip compile` into `requirements.lock` / `requirements-dev.lock` (exact versions + hashes, universal, Python 3.12); `scripts/compile-lock.sh` / `scripts/verify-lock.sh`; review flow documented in PLAN.md.
- **STACK-02 feasibility spike (M0):** ADK session/event lifecycle — GO (public seams, e2e smoke); McpToolset lifecycle — GO on mcp 1.29.0, broken on mcp 2.0.0; MCP-08 bounded-read seam — GO for HTTP/SSE (httpx injection), no stdio seam; uvicorn 0.52.1 API-20 — partial (414/431 + header-count cap planned via custom protocol class at M5). Resulted in REQUIREMENTS.md v2.5 phase note (stdio pre-parse cap deferred per user decision) and the `mcp>=1.24,<2` pin.
- **Minimal Dockerfile (CNT-01/04/05/06/08):** digest-pinned `python:3.12-slim`, multi-stage, hash-verified venv install, exec-form `ENTRYPOINT`, one worker, no reload; `.dockerignore`. Exit check passed: running container serving on 8080.
- **CI skeleton:** lint/type-check/lockfile-hash/placeholder-test jobs (SHA-pinned actions, read-only permissions, zizmor clean).
- **LICENSE placeholder only** — no license decision yet; product name **Agentbase** is chosen but pending trademark/domain/registry clearance (see TODO.md "Open decisions").
- **Product rename — "AgentStrata" → "Agentbase":** the chosen product name propagated consistently across docs, code, identifiers, manifests, schemas, and tests (logger names, image names, package/distribution name, K8s resource names, Redis key prefix, FastAPI title). No import paths changed (the Python module stays `app.*`). Clearance is still pending, so the name remains provisional.

- **Milestone 1 — Configuration engine (complete; SCH/CFG/CAP/MODE, BASE-01):** Pydantic v2 schema with camelCase aliases, `extra="forbid"`, strict mode; full `engine`/`llm`/`tools.mcpServers`/`storage`/`server`/`k8s`/`observability` fields plus phase-gated `agents`/`approval`/`rag` stubs (SCH-01..09); bundled base `config/agent.yaml` (BASE-01); JSON Schema generation → `schemas/agent.schema.json` with CI zero-diff (SCH-02, DEL-02). Tier 1–7 resolver — bundled/mounted file discovery in exact candidate order, deep-merge (recursive map merge, wholesale list replace, null-reset), relaxed schema-aware env binding with ambiguity/near-match detection, `AGENT_APPLICATION_JSON` inline JSON, `--<dotted.path>=<value>` CLI flags, per-leaf provenance, source-safety (UTF-8, 1 MiB cap, single-mapping root, duplicate-key rejection, immutable byte snapshots) (CFG-01..11a). Validation pipeline — `model_validate` + alias-only external walk, full cross-field checklist, deterministic aggregate error reporting, capability fail-closed gating (CAP-01), `/health` capability reporting (CAP-02), exact CFG-15 boot order. Operational-mode selection — `k8s.enabled` + `KUBERNETES_SERVICE_HOST` detection, `k8s.required` fail-closed vs. warn-and-run-standalone (MODE-01..04). NFR-05 verified: identical resolver inputs (incl. permuted env-var enumeration order) produce byte-identical `--dump-config` output.

- **Milestone 2 — Storage and sessions (complete; SES):** session/run/idempotency record shapes with internal `schema_version`; storage backend interface including fencing + `StorageSettings` bounds (SES-01..03). Four backends: `memory` (in-process maps + locks + restart-loss warning), `file` (path layout `{path}/{agent}/{principal_digest}/{sid}.json`, atomic write via exclusive temp → fsync → same-filesystem replace, symlink-traversal rejection, readiness probing), `redis` (Cluster-compatible hash tags, atomic Lua revision-CAS mutations + fencing lease, TTL sweeps), `postgres` (`agent_sessions` + run/idempotency tables, transactional versioned migrations, JSONB data, session-scoped advisory-lock fencing on a dedicated connection) (SES-04/05). Retention & bounds — TTL sweeps that skip live-leased sessions and recheck revision before delete; atomic `maxSessions`/`maxRunsPerSession`/`maxIdempotencyRecordsPerSession` enforcement (SES-06/07). Cascade delete with busy-on-nonterminal-run on all four backends; per-backend `close()` flush (SES-08). ADK session-service adapter sharing one revisioned transaction path with ADK events, proven end-to-end with a real `LlmAgent` run (SES-09). 108 shared contract tests passing on all four backends (memory + file + redis(FakeRedis) + postgres(SqliteDb)).

- **Milestone 3 — Engine execution (complete; LLM/ENG):** model connectors — Gemini native + Vertex AI (ADC), LiteLLM bridge model-string mapping (`openai/`/`anthropic/`/`ollama_chat/`/verbatim), retry policy (≤2 retries on transport/429/5xx, 1s→2s backoff + jitter, honor `Retry-After`, never replay after a delta/tool call), credential health state machine (LLM-01..03). One immutable root-agent per Applied Config generation (ENG-01). `AgentRunner` façade over `Runner.run_async` with the internal `AgentEvent` union (ENG-02). Admission pipeline in the exact ENG-03 8-step order. Context bounds/pruning with uncommitted-until-success (ENG-04). Run state machine `created→running→succeeded|failed|cancelled` (+`cancelling`) with compare-and-swap terminal transition and restart reconciliation (`run_interrupted`, `tool_outcome_unknown`) (ENG-05). Transactional persistence — admit without appending history, commit pruning+turn+usage only on success, revert on failure (ENG-06). Iteration/output/token limits with correct `finish_reason`/`x_agent_status` mapping and code-point-safe truncation (ENG-07/08). Tool-call-ID dedup with `executing`/`completed`/`failed` states, replay-safe, no auto-retry (ENG-09). Public error sanitization — no internal/SQL/path/secret detail leaks (ENG-10).

- **Milestone 4 — MCP tool integration (complete; MCP):** `McpToolset` wiring per transport — stdio, Streamable HTTP, legacy SSE, deprecated `http` alias (MCP-01); per-server reconciler with exponential backoff (1s→2s→4s→…capped 60s + jitter), reset on success; readiness gating — `/readyz` 503 while any `required: true` server is disconnected (MCP-02); tool filter (allow/deny, deny wins) + collision-safe renaming (`{server}_{tool}`, `_2`, `_3`,…) with `/health` reporting (MCP-03); result handling — canonical JSON serialization, code-point-safe truncation at `maxResultBytes`, 500-code-point redacted event previews (MCP-04); ref-counted per-server toolset lifecycle manager with close on rebuild/shutdown (MCP-05); stdio sandboxing — `shell=False`, minimal inherited env, `${VAR}` interpolation at connect time (MCP-06); call-outcome handling — no auto-retry, cancellation propagation (MCP-07); bounded parsing — `maxTransportMessageBytes` pre-parse cap, `maxTools`/name/description/schema size caps (MCP-08), phased per REQUIREMENTS.md v2.5 (HTTP/SSE cap enforced via `bounded_httpx_client_factory`; stdio cap deferred until a google-adk release supports the mcp 2.x `Transport` seam). 18 protocol tests against the official MCP SDK.

- **Milestone 5 — API surface and security (complete; API/SEC):** health/metadata endpoints — `/healthz` (no I/O), `/readyz` (storage + MCP readiness; 503 `draining` during graceful shutdown), `/health` (per-component status + config generation/hash), `/config` (Applied Config with recursive redaction + `exposeSystemInstruction` gating) (API-01..04). OpenAI-compatible chat — request field-subset validation, stateful (exactly one user message) vs. stateless rules, `Idempotency-Key` SHA-256 canonicalization/replay, non-streaming response shape, SSE streaming (delta→finish→optional usage→`[DONE]`), overrides gating, usage reporting, full error-code table, `GET /v1/models`, OpenAPI docs with golden diff in CI, case rules (camelCase config / snake_case `/v1/`), bounded HTTP parser (API-05..08, 06a, 12..15, 17..20). Session management — create/get/delete, no enumeration, identical 404 for unknown/expired/foreign (API-09). Auth — `none` (high-severity audit warning on non-loopback bind), `apiKey` (constant-time compare, `Bearer`/`X-API-Key`, must-match), `jwt` (RS256/ES256, JWKS refresh/rotation, stale-key cutoff, fail-closed on unreachable JWKS), fail-closed boot on missing API-key secret (SEC-01/03/08). Recursive secret redaction across dumps/API/logs/traces/status (SEC-02); secret-ref resolution (file-wins, point-of-use re-read) (SEC-04); egress allowlist with TLS never disabled (SEC-05); CORS exact-origin match, `*` requires credentials disabled (SEC-06); trusted-proxy forwarded-header parsing (SEC-09); security audit events (SEC-10); response hardening — `nosniff`, restrictive docs CSP, log-injection guarding (SEC-11). **API-08a (complete):** streaming runs over a bounded output queue (`server.streamQueueEvents`) with a producer/consumer; the producer's `put` times out after `server.slowConsumerSeconds` of a full queue and the consumer polls client-disconnect at ≤1 s — either trigger requests run cancellation within 1 s and emits one `x_agent_event` error chunk then `[DONE]` (status stays 200, no nonstandard finish reason, no usage chunk on a cancelled stream).

- **Milestone 6 — Kubernetes watcher and reload (complete; K8S/REL):** ConfigMap watch client — initial GET/list, `resourceVersion` watch, 410-Gone re-list, periodic full re-list, bounded timeouts (K8S-01/02); overlay parsing under CFG-03a merged as tier 8 + `schemas/agent-overlay.schema.json` (K8S-03, DEL-02); reload categorization (live-snapshot/component-rebuild/restart-required) via recursive `changed_paths` (REL-02); transactional apply with build+health-check replacements before atomic swap and full rollback on any rebuild failure (REL-01/03); generation/hash tracking exposed via `/health` and `/config` (REL-04); deletion/resync — tier-8 removal falls back to tiers 1–7 (REL-05); reload audit logging (REL-06); watcher health — log-throttled nonfatal errors, independent per-replica no-op detection (K8S-05/07); least-privilege `rbac.yaml` + `deployment.yaml`/Service with probes, security context, one worker (K8S-08); merge exclusions — never merge K8s labels/annotations/managed-fields/resource-version (K8S-09). NFR-08 zero-downtime reload proof passes inside the shipped image (generations advance on both live-snapshot and component-rebuild updates, zero failed admitted requests, PID stable).

- **Milestone 7 — Observability (complete; OBS):** structured logging facade (JSON/text, `ts`/`level`/`logger`/`event`/`msg` + request correlation) (OBS-01); request-ID validation/generation + propagation + independent `traceparent` handling (OBS-02); `runtime_started`/`runtime_stopped` boot/shutdown events with masked secrets (OBS-03); OTel tracing (`http.request → agent.execute → llm.call|mcp.tool_call` + config-reload/storage spans, no message-content/credential attributes, OTLP HTTP exporter with lazy imports + bounded queues + nonfatal export failure) (OBS-04); OTel metrics (run counts/latency/tokens/denials/dependency-state/reload-outcome counters, low-cardinality labels) (OBS-05); zero-cost-when-disabled — verified opentelemetry absent from `sys.modules` when disabled (OBS-06).

- **Milestone 8 — Container hardening and release packaging (complete except M8 image-based exit checks; CNT/TRC/DEL):** multi-arch build (`linux/amd64` + `linux/arm64`) from one manifest/lock with digest reporting (CNT-02); non-root arbitrary-UID support (`USER 10001:0`, group-writable paths) (CNT-03); graceful shutdown (CNT-07) — `app/lifecycle.py::ShutdownManager` + a `ManagedServer` uvicorn wrapper: first SIGTERM/SIGINT atomically enters draining (`/readyz` 503, new chat 503, in-flight runs keep their deadline up to `shutdownGraceSeconds`, `healthz` stays live); at grace expiry it cancels in-flight runs and closes components in order (watcher→MCP→storage→OTel) then stops the listener, exiting 0 only if every flush/close succeeded (else 1); a second signal hard-exits 1; manifests set `terminationGracePeriodSeconds: 35` (≥ grace + 10 s); HEALTHCHECK — bound-port file + `python -m app.healthcheck` loopback probe (CNT-10); read-only rootfs support — writes confined to `/tmp` and `storage.path` (CNT-11); supply chain — SPDX/CycloneDX SBOM, vulnerability scan (CRITICAL/HIGH block policy), build provenance, keyless signing (CNT-12); secrets/build hygiene — no secrets in layers/history, `--require-hashes` install, canary-secret scan (CNT-13); `docker-compose.yaml` — runtime + Redis + Postgres + one sample MCP server (CNT-09); deployment/configuration documentation covering every supported auth/storage option (DEL-01); release evidence capture — image digest, commit, test results traceable per requirement (TRC-01/02). **Remaining (M8 image-based exit checks):** NFR-00 full benchmark/chaos run.

- **M8 image-based exit checks (in progress):** the §18 ACC-01 acceptance run now PASSES inside the shipped image on both architectures (336/336 each, current code; evidence in `docs/acceptance-{amd64,arm64}.{log,json}`), and the NFR-08 zero-downtime reload proof passes (generations advance on both live-snapshot and component-rebuild updates, zero failed admitted requests, PID stable; evidence in `docs/nfr-report.json`). New harness artifacts: `scripts/run-image-acceptance.sh` (per-platform build + suite-in-image with MSYS-safe mounting, shadow-proof `pythonpath=/app`, staleness gate, TRC-02 evidence JSON), `Dockerfile.test` (runtime image + locked dev tooling), and `scripts/image-nfr.py` (full image-driven NFR suite: NFR-01/02/03/04/07/08/09/10 against the shipped image under the NFR-00 environment with a deterministic local mock model). Fixes the gates surfaced: (a) google-adk 2.6.1's hardcoded 5 s stdio connect timeout fires mid-handshake on slow/emulated platforms — `wrap_stdio_params` now passes ADK's own `StdioConnectionParams(timeout=30)`; (b) `RetryableLlm` never initialized its pydantic `model` field, so ADK's request builder crashed every production run (`AttributeError`); (c) `SecretResolver()` snapshotted an empty env, so `apiKeyEnv` never resolved and every provider call went out without credentials; (d) the K8s rebuild path was broken twice over — `ReloadManager` called `build_components(config, generation)` against main.py's `(config, backend, generation)` signature (generation silently bound as backend → every rebuild failed health-check), and the chat route captured `runner` at register time (a successful swap never reached the live surface) — both fixed (explicit backend binding + per-request runner resolution); (e) the tier-8 `ConfigMapWatcher` was constructed but never `run()` in production — the watch loop was dead code, fixed by starting it from an app startup hook; (f) the reload atomic swap wiped manager-owned singletons (`watcher`/`shutdown`/`run_slots`/`run_registry`/`observability`) on the first rebuild — fixed by carrying non-replaced keys across the swap; (g) API-13 streaming never delivered real model deltas (no `streaming_mode` on the ADK RunConfig → one big end-of-call delta) — fixed by propagating `streaming` to `StreamingMode.SSE`; (h) the streaming-slot leak (`_stream`'s `finally` never released the run-slot) — fixed with a release in teardown + regression tests. Also implemented from the review backlog: the NFR-03 in-flight run cap (503 `overloaded`, `RunSlotGate`) and the API-20 replica-local rate limiter (429 + `Retry-After`, probes exempt) — both were listed in the error tables but unenforced; in-flight-run cancellation at CNT-07 grace expiry (run registry, terminal states persist before storage closes); `_stream` teardown no longer yields from `finally`; runner `execute()` split into CancelledError/GeneratorExit/Exception handlers; JWKS refresh lock; MCP release over-release guard; constant-time credential compare; `_user_state` dead storage removed; cancellation-stress and streaming-slot regression tests. Traceability generator now maps two-letter IDs (MA-01..05 deferred placeholders). Full host suite: 336 tests. **Known limitation:** the run cap and rate limiter are classified `live_snapshot` but are built once at boot, so a live reload of `server.maxConcurrentRequests` / `server.rateLimit` only takes effect on the next component-rebuild or restart.
- **M8 supply chain (CNT-12/13) exit-check run:** CycloneDX SBOM (`docs/supplychain/sbom-agentbase-amd64.cdx.json`, 3 097 components) and SPDX SBOM (`sbom-agentbase-amd64.spdx.json`, 184 packages); trivy CRITICAL/HIGH scan — 23 OS-level findings in the pinned base image with NO available fix yet (debian trixie; the pinned `python:3.12-slim` digest is the current latest), which is release-blocking per the recorded CNT-12 policy until Debian ships fixes; the scan's 2 fixable python findings (pip's vendored msgpack/setuptools) were eliminated by removing pip from the runtime image (CNT-01/12) — re-scan shows 0 fixable python findings; build provenance — buildx `--provenance=true` OCI layout with an in-toto SLSA v1 attestation; keyless signing — `.github/workflows/release.yml` (cosign keyless via GitHub OIDC on `v*` tag push; cannot run locally without an OIDC identity); canary-secret scan — `scripts/canary-scan.py` passed (layers, history, `.dockerignore` context boundary). Full host suite: 339 tests; image acceptance re-verified at 339/339 on both architectures with the pip-free runtime image.
- **Phase 2 — Multi-agent and ACP (complete; §13 + API-16, annex §13.1):** `agents[]` schema (MA-01: DNS-label names distinct from the root, systemInstruction required, description ≤ 2000, optional llm deep-merged over root, toolServers defaulting to every MCP server; nested/cyclic structurally rejected) with cross-field validation and regenerated schemas. Construction (MA-02): the root becomes an ADK coordinator with sub_agents in configured order (empty list retains P1 fixtures); ADK transfer routing; the per-run iteration budget is shared across transfers. Tool isolation (MA-03): MCP final names are now computed GLOBALLY across servers (deterministic, collision-safe); agents receive only their toolServers' tools (root = all servers), attach/detach re-syncs under a lock — which also fixed the P1 gap where MCP toolsets were never attached to any agent (tool calls always failed). Transfer events (MA-04): `agent_transfer` in event/debug streams only (text mode stays text-only — the API-13 streaming gating is now enforced); transfers recorded in the run audit, never as user-visible session messages; unknown transfer targets fail the run with `provider_error` (no silent fallback). ACP (API-16): `GET /acp/agents` manifest + `POST /acp/runs` (non-streaming + SSE with the P1 vocabulary incl. agent_transfer) per the frozen annex, registered only when `server.protocols.acp` is true. Reload (MA-05): `agents` is a component rebuild with rollback; rebuilds during in-flight runs are safe. Capability flip (CAP-02): `multiAgent`/`acp` true in /health, phase P2; approval/rag stay fail-closed; P1 gate tests re-baselined. Acceptance: 368 tests on the host and 368/368 in-image on both architectures.
- **Compose smoke + CNT-10 health marker (P1 exit check):** `docker compose up` verified with all four services (runtime healthy, sample MCP server connected over stdio via the mounted ./scripts share, redis + postgres healthy, apiKey auth 401/200). The CNT-10 bound-port marker (/tmp/agentbase.ready) was never written, so the container HEALTHCHECK failed forever; the app lifespan now writes it after bind (regression test). Full host suite: 340 tests.

### Documentation set

- [README.md](README.md), [PLAN.md](PLAN.md), [TODO.md](TODO.md), and this changelog, establishing the project's documentation set alongside REQUIREMENTS.md.
- PLAN.md's 9 dependency-ordered P1 milestones (bootstrap → config → storage → engine → MCP → API/security → K8s reload → observability → container/release), each tied to the requirement IDs it satisfies.
- TODO.md tracks only the remaining work (plus later phases, resolved decisions, open decisions, and deferred scope).

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
