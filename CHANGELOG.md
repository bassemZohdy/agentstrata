# Changelog

This file records what landed, milestone by milestone, against
[REQUIREMENTS.md](REQUIREMENTS.md). P1–P5 are implemented and passing the
host-based test suite; the image-based release gates (NFR-00 benchmark,
§18 ACC-01 acceptance, multi-arch acceptance) are run at release time per
docs/release.md.

## [Unreleased]

### 2026-08-06 review backlog (R-01…R-32) — closed

Full-project review backlog from 2026-08-06: **closed**. 27 items plus
six review-loop follow-ups landed across 17 commits (`a70465d` →
`d28756c`); the host suite grew 527 → 599 tests. Per-item detail and
verification notes are in [docs/review-log.md](docs/review-log.md); the
six remaining stragglers stay tracked in [TODO.md](TODO.md).

**P0 — correctness and contract violations**

- **R-01 Middleware order:** hardening + request-id middleware now
  outermost (registration order rate-limit → auth → hardening →
  request-id), so 401/403/429 responses carry `X-Request-Id` + every
  hardening header, and SEC-10 auth-failure audits record the real
  request id (not `""`).
- **R-02 Live-snapshot reloads (the founding finding):** mutable
  `components["config"]` holder swapped atomically by both reload
  categories; every route handler re-binds the current config per
  request; `_applied_dump` renders the live generation; rebuilds
  propagate the holder. `TestLiveSnapshotLeaves` asserts one observable
  effect per leaf.
- **R-03 Per-run state on singletons:** `_run_started` moved into the
  per-run scope, `agentbase_active_runs` inc/dec strictly paired in
  `execute()`, `RagRetriever.degraded` replaced by a per-call
  `(context, degraded)` return. Verified under real concurrency (6 runs
  → gauge at 0).
- **R-04 Storage sweep scheduling:** `storage.sweepIntervalSeconds`
  (default 60 s) + lifespan sweep loop + cancellation at shutdown +
  `agentbase_storage_sweeps_total{kind}` counter; reconciliation is
  staleness-gated so in-flight runs are never raced.
- **R-05 MCP rebuild start:** replacement `ServerManager` starts inside
  the rebuild try-block (failed start rolls back to last-known-good);
  rebuild health check requires `_started`.
- **R-06 ACP approvals:** HITL-01 stateful-request guard (400
  `approval_session_required`) and the annex-shaped 202
  `run.pending_approval` response (was 500).

**P1 — robustness and security**

- **R-07 JWKS refresh cadence:** `refreshSeconds` honored via a
  last-fetched timestamp + refresh lock; `_jwks_failed` removed; JWK
  `alg` pinned; stale-key cutoff made continuous (see R-27).
- **R-08 Idempotency contract:** completed records replay; in-flight
  duplicates → 409 `idempotency_in_progress`; partial streams release
  the record.
- **R-09 Streaming body cap:** `_read_body` enforces
  `server.maxRequestBytes` before buffering.
- **R-10 Postgres connection lifecycle:** a connection **factory**
  replaces the one-shot coroutine; `_ensure` checks `closed`;
  standalone statements retry once on drop; connection pool deferred as
  a documented STACK-01 limit.
- **R-11 MCP reconciler:** dead-but-connected session probe (4 genuine
  stdio integration tests) + `maxTools` truncation propagated to the
  attached toolset.
- **R-12 Shutdown hygiene:** `reconcile_task`/`sweep_task`/`watcher_task`
  cancelled first in the close order; `_drain_after_grace` waits on the
  in-flight run tasks (early finish shortens pod termination).
- **R-13 WebSocket:** `run.start` gated by the shared per-principal rate
  limiter (OBS-05 denial counter); `maxMessageBytes` compared on the
  UTF-8 byte length, not code points; accept-then-close(1008) auth
  decision documented.

**P2 — consistency and observability**

- **R-14 Usage shape:** one shared `_normalize_usage()` across
  chat/ACP/WS (ACP no longer drops `costUsd`, WS no longer forwards the
  raw internal dict); `costUsd` field decision + the ACP streaming
  usage-chunk annex confirmation recorded in `docs/decisions.md`.
- **R-15 Reload audit:** the pre-increment generation is passed
  explicitly — a 1→2 reload logs `old_generation=1 new_generation=2`.
- **R-16 RAG isolation:** search filtered by `embedding_model` in all
  three stores; `zip(strict=True)` makes a dimension mismatch an
  explicit error; `min_score`/`top_k` ordering aligned across stores;
  ingest embedding batched at 32.
- **R-17 Metrics:** `_CardinalityGuard` keyed per metric (one
  high-cardinality metric can no longer starve the others); `# HELP`
  emitted from the instrument descriptions.
- **R-18 204 responses:** bare `Response(status_code=204)` — no body,
  no content-type (RFC 9110).
- **R-19 Session error mapping:** `_session_error` maps CapacityError →
  503 `storage_capacity`, InvalidSessionId → 400, SessionBusy → 409,
  everything else → 503 `storage_unavailable`; GET/DELETE validate the
  session id.

**P3 — dead code, hygiene, docs, CI**

- **R-20 Unwired engine contracts:** per-item decisions — `begin_iteration`
  wired; `cap_output_tokens` deleted (google-adk RunConfig cannot carry
  it); the unwired `RunConfig(temperature=…)` kwargs **removed (latent
  bug: they raised `ValidationError`, degrading every overridden run to
  `provider_error`)**; dead `ToolLedger` plumbing and `RunResult`
  deleted — `usage.estimated` now surfaces on all three API surfaces.
- **R-21 Dead code:** 8 items removed (unreachable returns, duplicate
  loggers/imports, `__import__` calls, swallowed-`OSError` log, stray
  `operator/` dir).
- **R-22 Operator:** exponential backoff with jitter (base 0.25 s, cap
  30 s) + `agentbase.operator` logger namespace.
- **R-23 Docs/CI:** traceability MAPPINGS reconciled (API-08a, CNT-02/07);
  CI job renamed + ruff steps for tests; `agentstrata.io` naming
  decision recorded.
- **R-24 Doc-drift cleanup (closed during the TODO/CHANGELOG cleanup):**
  README's doc-set table + status section now describe TODO.md as
  remaining work (stragglers + planned-scope epics) instead of a closed
  backlog; TODO.md rewritten to track only open items, with the
  per-commit review history moved to `docs/review-log.md`.

**Review-loop follow-ups (R-26…R-32 — all fixed in `d28756c`)**

- **R-26 Driver outages → 503:** sessions map non-typed exceptions to
  `storage_unavailable`; root fix at the boundaries — redis `_eval` and
  psycopg `_run`/`_PsycopgTxn` wrap driver errors as
  `BackendUnavailableError`; dead `StorageUnavailable` deleted (R-20).
- **R-27 JWKS cutoff continuous:** the stale-key cutoff is evaluated on
  EVERY request (fail-closed past 3× the refresh interval), not only at
  refresh-attempt instants; test probes between attempt boundaries
  (0/31 accepted past the cutoff, was 24/31).
- **R-28 Redis boundary narrowed:** `_eval` wraps only
  `ConnectionError`/`TimeoutError`/`BusyLoadingError`; script/argument
  errors (ResponseError/DataError) propagate as code bugs.
- **R-29 Prometheus reload honesty:** `observability.prometheus.*`
  classifies `restart_required` (the route is boot-bound — no more
  `applied_live` that 404s); the rate-limiter exempt set reads the live
  config holder.
- **R-30 Slot-leak fix (highest severity):** any admission failure in
  chat/ACP releases the run slot and maps `BackendUnavailableError` →
  503 — no permanent replica lockout after a storage blip.
- **R-31 Transaction-aware retry:** `_PsycopgDb._run` retries only
  outside a transaction; `_PsycopgTxn.__aexit__` raises when the
  original connection is gone — never COMMITs on a fresh connection
  (was: silent partial commit with false success).
- **R-32 Orphaned corpus alerting:** `orphaned_chunk_count` on all three
  stores; boot warning + `/health` `rag.orphanedChunks`; re-ingest
  story documented in `docs/deployment.md`.

### P5-4: Per-request cost-in-dollars accounting (COST-01/02) — DONE

- New `costs` config section (default disabled): USD per 1M tokens with
  `defaultInputPerMillion`/`defaultOutputPerMillion` + per-model
  overrides; duplicate model entries and negative prices are config
  errors.
- The runner computes `costUsd = (in*inPrice + out*outPrice)/1e6` when
  enabled (exact `llm.model` entry wins, else defaults), records it in
  the run outcome (`cost_usd`) + committed usage, reports it as
  `usage.costUsd` in non-streaming responses and the final streaming
  usage chunk, and records the OBS-05 `agentbase_cost_usd_total{model}`
  counter. Disabled = byte-identical OpenAI usage shape (tested).
- REQUIREMENTS: the §1.4 deferral is REMOVED (the last open deferral in
  the project); API-14 amended; COST-01/02 added; OBS-05 metric list
  extended; traceability + schemas regenerated; deployment.md costs
  section; PLAN.md P5-4.

### P5-4 finish line (2026-08-06) — usage-shape consistency and compliance

The 2026-08-05 deep review found the P5-4 surface inconsistent with its
spec; the finish line closed every gap:

- **Usage shape consistency (API-07/08, COST-01):** `_normalize_usage()`
  in `app/protocol/routes/chat.py` maps the internal
  `input_tokens`/`output_tokens`/`cost_usd` record to the
  OpenAI-compatible `prompt_tokens`/`completion_tokens`/`total_tokens`
  (+ `costUsd` when computed) and is shared by non-streaming responses
  and the final SSE usage chunk; the committed run usage now carries
  `cost_usd` (session usage stays token-only for the ENG-08 budget).
- **API-14:** the SSE usage chunk is emitted only when the client sends
  `stream_options: {"include_usage": true}` (usage is still persisted
  when not sent).
- **Type hints:** `Done.usage`, `_non_streaming_body.usage`,
  `_stream.usage`, `RunRecord.usage`, and the `update_run` usage
  parameter (contract + all backends) are `dict[str, Any]` so a float
  `cost_usd` is honest; `SessionRecord.usage` stays `dict[str, int]`.
- **Reload (REL-02):** `costs` is classified `component_rebuild` (the
  runner holds an immutable `AppliedConfig`); mock runner stores the
  internal usage shape (NFR-00/02).
- **Tests (COST-02):** streaming costUsd chunk (enabled/disabled),
  nonzero-token cost calculation through a real run (TokenLlm reports
  1000/2000 tokens → `costUsd == 0.011`), model-lookup fallback/empty
  list, disabled-cost metric absence, failed-run records no cost metric
  (OBS-05), negative-price validation extended to per-model entries,
  and the NFR-06 openai-SDK `costUsd` extra-field compatibility test.
- **Docs:** REQUIREMENTS.md COST-01/02 verified against implementation
  (typo fixed); deployment.md cost section rewritten (price table,
  matching rules, scraping, disabled default, label cardinality) +
  `## Human-in-the-loop` and `## Phase 5 extensions` headings; `/health`
  reports `capabilities.costs: true` (not phase-gated).
- **Hygiene:** unawaited coroutine fixed in test_rag.py; `_FakeRequest`
  pyright errors fixed via a `_StreamRequest` Protocol; google.adk
  MCPTool deprecation confirmed to be upstream (2.6.1/2.6.2) — our code
  already uses `McpToolset`; deferral documented.
- Host suite: **527 passed**; ruff + mypy (app, scripts, tests) clean;
  schemas zero-diff.
- **TRC-01:** the P2 ACP acceptance-annex IDs `A-1`..`A-6` are now
  mapped in the traceability matrix — the generator's ID regex also
  matches single-letter annex IDs, and regeneration is deterministic
  (184 requirements mapped).

Open deferrals (documented, not release-blocking): google.adk upstream
MCPTool replacement; multi-agent per-sub-agent cost pricing (until P2
cost tests); stdio pre-parse byte cap; ACC-01 real-instance storage
proofs; NFR-00 image-based release gates.

### P5-3: Kubernetes CRD / operator (K8S-11/12) — DONE

- `scripts/gen-schemas.py` now also emits the CRD
  `k8s_operator/crd/agentconfigs.agentstrata.io.yaml` — group
  `agentstrata.io`, v1, Namespaced, status subresource, validation schema
  generated from the SAME pydantic model as schemas/agent.schema.json
  (a config-schema change fails the gen-schemas zero-diff gate).
- New `k8s_operator` package (named to avoid shadowing the stdlib
  `operator` module): `reconcile.py` (pure: ConfigMap with the tier-8
  `agent.yaml` overlay, Deployment with the required
  `agentstrata.io/image` annotation + non-root/read-only-rootfs/drop-ALL
  security context + probes + 35 s termination grace + AGENT_K8S_* env,
  ClusterIP Service, ownerReferences for GC cleanup, fail-closed
  Ready=False status on invalid spec or missing image),
  `kube.py` (in-cluster client + FakeKubeClient substitute),
  `loop.py` (reconcile-all + streaming watch with resync fallback),
  `main.py` (CLI), and `rbac.yaml` (least-privilege ClusterRole).
- REQUIREMENTS: K8S-11/K8S-12 added, the §1.4 CRD deferral removed;
  traceability regenerated; deployment.md operator section; phase
  flipped P4 → P5 (capabilities.py + health/CLI tests).
- Tests: 10 operator tests (create/update reconcile, fail-closed status,
  observedGeneration, reconcile-all loop, manifest validity); 508 host
  tests; ruff/mypy/manifest clean.

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

### Product name decision RESOLVED (2026-08-05)

- The open human-call item is closed: the project is open-source and
  non-commercial, so trademark/domain/registry clearance is not required
  and the name stays as-is ("Agentbase" / "AgentStrata"). The clearance
  research (2026-08-05, PyPI/Docker Hub/GitHub/npm/domains/USPTO-EUIPO
  surfaces + the ParamAgent/BaseAgent/Agenter/AgentImage candidate check)
  stays on record in [docs/decisions.md](docs/decisions.md) — if the
  project ever turns commercial, the
  registry-clear fallback is `agent-strata` (free on PyPI, npm, Docker
  Hub, and GitHub).

### Product-name clearance research (recorded 2026-08-05)

- The clearance item's checkable part is DONE: every registry was
  probed and the provisional "Agentbase" name is encumbered everywhere:
  PyPI `agentbase` taken (unrelated OmniAgents package; `agent-strata` is
  free), Docker Hub namespace taken (`abi-image-v2`), GitHub login taken
  (user "AgentBase"), npm free, and `agentbase.com` (AgentBase UK, since
  ~2005), `agentbase.io` (AgentBase LLC), and `agentbase.sh` (a serverless
  AI-agent platform in the SAME product space) are all registered. No exact
  AGENTBASE USPTO/EUIPO registration surfaced, but the mark is in active
  commercial use — including Demandbase's "Agentbase" AI-agent product line
  (PRNewswire 2025-05) — so classes 9/42 carry high confusion/opposition
  risk. Verdict recorded in [docs/decisions.md](docs/decisions.md):
  provisional-only; a rename before any
  public release is strongly advised (the repo is already `agentstrata` and
  `agent-strata` is clear on PyPI). The keep-vs-rename DECISION itself
  remains a human call.

### 2026-08-05 review fixes (round 2)

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

### Review fixes (2026-08-05)

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
- **LICENSE placeholder only** — no license decision yet; product name **Agentbase** is chosen but pending trademark/domain/registry clearance (see [docs/decisions.md](docs/decisions.md)).
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
