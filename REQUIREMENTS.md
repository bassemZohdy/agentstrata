# Agentbase — Universal Cloud-Native Agent Runtime

| | |
| --- | --- |
| **Document** | Product and Software Requirements Specification (SRS) — authoritative implementation baseline |
| **Product name** | **Agentbase** (chosen; pending trademark, domain, and package-registry clearance) |
| **Version** | 2.4 |
| **Date** | 2026-08-02 |
| **Status** | Draft requirements baseline — see §18 for the acceptance criteria a release must satisfy |
| **Audience** | Product owners, implementers, security reviewers, SRE/platform teams, and test authors |
| **Authority** | This file is the only product-requirements source. Generated schemas and API fixtures are derived artifacts and MUST NOT contradict it. |

**Revision history**

| Version | Change |
| --- | --- |
| 1.0 | Initial consolidated baseline |
| 1.1 | Review pass: NFRs (§6), concurrency and session-serialization limits, `GET /v1/models`, `--validate`, JWT algorithm allowlist, TTL sweeps, container hardening, `engine.topP` |
| 2.0 | Independent requirements review: fail-closed phase capabilities, deterministic config parsing, bounded transports and sessions, atomic multi-replica session semantics, explicit run lifecycle, JWT/proxy hardening, transactional reload, measurable release gates |
| 2.1 | Editorial pass: each rule now stated once with cross-references replacing duplicates; removed historical commentary; no behavioral changes |
| 2.2 | Scope pass: dropped WebSocket API and the Kubernetes CRD path (ConfigMap watching only) as premature transports/surfaces; storage remains configurable across all four backends with a shared contract test plus an extra fencing proof for the multi-replica ones; replaced the internal file-tree/pytest-tooling/release-governance sections (§§17-19, GATE-01, STACK-02) with outcome-based deliverables, acceptance criteria, and traceability requirements — this document states what the runtime must do, not how it is built or tested |
| 2.3 | Final consistency pass: removed a stale "phase gate" reference to the deleted GATE-01, de-duplicated the `docker-compose.yaml` deliverable description against CNT-09, added the OpenAI SDK compatibility matrix to the deliverables list, and clarified the image-size measurement rule |
| 2.4 | Fixed a real contradiction: DEL-01 claimed internal code organization was entirely free, but CNT-04/CNT-10 fix the entrypoint/healthcheck module path (`app.main`/`app.healthcheck`) so Docker has a concrete command to invoke. DEL-01 now names that one narrow exception. |
| 2.5 | STACK-02 phase-scope decision: `maxTransportMessageBytes` enforcement is phased — Streamable HTTP and legacy SSE get the pre-parse cap in the P1 release (bounded-read seam exists via httpx injection on the locked stack); the stdio transport's pre-parse cap is deferred until a google-adk release supports the mcp 2.x `Transport` protocol seam (google-adk 2.6.1 pins `mcp>=1.24,<2`; mcp 1.29.0's `stdio_client` has no bounded-read injection point). MCP-08 now carries this note. |

**Section index**

| Phase | Sections |
| --- | --- |
| P1 core (§§2–12, 16–18) | [1](#1-purpose-and-product-overview) Purpose · [2](#2-operational-modes) Operational modes · [3](#3-externalized-configuration-engine) Config engine · [4](#4-agent-definition-schema) Agent Definition schema · [5](#5-engine-execution-adk) Engine execution · [6](#6-non-functional-requirements) NFRs · [7](#7-watcher-driven-configuration-reload) Config reload · [8](#8-sessions-and-storage) Sessions/storage · [9](#9-api-surface) API surface · [10](#10-kubernetes-watcher-mode) K8s watcher · [11](#11-security) Security · [12](#12-observability) Observability · [16](#16-container-and-runtime-packaging) Container packaging · [17](#17-required-deliverables) Deliverables · [18](#18-acceptance-criteria) Acceptance criteria |
| P2 (§13) | [13](#13-multi-agent-phase-2) Multi-agent |
| P3 (§14) | [14](#14-human-in-the-loop-approval-phase-3) Human-in-the-loop approval |
| P4 (§15) | [15](#15-rag--long-term-memory-phase-4) RAG / long-term memory |
| All phases | [19](#19-traceability-and-release-evidence) Traceability and release evidence |

### Normative conventions

**DOC-01 (document contract)** — The metadata, authority, revision rules, and conventions in this opening section are normative and are traced under this ID.

- The capitalized key words **MUST**, **MUST NOT**, **SHOULD**, **SHOULD NOT**, and **MAY** are interpreted as described by RFC 2119 and RFC 8174.
- Each normative requirement has a stable ID (`CFG-03`, `API-07`, …). Code, tests, and release evidence MUST be traceable to those IDs.
- A stable-ID block owns its opening paragraph and every immediately following continuation paragraph, list, table, example, or fixture until the next stable ID or a heading of the same/higher level. Those subordinate clauses are traced under the owning ID; a normative clause outside such a block is a lint error.
- Examples are **illustrative by default**. Only an example explicitly labeled “normative fixture” is binding.
- Normative prose takes precedence over tables, then over explicitly normative fixtures. A contradiction between normative requirements is a specification defect; implementers MUST NOT invent a precedence rule and MUST raise it for baseline correction.
- Unless stated otherwise, “byte” means an octet; sizes are measured after transfer decoding and before JSON/YAML parsing.
- All timestamps emitted by the runtime MUST be RFC 3339 UTC with a `Z` suffix and millisecond precision. OpenAI-compatible `created` fields are Unix seconds, as explicitly specified in §9.
- Public JSON/YAML field names are case-sensitive. Camel-case schema aliases and the specifically documented OpenAI snake-case fields are the only accepted public spellings.
- Where this document is silent, the implementation MUST choose the smallest behavior needed to satisfy an existing requirement and MUST NOT add externally visible features.

---

## 1. Purpose and product overview

**PROD-01 (product goal)** — This section defines the product boundary and primary success outcome.

Build a **declarative, production-grade AI agent runtime** delivered as one multi-stage Docker image. An operator supplies the agent’s instructions, model binding, tools, storage, protocols, and operational policy entirely through external configuration. Changing those concerns MUST NOT require application-code changes or rebuilding the image.

The product is a runtime and control boundary, not an agent builder. Its primary users are platform engineers and SREs who need the same agent definition to run locally, in Docker, on Kubernetes/OpenShift, or on a managed container platform.

**P1 success outcome:** given one valid Agent Definition and provider credentials, an operator can start a hardened container, call it through an OpenAI-compatible text API, use configured MCP tools, persist isolated sessions, observe health/traces/logs, and change supported configuration without losing in-flight work.

### 1.1 Technology stack (fixed — MUST use)

**STACK-01 (fixed stack and lock)** — Releases MUST use the following stack and the exact dependency versions and hashes in `requirements.lock`. `requirements.txt` declares direct compatible ranges; CI resolves and reviews lock updates. A library upgrade that changes a documented API shape or lifecycle is a requirements-impacting change, not an automatic dependency bump.

| Component | Requirement |
| --- | --- |
| Language | Python 3.12 |
| Validation | Pydantic v2 |
| HTTP server | FastAPI + Uvicorn (single async worker; see CNT-08) |
| Agent engine | Google ADK (Agent Development Kit) — owns the agent loop. Runtime persistence MUST be exposed to ADK through a session-service adapter (§8); two independent histories MUST NOT exist. |
| Multi-provider LLM | ADK's LiteLLM bridge (`google.adk.models.lite_llm.LiteLlm`) for all non-Gemini providers |
| MCP | Official Python `mcp` SDK, consumed through ADK Python’s `google.adk.tools.mcp_tool.McpToolset` only. The runtime MUST NOT implement a parallel MCP protocol client. |
| K8s client | Official `kubernetes` Python client |
| Container base | `python:3.12-slim`, multi-stage build |

**STACK-02 (dependency feasibility)** — The locked dependency versions MUST actually support the ADK session/event lifecycle, the `McpToolset` connection/cancellation lifecycle, the MCP-08 pre-buffer bounds for every enabled transport, and the API-20 Uvicorn parser bounds through supported public or explicitly stable extension seams — not private-internal monkey-patching. If a locked version cannot, the dependency choice, trust boundary, or phase scope MUST change.

### 1.2 Definitions

- **Agent Definition** — the YAML/JSON document conforming to the schema in §4.
- **Resolved Config** — the validated `AgentConfig` produced from tiers 1–7 and, in watcher mode, the current tier-8 overlay (§3).
- **Applied Config** — the immutable configuration snapshot actually used by new requests. It may differ from a rejected or restart-required desired update (§7).
- **Tier** — one source level in the configuration precedence hierarchy.
- **Principal** — the stable effective identity used for rate limiting, ownership, and audit (§11); it is derived from authentication or is the literal `anonymous` when auth is disabled.
- **Session** — a principal-owned server-side conversation identified by `session_id`, persisted per §8.
- **Run** — one execution attempt with a stable `run_id`, state, usage, and terminal outcome (§5).
- **Profile** — a named environment variant (`dev`, `prod`, …) selecting overlay files.
- **Capability** — a phase-gated feature compiled into the running build and reported by `GET /health`.

### 1.3 Delivery phases (all committed scope)

**PHASE-01 (delivery contract)** — The full product direction is committed, but only the active phase’s baselined contracts are implementation-ready.

Each phase MUST be independently releasable, retain previous contracts unless a versioned breaking change is approved, and pass its own acceptance criteria (§18) before the next phase begins.

| Phase | Contents |
| --- | --- |
| **P1 — Core runtime** | §§2–12 and §§16–18, excluding items explicitly tagged P2/P3/P4 |
| **P2 — Multi-agent and agent-to-agent REST** | Sub-agent hierarchies (§13) and the ACP surface (API-16) |
| **P3 — Human-in-the-loop** | Tool-approval flow (§14) |
| **P4 — RAG / long-term memory** | Retrieval configuration (§15) |

**CAP-01 (fail closed)** — The schema MUST parse disabled future-phase sections so one definition can move between releases. A build that does not implement a capability MUST:

- accept its absent or explicitly disabled/default configuration;
- report the capability as `false` in `GET /health`; and
- exit 78 before opening a listening socket if the configuration would enable or rely on it.

Specifically, a P1 build MUST reject non-empty `agents`, `server.protocols.acp: true`, `approval.enabled: true`, or `rag.enabled: true`. Logging a warning and continuing is prohibited.

**CAP-02** — The image MUST expose its phase and capabilities at build time and through `GET /health`. A capability MUST be reported `true` only when its acceptance suite is present and passing.

### 1.4 Explicitly out of scope

- A graphical UI of any kind.
- Fine-tuning, training, or model hosting.
- Acting as an MCP *server* (the runtime is an MCP *client* only).
- Zed's Agent Client Protocol (stdio JSON-RPC) and Google A2A. “ACP” in this document means only the phase-2 REST **Agent Communication Protocol** surface in API-16.

### 1.5 Personas and trust boundaries

**TRUST-01** — Agent Definition sources, CLI flags, mounted secret files, and the container deployment manifest are trusted operator input. HTTP requests, message content, MCP tool metadata/results, provider responses, forwarded headers, and dependency failures are untrusted.

**TRUST-02** — One running instance serves exactly one top-level agent definition but MAY serve multiple authenticated principals. Session, run, approval, and RAG data MUST be isolated by agent name and principal; knowing an identifier MUST NOT reveal whether another principal owns it.

**TRUST-03** — LLM providers, MCP servers, Redis/PostgreSQL, JWKS endpoints, Kubernetes, and OTLP collectors are external dependencies. Each dependency’s startup, readiness, retry, timeout, degradation, and secret boundary MUST be explicit in this specification.

---

## 2. Operational modes

**MODE-01** — At boot, after resolving config tiers 1–7, the runtime MUST select its mode:

- **Kubernetes watcher mode** if `k8s.enabled == true` **AND** the env var `KUBERNETES_SERVICE_HOST` is set.
- **Standalone mode** otherwise.

**MODE-02** — In both modes the runtime MUST start the FastAPI server and agent engine after tiers 1–7 pass schema, capability, and security validation. Kubernetes watcher mode is standalone mode plus the tier-8 watcher (§10); it MUST NOT disable a capability supported by the build.

**MODE-03** — If `k8s.enabled == true` but `KUBERNETES_SERVICE_HOST` is absent, behavior depends on intent: with `k8s.required: false`, log one warning (`k8s.enabled ignored: not running in a cluster`) and run standalone; with `k8s.required: true`, exit 78 before bind because a mandatory tier-8 source cannot become available.

**MODE-04** — Standalone mode does not watch local files. Configuration changes take effect only after process restart. Watcher-driven live reload is defined in §7.

---

## 3. Externalized configuration engine

### 3.1 Precedence tiers

**CFG-01** — Configuration MUST be assembled by deep-merging the following tiers in ascending order; the highest tier that supplies a leaf wins.

| Tier | Source | Notes |
| --- | --- | --- |
| 1 | Bundled base file `/app/config/agent.yaml` | Shipped in image; MUST exist |
| 2 | Bundled profile file `/app/config/agent-{profile}.yaml` | Skipped if absent |
| 3 | Mounted base file — first existing of `{configDir}/agent.yaml`, `{configDir}/agent.yml`, `{configDir}/agent.json`, `{configDir}/config.yaml`, checked in that order | Only the first match is loaded; skipped if none |
| 4 | Mounted profile file — first existing of `{configDir}/agent-{profile}.yaml`, `{configDir}/agent-{profile}.yml`, `{configDir}/agent-{profile}.json`, checked in that order | Only the first match is loaded; skipped if none |
| 5 | OS environment variables with prefix `AGENT_` (relaxed binding, §3.3) | |
| 6 | Inline JSON env var `AGENT_APPLICATION_JSON` (a single JSON object) | Invalid JSON here is a **fatal boot error** |
| 7 | CLI flags `--<dotted.path>=<value>` (§3.4) | |
| 8 | Kubernetes watched-resource overlay (§10) | Highest; watcher mode only; MAY be a partial object |

**CFG-02** — `configDir` defaults to `/etc/agent` and MAY be overridden by `AGENT_CONFIG_DIR`, then by `--config-dir`. It MUST be an absolute path. This bootstrap setting is never read from an Agent Definition.

**CFG-03 (profile bootstrap)** — The active profile MUST be read only from `AGENT_PROFILE`, overridden by `--profile`. It MUST match `^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$`; path separators, `..`, whitespace, and comma-separated profiles are invalid. A top-level `profile` field is informational and MUST NOT select files. If no active profile exists, tiers 2 and 4 are skipped.

**CFG-03a (source parsing)** — Every file and tier-8 payload MUST be UTF-8, no larger than 1 MiB, and contain one mapping at its root. YAML loaders MUST use safe mode and reject duplicate mapping keys rather than silently accepting the last value. JSON duplicate keys MUST likewise be rejected. Empty files, invalid encoding, aliases that create recursive structures, non-object roots, and parse failures are configuration errors naming the source and exiting 78 at boot; an invalid tier-8 update follows REL-01.

**CFG-03b (source selection)** — When two candidate files exist in the same tier, the resolver MUST load only the first path in CFG-01 and log one warning listing ignored siblings. A missing optional file is not an error. File reads MUST use a single immutable byte snapshot so a concurrent mount update cannot produce a partially parsed document.

### 3.2 Merge semantics

**CFG-04** — Merging mappings MUST be recursive. If both values are mappings, recurse; otherwise the higher-tier value replaces the lower-tier value. The resolver MUST retain provenance for every resulting leaf, including values supplied by schema defaults.

**CFG-05 (lists)** — Lists MUST be **replaced wholesale**, never merged element-wise. If tier 5 defines `tools.mcpServers`, the lower tiers' list is discarded completely.

**CFG-06 (null reset)** — For a schema-defined field, explicit `null` removes every lower-tier value and requests the field’s declared default. If the field has no default, it becomes missing and validation fails. For a nested model, `null` resets the whole model to its defaults; for a list it resets the whole list. Inside an explicitly passthrough mapping, `null` remains literal JSON null. A reset value’s provenance is the tier that supplied `null`, annotated as `reset-to-default`.

### 3.3 Relaxed environment binding

**CFG-07** — Environment binding MUST be schema-aware and deterministic:

1. Enumerate schema-defined leaf paths, plus whole list/model/passthrough-map paths; list indexes and individual passthrough keys are not bindable.
2. Build a canonical environment alias by converting each camel-case path segment to upper snake case and joining segments with `_` after `AGENT_`.
3. Compare the supplied suffix and aliases case-insensitively after removing underscores. A unique match binds; zero matches follows CFG-08; more than one match is a fatal ambiguity.

Thus `AGENT_ENGINE_SYSTEM_INSTRUCTION` binds to `engine.systemInstruction` and `AGENT_LLM_MODEL` to `llm.model`. If multiple environment variables bind the same target path, resolution MUST fail rather than depend on OS enumeration order.

**CFG-08** — An `AGENT_*` variable that matches no schema path MUST log a warning naming the variable and at most three closest paths, then be ignored. Reserved resolver variables — `AGENT_PROFILE`, `AGENT_CONFIG_DIR`, and `AGENT_APPLICATION_JSON` — are exempt. A variable that almost matches a security-sensitive path (`*KEY*`, `*TOKEN*`, `*SECRET*`, `*PASSWORD*`) MUST be named in the warning but its value MUST never be logged.

**CFG-09 (values)** — Lists, nested models, and passthrough maps MUST be supplied as JSON values. Scalars use target-aware parsing: booleans accept only case-insensitive `true` or `false`; integers use base 10; floats MUST be finite; enums use their documented literal values; strings remain unchanged. The case-insensitive literal `null` invokes CFG-06 for every type. Empty strings are valid only for string fields. A failure MUST exit 78 and name the source, path, and expected type without echoing a value from a secret-sensitive path.

### 3.4 CLI flags

**CFG-10** — The entrypoint MUST accept `--<dotted.path>=<value>` for any bindable schema path, plus `--profile <name>`, `--config-dir <absolute-path>`, `--dump-config`, `--validate`, `--version`, and `--help`. Dotted-path values follow CFG-09. If the same dotted path occurs more than once, the last CLI occurrence wins and a warning names the path. Unknown paths, positional arguments, missing values, and malformed flags exit 64 (EX_USAGE) with the closest valid paths.

**CFG-10a (`--validate`)** — Resolve and validate tiers 1–7 without starting the server, opening network connections, reading Kubernetes, or resolving referenced secret contents. On success print exactly `OK\n` to stdout and exit 0. On configuration failure print the CFG-12 aggregate report to stderr and exit 78. `--validate` and `--dump-config` are mutually exclusive.

**CFG-11 (`--dump-config`)** — Resolve and validate tiers 1–7, recursively mask secrets per SEC-02, then print canonical YAML and exit without starting components. Canonical output MUST use schema field order, lexicographically sorted passthrough-map keys, stable scalar quoting, UTF-8, LF endings, one final newline, and no timestamps. Every leaf (including list-item leaves) MUST have a winning-source comment such as `temperature: 0.2  # tier 7: cli`; defaulted and reset values MUST be labeled. Stdout contains only YAML; diagnostics go to stderr. Identical inputs MUST produce byte-identical output.

**CFG-11a** — `--version` MUST print the runtime semver, build commit or `unknown`, schema major, and supported phase, then exit 0 without loading configuration. `--help` MUST document every bootstrap flag and exit 0.

### 3.5 Validation

**CFG-12** — After merging and applying null resets, the result MUST be validated once with `AgentConfig.model_validate()`, followed by capability and cross-field validation. Every independent error MUST be reported in one deterministic aggregate sorted by config path and error code. Each item includes the source tier and path; secret values are omitted. Boot and CLI validation failures exit 78. A tier-8 failure follows REL-01 and never terminates the process.

**CFG-13** — Unknown top-level or nested fields MUST be rejected (`extra="forbid"`). Before Pydantic validation, every external YAML/JSON/env/inline/CLI/tier-8 mapping key MUST pass an alias-only shape walk against the generated camelCase schema; Python field names such as `system_instruction` are rejected externally even though internal direct model construction may use them. The only passthrough maps are `llm.extra` and `rag.store.options`; their keys remain arbitrary, but SEC-02 still applies recursively. `$schema` and `schemaVersion` are explicit top-level fields, not unknown-field exceptions.

**CFG-14** — Cross-field validation MUST enforce at minimum:

- `storage.type` in `{redis, postgres}` ⇒ `storage.connectionStringEnv` or `storage.connectionStringFile` is set.
- `storage.type == "file"` ⇒ `storage.path` is set.
- MCP `transport == "stdio"` ⇒ `command` set and `url` unset; `transport` in `{sse, streamable-http}` ⇒ `url` set and `command` unset.
- `server.auth.mode == "apiKey"` ⇒ `server.auth.apiKeyEnv` or `apiKeyFile` set; `mode == "jwt"` ⇒ `server.auth.jwt.issuer` and `jwksUrl` set.
- At least one protocol implemented by the active phase is enabled; a health-only runtime is invalid.
- `llm.provider == "ollama"` ⇒ `llm.baseUrl` set.
- `llm.vertex.enabled` ⇒ provider is `gemini`, project is non-empty, and API-key refs are absent; any non-default Vertex field with another provider is invalid.
- MCP server names are unique; timeout and size limits are positive; static `headers` contain no secret-sensitive key names defined by SEC-02.
- Each MCP `maxResultBytes` is no greater than its `maxTransportMessageBytes`.
- `engine.overrides.temperatureMax` and `maxTokensMax` are not lower than their corresponding configured defaults.
- `server.maxMessageBytes ≤ server.maxRequestBytes`; `engine.maxOutputBytes ≤ 16777216`.
- `k8s.required: true` ⇒ `k8s.enabled: true`.
- `server.auth.jwt.principalClaim` is non-empty; every trusted proxy entry parses as a CIDR.
- Capability validation follows CAP-01 after schema validation and before any secret or network access.

**CFG-15 (validation order)** — Boot order MUST be: parse bootstrap flags → read tiers 1–7 → merge/reset → schema and cross-field validation → capability validation → establish the fail-closed auth state required by SEC-03 → construct components → bind the server → start dependency reconcilers. No listening socket may open before configuration/capability validation and API-key validation complete.

---

## 4. Agent Definition schema

### 4.1 General

**SCH-01** — Pydantic v2 models MUST use `model_config = ConfigDict(populate_by_name=True, alias_generator=to_camel, extra="forbid", strict=True)`. Agent Definition documents use camelCase and CFG-13 performs external alias-only validation before `model_validate`. Snake-case input is accepted only for direct internal Python construction and MUST NOT be advertised as a public document format. File/JSON tiers rely on already typed parser values and do not receive Pydantic string coercion; env/CLI coercion is only CFG-09.

**SCH-02** — JSON Schema draft 2020-12 MUST be generated from `AgentConfig.model_json_schema()`, assigned a stable `$id` containing schema major 1, committed at `schemas/agent.schema.json`, and checked for a zero diff in CI. `schemaVersion` is the compatibility gate: adding optional fields is backward-compatible; removing, renaming, changing defaults, tightening accepted values, or changing behavior requires a new schema major and migration notes.

### 4.2 Field reference (normative)

**SCH-03 (core field contract)** — Every field through the `llm` table below MUST exist with the listed type, default, and constraint. “Secret ref pair” means two optional non-empty strings `<name>Env` and `<name>File`; when both are set, file wins (SEC-04). Secret contents never become part of `AgentConfig`; resolution returns a separate secret value at the point of use.

#### Top level

| Field | Type | Default | Constraints |
| --- | --- | --- | --- |
| `$schema` | str | `""` | Informational URI used by editors; retained in dumps, not fetched at runtime |
| `schemaVersion` | int | `1` | MUST equal `1` in this specification |
| `name` | str | — **required** | `^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$` (DNS-1123 label) |
| `description` | str | `""` | ≤ 2,000 Unicode code points |
| `profile` | str | `""` | Informational only (CFG-03) |

#### `engine`

| Field | Type | Default | Constraints |
| --- | --- | --- | --- |
| `systemInstruction` | str | — **required** | non-empty |
| `temperature` | float | `0.7` | `0.0 ≤ x ≤ 2.0` |
| `topP` | float | `1.0` | `0.0 < x ≤ 1.0`; nucleus sampling, passed to the model when the provider supports it |
| `maxTokens` | int | `4096` | `1..1000000`; max **output** tokens per LLM call |
| `maxOutputBytes` | int | `1048576` | `1..16777216`; max accumulated assistant UTF-8 bytes per run |
| `timeoutSeconds` | int | `300` | `1..3600`; wall-clock deadline for one **entire** request (all LLM calls + tools). On expiry: abort, return error `agent_timeout` |
| `maxIterations` | int | `10` | `1..1000`; max LLM→tool→LLM cycles. Exhaustion returns any completed text with `finish_reason: "length"` and `x_agent_status: "iteration_limit"` |
| `historyMaxMessages` | int | `200` | `1..10000`; maximum complete persisted conversation messages used as context |
| `historyMaxBytes` | int | `4194304` | `1024..67108864`; maximum UTF-8 bytes of persisted conversational content per session |
| `streaming` | enum | `"text"` | `text \| events \| debug` (API-13) |
| `overrides.allowTemperature` | bool | `true` | Per-request override permission (API-12) |
| `overrides.allowMaxTokens` | bool | `true` | |
| `overrides.temperatureMax` | float | `2.0` | Cap for overridden values |
| `overrides.maxTokensMax` | int | `8192` | `1..1000000`; cap for overridden values |
| `tokenBudget.perRequest` | int | `0` | `0..1000000000000`; `0` = unlimited; total tokens (in+out) per request |
| `tokenBudget.perSession` | int | `0` | `0..1000000000000`; `0` = unlimited; cumulative per session; exceeding ⇒ error `budget_exceeded` |

#### `llm`

| Field | Type | Default | Constraints |
| --- | --- | --- | --- |
| `provider` | enum | `"gemini"` | `gemini \| openai \| anthropic \| ollama \| litellm` |
| `model` | str | — **required** | 1..256 code points; MUST NOT be validated against a hardcoded model list |
| `apiKey(Env/File)` | secret ref pair | unset | |
| `baseUrl` | str | `""` | Required for `ollama` (CFG-14); optional custom endpoint otherwise |
| `contextWindowTokens` | int | `0` | `0` = unknown; otherwise `> engine.maxTokens` and used by ENG-04 |
| `vertex.enabled` | bool | `false` | `gemini` only: use Vertex AI with Application Default Credentials instead of API key |
| `vertex.project` | str | `""` | Required when `vertex.enabled` |
| `vertex.location` | str | `"us-central1"` | |
| `extra` | dict[str, JSON value] | `{}` | Passthrough kwargs to the model connector; sensitive-key rules in SEC-02 apply |

**LLM-01** — `provider: gemini` with `vertex.enabled: false` MUST use ADK's native Gemini model with the API key. `vertex.enabled: true` MUST use ADC (no key required). All other providers MUST be constructed via ADK's LiteLLM bridge with the LiteLLM model string formed as: `openai` → `openai/{model}`, `anthropic` → `anthropic/{model}`, `ollama` → `ollama_chat/{model}` (with `api_base = baseUrl`), `litellm` → `{model}` used verbatim (escape hatch for any LiteLLM-supported provider).
**LLM-02** — Missing or invalid model credentials MUST NOT crash boot unless those credentials also protect runtime authentication under SEC-03. A missing required credential sets health `llm.status: "unavailable"`; present but untested credentials start `"unknown"`; a successful call sets `"available"`; a provider authentication failure sets `"unavailable"`. Each request re-resolves a file-backed point-of-use credential, so that unavailable state is not sticky after projected-file rotation; env-backed values remain the process-start snapshot under SEC-04 and require restart. A still-missing/invalid credential fails that request with `provider_auth` without secret detail (health-status implications are governed by API-03).
**LLM-03** — A provider call that fails before any response delta MAY be retried at most twice for transport errors, HTTP 429, or HTTP 5xx. Backoff is 1 s then 2 s plus 0–250 ms jitter and MUST honor a longer valid `Retry-After`, all within the run deadline. Once a delta has been emitted or a tool call has begun, that call MUST NOT be automatically replayed. Provider authentication, invalid requests, content-policy failures, and quota/billing failures are not retried. There is no cross-model fallback.

#### `tools.mcpServers[]`

**SCH-04 (MCP field contract)** — `tools.mcpServers` is a list with default `[]`, replaced wholesale per CFG-05; every item has the fields and constraints below.

| Field | Type | Default | Constraints |
| --- | --- | --- | --- |
| `name` | str | — **required** | Unique within the list; DNS-1123 label |
| `transport` | enum | — **required** | `stdio \| sse \| streamable-http`. (`http` MUST be accepted as a deprecated alias for `streamable-http` with a warning) |
| `url` | str | `""` | For sse/streamable-http |
| `command` | str | `""` | For stdio |
| `args` | list[str] | `[]` | stdio |
| `env` | dict[str,str] | `{}` | Explicit stdio child env; values MAY contain exact `${VAR}` interpolation |
| `headers` | dict[str,str] | `{}` | Non-secret static HTTP headers |
| `secretHeaders` | dict[str, SecretHeaderRef] | `{}` | Each value is `{env?, file?, prefix?: ""}`; file wins; rendered value is `prefix + secret` |
| `authToken(Env/File)` | secret ref pair | unset | If set, sent as `Authorization: Bearer <token>` |
| `required` | bool | `false` | `true` ⇒ gates readiness (MCP-02) |
| `toolFilter.allow` | list[str] | `[]` | Empty = all tools |
| `toolFilter.deny` | list[str] | `[]` | Deny wins over allow |
| `connectTimeoutSeconds` | int | `10` | `> 0`; each connection attempt |
| `timeoutSeconds` | int | `30` | `> 0`; each tool call |
| `maxTools` | int | `128` | `1..1000`; maximum admitted discovered tools |
| `maxTransportMessageBytes` | int | `1048576` | `4096..16777216`; pre-parse cap for every inbound MCP message |
| `maxResultBytes` | int | `100000` | `1..4194304` and ≤ transport cap; UTF-8 bytes admitted into model context |

**MCP-01 (degraded start)** — Boot MUST NOT fail because an MCP server is unreachable. Each server has an independent reconciler with exponential backoff 1 s → 2 s → 4 s → … capped at 60 s plus 0–250 ms jitter. A successful connection resets backoff. On connection, the runtime discovers and filters tools through `McpToolset`; on disconnect, new runs stop seeing those tools while in-flight calls terminate by timeout/cancellation. Legacy `sse` and alias `http` MUST log one deprecation warning per config generation; Streamable HTTP is the preferred remote transport.
**MCP-02 (readiness)** — `/readyz` MUST return 503 while any `required: true` server is disconnected; optional servers never gate readiness. `/health` MUST report per-server status (API-03).
**MCP-03 (filter and naming)** — Filters match exact raw MCP tool names and are applied before collision handling; deny wins. Servers and their discovered tools are processed in configured-list order, then server-provided order. The first unused raw name is retained. If already used, try `{serverName}_{rawName}`; if that is used, append `_2`, `_3`, and so on until unique. Every rename MUST be logged and reported in `/health`. The final name MUST satisfy the active model connector’s tool-name constraints; an incompatible tool is excluded and marks a required server unready.
**MCP-04 (results)** — Structured results MUST first be serialized to canonical JSON; text remains text. Before model ingestion, encode as UTF-8 and, when larger than `maxResultBytes`, truncate at a code-point boundary so the final bytes including `\n[truncated by runtime]` do not exceed the limit. External event previews are separately capped at 500 Unicode code points and recursively redacted.
**MCP-05 (lifecycle)** — One runtime-global `McpToolset`/connection lifecycle exists per configured server and is shared across sessions. The manager owns explicit async close on rebuild and shutdown; removed components close only after their in-flight reference count reaches zero or the shutdown deadline expires.
**MCP-06 (stdio boundary)** — Stdio processes MUST launch with `shell=False`. Their environment consists only of present values from `PATH`, `LANG`, `LC_ALL`, and `TMPDIR`, plus the configured `env` map. They MUST NOT inherit the full runtime environment. Each exact `${VAR}` is resolved from the parent environment at connection time; an unset reference causes that server’s connection attempt to fail without revealing the variable value.
**MCP-07 (call outcome)** — Tool calls are never automatically retried. Timeout or transport failure becomes one structured error result visible to the agent and counts as an iteration. Request cancellation MUST propagate to the SDK/tool process; if cancellation is unsupported, the result is discarded and the run still terminates by its deadline.
**MCP-08 (untrusted metadata and framing)** — The adapter supplied to the official MCP SDK MUST enforce `maxTransportMessageBytes` while reading each HTTP/SSE/stdio message, before full buffering or JSON decoding; exceeding it disconnects that server and records a bounded error. Discovery admits at most `maxTools` after filtering. A tool name is at most 128 UTF-8 bytes, a description at most 4,096 code points, and canonical input schema at most 65,536 bytes; an oversized tool is excluded. Exclusion/overflow marks an optional server degraded and a required server unready. The locked SDK/ADK versions MUST expose a tested bounded-stream seam; replacing `McpToolset` with a parallel protocol client is not an allowed workaround. **Phase-scope note (2.5):** the pre-parse byte cap is enforced on Streamable HTTP and legacy SSE transports from the P1 release (the `httpx_client_factory`/`http_client` injection seam exists on the locked stack). The stdio transport's pre-parse cap is deferred: google-adk 2.6.1 (latest) pins `mcp>=1.24,<2`, and mcp 1.29.0's `stdio_client` offers no bounded-read injection point; mcp 2.x's documented `Transport` protocol seam is incompatible with ADK 2.6.1. Stdio servers remain fully supported; the config field still validates for them, but the cap is documented as not yet enforced on stdio — revisit when a google-adk release supports the mcp 2.x `Transport` seam.

#### `storage`

**SCH-05 (storage field contract)** — The storage fields below are required with the listed defaults and constraints.

| Field | Type | Default | Constraints |
| --- | --- | --- | --- |
| `type` | enum | `"memory"` | `memory \| file \| redis \| postgres` |
| `path` | str | `""` | `file` type: directory for session JSON files |
| `connectionString(Env/File)` | secret ref pair | unset | `redis` / `postgres` |
| `sessionTtlSeconds` | int | `86400` | `0` or `≥ 60`; `0` disables age expiry, not the capacity limit |
| `runTtlSeconds` | int | `604800` | `≥ 60`; maximum terminal run/audit retention |
| `maxSessions` | int | `10000` | `> 0`; per agent across all principals |
| `maxRunsPerSession` | int | `1000` | `> 0`; oldest terminal records may be removed first |
| `maxIdempotencyRecordsPerSession` | int | `1000` | `> 0`; in-progress/unexpired records are never evicted |
| `lockAcquireSeconds` | float | `0.0` | `0` = fail immediately with `session_busy`; otherwise `0 < x ≤ 5` |
| `idempotencyTtlSeconds` | int | `86400` | `≥ 60`; retention for completed idempotency records |

See §8 for behavior.

#### `server`

**SCH-06 (server field contract)** — The serving, protocol, authentication, and transport-limit fields below are required with the listed defaults and constraints.

| Field | Type | Default | Constraints |
| --- | --- | --- | --- |
| `host` | str | `"0.0.0.0"` | |
| `port` | int | `8080` | 1–65535 |
| `protocols.openaiCompat` | bool | `true` | §9 REST + SSE |
| `protocols.acp` | bool | `false` | Phase 2; CAP-01 forbids enabling it in P1 |
| `corsOrigins` | list[str] | `["*"]` | |
| `corsAllowCredentials` | bool | `false` | MUST remain false when origins contains `*` |
| `auth.mode` | enum | `"none"` | `none \| apiKey \| jwt` |
| `auth.apiKey(Env/File)` | secret ref pair | unset | Compared constant-time against `Authorization: Bearer <key>` or `X-API-Key` header |
| `auth.jwt.issuer` | str | `""` | |
| `auth.jwt.audience` | str | `""` | Empty = not checked |
| `auth.jwt.jwksUrl` | str | `""` | HTTPS except loopback development; SEC-08 |
| `auth.jwt.principalClaim` | str | `"sub"` | Claim MUST exist as a non-empty string |
| `auth.jwt.refreshSeconds` | int | `3600` | `≥ 60`; background JWKS refresh interval |
| `auth.jwt.timeoutSeconds` | int | `5` | `> 0`; JWKS request timeout |
| `rateLimit.enabled` | bool | `false` | |
| `rateLimit.requestsPerMinute` | int | `60` | `> 0`; per-principal fixed window; counters are replica-local |
| `trustedProxyCidrs` | list[str] | `[]` | Only these direct peers may supply forwarding headers |
| `maxConcurrentRequests` | int | `100` | `1..10000`; in-flight chat runs beyond the cap ⇒ 503 `overloaded` |
| `maxRequestLineBytes` | int | `8192` | `1024..16384`; enforced by the HTTP parser |
| `maxHeaderBytes` | int | `32768` | `4096..131072`; aggregate names/values/line framing |
| `maxHeaderCount` | int | `100` | `1..200`; applies to HTTP requests |
| `maxRequestBytes` | int | `1048576` | `1024..16777216`; maximum decoded HTTP body; larger ⇒ 413 |
| `maxMessageBytes` | int | `262144` | `1..4194304` and ≤ request cap; maximum UTF-8 bytes in one chat message |
| `streamQueueEvents` | int | `64` | `1..1024`; bounded per-client SSE output queue |
| `slowConsumerSeconds` | int | `10` | `1..300`; full queue for this duration cancels the run |
| `exposeSystemInstruction` | bool | `false` | Whether `GET /config` includes `engine.systemInstruction` |
| `shutdownGraceSeconds` | int | `25` | `1..300`; CNT-07 |

#### `k8s`

**SCH-07 (Kubernetes field contract)** — The watcher fields below are required with the listed defaults and constraints.

| Field | Type | Default | Constraints |
| --- | --- | --- | --- |
| `enabled` | bool | `false` | |
| `required` | bool | `false` | When true, readiness waits for one valid tier-8 sync |
| `namespace` | str | `"default"` | |
| `name` | str | value of top-level `name` | Name of the watched ConfigMap |
| `resyncSeconds` | int | `300` | `≥ 30`; full re-list interval |

#### `observability`

**SCH-08 (observability field contract)** — The logging and telemetry fields below are required with the listed defaults and constraints.

| Field | Type | Default | Constraints |
| --- | --- | --- | --- |
| `logLevel` | enum | `"INFO"` | `DEBUG \| INFO \| WARNING \| ERROR` |
| `logFormat` | enum | `"json"` | `json \| text` |
| `includeToolArguments` | bool | `false` | Applies to logs/traces only; never disables SEC-02 redaction |
| `otel.enabled` | bool | `false` | When true, exporter config comes from standard `OTEL_EXPORTER_OTLP_*` env vars |
| `otel.serviceName` | str | value of top-level `name` | |

#### Phase-gated sections

**SCH-09 (phase-section contract)** — Future-phase sections remain explicitly present and fail closed.

`agents[]` (§13, P2), `approval` (§14, P3), and `rag` (§15, P4) MUST exist in every phase schema with empty/disabled defaults. CAP-01 governs unsupported builds. Unknown non-default fields in a future schema major are still rejected; forward compatibility never permits fail-open behavior.

### 4.3 Bundled base config (tier 1, normative content)

**BASE-01 (bundled definition)** — The bundled definition is a release-tested operational default, not an alternate schema.

`/app/config/agent.yaml` MUST be semantically equivalent to schema defaults with these explicit required values:

```yaml
schemaVersion: 1
name: "agent"
engine:
  systemInstruction: "You are a helpful assistant."
llm:
  provider: "gemini"
  model: "gemini-2.5-flash"
  apiKeyEnv: "GEMINI_API_KEY"
```

The model name is an image default, not a hardcoded allowlist. CI MUST validate this bundled file through the same resolver used at runtime.

---

## 5. Engine execution (ADK)

**ENG-01** — Each Applied Config generation MUST own one immutable root-agent component. It constructs ADK `LlmAgent` with `name`, `instruction = engine.systemInstruction`, model per LLM-01, `temperature`, `top_p`, `max_output_tokens`, and the connected/filtered MCP toolsets. A component swap follows REL-02; a run never observes half of two generations.

**ENG-02** — Execution MUST go through an `AgentRunner` façade wrapping ADK `Runner.run_async` and accepting normalized messages, principal/session context, overrides, a cancellation token, and the Applied Config snapshot. Its internal `AgentEvent` union is `text_delta`, `tool_call`, `tool_result`, `agent_transfer` (P2), `approval_required` (P3), `iteration`, `done`, and `error`. Adapters in §9 are the only code that converts these events to public protocols.

**ENG-03 (admission order)** — Every HTTP chat attempt MUST pass this order:

1. assign/validate request ID and enforce transport byte limits;
2. authenticate and derive the principal;
3. verify the route capability and parse/validate the request;
4. enforce rate limit and global concurrency;
5. resolve or atomically create the session, idempotency record, and distributed session lease;
6. check session/request budget eligibility;
7. create a run record and start the monotonic deadline;
8. execute iteration and token controls.

Rejected attempts before step 7 MUST NOT mutate session history. Locks, concurrency permits, and component references MUST be released in `finally` blocks.

**ENG-04 (context bounds)** — The configured system instruction is always first and is never pruned. For a stateful run, before each LLM call the runtime MUST form a candidate context by removing the oldest complete persisted conversational turns until both `historyMaxMessages` and `historyMaxBytes` are satisfied after including the pending user message. The removals are not committed until the successful ENG-06 transaction, which then sets `historyTruncated: true`. When `llm.contextWindowTokens > 0`, the candidate MUST additionally drop oldest complete turns until estimated input plus reserved output fits. The pending user message is never pruned; if it cannot fit, fail with `context_length_exceeded`. For stateless calls, client-supplied messages are never silently pruned; an input known not to fit fails with that code. If the context window is unknown, a provider context error maps to the same code. Summarization is out of scope.

**ENG-05 (run state/recovery)** — A run has `run_id` (UUIDv4), `request_id`, principal, optional session, config generation, timestamps, cumulative usage, state, and terminal outcome. P1 transitions are `created → running → succeeded|failed|cancelled`, with optional `running → cancelling → cancelled`. P3 adds `running ↔ awaiting_approval` under §14. A compare-and-swap transition MUST make exactly one terminal state win in timeout, disconnect, shutdown, and approval races. After restart or definitive ownership loss, a persistent P1 nonterminal run is never resumed: once its lease is absent, reconcile it to `failed/run_interrupted`; if a tool record was `executing`, use `tool_outcome_unknown`. Complete any associated idempotency record with that stored failure. P3 `awaiting_approval` records follow HITL-05 instead.

**ENG-06 (stateful persistence)** — On admission, atomically create the run record with the bounded normalized input, but do not append that input to conversational history. At successful completion, one revision-checked transaction commits any ENG-04 pruning, appends the complete user/assistant turn, stores actual usage, and marks the run succeeded. On failure or cancellation, persist terminal state and actual usage but append neither the user message nor partial assistant text; completed tool activity remains in the run audit only and is not replayed as conversation context. Tool-call safety records are persisted incrementally under ENG-09. Idempotent replay returns the stored public outcome and MUST NOT repeat model or tool work.

**ENG-07 (limits)** — The monotonic deadline covers queue-free execution, all model/tool calls, retries, and P1 cancellation cleanup. Each completed LLM→tool→LLM cycle increments `maxIterations`. Iteration exhaustion is a successful HTTP completion with `finish_reason: "length"` and `x_agent_status: "iteration_limit"`; it is not a fabricated OpenAI `finish_reason: "error"`. Assistant text is accumulated across the entire run as UTF-8; before emitting/storing a delta that would exceed `maxOutputBytes`, keep only the largest code-point-safe prefix that fits, cancel further generation, and complete successfully with `finish_reason: "length"` and `x_agent_status: "output_limit"`. No later tool call may start after that limit.

**ENG-08 (token accounting)** — Provider-reported usage is authoritative and MUST be accumulated exactly once per call and per session. Before every call, known remaining budget caps `max_output_tokens`; an exhausted budget fails with `budget_exceeded`. Because some providers cannot count input before execution, a single call MAY exceed a budget by its reported input usage; no later call may start, and the overshoot is recorded. This limitation MUST appear in operator documentation. Missing usage is estimated and labeled `estimated: true`; it MUST NOT silently count as zero.

**ENG-09 (tool side effects)** — Each ADK tool-call ID is executed at most once within a run. For a stateful run, persist a call record as `executing` before invocation and its bounded result as `completed` or `failed` after return; a stateless run keeps the same states only in bounded process memory because API-06 forbids durable run data. Repeated delivery of a completed ID returns the stored result. A durable ID left `executing` across lost ownership or process failure becomes `outcome_unknown` and MUST NOT be invoked again; the run fails with `tool_outcome_unknown`. A crashed stateless request cannot be resumed, and a client retry is a new run that may cause a new side effect; operator/API documentation MUST state that limitation. The runtime never automatically retries a tool; LLM-generated later calls are new side effects and receive new IDs. Cancellation after an external side effect does not imply rollback and MUST be represented accurately in the run audit.

**ENG-10 (public errors)** — Internal exception text, provider bodies, stack traces, SQL/Redis details, filesystem paths, and secret material MUST NOT enter public errors. Public messages are stable summaries from API-15; detailed redacted diagnostics are correlated by `request_id` and `run_id` in logs.

---

## 6. Non-functional requirements

**NFR-00 (benchmark contract)** — Release performance gates use the environment and evidence defined below.

Release performance gates run against the Linux `amd64` image with a 1.0 CPU quota and 512 MiB memory limit, local memory storage, no MCP servers, authentication disabled, OTel disabled, and the deterministic mock `AgentRunner`. The report MUST record host, kernel, container runtime, image digest, dependency lock hash, warm-up, sample count, percentiles, failures, and peak RSS.

**NFR-01 (startup)** — Across 20 fresh-container starts, p95 process-start-to-first-`/healthz` latency MUST be ≤ 5 s. Image-pull time is excluded; Python/module import and server bind are included. MCP/provider/storage reconnection is asynchronous.
**NFR-02 (runtime overhead)** — After 100 warm-up requests, p95 server overhead across 1,000 non-streaming requests at concurrency 10 MUST be < 50 ms, measured from request receipt through validation/session work to serialization of a deterministic in-process mock result.
**NFR-03 (concurrency)** — One replica MUST hold 100 admitted streaming runs for 30 s, deliver at least one event per second per run, reject none below the configured cap, and remain below 512 MiB peak RSS. A 101st run at the default cap MUST receive 503 `overloaded` without starting model work.
**NFR-04 (footprint)** — After 60 s idle following startup and garbage collection, RSS MUST be ≤ 300 MiB. The measurement MUST be repeated five times and report the maximum.
**NFR-05 (determinism)** — Config resolution MUST be fully deterministic: identical resolver inputs produce an identical canonical masked serialization. `--dump-config` verifies that property for tiers 1–7; watcher-mode tests verify it again after applying the same tier-8 resource.
**NFR-06 (compatibility)** — Each release MUST publish a bounded official `openai` Python SDK compatibility matrix containing a tested minimum and maximum version. CI tests both endpoints of that range with only `base_url` and API key configuration: `models.list` and `chat.completions.create` with and without streaming. New SDK releases do not automatically become supported.
**NFR-07 (bounded resources)** — HTTP bodies, individual messages, session context, tool results, output queues, idempotency records, and per-session lock entries MUST all have finite bounds and eviction/termination behavior defined in this specification. A slow or disconnected client MUST NOT cause unbounded memory growth.
**NFR-08 (reload availability)** — A valid live or rebuild update MUST cause zero failed admitted requests and no listener restart. Requests admitted before the atomic swap finish against their original generation; later requests use the new generation.
**NFR-09 (dependency recovery)** — After Redis/PostgreSQL, a required MCP server, JWKS endpoint, or OTLP collector becomes reachable again, the component MUST recover without process restart. Readiness MUST converge within the dependency’s next bounded retry/refresh interval. A rotated file-backed model secret is re-read and may recover on the next model request without restart; an env-backed secret requires restart under SEC-04.
**NFR-10 (portability)** — The same image digest MUST pass functional smoke tests on `linux/amd64` and `linux/arm64`, under Docker, Kubernetes, and an arbitrary OpenShift UID with read-only root filesystem as constrained by §16.

---

## 7. Watcher-driven configuration reload

**REL-01** — Only the Kubernetes watcher supplies runtime reloads. Each event is treated as a new tier-8 overlay and the complete tiers 1–8 result MUST pass CFG-12, CFG-14, and CAP-01 before any mutation. Invalid input keeps the Applied Config and all components unchanged, logs one redacted error summary, and never crashes the process.

**REL-02** — Every schema leaf is classified exhaustively:

| Category | Fields | Action |
| --- | --- | --- |
| Live snapshot | `$schema`, `description`, `engine.maxOutputBytes/timeoutSeconds/maxIterations/historyMaxMessages/historyMaxBytes/streaming/overrides/tokenBudget`, `storage.sessionTtlSeconds/runTtlSeconds/maxSessions/maxRunsPerSession/maxIdempotencyRecordsPerSession/lockAcquireSeconds/idempotencyTtlSeconds`, `server.rateLimit.*`, `server.maxConcurrentRequests/maxRequestBytes/maxMessageBytes/streamQueueEvents/slowConsumerSeconds/exposeSystemInstruction/shutdownGraceSeconds`, `observability.logLevel/includeToolArguments` | Included in the next Applied Config snapshot |
| Component rebuild | `engine.systemInstruction/temperature/topP/maxTokens`, `llm.*`, `tools.*`, and supported phase components `agents/approval/rag` | Build and health-check replacements without exposing them, then atomically swap |
| Restart required | `schemaVersion`, `name`, `profile`, `storage.type/path/connectionString*`, `server.host/port/protocols/cors*/auth/trustedProxyCidrs/maxRequestLineBytes/maxHeaderBytes/maxHeaderCount`, `k8s.*`, `observability.logFormat/otel/serviceName` | Reject the entire update as `restart_required`; do not partially apply |

**REL-03 (transaction)** — A valid update containing no restart-required change is all-or-nothing. Replacement components MUST be constructed and reach their defined initial state before one atomic Applied Config pointer swap. If any rebuild fails, close replacements, retain the full last-known-good generation, report `rebuild_failed`, and do not increment generation. In-flight runs retain references to their original generation until completion; retired components close under MCP-05.

**REL-04 (generation)** — Initial tiers 1–7 startup is `configGeneration: 1`. A successful, semantically different atomic swap increments by exactly one. A duplicate watch event, metadata-only Kubernetes event, rejected update, or failed rebuild does not increment it. `GET /health` and `GET /config` expose the Applied Config generation and a SHA-256 `configHash` of its canonical masked JSON.

**REL-05 (deletion and resync)** — Deleting the watched object removes tier 8 and attempts the same transactional resolution using tiers 1–7. If `k8s.required: true`, readiness becomes 503 until a valid tier-8 object is applied, even if the fallback config can serve. Object reappearance, watch reconnect, and full resync use identical no-op detection.

**REL-06 (audit)** — Every attempt MUST log resource version, outcome, old/new generation, sorted changed paths, and duration. Values are omitted; secret paths are labeled `changed: true` only.

---

## 8. Sessions and storage

**SES-01 (record)** — A session is keyed by `(agent_name, principal_id, session_id)` and stores that identity, revision, ordered conversational messages, cumulative usage, `history_truncated`, `created_at`, and `updated_at`. The same client-chosen `session_id` MAY safely exist for different principals or agents. Runs, tool audit records, and idempotency records are associated data but are not blindly replayed as model conversation. Stored JSON has an explicit internal `schema_version`.

| Type | Behavior |
| --- | --- |
| `memory` | In-process maps and locks. Boot logs that data is lost on restart and not shared across replicas. Production manifests MUST use one replica with this backend. |
| `file` | `{path}/{agent_name}/{principal_digest}/{session_id}.json`, with safe fixed-format components. Writes use an exclusive temp file in the target directory, fsync its contents, same-filesystem replace, then fsync the parent directory; traversal through attacker-controlled symlinks is rejected. The directory MUST pass create/write/fsync/rename/delete probing before readiness. This backend supports one replica/process only. |
| `redis` | Session key includes `agent_name`, full principal digest, and `session_id`; revision mutations use atomic Lua or transactions; lock and idempotency keys share the same hash tag for Redis Cluster compatibility. |
| `postgres` | `agent_sessions` uses primary key `(agent_name, principal_id, session_id)`, plus revision, JSONB data, and timestamps. Runs/idempotency use identically scoped companion tables. Schema creation/migration is transactional and versioned. |

**SES-02 (identifier and create)** — `session_id` MUST match `^[A-Za-z0-9_-]{1,128}$`; invalid input returns 400 `invalid_session_id`. If absent, generate UUIDv4. A valid unknown ID on a chat request is created atomically in the caller’s principal namespace; `POST /v1/sessions` also creates explicitly. A same-principal create race MUST produce one revision-1 record. It MUST NOT use an unconditional upsert that can replace history. Before a new session is committed, delete eligible expired sessions and atomically enforce `maxSessions`; if still full, fail 503 `storage_capacity` without evicting a live or unexpired session.

**SES-03 (principal isolation)** — API-key principal ID is `apikey:` plus the full SHA-256 hex digest of the key. JWT principal ID is `jwt:` plus the full SHA-256 hex digest of issuer, a NUL separator, principal-claim name, a NUL separator, and claim value. With auth disabled, the principal is `anonymous` and operator docs MUST warn that client-chosen sessions are mutually accessible. Every lookup is scoped by principal from the first storage operation; a record in another namespace is indistinguishable from absence and is never queried as a fallback.

**SES-04 (availability)** — An unavailable persistent backend makes stateful requests fail 503 `storage_unavailable` and readiness fail 503. Stateless chat remains available and `/healthz` remains live. Boot starts a background reconnect loop using MCP-01 backoff. File storage is unavailable when its path is absent, unsafe, or not writable; it is not assumed healthy merely from its configured type.

**SES-05 (global serialization and fencing)** — At most one run may hold the current session ownership fence across all replicas:

- memory/file use an in-process lock and are limited to one replica;
- Redis atomically acquires a token-valued lease plus a monotonically increasing session fencing number, renews only by token match, and releases only by token match;
- PostgreSQL holds a session-scoped advisory lock on a dedicated connection for the run lifetime and increments a persisted session fencing number on acquisition.

Acquisition waits at most `lockAcquireSeconds`, then returns 409 `session_busy`. A run MUST verify current token/fence ownership immediately before every model call, tool side effect, and storage commit. The Redis lease duration is at least `engine.timeoutSeconds + server.shutdownGraceSeconds + 30` and renews before one third elapses. A failed, late, or uncertain renewal—or PostgreSQL lock-connection uncertainty—immediately triggers cancellation and prohibits new external actions; an already-running result is discarded, and an executing tool follows ENG-09. Every mutation compares revision and fence so a paused/partitioned former owner cannot write after a successor acquires ownership.

**SES-06 (retention)** — `updated_at` and session TTL are refreshed only by explicit session creation or a successful stateful conversation mutation, not by metadata reads or failed runs. Memory/file/PostgreSQL sweep every 10 minutes; Redis applies expiration/index cleanup atomically with mutations. A sweep MUST skip a session with a live run/lease and recheck revision before deletion. `sessionTtlSeconds: 0` disables age expiry but never `maxSessions`. Terminal run/audit records older than `runTtlSeconds` are deleted; an active run, pending approval, or record required by an unexpired idempotency entry is retained. Expired, unknown, and foreign sessions all return 404.

**SES-07 (bounds)** — After each successful stateful run, ENG-04 pruning MUST keep conversation within both history bounds. At `maxRunsPerSession`, the oldest terminal run/audit records not protected by SES-06 are deleted before admitting another; if capacity cannot be freed, fail `storage_capacity`. Completed idempotency entries expire after `idempotencyTtlSeconds`; at `maxIdempotencyRecordsPerSession`, a request with a new idempotency key fails `storage_capacity` rather than evicting an unexpired record. Per-session lock objects are removed as soon as they have no owner or waiter. Run inputs, audit payloads, tool results, and idempotency outcomes are stored only after MCP/API caps. A bound violation fails the pending mutation atomically; it MUST NOT leave corrupt JSON, partial history, or partial usage.

**SES-08 (delete and shutdown)** — Deleting a session with a nonterminal run returns 409 `session_busy`. Successful deletion atomically removes session, run, approval, and idempotency records for that session. Shutdown flushes admitted mutations before closing the backend; failure is logged and causes exit 1 rather than falsely reporting a clean exit.

**SES-09 (ADK adapter)** — The runtime `SessionStore` MUST implement or adapt ADK’s session-service contract so ADK events and the authoritative record share one revisioned transaction path. An independent ADK in-memory history alongside the configured backend is prohibited.

---

## 9. API surface

**API-00 (surface-wide contract)** — Unless tagged P2+, endpoints below are P1. Disabled protocol routes MUST be absent and return ordinary 404, not a 501 stub. Runtime auth applies to every route except `/healthz` and `/readyz`. Every response produced after an ASGI scope exists, including errors, MUST include `X-Request-Id`; sensitive/config responses include `Cache-Control: no-store`. A request rejected by Uvicorn’s bounded parser before scope creation may instead receive a body-free 400/414/431 and connection close, because application middleware cannot safely create an envelope or request ID at that point.

### 9.1 Health and metadata

**API-01** — `GET /healthz` returns 200 `{"status":"ok"}` from server bind until process exit, including while unready or draining. It MUST perform no network or storage I/O.

**API-02** — `GET /readyz` returns 200 `{"status":"ready","configGeneration":N}` only when the server accepts new work, the Applied Config is valid, auth has usable key material, configured storage passed its health rule, every required MCP server is connected, and a required tier-8 initial sync has succeeded. During shutdown or failure it returns 503 `{"status":"unready","reasons":[{"code":"storage_unavailable"}]}`. Reasons use stable codes, are sorted, and contain no endpoints, paths, or exception text.

**API-03** — Protected `GET /health` (anonymous only when `auth.mode: none`) returns 200 for both healthy and degraded states. The illustrative shape is:

```json
{
  "status": "ok",
  "agent": "agent",
  "version": "2.0.0",
  "phase": "P1",
  "capabilities": {"multiAgent": false, "approval": false, "rag": false, "acp": false},
  "configGeneration": 3,
  "configHash": "<sha256>",
  "llm": {"provider": "gemini", "model": "gemini-2.5-flash", "status": "available"},
  "mcp": [{"name": "k8s-mcp", "connected": true, "tools": 12, "required": false, "renamedTools": {}}],
  "storage": {"type": "redis", "connected": true},
  "watcher": {"enabled": true, "synced": true}
}
```

`status` is `degraded` when an optional MCP server, the model, watcher, or telemetry exporter is unavailable. An untested `llm.status: "unknown"` does not make health degraded. Component arrays and maps use stable configured order.

**API-04** — Protected `GET /config` (anonymous only when `auth.mode: none`) returns the Applied Config, never an un-applied desired update. Secret contents and sensitive passthrough values are recursively `"<redacted>"`; secret-reference field names MAY show the referenced env-var name or file basename, never an absolute file path. `engine.systemInstruction` is redacted unless `exposeSystemInstruction` is true. The response adds `activeProfile`, `configGeneration`, and `configHash`.

### 9.2 OpenAI-compatible chat (enabled by `server.protocols.openaiCompat`)

**API-05 (request)** — `POST /v1/chat/completions` accepts this P1 text subset:

| Field | Contract |
| --- | --- |
| `model` | Required non-empty string. It MUST exactly equal the ID returned by `GET /v1/models`; otherwise 404 `model_not_found`. |
| `messages` | Required array with 1..`engine.historyMaxMessages` items. Each item has `role`, string `content`, and optional string `name`. |
| `stream` | Optional bool, default `false`. |
| `stream_options.include_usage` | Optional bool, default `false`; valid only when streaming. Unknown stream-option fields are rejected. |
| `temperature`, `max_tokens` | Optional overrides governed by API-12. |
| `session_id` | Optional runtime extension governed by SES-02. |
| `user` | Accepted and ignored; it MUST NOT be logged or used as the security principal. |

Accepted roles are `developer`, `system`, `user`, and `assistant`; content is non-empty UTF-8 text within `maxMessageBytes`; optional `name` matches `^[A-Za-z0-9_-]{1,64}$`. Client developer/system messages remain request context after the configured root instruction and never replace `engine.systemInstruction`. Array/multimodal content, `tool`/`function` roles, null content, and unknown message fields return 400 `invalid_request`. Top-level fields not listed in API-05—including client-defined tools/functions and unsupported generation controls—also return 400 rather than being silently ignored. `user` is the sole explicitly ignored compatibility field. Forward compatibility means the tested NFR-06 SDK range, not accept-anything parsing; the runtime always produces one choice.

**API-06 (state rules)** — With `session_id`, `messages` MUST contain exactly one `user` message; server history is authoritative and ENG-06 persists the run. Without `session_id`, the call is stateless, uses the complete validated message array, and writes no session/run/idempotency data.

**API-06a (idempotency)** — A non-streaming stateful chat MAY include `Idempotency-Key` matching `^[A-Za-z0-9._:-]{1,128}$`. The scope is principal + method + route + key. Before model work, hash the UTF-8 RFC 8785 canonical JSON of exactly `model`, normalized `messages` (only role/content/present name), `session_id`, and effective `temperature`/`max_tokens` using SHA-256. On first admission, omitted overrides become the values from that Applied Config; the record stores those normalized values. When the key already exists, an omitted override is normalized to the stored value, so omission and explicitly repeating that value match even after reload. Exclude `stream`, ignored `user`, idempotency/request/auth headers, request ID, and config generation. Same key/hash while running returns 409 `idempotency_in_progress`; after completion it replays the stored status/body and adds `Idempotent-Replayed: true`; same key with a different hash returns 409 `idempotency_conflict`. Streaming or stateless requests with this header return 400 `invalid_request`.

**API-07 (non-stream response)** — Success returns HTTP 200 with `id: "chatcmpl-<uuid4>"`, `object: "chat.completion"`, Unix-second `created`, configured `model`, and exactly one `choices` item with `index: 0`, `message: {"role":"assistant","content":"..."}`, and standard `finish_reason: "stop"|"length"`. `usage` contains integer `prompt_tokens`, `completion_tokens`, and `total_tokens`. Runtime extensions are `run_id`, `config_generation`, optional `session_id`, and optional `x_agent_status`. A fatal outcome before a successful completion uses API-15 and MUST NOT return a fabricated completion with `finish_reason: "error"`.

**API-08 (streaming)** — `stream: true` returns SSE with `Content-Type: text/event-stream` and no proxy-buffering cache headers. Each ordinary data object uses the OpenAI `chat.completion.chunk` shape with stable `id`, `created`, and `model`; choice deltas use index 0. The stream:

1. emits an assistant-role delta;
2. emits text deltas and permitted extension-event chunks;
3. emits a choice chunk with standard `finish_reason: "stop"|"length"`;
4. when `stream_options.include_usage: true`, emits one additional chunk with `choices: []` and total `usage`; and
5. terminates with exactly `data: [DONE]\n\n`.

When `engine.streaming` is `events` or `debug`, an extension chunk has `choices: []` and top-level `x_agent_event`. Tool-call arguments are recursively redacted and size-bounded; tool results contain only the MCP-04 preview. Debug mode additionally exposes iteration number and config generation. Text mode emits no extension chunks.

**API-08a (stream failure/disconnect/backpressure)** — If a fatal error occurs before response headers, return the normal API-15 HTTP error. After streaming begins, emit one `x_agent_event: {"type":"error","code":"...","message":"..."}` chunk and then `[DONE]`; HTTP status remains 200 and no nonstandard finish reason is used. Client disconnect, or a full output queue for `slowConsumerSeconds`, MUST request run cancellation within 1 s. ENG-06 defines persistence; partial assistant text is not added to future conversation context. A disconnected stream may not receive its usage chunk.

### 9.3 Session management

**API-09** — Session routes use the effective principal (including `anonymous` in no-auth mode) and never provide a list/enumeration endpoint:

- `POST /v1/sessions` accepts an optional body `{"session_id":"..."}`, creates atomically in the caller’s namespace, and returns 201 `{"session_id","created_at","revision":1}`. Empty body generates UUIDv4. An existing ID in the caller’s namespace returns 409 `session_exists`; the same ID in another namespace has no effect.
- `GET /v1/sessions/{id}` returns `session_id`, timestamps, revision, `message_count`, `history_truncated`, and cumulative usage—never message bodies, tool arguments/results, or owner identifiers.
- `DELETE /v1/sessions/{id}` returns 204 after SES-08 deletion.

Unknown, expired, and foreign IDs return identical 404 responses.

### 9.4 Overrides, usage, errors

**API-12** — Per-request overrides are limited to `temperature` and `max_tokens`, gated by `engine.overrides.allow*`. Values MUST satisfy the base schema and configured override maximum; invalid, above-cap, or disabled overrides return 400 `override_not_allowed` rather than being silently clamped. Model/provider/tool/root-instruction selection remains config-only; the required request `model` is routing validation, not an override.
**API-13** — `engine.streaming` values: `text` = text deltas only; `events` = + tool_call/tool_result; `debug` = + iteration events.
**API-14** — Non-streaming success always contains usage. Streaming returns a final usage chunk only when `stream_options.include_usage` is true, matching the OpenAI contract; actual usage is still persisted even when not sent. Estimated usage includes extension `usage.estimated: true`. Token counting is the extent of P1 cost accounting. When `costs.enabled` (P5-4, COST-01), the usage object additionally carries `usage.costUsd` (USD, rounded to 6 decimals) computed from the costs table; when disabled the usage object is byte-identical to the OpenAI shape.
**API-15 (errors)** — Endpoints under `/v1/` MUST use the OpenAI error envelope: `{"error":{"message":"...","type":"<code>","code":"<code>"}}`. Non-`/v1/` endpoints use `{"status":"error","code":"...","message":"..."}`. Defined codes and HTTP statuses:

| code | HTTP |
| --- | --- |
| `invalid_request` / `invalid_session_id` / `override_not_allowed` / `context_length_exceeded` / `approval_session_required` | 400 |
| `unauthorized` | 401 |
| `not_found` / `model_not_found` | 404 |
| `method_not_allowed` | 405 |
| `session_busy` / `session_exists` / `idempotency_in_progress` / `idempotency_conflict` | 409 |
| `uri_too_long` | 414 |
| `payload_too_large` | 413 |
| `unsupported_media_type` | 415 |
| `headers_too_large` | 431 |
| `rate_limited` / `budget_exceeded` | 429 (+ `Retry-After` when retry time is known) |
| `overloaded` / `storage_unavailable` / `storage_capacity` / `auth_unavailable` / `rag_unavailable` / `run_interrupted` | 503 |
| `provider_auth` | 502 |
| `provider_error` / `tool_outcome_unknown` | 502 |
| `agent_timeout` | 504 |
| `internal` | 500 |

All FastAPI/Pydantic validation failures under `/v1/` MUST be translated from framework-default 422 into 400 `invalid_request` with a stable field path. A missing/unsupported route uses 404 `not_found`; a wrong method uses 405 `method_not_allowed`; malformed JSON uses 400 `invalid_request`. Each uses the applicable envelope. Public messages MUST NOT expose validation internals or accepted secret values. `busy`, `cancelled`, `stale_approval`, and `rag_degraded` are documented protocol event/state codes, not additional HTTP mappings.

**API-16 (ACP, P2)** — A P1 build rejects `protocols.acp: true` at boot and has no ACP routes. A P2 baseline MUST implement `GET /agents` and `POST /runs` before reporting `acp: true`; a 501 placeholder is prohibited. The P2 acceptance annex MUST freeze its manifest, input/output parts, streaming events, auth, session, idempotency, and error schemas before implementation.
**API-17 (`GET /v1/models`)** — Part of the openaiCompat surface: returns the OpenAI list shape `{"object":"list","data":[{"id":"<llm.model>","object":"model","created":0,"owned_by":"<llm.provider>"}]}` — exactly the one configured model. Required for out-of-the-box compatibility with OpenAI SDKs and chat UIs that probe models before chatting (NFR-06).
**API-18 (OpenAPI docs)** — FastAPI `/openapi.json` and `/docs` remain enabled and protected by runtime auth. The schema MUST document security schemes, all runtime extensions, request limits, SSE event schemas, and every API-15 response. Health endpoints are explicitly anonymous in the schema. A golden OpenAPI artifact is diffed in CI.
**API-19 (serialization case)** — Agent Definition/config and non-`/v1/` JSON use camelCase. OpenAI-compatible REST/SSE and session APIs use the documented snake_case fields. Internal Python names do not leak.

**API-20 (HTTP/rate behavior)** — Uvicorn MUST use a bounded HTTP parser configured so request-line, aggregate-header, and header-count limits apply before unbounded allocation to ordinary requests. Over-limit parsed requests use 414 `uri_too_long` or 431 `headers_too_large`; API-00 governs pre-scope rejection. JSON POST routes require `Content-Type: application/json` (optional UTF-8 charset); otherwise 415 `unsupported_media_type`. Body limits apply while reading fixed or chunked bodies, before allocation/parsing beyond the cap. Rate limiting uses a replica-local fixed UTC-minute window keyed by authenticated principal; with auth disabled it uses the direct peer IP or the first valid forwarded client IP only when the direct peer is in `trustedProxyCidrs`. Health probes are not rate-limited. Excess returns the remaining whole seconds to window reset in `Retry-After`.

---

## 10. Kubernetes watcher mode

**K8S-01** — The watch target is a ConfigMap at `{namespace}/{name}`; key `agent.yaml` contains a UTF-8 tier-8 YAML overlay. A Custom Resource is out of scope (§1.4) — the same reload mechanics can adopt one later without changing tiers 1–7.

**K8S-02** — Load in-cluster credentials only. Perform an initial GET/list, then watch from its `resourceVersion`; reconnect with bounded jitter, handle 410 Gone by re-listing, and perform a full re-list every `resyncSeconds`. Only events for the configured name/UID affect tier 8. API calls use explicit connect/read timeouts no greater than 30 s so shutdown cannot hang.
**K8S-03** — Watched data is a partial overlay, not a second complete Agent Definition. It is parsed under CFG-03a, merged as highest tier, then the complete result is validated. CI MUST generate `schemas/agent-overlay.schema.json` from the full schema by making fields optional while preserving field constraints, so operators can validate a ConfigMap's `agent.yaml` before applying it.
**K8S-04** — Apply changes per §7 (validate → tiered reload → last-known-good on failure).
**K8S-05** — Initial 404, deletion, 403, timeout, and watch loss are nonfatal to liveness. They update watcher health and apply REL-05. Identical errors are log-throttled to once per 60 s plus one recovery message. When `k8s.required` is false, the runtime may serve tiers 1–7; when true, it remains unready until a valid overlay is applied.
**K8S-07** — Replicas watch and reconcile independently with no leader election. Merge and no-op detection MUST be deterministic. During a rolling release, different replicas may transiently observe different generations; deployment documentation MUST state this limitation and operators MUST use per-pod `/health` for diagnosis.
**K8S-08** — Deliverables in `manifests/`: least-privilege `rbac.yaml` (get/list/watch the configured ConfigMap only); and illustrative Deployment/Service with API-01/02 probes, security context, resource requests/limits, one worker, authenticated runtime configuration, and storage-compatible replica guidance.
**K8S-09** — Kubernetes labels, annotations, managed fields, and resource version are never merged into AgentConfig.

---

## 11. Security

**SEC-01 (authentication)** — Runtime auth protects every route except `/healthz` and `/readyz`. API-key mode accepts exactly one credential from `Authorization: Bearer` or `X-API-Key`; if both are present they MUST match or authentication fails. Compare decoded bytes using `hmac.compare_digest`. Empty, malformed, repeated, comma-joined, or non-Bearer Authorization values fail 401. Authentication is evaluated before session lookup. `auth.mode: none` is an explicit development/controlled-network risk: binding it to a non-loopback address emits one high-severity startup audit warning, and supplied Kubernetes/production examples MUST use API-key or JWT auth.

**SEC-02 (recursive redaction)** — One shared, recursively applied masking utility MUST protect config dumps, `/config`, logs, traces, health details, status patches, events, exceptions, and test failure output. Schema-defined fields carry explicit secret metadata; known non-secret fields such as `maxTokens` are not reclassified by name. For arbitrary maps, headers, query parameters, and exception/log structures, normalize a key by lowercasing and removing `-_ .`; it is sensitive when it equals or ends with `authorization`, `cookie`, `apikey`, `token`, `secret`, `password`, `credential`, `privatekey`, or `connectionstring`, including those suffixes followed by `env`, `file`, or `ref`. Resolved secret values and known secret-ref contents are masked regardless of key; API-04 is the only exception for displaying a reference locator. Values become `***` in text/YAML and `<redacted>` in APIs. Sensitive query parameters are redacted before access logging. Passthrough maps and static MCP headers containing a sensitive key are configuration errors; operators MUST use defined secret refs/`secretHeaders`.

**SEC-03 (auth fail closed)** — Missing auth configuration or an unreadable/empty API-key secret exits 78 before bind. JWT mode with syntactically valid configuration but an unreachable JWKS endpoint MAY bind only in fail-closed state: readiness is 503, every protected route returns 503 `auth_unavailable`, and a background retrier starts. It MUST never fall back to `auth.mode: none`. Invalid JWKS content or absence of every allowed verification key is treated the same.

**SEC-04 (secret references)** — File wins over env. Secret files are read at point of use to support projected-secret rotation, may follow the platform-created symlink, MUST be regular-file targets no larger than 64 KiB, and have one trailing CRLF/LF removed; embedded NUL is invalid. Env references name variables and snapshot their value at process start. Unset/empty/unreadable refs cause the owning component’s documented missing-credential behavior without logging content. Absolute secret paths never appear publicly.

**SEC-05 (configured egress)** — The runtime may initiate network requests only to provider base URLs, MCP URLs, JWKS URL, storage connection target, Kubernetes API, and OTLP endpoint derived from trusted configuration/standard OTel env. URL-bearing request/message/tool data MUST NOT be fetched by runtime infrastructure unless a configured MCP tool chooses to do so. HTTPS certificate/hostname verification is always enabled; disabling it through passthrough options is prohibited. Loopback HTTP is permitted for development, and cluster-local HTTP for MCP/OTLP is operator risk. Deployment docs MUST recommend default-deny egress policy.

**SEC-06 (HTTP CORS)** — HTTP origins are matched exactly after standards-compliant origin parsing; substring, suffix, and reflected-origin matching are prohibited. `*` may be used only with `corsAllowCredentials: false`. When auth is enabled and `*` is configured, log one startup warning. Preflight permits only documented methods and headers.

**SEC-07 (prompt/tool trust)** — Client messages and MCP results are untrusted model input, not security instructions. The configured root instruction is retained, results are bounded/redacted, and tool side effects remain protected by MCP credentials and, in P3, approval policy. The runtime MUST NOT claim prompt-injection prevention or infer authorization from model text. A model deciding to call a tool is not itself an authorization decision.

**SEC-08 (JWT/JWKS)** — Accept only RS256 and ES256; reject `none`, HMAC, unknown algorithms, duplicate header parameters, missing `kid`, and key/algorithm mismatch. Validate signature, exact issuer, required `exp`, optional `nbf` with 30 s leeway, configured audience, and a non-empty string principal claim. Refresh in background every `refreshSeconds`; unknown `kid` triggers one throttled immediate refresh. A failed refresh retains last-known-good keys for at most `2 * refreshSeconds`; after that protected routes fail closed and readiness is 503 until recovery.

**SEC-09 (proxy handling)** — Forwarded headers are ignored unless the direct peer belongs to `trustedProxyCidrs`; then parse the standardized chain defensively and choose the first untrusted hop from the right.

**SEC-10 (audit events)** — Structured security audit logs are required for auth success/failure (without credentials), rate-limit denial, foreign/unknown session access as one indistinguishable outcome, capability rejection, config apply/reject, approval decisions, and admin document ingestion/deletion. Each includes timestamp, request/run ID, principal digest when authenticated, action, outcome, and config generation; it excludes message/tool content by default.

**SEC-11 (response hardening)** — API responses set `X-Content-Type-Options: nosniff`; docs set a restrictive Content Security Policy compatible with Swagger UI. Error responses and health reasons are cache-disabled. Request IDs, JWT claims, tool names, and upstream strings are length/control-character validated before logging to prevent log injection.

---

## 12. Observability

**OBS-01 (logs)** — Stdlib `logging` is the application facade. JSON format emits one object per line with `ts`, `level`, `logger`, `event`, and `msg`; request context adds `request_id`, `run_id`, optional `session_id`, principal digest, and `config_generation`. Text format is human-readable but carries the same correlation values. Logs go only to stdout/stderr. Access logs use route templates, not raw paths/query strings, and never record headers or bodies.

**OBS-02 (request IDs)** — Accept incoming `X-Request-Id` only when it matches `^[A-Za-z0-9._:-]{1,128}$`; otherwise generate UUIDv4 and log a value-free `invalid_request_id` warning. Return the chosen ID on every HTTP response, propagate it through async tasks, and include it on upstream requests where safe. Honor valid W3C `traceparent` independently from request ID.

**OBS-03 (boot)** — One `runtime_started` event MUST report runtime version/phase, config generation/hash, agent name, active profile, mode, provider/model, storage type, MCP names/transports/required flags, enabled protocols/capabilities, auth mode, bind address, and process UID/GID. Secrets and absolute secret paths are masked. A corresponding `runtime_stopped` event reports reason, drain counts, duration, and clean/unclean outcome.

**OBS-04 (traces)** — When OTel is enabled, emit `http.request → agent.execute → llm.call|mcp.tool_call` spans plus config-reload/storage spans. Attributes include route template, status/error code, model, tool/server name, config generation, duration, retry count, and token counts; never message content, tool results, credentials, session IDs, JWT claims, or raw URLs. `includeToolArguments` may add only a redacted 4 KiB span event. Use standard OTEL exporter/sampler/resource env vars, lazy imports, and bounded export queues. Export failure is nonfatal and marks health degraded.

**OBS-05 (metrics)** — When `observability.prometheus.enabled` is true, serve
the Prometheus text exposition (format version 0.0.4) at the configured
`observability.prometheus.path` (default `/metrics`). The endpoint exports
counters/histograms for admitted/completed/failed runs, an active-runs gauge,
run latency, model/tool calls, tokens, rate/concurrency denials, reload
outcomes, output-queue cancellations, and (when COST-01 is enabled) the
accumulated USD cost by model. Labels MUST be low-cardinality;
request/session/run/principal IDs are prohibited labels; each metric caps its
distinct label sets (default 128) and drops beyond the cap with a warning. The
scrape path is exempt from the replica-local rate limiter. When OTel is also
enabled the same instruments export via OTLP; the Prometheus registry is
process-local and shared across live reloads.

**OBS-06 (disabled cost)** — With OTel disabled, OTel packages MUST NOT be imported, providers/threads MUST NOT start, and no span/metric objects may be allocated per request. A single configuration branch is acceptable; “zero overhead” does not require literally zero CPU instructions.

---

## 13. Multi-agent (Phase 2)

**MA-01 (schema)** — Each `agents[]` item has required unique DNS-label `name`, required non-empty `systemInstruction`, `description` ≤ 2,000 code points, optional full `llm` block inherited from root by deep merge, and `toolServers: list[str]` defaulting to every configured MCP server. Root name and sub-agent names MUST be distinct; every tool-server reference MUST exist. P2 supports one flat level of root-owned sub-agents; nested/cyclic definitions are rejected.

**MA-02 (construction)** — With a non-empty list, root becomes an ADK coordinator and entries become ADK `sub_agents` in configured order. Routing uses ADK’s native transfer informed by name/description. Empty list MUST retain P1 behavior and public fixtures. All agents share the run’s principal, session adapter, cancellation, deadline, iteration counter, request/session budget, and Applied Config generation.

**MA-03 (tool isolation)** — A sub-agent receives only toolsets named by `toolServers`, after MCP filtering/collision mapping. It cannot call the coordinator’s hidden tools or another sub-agent directly except through an ADK transfer. Transfer does not grant a new principal or reset a budget/approval decision.

**MA-04 (events/state)** — Event/debug streams add `{"type":"agent_transfer","from":"root","to":"researcher"}`; text mode remains text-only. Transfers are stored in the run audit, not as user-visible session messages. A transfer to an unknown/unavailable agent fails the run with `provider_error` and no silent fallback.

**MA-05 (reload/acceptance)** — `agents` is a component rebuild. P2 capability is true only after tests cover deterministic construction, routing fixtures, tool isolation, shared limits/cancellation, transfer events, session replay, reload with in-flight runs, and a single-agent regression suite.

### 13.1 ACP acceptance annex (API-16, normative, frozen before P2 implementation)

This annex freezes the P2 ACP REST surface. Changes require a versioned spec revision, not a silent implementation drift. "ACP" here means only this REST surface (API-16); §1.4 exclusions (ACP-stdio, A2A) stand.

**A-1 (surface)** — Routes live under the `/acp` prefix, registered only when `server.protocols.acp: true`; otherwise the paths are ordinary 404s (API-00). Case: snake_case (API-19). All routes require runtime auth (API-00); `/healthz`/`/readyz` stay anonymous. Every response carries `X-Request-Id`.

**A-2 (`GET /acp/agents`, manifest)** — Returns the agent manifest:

```json
{
  "object": "agent.manifest",
  "name": "agent",
  "description": "",
  "tools": ["server_tool"],
  "sub_agents": [
    {"name": "researcher", "description": "", "tools": ["server_tool"]}
  ]
}
```

`tools`/`sub_agents[].tools` use the FINAL (post-filter, post-collision-rename) tool names (MA-03). Stable configured order (API-03). `name` is the top-level agent name.

**A-3 (`POST /acp/runs`, request)** — Body (snake_case):

```json
{
  "session_id": "optional; SES-02 syntax",
  "message": {"role": "user", "content": "text"},
  "stream": true,
  "idempotency_key": "optional"
}
```

`message.content` is non-empty and bounded by `server.maxMessageBytes`. `session_id` optional: absent creates a session (stateless-style); present must be a valid SES-02 ID. `Idempotency-Key` header or `idempotency_key` field: API-06a canonicalization (SHA-256 of the trimmed key) and replay semantics apply. A run is admitted through the same admission pipeline as chat (ENG-03: request id, auth, capability, rate limit, budget, run record) and consumes the same `server.maxConcurrentRequests` slots.

**A-4 (`POST /acp/runs`, response)** — `stream: false` returns 200 with:

```json
{
  "object": "run.completion",
  "run_id": "run-…",
  "session_id": "sess-…",
  "choices": [{"index": 0, "message": {"role": "assistant", "content": "…"}, "finish_reason": "stop"}],
  "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
}
```

`stream: true` returns `text/event-stream` with the P1 SSE event vocabulary (delta chunks, finish chunk, optional usage chunk, `[DONE]`) plus the P2 `agent_transfer` event:

```json
{"type": "agent_transfer", "from": "root", "to": "researcher"}
```

`agent_transfer` appears only in event/debug streams (API-13); text mode remains text-only (MA-04). Mid-stream disconnect/cancellation follows API-08a.

**A-5 (auth, session, idempotency, errors)** — Auth: API-00 runtime auth on every `/acp` route. Session: SES-02 semantics; a `session_id` owned by another principal is an ordinary 404 `not_found` (no enumeration, API-09). Idempotency: API-06a; a completed replay returns the stored run result with 200; an in-progress key returns 409 `idempotency_in_progress`. Errors: the API-15 envelope and status table apply verbatim (`invalid_request` 400, `unauthorized` 401, `not_found` 404, `method_not_allowed` 405, `payload_too_large` 413, `rate_limited` 429, `provider_error` 502, `agent_timeout` 504, `internal` 500, …). Model routing: the request `model` field is not part of ACP runs; the configured model is used (API-12).

**A-6 (golden fixtures)** — The P2 acceptance suite pins: the manifest shape (root + sub-agents + final tool names), a non-streaming run, a streaming run with an `agent_transfer` event, idempotency replay + conflict, foreign-principal 404, every A-5 error mapping, and the ACP-disabled 404s.

## 14. Human-in-the-loop approval (Phase 3)

**HITL-01 (schema/fail closed)** — `approval` is `{enabled: false, tools: [], timeoutSeconds: 300, onTimeout: "deny"}`; `tools` contains exact `server/rawTool` or `server/*` patterns, matched before public tool renaming. Enabling requires P3 capability, auth not `none`, and Redis/PostgreSQL storage; memory/file are rejected. While enabled, every chat request MUST be stateful with `session_id`; reject a stateless request before model work with 400 `approval_session_required`. `onTimeout: "allow"` is accepted only when explicitly configured and emits a high-severity startup audit warning.

**HITL-02 (durable checkpoint)** — Before a matched tool executes, atomically persist a durable checkpoint and approval record containing `approval_id`, run/session/principal/config generation, server/raw/final tool names, requested/expiry times, and `pending` state. The protected checkpoint contains the full canonical arguments required for exact resume; public approval metadata contains only their hash and redacted preview. Transition the run `running → awaiting_approval`. No tool side effect may start before that transaction commits. P3 deployment documentation and supplied manifests MUST require backend access controls, transport encryption, encryption at rest, backup protection, and retention appropriate to potentially sensitive checkpoint data; the runtime MUST NOT claim that an unverified backend setting provides encryption.

**HITL-03 (client contract)** — SSE emits `approval_required`, then `[DONE]`. Non-streaming REST returns 202 with `run_id`, `approval_id`, `session_id`, and `expires_at`. Awaiting approval is a durable state, so this run detach is the sole exception to API-08a disconnect cancellation. P3 adds:

- `GET /v1/runs/{run_id}` for owner-scoped state/terminal result;
- `DELETE /v1/runs/{run_id}` for idempotent cancellation;
- `GET /v1/approvals?session_id=` for owner-scoped pending metadata; and
- `POST /v1/approvals/{approval_id}` with `{"decision":"approve"|"deny","reason"?}`.

Reason is ≤ 2,000 code points and stored/audited but never inserted into a system instruction.

**HITL-04 (decision race)** — Approval POST uses compare-and-swap. First approve, deny, timeout, cancel, config-stale, or shutdown-abort transition wins. Repeating the same decision returns the stored outcome; a conflicting decision returns 409. Approval pauses the engine execution deadline; `timeoutSeconds` uses a separate monotonic/durable expiry. Denial resumes with a structured denied tool result. Approval resumes exactly once from the checkpoint and reuses the original tool-call ID.

**HITL-05 (restart/config change)** — A reconciler resumes/finishes pending records after restart. If the Applied Config generation differs before execution, the pending approval terminates `stale_approval` and the tool MUST NOT execute. Timeout `deny` follows denial; timeout `allow` follows approval only after the same stale/cancellation checks. Retired component references need not remain alive across a pending approval.

**HITL-06 (acceptance)** — P3 capability requires crash/restart checkpoint tests and deterministic races for approve-vs-timeout, duplicate/conflicting decisions, disconnect, cancellation, config reload, storage loss, foreign principal access, and proof that a matched tool never starts before committed approval.

## 15. RAG / long-term memory (Phase 4)

**RAG-01 (schema)** — `rag` defaults disabled and contains `required: false`, `store {type: "chroma"|"pgvector", connectionString(Env/File), collection, options}`, `embedding {provider: "gemini"|"openai", model, apiKey(Env/File)}`, `topK: 5`, `minScore: 0.0`, `chunkChars: 1000`, `chunkOverlapChars: 200`, and `maxDocumentBytes: 10485760`. Constraints: `1 ≤ topK ≤ 100`, `0 ≤ minScore ≤ 1`, overlap < chunk size, and collection is a safe backend identifier.

**RAG-02 (tenancy/retrieval)** — Every document/chunk is keyed by agent name, principal ID, document ID, chunk index, embedding model, and content hash. Retrieval is strictly scoped to the run principal. Before each root-agent LLM call, retrieve at most `topK` chunks for the latest user message, sort by descending score then stable chunk ID, filter `minScore`, and insert one delimited context message after the configured system instruction. Retrieved text is explicitly labeled untrusted knowledge and MUST NOT be treated as authorization/instructions.

**RAG-03 (ingestion API)** — P4 adds owner-scoped:

- `POST /v1/documents` body `{"id"?, "text", "metadata"?}`, supporting `Idempotency-Key`, returns 201 with ID/chunk count/content hash;
- `GET /v1/documents/{id}` returns metadata/count/hash, never stored text by default; and
- `DELETE /v1/documents/{id}` returns 204 idempotently.

IDs follow session-ID syntax; text is non-empty and bounded by `maxDocumentBytes`; metadata is a JSON object ≤ 64 KiB with scalar/list-of-scalar values only. Normalize line endings, chunk deterministically by Unicode code points with configured overlap, and batch embeddings without changing chunk identity. Upsert is atomic: embedding failure leaves the previous version intact.

**RAG-04 (availability)** — If the store/embedding service is unavailable and `required: false`, retrieval logs one redacted error, emits `rag_degraded` only in events/debug mode, and answers without context; readiness remains 200. When required, readiness is 503 and chat returns `rag_unavailable`. Ingestion never degrades silently.

**RAG-05 (lifecycle/security)** — Changing store/embedding/chunk identity fields is a component rebuild and does not silently re-embed old documents; operator migration is explicit. Delete removes all scoped chunks. Secrets follow SEC-04, passthrough follows SEC-02, document content is excluded from logs/traces, and backups/retention are deployment responsibilities.

**RAG-06 (acceptance)** — P4 capability requires deterministic chunk/hash fixtures, principal isolation, metadata/size limits, idempotent atomic upsert, delete, stable ranking/tie-breaks, model-change behavior, prompt-boundary tests, and required/optional dependency failure recovery.

---

## 15a. WebSocket API (Phase 5)

**WS-01 (websocket)** — When `server.protocols.websocket` is true, serve a
WebSocket endpoint at `/v1/ws` with the SAME authentication as the REST
surface (Authorization/X-API-Key headers, or `?token=` injected as a bearer
for browser clients); failed auth closes the socket with code 1008 and
emits an `auth_failure` audit event. One active run per connection:
- Inbound JSON messages (bounded by `server.maxMessageBytes`; oversize
  closes with 1009): `run.start` (message + optional sessionId /
  idempotencyKey), `run.cancel` (cancels the connection's active run),
  `approval.decide` (approve|deny — routes to the SAME engine resume as
  the REST approvals API), `ping`/`pong`.
- Outbound messages mirror the SSE vocabulary: `run.started`,
  `run.iteration`, `run.delta`, `run.tool_call`, `run.tool_result`,
  `run.transfer`, `run.rag_degraded`, `approval.required`, `run.error`,
  `run.done`, `run.cancelled`, `approval.decided`, protocol-level `error`.
- Runs consume the replica-local run cap (`server.maxConcurrentRequests`)
  and the output queue honors `server.streamQueueEvents` +
  `server.slowConsumerSeconds` (a wedged consumer cancels the run and
  records the output-queue-cancellation metric, OBS-05). Client disconnect
  cancels the active run, which commits a terminal state (CNT-07).
- `run.cancel` with no active run answers an `error` `no_active_run`; a
  `run.start` while a run is active answers `run_in_progress`.

**WS-02 (acceptance)** — P5 websocket capability requires tests covering
auth (reject + `?token=` accept), a full run round trip (start → delta →
done), ping/pong, cancel semantics, oversize-message close, sequential
runs on one connection, and unknown-approval errors.

## 15b. Kubernetes CRD / operator (Phase 5)

**K8S-11 (operator)** — A CRD `agentconfigs.agentstrata.io` (group
`agentstrata.io`, v1, Namespaced, status subresource) whose `spec` IS the
Agent Definition document (the CRD validation schema is generated from the
same model as `schemas/agent.schema.json` — `scripts/gen-schemas.py`
regenerates `k8s_operator/crd/`). The `k8s_operator` package reconciles
each AgentConfig into:
- a ConfigMap `agentstrata-<cr>` holding the tier-8 overlay in key
  `agent.yaml` (the runtime's ConfigMap watcher consumes it via
  `AGENT_K8S_NAME`),
- a Deployment `agentstrata-<cr>` (image from the required
  `agentstrata.io/image` annotation; non-root, read-only rootfs, drop ALL,
  /healthz + /readyz probes, 35 s termination grace; single replica —
  multi-replica requires redis/postgres storage per SES-01),
- a Service `agentstrata-<cr>` (ClusterIP :8080),
- status: `observedGeneration`, a Ready condition, and the applied
  ConfigMap + resourceVersion.
All managed objects carry `app.kubernetes.io/managed-by:
agentstrata-operator` labels and an ownerReference to the CR (cluster GC
cleans up on delete). The operator runs in-cluster with
`k8s_operator/rbac.yaml` (least privilege), lists on start, then streams
watch events with a resync timeout; invalid specs and missing image
annotations fail closed with a Ready=False condition. The operator itself
is excluded from the runtime image and the NFR gates.

**K8S-12 (acceptance)** — P5 operator capability requires tests covering
create/update reconcile (ConfigMap overlay content, Deployment env/owner
refs, Service), fail-closed status (invalid spec, missing image),
observedGeneration tracking, the reconcile-all loop, and manifest
validity (CRD + RBAC parse).

## 15c. Cost accounting (Phase 5)

**COST-01 (costs)** — The `costs` config section (default disabled) prices
tokens per model in USD per 1M tokens: `defaultInputPerMillion` /
`defaultOutputPerMillion` plus per-model overrides (`models[].model` must
match the exact `llm.model` string; duplicate model entries are a config
error). When `costs.enabled`, every successful run computes
`costUsd = (input_tokens*inputPrice + output_tokens*outputPrice) / 1e6`
and: records it in the run outcome (`cost_usd`) and the committed usage,
reports it as `usage.costUsd` in non-streaming responses and the final
streaming usage chunk, and records it in the OBS-05 cost counter
(`agentbase_cost_usd_total{model}`). When disabled, no cost field appears
anywhere and no cost is computed (zero surface change).

**COST-02 (acceptance)** — P5 cost capability requires tests covering the
price lookup (exact model entry wins over defaults), the disabled-no-field
invariant, the response `usage.costUsd` field, the run-outcome `cost_usd`,
the OBS-05 cost counter, and the config validation (duplicates, negative
prices).

## 16. Container and runtime packaging

**CNT-01** — The builder uses a digest-pinned `python:3.12-slim`, verifies `requirements.lock` hashes, and installs into a virtual environment. The runtime uses the same pinned slim family and copies only the venv, `app/` (including bundled config), `schemas/`, licenses/notices, and the healthcheck module. It contains no compiler, package-manager cache, source-control data, tests, local config, or build credentials. `.dockerignore` enforces the context boundary.
**CNT-02** — `linux/amd64` and `linux/arm64` images MUST be built from one manifest and dependency lock, pass the same smoke suite, and report their digests. The amd64 uncompressed image size reported by the container runtime MUST be ≤ 400 MB for the P1 feature set, measured on the full image as shipped — not a variant with optional provider dependencies stripped out. Release evidence records both architectures.
**CNT-03 (OpenShift-compatible non-root)** — `USER 10001:0`. Every directory the process writes (none by default except optional `storage.path` and `/tmp`) MUST be group-0 owned and group-writable (`chgrp -R 0 && chmod -R g=u`). The image MUST run correctly under an **arbitrary UID** (OpenShift restricted SCC): no logic may assume UID 10001, no writes outside group-writable paths.
**CNT-04** — `ENTRYPOINT ["python","-m","app.main"]` (exec form — PID 1 receives signals directly; no shell wrapper, no tini).
**CNT-05** — Declare `VOLUME /etc/agent` and `EXPOSE 8080`.
**CNT-06** — `ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1`.
**CNT-07 (graceful shutdown)** — First SIGTERM/SIGINT atomically enters draining: readiness fails, new chat runs receive 503, existing runs retain their deadline up to `shutdownGraceSeconds`, and healthz remains live. At grace expiry cancel remaining runs, persist terminal states/usage, checkpoint P3 approvals, flush storage, close reconcilers/MCP/OTel, then stop the listener. Exit 0 only if required flush/close work succeeds; otherwise exit 1. A second signal immediately exits 1. Platform termination grace MUST be at least configured shutdown grace + 10 s; provided manifests use 35 s for the default.
**CNT-08** — Exactly one Uvicorn worker provides async concurrency. Horizontal replicas require Redis/PostgreSQL for shared sessions; memory/file manifests MUST remain at one replica. Uvicorn reload/debug mode is prohibited in the production image.
**CNT-09** — `docker-compose.yaml` MUST be provided at repo root wiring: the runtime, Redis, Postgres, and one sample MCP server, demonstrating mounted config + profile + runtime authentication + secrets-as-env.
**CNT-10 (HEALTHCHECK)** — After bind, the main process atomically writes the actual bound port to `/tmp/agent-runtime-port`. `python -m app.healthcheck` reads that file (fallback 8080), requests loopback `/healthz` with 2 s timeout, and exits 0 only on 200. Docker declares `HEALTHCHECK --interval=30s --timeout=3s --start-period=10s CMD ["python","-m","app.healthcheck"]`. This remains correct when `server.port` comes from files or CLI.
**CNT-11 (read-only rootfs)** — The runtime MUST function with a read-only root filesystem given a writable `/tmp` (K8s `readOnlyRootFilesystem: true` + emptyDir at `/tmp`). No writes outside `/tmp` and the configured `storage.path`. The provided `deployment.yaml` MUST set `readOnlyRootFilesystem: true`, `allowPrivilegeEscalation: false`, and drop all capabilities.
**CNT-12 (supply chain)** — A publishable release MUST generate SPDX or CycloneDX SBOMs for both images, vulnerability-scan OS/Python packages under the project’s documented severity policy, produce build provenance, and keylessly sign image digests and attestations. Failure blocks publication. Base-image and lock updates are reviewed changes; mutable tags alone are prohibited.
**CNT-13 (secrets/build hygiene)** — Image history and layers MUST contain no secret, `.env`, mounted config, package-index credential, or CI token. BuildKit secret mounts MAY supply private-index auth but MUST leave no layer/cache artifact. An automated canary-secret scan is part of the container acceptance criteria (§18).

---

## 17. Required deliverables

**DEL-01 (deliverables)** — The repository/release MUST include: `Dockerfile` and `.dockerignore` producing the runtime image; `docker-compose.yaml` (per CNT-09); pinned/locked dependency manifests; the generated JSON Schema (`schemas/agent.schema.json`, `schemas/agent-overlay.schema.json`) and OpenAPI artifacts; Kubernetes RBAC and Deployment/Service manifests for ConfigMap-watcher mode (§10); the published OpenAI SDK compatibility matrix (NFR-06); documentation covering configuration, deployment to Docker/Kubernetes/other container platforms, and every supported auth/storage option (§1); and an automated test suite satisfying §18. Internal code organization is an implementation decision, not a requirement, with one narrow exception: the entrypoint and healthcheck modules MUST be invocable as `app.main` and `app.healthcheck` (CNT-04, CNT-10), since the Dockerfile's `ENTRYPOINT` and `HEALTHCHECK` commands need a fixed, concrete path. Everything else — how config, engine, storage, protocol, security, and watcher concerns are split into files — is free, provided those concerns remain independently testable.

**DEL-02 (generated artifacts)** — Generated schemas and the OpenAPI document MUST be reproducible from source and carry a generator/version header; hand-editing them is prohibited.

---

## 18. Acceptance criteria

**ACC-01 (release contract)** — The active phase is releasable only when automated tests demonstrate every criterion below for the exact candidate, using injected/faked time, randomness, and model responses so no test depends on a live LLM provider or public network call.

- **Configuration (§3, §4)** — resolution is deterministic across every tier and precedence rule; the schema and phase/capability gates (CAP-01) validate correctly; every documented default, range, and constraint has a passing and a failing case.
- **Engine (§5)** — every run reaches exactly one terminal outcome; cancellation, timeout, iteration, and token-budget limits behave as specified; no tool call is ever executed twice for the same call ID.
- **MCP client (§4.2)** — connect/reconnect and every documented bound are proven against the official MCP SDK for each supported transport; tool filtering, naming, and collision handling behave as specified.
- **Storage (§8)** — the shared session/run/idempotency contract (principal isolation, atomic creation, TTL/capacity limits, deletion cascade, outage/recovery) passes identically for every configured backend (memory, file, Redis, PostgreSQL), demonstrated against a real instance of each rather than an in-memory substitute alone. Redis and PostgreSQL additionally demonstrate the session-fencing guarantee in SES-05, since that is what makes them safe for multi-replica deployment — this is the one area where the durable backends carry an extra proof obligation beyond the shared contract.
- **API surface (§9)** — every route, documented error code, and streaming behavior matches its contract, including the published minimum/maximum supported OpenAI SDK versions.
- **Kubernetes reload (§7, §10)** — every schema field's reload category behaves correctly, including rollback on a failed rebuild and no-op detection on a duplicate/no-change event.
- **Security (§11)** — fail-closed authentication, recursive secret redaction, egress restriction, CORS, and JWT/JWKS handling (including key rotation and JWKS outage) hold under adversarial and failure input.
- **Observability (§12)** — required log/trace/metric correlation fields are present, secrets never leak into them, and disabling OpenTelemetry removes its runtime cost.
- **Container (§16)** — both architectures build; the image runs non-root under an arbitrary UID with a read-only root filesystem; the healthcheck and graceful-shutdown behavior match specification.
- **Performance (§6)** — every threshold is met, and recovery from a provider, MCP, storage, Kubernetes, JWKS, or telemetry-endpoint failure matches this specification with no duplicated side effects and no corrupted or lost committed state.
- **Phases (§13–§15)** — a capability is reported and documented as supported (CAP-02) only once its own phase's criteria above pass in addition to the full P1 regression set.

Test tooling, fixtures, and CI implementation are engineering decisions and are intentionally not specified here.

---

## 19. Traceability and release evidence

**TRC-01 (requirement traceability)** — Every requirement ID in this document MUST be traceable to at least one automated test or documented verification step. Maintain this mapping in a human-readable form (e.g., a matrix keyed by requirement ID) so gaps are visible before release.

**TRC-02 (release provenance)** — Each released image MUST be traceable to the exact source commit, dependency-lock hash, and test results that verified it, so its behavior can be audited against this specification after the fact.

*End of specification.*
